"""
Real-time Canvas Change Detector.

Continuously monitors the SAI canvas for any DOM or visual change:
  - DOM mutation (element added/removed/modified)
  - Text content change in any visible region
  - New agent tab appearing
  - Sub-tab content update
  - Loading → ready state transition

On every detected change:
  1. Captures a full-page screenshot
  2. Captures element-level screenshots of the changed region
  3. Extracts DOM text, styles, and accessibility data for the changed region
  4. Appends a structured ChangeEvent to the timeline

The change timeline feeds directly into VisualJudge and RunReportGenerator.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = "screenshots"
POLL_INTERVAL   = 1.5   # seconds between DOM polls


# ── JS: fast DOM fingerprint (hash-able state of the visible DOM) ─────────────
_DOM_FINGERPRINT_JS = """() => {
    // Collect text + structural fingerprint of all visible content regions
    const parts = [];

    // Tab state
    const tabs = Array.from(document.querySelectorAll('[role="tab"]'))
        .filter(el => el.offsetParent)
        .map(el => (el.innerText||'').trim() + ':' + (el.getAttribute('aria-selected')||'false'));
    parts.push('tabs:' + tabs.join(','));

    // Loading state
    const loading = Array.from(document.querySelectorAll('*')).some(el => {
        if (!el.offsetParent) return false;
        const cls = (el.className||'').toString().toLowerCase();
        return cls.includes('animate-spin') || cls.includes('loading') || cls.includes('skeleton');
    });
    parts.push('loading:' + loading);

    // Canvas content hash — right-panel text
    const canvas = document.querySelector(
        '[data-testid="canvas"], [class*="canvas"], [class*="right-panel"], aside, main'
    );
    if (canvas) {
        const text = (canvas.innerText||'').trim().slice(0, 2000);
        parts.push('canvas:' + text.length + ':' + text.slice(0, 100));
    }

    // Chat message count and last message snippet
    const msgs = Array.from(document.querySelectorAll(
        '[class*="message"], [class*="chat-bubble"], [class*="assistant"]'
    )).filter(el => el.offsetParent && (el.innerText||'').trim().length > 10);
    parts.push('msgs:' + msgs.length);
    if (msgs.length > 0) {
        parts.push('last:' + (msgs[msgs.length-1].innerText||'').trim().slice(0, 80));
    }

    // Button state (detect new modals, cards)
    const btns = Array.from(document.querySelectorAll('button'))
        .filter(el => el.offsetParent)
        .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim().slice(0,30))
        .filter(t => t.length > 0)
        .slice(0, 20);
    parts.push('btns:' + btns.join('|'));

    return parts.join('||');
}"""


# ── JS: detect which region changed (rough bounding box of mutations) ─────────
_CHANGED_REGION_JS = """() => {
    // Returns the bounding boxes of elements that appear to have recent content
    // We use a heuristic: elements with data-last-update or recently-added classes
    const recent = [];
    const now = Date.now();

    // Find the most content-heavy visible element in the canvas
    const canvas = document.querySelector(
        '[data-testid="canvas"], [class*="canvas"], [class*="right-panel"], aside'
    );
    if (canvas && canvas.offsetParent) {
        const r = canvas.getBoundingClientRect();
        recent.push({
            region: 'canvas',
            x: Math.round(r.x), y: Math.round(r.y),
            width: Math.round(r.width), height: Math.round(r.height),
            text_preview: (canvas.innerText||'').trim().slice(0, 200),
        });
    }

    // Active tab content
    const activeTab = document.querySelector('[role="tab"][aria-selected="true"]');
    if (activeTab) {
        const r = activeTab.getBoundingClientRect();
        recent.push({
            region: 'active_tab',
            x: Math.round(r.x), y: Math.round(r.y),
            width: Math.round(r.width), height: Math.round(r.height),
            text_preview: (activeTab.innerText||'').trim().slice(0, 60),
        });
    }

    return recent;
}"""


class ChangeEvent:
    """One detected change on the SAI canvas."""

    def __init__(
        self,
        change_type: str,
        description: str,
        screenshot_path: str = "",
        element_screenshots: Optional[list] = None,
        dom_snapshot: str = "",
        text_before: str = "",
        text_after: str = "",
        agent: str = "",
        sub_tab: str = "",
        a11y_issues: Optional[list] = None,
        layout_issues: Optional[list] = None,
    ):
        self.change_type          = change_type
        self.description          = description
        self.screenshot_path      = screenshot_path
        self.element_screenshots  = element_screenshots or []
        self.dom_snapshot         = dom_snapshot
        self.text_before          = text_before[:500] if text_before else ""
        self.text_after           = text_after[:500]  if text_after  else ""
        self.agent                = agent
        self.sub_tab              = sub_tab
        self.a11y_issues          = a11y_issues or []
        self.layout_issues        = layout_issues or []
        self.timestamp            = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "change_type":         self.change_type,
            "description":         self.description,
            "screenshot_path":     self.screenshot_path,
            "element_screenshots": self.element_screenshots,
            "dom_snapshot":        self.dom_snapshot,
            "text_before":         self.text_before,
            "text_after":          self.text_after,
            "agent":               self.agent,
            "sub_tab":             self.sub_tab,
            "a11y_issues":         self.a11y_issues,
            "layout_issues":       self.layout_issues,
            "timestamp":           self.timestamp,
        }


class ChangeDetector:
    """
    Monitors the SAI canvas for real-time changes and captures evidence
    for every detected mutation.

    Usage:
        detector = ChangeDetector(page, screenshot_agent, run_id)
        detector.start()
        ...
        timeline = detector.get_timeline()
        detector.stop()
    """

    def __init__(self, page, screenshot_agent, run_id: str):
        self.page             = page
        self.ss               = screenshot_agent
        self.run_id           = run_id
        self._running         = False
        self._task: Optional[asyncio.Task] = None
        self._prev_fingerprint: str        = ""
        self._prev_tab_state:   str        = ""
        self._change_count:     int        = 0
        self._timeline:         list[dict] = []
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._monitor_loop())
        logger.info("[ChangeDetector] Started (run_id=%s)", self.run_id)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[ChangeDetector] Stopped. %d change events", self._change_count)

    def get_timeline(self) -> list[dict]:
        return list(self._timeline)

    def get_change_count(self) -> int:
        return self._change_count

    # ── Internal poll loop ─────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        # Initial fingerprint — baseline with no changes
        try:
            self._prev_fingerprint = await self.page.evaluate(_DOM_FINGERPRINT_JS) or ""
        except Exception:
            pass

        while self._running:
            try:
                await self._poll()
            except Exception as e:
                logger.debug("[ChangeDetector] poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self) -> None:
        try:
            fingerprint = await self.page.evaluate(_DOM_FINGERPRINT_JS) or ""
        except Exception:
            return

        if fingerprint == self._prev_fingerprint:
            return

        # Change detected — compute diff
        before = self._prev_fingerprint
        self._prev_fingerprint = fingerprint
        self._change_count += 1

        change_type = self._classify_change(before, fingerprint)
        description = self._describe_change(before, fingerprint, change_type)

        logger.info("[ChangeDetector] Change #%d: %s — %s",
                    self._change_count, change_type, description[:80])

        # Capture evidence
        ss_path = await self._capture_change_screenshot(change_type)
        element_ss = await self._capture_element_screenshots()
        dom_snap = await self._save_dom_snapshot(change_type)

        # A11y and layout checks on this state
        a11y_issues    = await self._run_quick_a11y()
        layout_issues  = await self._run_quick_layout()

        event = ChangeEvent(
            change_type=change_type,
            description=description,
            screenshot_path=ss_path or "",
            element_screenshots=element_ss,
            dom_snapshot=dom_snap or "",
            text_before=self._extract_canvas_text(before),
            text_after=self._extract_canvas_text(fingerprint),
            a11y_issues=a11y_issues,
            layout_issues=layout_issues,
        )
        self._timeline.append(event.to_dict())

    # ── Change classification ──────────────────────────────────────────────────

    def _classify_change(self, before: str, after: str) -> str:
        if "tabs:" in before and "tabs:" in after:
            b_tabs = self._part(before, "tabs")
            a_tabs = self._part(after,  "tabs")
            if b_tabs != a_tabs:
                return "tab_change"
        if self._part(before, "loading") == "True" and self._part(after, "loading") == "False":
            return "loading_complete"
        if self._part(before, "loading") == "False" and self._part(after, "loading") == "True":
            return "loading_started"
        if self._part(before, "msgs") != self._part(after, "msgs"):
            return "new_message"
        if self._part(before, "canvas") != self._part(after, "canvas"):
            return "canvas_updated"
        if self._part(before, "btns") != self._part(after, "btns"):
            return "ui_change"
        return "dom_mutation"

    def _describe_change(self, before: str, after: str, change_type: str) -> str:
        if change_type == "tab_change":
            b = self._part(before, "tabs")
            a = self._part(after,  "tabs")
            return f"Tab state changed: {b[:60]} → {a[:60]}"
        if change_type == "loading_complete":
            return "Loading indicator disappeared — content ready"
        if change_type == "loading_started":
            return "Loading indicator appeared — SAI is generating"
        if change_type == "new_message":
            b_count = self._part(before, "msgs")
            a_count = self._part(after,  "msgs")
            last    = self._part(after,  "last")
            return f"New message ({b_count} → {a_count}): {last[:80]}"
        if change_type == "canvas_updated":
            b_len = self._part(before, "canvas").split(":")[1] if ":" in self._part(before, "canvas") else "?"
            a_len = self._part(after,  "canvas").split(":")[1] if ":" in self._part(after,  "canvas") else "?"
            return f"Canvas content updated ({b_len} → {a_len} chars)"
        return f"DOM mutation detected (type={change_type})"

    @staticmethod
    def _part(fingerprint: str, key: str) -> str:
        for seg in fingerprint.split("||"):
            if seg.startswith(key + ":"):
                return seg[len(key) + 1:]
        return ""

    @staticmethod
    def _extract_canvas_text(fingerprint: str) -> str:
        seg = ChangeDetector._part(fingerprint, "canvas")
        return seg.split(":", 2)[-1] if ":" in seg else seg

    # ── Evidence capture ───────────────────────────────────────────────────────

    async def _capture_change_screenshot(self, change_type: str) -> Optional[str]:
        try:
            ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
            fname = f"{self.run_id}_change_{self._change_count:04d}_{change_type}_{ts}.png"
            path  = os.path.join(SCREENSHOTS_DIR, fname)
            await self.page.screenshot(path=path, full_page=False)
            logger.info("[ChangeDetector] Screenshot: %s", fname)
            return path
        except Exception as e:
            logger.debug("[ChangeDetector] screenshot error: %s", e)
            return None

    async def _capture_element_screenshots(self) -> list[dict]:
        """Capture screenshots of the canvas panel and any changed regions."""
        results = []
        try:
            regions = await self.page.evaluate(_CHANGED_REGION_JS) or []
            for region in regions[:3]:
                region_name = region.get("region", "region")
                # Canvas panel element screenshot
                canvas_sels = [
                    '[data-testid="canvas"]', '[class*="canvas"]',
                    '[class*="right-panel"]', 'aside', 'main',
                ]
                for sel in canvas_sels:
                    try:
                        el = await self.page.query_selector(sel)
                        if el and await el.is_visible():
                            ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
                            fname = f"{self.run_id}_change_{self._change_count:04d}_{region_name}_el_{ts}.png"
                            path  = os.path.join(SCREENSHOTS_DIR, fname)
                            await el.screenshot(path=path)
                            results.append({
                                "region":   region_name,
                                "path":     path,
                                "selector": sel,
                                "preview":  region.get("text_preview", ""),
                            })
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("[ChangeDetector] element screenshot error: %s", e)
        return results

    async def _save_dom_snapshot(self, change_type: str) -> Optional[str]:
        """Save a DOM snapshot for this change event."""
        try:
            from app.browser.screenshot_agent import DOM_SNAPSHOTS_DIR
            os.makedirs(DOM_SNAPSHOTS_DIR, exist_ok=True)
            ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
            fname = f"{self.run_id}_change_{self._change_count:04d}_{change_type}_{ts}.html"
            path  = os.path.join(DOM_SNAPSHOTS_DIR, fname)
            html  = await self.page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return path
        except Exception as e:
            logger.debug("[ChangeDetector] DOM snapshot error: %s", e)
            return None

    # ── Quick in-line checks (lightweight, non-blocking) ──────────────────────

    async def _run_quick_a11y(self) -> list[dict]:
        """Run a lightweight ARIA check only (contrast is skipped for speed)."""
        _quick_js = """() => {
            const issues = [];
            for (const btn of document.querySelectorAll('button')) {
                if (!btn.offsetParent) continue;
                const name = (btn.innerText || btn.getAttribute('aria-label') || '').trim();
                if (!name) issues.push({
                    rule: 'button-name', severity: 'major', element: 'button',
                    description: 'Button has no accessible name',
                    element_text: '',
                });
            }
            for (const img of document.querySelectorAll('img')) {
                if (!img.offsetParent) continue;
                if (!img.hasAttribute('alt')) issues.push({
                    rule: 'image-alt', severity: 'major', element: 'img',
                    description: 'Image missing alt attribute',
                    element_text: (img.src||'').split('/').pop().slice(0,30),
                });
            }
            return issues.slice(0, 10);
        }"""
        try:
            return await self.page.evaluate(_quick_js) or []
        except Exception:
            return []

    async def _run_quick_layout(self) -> list[dict]:
        """Run a lightweight overlap check only."""
        _quick_overlap = """() => {
            const vw = window.innerWidth, vh = window.innerHeight;
            const modals = Array.from(document.querySelectorAll(
                '[role="dialog"], [class*="modal"], [class*="overlay"]'
            )).filter(el => el.offsetParent);
            const issues = [];
            if (modals.length > 1) {
                issues.push({
                    rule: 'multiple-modals', severity: 'critical',
                    description: `${modals.length} modal/overlay elements visible simultaneously`,
                    element: 'dialog', element_text: '',
                });
            }
            for (const el of document.querySelectorAll('*')) {
                if (!el.offsetParent) continue;
                const r = el.getBoundingClientRect();
                if (r.right > vw + 5 && r.width < vw) {
                    issues.push({
                        rule: 'overflow-x', severity: 'major',
                        description: `Element overflows viewport by ${Math.round(r.right - vw)}px`,
                        element: el.tagName.toLowerCase(),
                        element_text: (el.innerText||'').trim().slice(0,30),
                    });
                    if (issues.length >= 3) break;
                }
            }
            return issues;
        }"""
        try:
            return await self.page.evaluate(_quick_overlap) or []
        except Exception:
            return []

    # ── Summary helpers ────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Return a compact summary of detected changes for the judge."""
        change_types: dict[str, int] = {}
        for ev in self._timeline:
            ct = ev.get("change_type", "unknown")
            change_types[ct] = change_types.get(ct, 0) + 1

        all_a11y    = [i for ev in self._timeline for i in ev.get("a11y_issues", [])]
        all_layout  = [i for ev in self._timeline for i in ev.get("layout_issues", [])]

        return {
            "total_changes":   self._change_count,
            "change_breakdown": change_types,
            "screenshots_captured": sum(1 for ev in self._timeline if ev.get("screenshot_path")),
            "a11y_issues_total":   len(all_a11y),
            "layout_issues_total": len(all_layout),
            "timeline_length":     len(self._timeline),
        }
