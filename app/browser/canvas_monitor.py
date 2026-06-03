"""
Canvas State Monitor — DOM-Intelligence edition.

Monitors the SAI canvas right panel after SAI hands off to agents.
Uses DOMIntelligence JS probes instead of brittle CSS selectors.

For each agent tab:
  - Waits for the tab to appear (DOM scan, not CSS guess)
  - Clicks it via JS
  - Waits until the agent finishes loading (spinner gone + content stable)
  - Clicks every sub-tab: Overview, Output, Questions, Thinking,
    Workflow, Architecture, Execution trace
  - Reads content text and takes screenshots of each
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from app.browser.dom_intelligence import DOMIntelligence
from app.browser.sai_navigator import (
    AGENT_TAB_SELECTORS,
    SUB_TAB_SELECTORS,
    SUBTAB_CONTENT_SELECTORS,
    FLOW_VIEW_SELECTORS,
    _try_selectors,
)
from app.browser.screenshot_agent import ScreenshotAgent

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2.0

ALL_SUB_TABS = [
    "Overview",
    "Output",
    "Questions",
    "Thinking",
    "Workflow",
    "Architecture",
    "Execution trace",
]

SCREENSHOT_SUB_TABS = {
    "Overview", "Output", "Questions", "Thinking",
    "Workflow", "Architecture", "Execution trace",
}

CANVAS_PANEL_SELECTORS = [
    '[data-testid="canvas-panel"]',
    '[class*="canvas-panel"]',
    '[class*="CanvasPanel"]',
    '[data-testid="flow-view"]',
    '[class*="flow-view"]',
    '[class*="FlowView"]',
    'aside',
    '[class*="right-panel"]',
    '[class*="RightPanel"]',
]


class CanvasEvent:
    def __init__(self, event_type: str, agent: str = "", sub_tab: str = "",
                 content_preview: str = "", screenshot_path: str = ""):
        self.event_type      = event_type
        self.agent           = agent
        self.sub_tab         = sub_tab
        self.content_preview = content_preview[:400] if content_preview else ""
        self.screenshot_path = screenshot_path
        self.timestamp       = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "event_type":      self.event_type,
            "agent":           self.agent,
            "sub_tab":         self.sub_tab,
            "content_preview": self.content_preview,
            "screenshot_path": self.screenshot_path,
            "timestamp":       self.timestamp,
        }


class CanvasMonitor:
    """
    Continuously monitors the Vanij canvas right panel.
    Uses DOMIntelligence for all element detection.
    Call start() after SAI handoff; stop() when done.
    """

    def __init__(self, page, screenshot_agent: ScreenshotAgent, answer_engine=None):
        self.page              = page
        self.ss                = screenshot_agent
        self.dom               = DOMIntelligence(page)
        self._running          = False
        self._task: Optional[asyncio.Task] = None
        self.canvas_timeline:  list[dict] = []
        self._prev_active_agent            = ""
        self._prev_content_hash: dict[str, str] = {}
        self._agent_states:    dict[str, str] = {}
        self._appeared_agents: list[str] = []
        self._flow_view_captured           = False
        self._answer_engine    = answer_engine  # AnswerEngine for canvas Questions tab
        self._answered_form_hashes: set[str] = set()  # dedup: don't re-answer same form

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._monitor_loop())
        logger.info("[CanvasMonitor] Started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[CanvasMonitor] Stopped. %d events", len(self.canvas_timeline))

    def get_timeline(self) -> list[dict]:
        return self.canvas_timeline

    def get_appeared_agents(self) -> list[str]:
        return list(self._appeared_agents)

    def get_agent_state(self, agent_name: str) -> str:
        return self._agent_states.get(agent_name, "unknown")

    # ── Background monitor loop ───────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._poll_canvas()
            except Exception as e:
                logger.debug("[CanvasMonitor] Poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_canvas(self) -> None:
        # 0. Capture Flow View once when it first appears
        if not self._flow_view_captured:
            fv_sel = await _try_selectors(self.page, FLOW_VIEW_SELECTORS, timeout=500)
            if fv_sel:
                self._flow_view_captured = True
                ss_path = await self.ss.capture(label="flow_view_appeared", event_type="canvas")
                self.canvas_timeline.append(CanvasEvent(
                    event_type="flow_view_appeared",
                    agent="flow_view",
                    screenshot_path=ss_path or "",
                ).to_dict())
                logger.info("[CanvasMonitor] Flow View captured")

        # 1. Use DOM intelligence to find visible agent tabs
        try:
            tabs = await self.dom.get_visible_agent_tabs()
            for tab in tabs:
                name = tab.get("name", "").strip()
                if not name or name in self._appeared_agents:
                    continue
                self._appeared_agents.append(name)
                ss_path = await self.ss.capture_agent_state(name, label="tab_appeared")
                self.canvas_timeline.append(CanvasEvent(
                    event_type="agent_tab_appeared",
                    agent=name,
                    screenshot_path=ss_path or "",
                ).to_dict())
                logger.info("[CanvasMonitor] Agent tab appeared: %s", name)
                self._agent_states[name] = "loading"
        except Exception as e:
            logger.debug("[CanvasMonitor] tab scan error: %s", e)

        # 2. Track loading → ready transitions
        is_loading = await self.dom.is_loading()
        for agent_name in self._appeared_agents:
            new_state  = "loading" if is_loading else "ready"
            prev_state = self._agent_states.get(agent_name, "unknown")
            if prev_state == "loading" and new_state == "ready":
                ss_path = await self.ss.capture_agent_state(agent_name, label="agent_ready")
                self.canvas_timeline.append(CanvasEvent(
                    event_type="agent_output_ready",
                    agent=agent_name,
                    screenshot_path=ss_path or "",
                ).to_dict())
                logger.info("[CanvasMonitor] Agent ready: %s", agent_name)
            self._agent_states[agent_name] = new_state

        # 3. Content change detection
        try:
            content = await self.dom.read_canvas_content()
            if content:
                h = str(hash(content[:200]))
                agent_guess = self._prev_active_agent or "unknown"
                if h != self._prev_content_hash.get(agent_guess, ""):
                    self._prev_content_hash[agent_guess] = h
                    self.canvas_timeline.append(CanvasEvent(
                        event_type="content_updated",
                        agent=agent_guess,
                        content_preview=content[:400],
                    ).to_dict())
        except Exception:
            pass

        # 4. Proactively answer any canvas Questions form that is currently visible
        if self._answer_engine is not None:
            try:
                await self._poll_and_answer_canvas_form()
            except Exception as e:
                logger.debug("[CanvasMonitor] canvas form poll error: %s", e)

    # ── Wait for agent to finish loading ─────────────────────────────────────

    async def _wait_agent_content_stable(
        self, agent_name: str, timeout_seconds: int = 60
    ) -> None:
        """
        Wait until the agent tab content is stable (loading gone, text not changing).
        Uses DOM intelligence — no hardcoded selectors.
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        prev_text = ""
        poll = 1.0

        while asyncio.get_event_loop().time() < deadline:
            loading = await self.dom.is_loading()
            if not loading:
                content = await self.dom.read_canvas_content()
                if content and content == prev_text and len(content) > 20:
                    logger.info("[CanvasMonitor] Agent '%s' content stable", agent_name)
                    return
                prev_text = content
            else:
                logger.debug("[CanvasMonitor] Agent '%s' still loading…", agent_name)
            await asyncio.sleep(poll)

        logger.warning(
            "[CanvasMonitor] Agent '%s' content never stabilised after %ds — proceeding",
            agent_name, timeout_seconds,
        )

    # ── Flow View evidence ────────────────────────────────────────────────────

    async def capture_flow_view_evidence(self, navigator, expected_agents: list = None,
                                          user_query: str = "") -> dict:
        """
        Capture the Flow View panel using FlowGraphValidator for structured
        phase extraction, per-phase screenshots, and structural validation.
        Falls back to the legacy flat inner_text approach on any failure.
        """
        from app.browser.flow_graph_validator import FlowGraphValidator
        try:
            fgv = FlowGraphValidator(
                page=self.page,
                screenshot_agent=self.ss,
                run_id=self.ss.run_id or "run",
            )
            evidence = await fgv.capture_and_validate(
                expected_agents=expected_agents or [],
                user_query=user_query,
            )
            return evidence
        except Exception as e:
            logger.warning("[CanvasMonitor] FlowGraphValidator failed, using legacy: %s", e)

        # ── Legacy fallback ───────────────────────────────────────────────────
        evidence = {
            "appeared":      False,
            "phases":        [],
            "phase_count":   0,
            "planned_steps": "",
            "screenshots":   [],
            "screenshot_path": None,
            "validation":    {},
            "timestamp":     datetime.now(timezone.utc).isoformat(),
        }
        try:
            ss = await self.ss.capture(label="flow_view_overview", event_type="canvas")
            if ss:
                evidence["screenshots"].append({"label": "flow_view_overview", "path": ss})
                evidence["screenshot_path"] = ss

            fv_sel = await _try_selectors(self.page, FLOW_VIEW_SELECTORS, timeout=5000)
            if fv_sel:
                evidence["appeared"] = True
                try:
                    text = await self.page.inner_text(fv_sel)
                    evidence["planned_steps"] = (text or "").strip()[:800]
                except Exception:
                    pass
                try:
                    el = await self.page.query_selector(fv_sel)
                    if el:
                        os.makedirs("screenshots", exist_ok=True)
                        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        path = f"screenshots/{self.ss.run_id or 'run'}_flow_view_element_{ts}.png"
                        await el.screenshot(path=path)
                        evidence["screenshots"].append({"label": "flow_view_element", "path": path})
                        evidence["screenshot_path"] = path
                except Exception:
                    pass
        except Exception as e2:
            logger.warning("[CanvasMonitor] capture_flow_view_evidence legacy: %s", e2)
        return evidence

    # ── Human-like element click (real pointer events) ───────────────────────

    async def _human_click_tab(self, selectors: list, label: str, timeout: int = 15) -> bool:
        """
        Click a tab using a shared deadline across all selectors.
        Scrolls into view first (handles right-panel tabs that are off-screen),
        then real Playwright pointer events. JS fallback if all locators miss.
        """
        loop = asyncio.get_event_loop()
        dead = loop.time() + timeout

        while loop.time() < dead:
            for sel in selectors:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0:
                        await el.scroll_into_view_if_needed()
                        await asyncio.sleep(0.1)
                        if await el.is_visible() and await el.is_enabled():
                            await el.hover()
                            await asyncio.sleep(0.12)
                            await el.click()
                            logger.info("[CanvasMonitor] Clicked '%s' via: %s", label, sel)
                            await asyncio.sleep(0.5)
                            return True
                        # force-click even if partially clipped
                        await el.click(force=True)
                        logger.info("[CanvasMonitor] Force-clicked '%s' via: %s", label, sel)
                        await asyncio.sleep(0.5)
                        return True
                except Exception:
                    pass

            # JS fallback: match by text content
            try:
                tab_text = label.replace("agent tab ", "").replace("sub-tab ", "").strip()
                clicked = await self.page.evaluate("""(text) => {
                    const els = Array.from(document.querySelectorAll(
                        'button, [role="tab"], [class*="tab"], a'
                    )).filter(el => !el.disabled);
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (t === text || t.startsWith(text)) {
                            el.scrollIntoView({block: 'center'});
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""", tab_text)
                if clicked:
                    logger.info("[CanvasMonitor] JS-clicked '%s'", label)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                pass

            await asyncio.sleep(0.5)

        logger.warning("[CanvasMonitor] Could not click '%s'", label)
        return False

    # ── Per-agent evidence capture ────────────────────────────────────────────

    async def capture_agent_evidence(self, agent_name: str, navigator) -> dict:
        """
        Full canvas capture sequence for one agent:

          1. Click the agent tab and wait for content to stabilise.
          2. Take a full-page screenshot (orientation/context).
          3. Focus the right-side canvas panel → Ctrl+scroll zoom in × 3
             → screenshot the zoomed canvas element → zoom back out.
          4. For every sub-tab (Overview, Output, Questions, Thinking,
             Workflow, Architecture, Execution trace):
               a. Click the sub-tab with real Playwright pointer events.
               b. Wait for content to load and stabilise.
               c. Answer any question forms (Questions tab).
               d. Scroll through virtualised content and extract full text.
               e. Screenshot the sub-tab content element (zoomed).
               f. Zoom out back to normal.
          5. Return evidence dict with all screenshots + extracted text.
        """
        from app.browser.canvas_zoomer import CanvasZoomer

        evidence = {
            "agent":           agent_name,
            "appeared":        agent_name in self._appeared_agents,
            "state":           self._agent_states.get(agent_name, "unknown"),
            "sub_tabs":        {},
            "screenshots":     [],
            "timeline_events": [e for e in self.canvas_timeline if e.get("agent") == agent_name],
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

        # ── 1. Click agent tab ────────────────────────────────────────────────
        agent_tab_selectors = [
            f'button:has-text("{agent_name}")',
            f'[role="tab"]:has-text("{agent_name}")',
            f'[class*="tab"]:has-text("{agent_name}")',
            f'a:has-text("{agent_name}")',
        ]
        clicked = await self._human_click_tab(agent_tab_selectors, f"agent tab {agent_name}", timeout=15)
        if not clicked:
            clicked = await self.dom.click_agent_tab(agent_name)
        if not clicked:
            clicked = await navigator.navigate_to_agent_tab(agent_name)

        if not clicked and not evidence["appeared"]:
            logger.warning("[CanvasMonitor] Agent tab '%s' never appeared", agent_name)
            ss = await self.ss.capture_agent_state(agent_name, label="never_appeared")
            if ss:
                evidence["screenshots"].append({"label": "never_appeared", "path": ss, "sub_tab": ""})
            return evidence

        if clicked:
            evidence["appeared"] = True
            if agent_name not in self._appeared_agents:
                self._appeared_agents.append(agent_name)
            self._agent_states[agent_name] = "loading"
            logger.info("[CanvasMonitor] Clicked agent tab: %s", agent_name)

        # ── 2. Wait for content to stabilise ─────────────────────────────────
        await self._wait_agent_content_stable(agent_name, timeout_seconds=120)
        self._agent_states[agent_name] = "ready"

        # Full-page screenshot (shows the tab in context, un-zoomed)
        full_ss = await self.ss.capture_agent_state(agent_name, label="tab_opened")
        if full_ss:
            evidence["screenshots"].append({"label": "tab_opened", "path": full_ss, "sub_tab": ""})
            logger.info("[CanvasMonitor] Full-page SS after tab open: %s", full_ss)

        # ── 2b. AIA: capture the workflow graph specifically ──────────────────
        # AIA renders a visual workflow graph showing the build plan.
        # Capture it at full resolution before touching any sub-tabs.
        if agent_name == "AIA":
            await self._capture_aia_workflow_graph(evidence)

        # ── 3. Zoom canvas panel → screenshot → zoom out ─────────────────────
        os.makedirs("screenshots", exist_ok=True)
        ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_agent  = agent_name.replace(" ", "_")
        zoomed_path = f"screenshots/{self.ss.run_id or 'run'}_{safe_agent}_canvas_zoomed_{ts}.png"

        async with CanvasZoomer(self.page) as cz:
            zoomed_ss = await cz.screenshot_panel_after_zoom(
                path=zoomed_path,
                zoom_ticks=3,
            )
        if zoomed_ss:
            evidence["screenshots"].append({
                "label":   "canvas_panel_zoomed",
                "path":    zoomed_ss,
                "sub_tab": "",
            })
            logger.info("[CanvasMonitor] Canvas zoomed SS: %s", zoomed_ss)
        else:
            logger.warning("[CanvasMonitor] Could not take zoomed canvas SS for %s", agent_name)

        # ── 4. Click every sub-tab, screenshot, extract text ─────────────────
        for sub_tab in ALL_SUB_TABS:
            sub_tab_selectors = [
                f'button:has-text("{sub_tab}")',
                f'[role="tab"]:has-text("{sub_tab}")',
                f'[class*="tab"]:has-text("{sub_tab}")',
                f'[class*="subtab"]:has-text("{sub_tab}")',
            ]

            # 4a. Click sub-tab
            tab_clicked = await self._human_click_tab(sub_tab_selectors, f"sub-tab {sub_tab}", timeout=6)
            if not tab_clicked:
                tab_clicked = await self.dom.click_sub_tab(sub_tab)
            if not tab_clicked:
                tab_clicked = await navigator.navigate_to_sub_tab(sub_tab)
            if not tab_clicked:
                logger.debug("[CanvasMonitor] Sub-tab '%s' not found for %s — skipping",
                             sub_tab, agent_name)
                continue

            logger.info("[CanvasMonitor] Opened sub-tab: %s / %s", agent_name, sub_tab)

            # 4b. Wait for content to load and stabilise
            await self._wait_sub_tab_content_ready(timeout_seconds=30)

            # 4c. Answer question forms on Questions tab
            if sub_tab == "Questions" and self._answer_engine is not None:
                await self._answer_canvas_questions_tab(agent_name)

            # 4d. Scroll through virtualised content and extract full text
            content = await self._scroll_and_extract_full_content()
            if not content:
                async with CanvasZoomer(self.page) as cz:
                    content = await cz.read_text()
            if not content:
                content = await self.dom.read_canvas_content()
            if not content:
                try:
                    content = await navigator.read_sub_tab_content(sub_tab)
                except Exception:
                    content = ""

            evidence["sub_tabs"][sub_tab] = content or ""
            logger.info("[CanvasMonitor] %s / %s: %d chars extracted",
                        agent_name, sub_tab, len(content))

            # 4e. Screenshot the sub-tab content element — zoom in, capture, zoom out
            if sub_tab in SCREENSHOT_SUB_TABS:
                ts_sub     = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                safe_sub   = sub_tab.replace(" ", "_")
                zoomed_sub_path = (
                    f"screenshots/{self.ss.run_id or 'run'}"
                    f"_{safe_agent}_{safe_sub}_zoomed_{ts_sub}.png"
                )

                # Try to screenshot the specific content element (zoomed)
                took_element_ss = False
                for content_sel in SUBTAB_CONTENT_SELECTORS:
                    try:
                        el = await self.page.query_selector(content_sel)
                        if el and await el.is_visible():
                            async with CanvasZoomer(self.page) as cz:
                                result = await cz.screenshot_panel_after_zoom(
                                    path=zoomed_sub_path,
                                    selector=content_sel,
                                    zoom_ticks=3,
                                )
                            if result:
                                evidence["screenshots"].append({
                                    "label":   f"{safe_sub}_zoomed",
                                    "path":    result,
                                    "sub_tab": sub_tab,
                                })
                                logger.info(
                                    "[CanvasMonitor] Sub-tab zoomed SS: %s / %s → %s",
                                    agent_name, sub_tab, result,
                                )
                                took_element_ss = True
                                break
                    except Exception:
                        continue

                # Fallback: full-page screenshot of the current sub-tab view
                if not took_element_ss:
                    fallback_ss = await self.ss.capture_agent_state(
                        agent_name, sub_tab=sub_tab, label="subtab_fallback"
                    )
                    if fallback_ss:
                        evidence["screenshots"].append({
                            "label":   f"{safe_sub}_fallback",
                            "path":    fallback_ss,
                            "sub_tab": sub_tab,
                        })

        # ── 5. App Studio: return to Output tab for final zoomed screenshot ───
        if agent_name == "App Studio":
            output_selectors = [
                'button:has-text("Output")',
                '[role="tab"]:has-text("Output")',
            ]
            await self._human_click_tab(output_selectors, "App Studio Output", timeout=6)
            await self._wait_sub_tab_content_ready(timeout_seconds=15)
            ts_final   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            final_path = (
                f"screenshots/{self.ss.run_id or 'run'}"
                f"_App_Studio_Output_final_zoomed_{ts_final}.png"
            )
            async with CanvasZoomer(self.page) as cz:
                final_ss = await cz.screenshot_panel_after_zoom(
                    path=final_path, zoom_ticks=3,
                )
            if final_ss:
                evidence["screenshots"].append({
                    "label":   "final_solution_zoomed",
                    "path":    final_ss,
                    "sub_tab": "Output",
                })
                logger.info("[CanvasMonitor] App Studio final solution zoomed SS: %s", final_ss)

        logger.info(
            "[CanvasMonitor] Done '%s': %d sub-tabs captured, %d screenshots taken",
            agent_name, len(evidence["sub_tabs"]), len(evidence["screenshots"]),
        )
        return evidence

    # ── Continuous canvas form polling ────────────────────────────────────────

    async def _poll_and_answer_canvas_form(self) -> None:
        """
        Called every poll cycle. Checks the ENTIRE visible page for any
        unanswered question form with a Submit button.
        Uses a fingerprint of QUESTION LABELS (not input state) for dedup —
        so a form that appeared but had MCQs un-clicked is NOT skipped.
        """
        # Quick probe: visible, enabled Submit / Submit answers button?
        has_submit = await self.page.evaluate("""() => {
            return !!Array.from(document.querySelectorAll('button'))
                .find(b => /submit answers|submit/i.test((b.innerText||'').trim())
                       && b.offsetParent !== null
                       && !b.disabled);
        }""") or False

        if not has_submit:
            return

        # Fingerprint = sorted question-label texts only (not input values/state)
        # This means the same form is always the same fingerprint regardless of
        # whether MCQs are checked or text fields are filled.
        form_fingerprint = await self.page.evaluate("""() => {
            const labels = [];
            // Grab every visible label/legend/heading near inputs
            for (const el of document.querySelectorAll(
                'label, legend, [class*="label"], [class*="question-title"], p, h3, h4, h5'
            )) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').replace(/\\s+/g,' ').trim();
                if (t.length > 4 && t.length < 200) labels.push(t.slice(0,60));
            }
            return [...new Set(labels)].sort().join('|');
        }""") or ""

        if not form_fingerprint or form_fingerprint in self._answered_form_hashes:
            return

        logger.info("[CanvasMonitor] New canvas form detected — answering (fp=%s…)",
                    form_fingerprint[:80])
        await self._answer_visible_form()
        self._answered_form_hashes.add(form_fingerprint)

    async def _answer_visible_form(self) -> None:
        """
        Answer all visible question inputs on the page and click Submit.
        Handles MCQ (radio/checkbox) and free-text inputs (input/textarea).
        Scopes the scan to the right-side canvas panel when present so it
        reliably finds questions that live in the PRD/canvas Questions tab.
        After submit, waits for Overview to appear and captures it.
        """
        try:
            # ── Step 1: Scan all questions ────────────────────────────────────
            questions = await self.page.evaluate("""() => {
                const results = [];
                const seenInputs = new WeakSet();

                // Scope to right panel if present (Questions form lives there)
                const rightPanel = document.querySelector(
                    '[class*="right-panel"], [class*="RightPanel"], aside, ' +
                    '[class*="canvas-panel"], [class*="CanvasPanel"], ' +
                    '[data-testid="canvas-panel"], [data-testid="flow-view"]'
                );
                const ROOT = (rightPanel && rightPanel.offsetParent) ? rightPanel : document.body;

                // Helper: is an element or any of its ancestors visible on screen?
                // Custom radio buttons often hide the <input> itself (opacity:0 / position:absolute)
                // but the parent label/wrapper is visible. We include those too.
                function isEffectivelyVisible(el) {
                    if (!el) return false;
                    // Direct check
                    if (el.offsetParent !== null) return true;
                    // Check if parent is visible (hidden input inside visible wrapper)
                    let p = el.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        if (p.offsetParent !== null) return true;
                        p = p.parentElement;
                    }
                    return false;
                }

                // ── Radio / checkbox groups (grouped by name attribute) ────────
                const radios = Array.from(ROOT.querySelectorAll(
                    'input[type="radio"]:not([disabled]), input[type="checkbox"]:not([disabled])'
                )).filter(el => isEffectivelyVisible(el));

                // Group by name (most reliable grouping for radio buttons)
                const byName = {};
                for (const r of radios) {
                    const key = r.name || r.closest('fieldset,ul,ol,[role="group"]')?.id || 'grp_' + results.length;
                    if (!byName[key]) byName[key] = [];
                    seenInputs.add(r);

                    // Resolve label text — try 4 strategies
                    let labelText = '';
                    // 1. <label for="id">
                    if (r.id) {
                        const lbl = document.querySelector('label[for="' + CSS.escape(r.id) + '"]');
                        if (lbl) labelText = lbl.innerText.replace(/\\s+/g,' ').trim();
                    }
                    // 2. wrapping <label>
                    if (!labelText) {
                        const wrap = r.closest('label');
                        if (wrap) {
                            const clone = wrap.cloneNode(true);
                            clone.querySelectorAll('input').forEach(n => n.remove());
                            labelText = clone.innerText.replace(/\\s+/g,' ').trim();
                        }
                    }
                    // 3. next/previous sibling text node or element
                    if (!labelText) {
                        const sib = r.nextElementSibling || r.previousElementSibling;
                        if (sib && sib.tagName !== 'INPUT') labelText = sib.innerText?.replace(/\\s+/g,' ').trim() || '';
                    }
                    // 4. parent's direct text
                    if (!labelText && r.parentElement) {
                        const clone = r.parentElement.cloneNode(true);
                        clone.querySelectorAll('input').forEach(n => n.remove());
                        labelText = clone.innerText.replace(/\\s+/g,' ').trim().slice(0,80);
                    }

                    // Find the best clickable element: prefer visible parent label/wrapper
                    // over the input itself (handles custom-styled radio buttons)
                    let clickTarget = null;
                    const wrap = r.closest('label') || r.parentElement;
                    if (wrap && wrap !== document.body && wrap.offsetParent !== null) {
                        clickTarget = wrap;
                    }

                    byName[key].push({
                        text: labelText || r.value || '',
                        type: r.type,
                        id: r.id || '',
                        name: r.name || '',
                        checked: r.checked,
                        selectorById: r.id ? '#' + CSS.escape(r.id) : null,
                        selectorByVal: r.name ? ('input[name="' + CSS.escape(r.name) + '"][value="' + CSS.escape(r.value) + '"]') : null,
                        // CSS selector for the visible wrapper to click
                        wrapperSelector: (clickTarget && clickTarget.id)
                            ? '#' + CSS.escape(clickTarget.id)
                            : null,
                        value: r.value || '',
                    });
                }

                for (const [key, opts] of Object.entries(byName)) {
                    if (!opts.length) continue;
                    // Find the question label that precedes this group
                    let questionText = '';
                    const firstEl = opts[0].selectorById ? document.querySelector(opts[0].selectorById) : null;
                    if (firstEl) {
                        // Walk up looking for a label/legend/p/h* before the group
                        let el = firstEl.closest('fieldset,div,li,section') || firstEl.parentElement;
                        for (let i = 0; i < 5 && el && el !== document.body; i++) {
                            const cand = el.querySelector('legend,label,p,h3,h4,h5,span[class*="label"],span[class*="question"]');
                            if (cand && cand.offsetParent && !cand.contains(firstEl)) {
                                const t = cand.innerText.replace(/\\s+/g,' ').trim();
                                if (t.length > 5 && t.length < 300) { questionText = t; break; }
                            }
                            el = el.parentElement;
                        }
                    }
                    results.push({ type: 'mcq', questionText, options: opts });
                }

                // ── Text inputs / textareas ────────────────────────────────────
                const textEls = Array.from(ROOT.querySelectorAll(
                    'input[type="text"]:not([disabled]):not([readonly]),' +
                    'input:not([type]):not([disabled]):not([readonly]),' +
                    'textarea:not([disabled]):not([readonly])'
                )).filter(el => isEffectivelyVisible(el) && !seenInputs.has(el));

                for (const el of textEls) {
                    seenInputs.add(el);
                    let labelText = '';
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (lbl) labelText = lbl.innerText.replace(/\\s+/g,' ').trim();
                    }
                    if (!labelText) {
                        let anc = el.parentElement;
                        for (let i = 0; i < 5 && anc && anc !== document.body; i++) {
                            for (const tag of ['label','p','h3','h4','h5','span','legend','div[class*="label"]']) {
                                const found = anc.querySelector(tag);
                                if (found && found.offsetParent && !found.contains(el)) {
                                    const txt = found.innerText.replace(/\\s+/g,' ').trim();
                                    if (txt.length > 3 && txt.length < 300) { labelText = txt; break; }
                                }
                            }
                            if (labelText) break;
                            anc = anc.parentElement;
                        }
                    }
                    results.push({
                        type: 'text',
                        questionText: labelText || el.placeholder || el.name || 'Please fill in',
                        placeholder: el.placeholder || '',
                        selectorById: el.id ? ('#' + CSS.escape(el.id)) : null,
                        tagName: el.tagName.toLowerCase(),
                        currentValue: el.value || '',
                    });
                }
                return results;
            }""") or []

            if not questions:
                logger.debug("[CanvasMonitor] _answer_visible_form: no questions found")
                return

            logger.info("[CanvasMonitor] Answering %d form question(s)", len(questions))

            # ── Step 2: Answer each question ──────────────────────────────────
            for q in questions:
                q_text = q.get("questionText", "").strip()
                q_type = q.get("type", "text")

                if q_type == "mcq":
                    opts = [o for o in q.get("options", []) if o.get("text")]
                    if not opts:
                        continue

                    opt_texts = [o["text"] for o in opts]
                    prompt = (
                        f"SAI is asking: {q_text or 'Which option do you prefer?'}\n\n"
                        f"Options:\n" + "\n".join(f"- {t}" for t in opt_texts)
                        + "\n\nChoose the most relevant option for this persona. "
                        "Reply with ONLY the exact option text, nothing else."
                    )
                    raw = self._answer_engine.generate(prompt)
                    raw_lower = raw.lower().strip()

                    # Pick best matching option
                    chosen_idx = 0
                    for i, o in enumerate(opts):
                        ot = o["text"].lower().strip()
                        if ot == raw_lower or ot in raw_lower or raw_lower in ot:
                            chosen_idx = i
                            break
                    else:
                        for i, o in enumerate(opts):
                            words = [w for w in o["text"].lower().split() if len(w) > 3]
                            if any(w in raw_lower for w in words):
                                chosen_idx = i
                                break

                    chosen = opts[chosen_idx]
                    logger.info("[CanvasMonitor] MCQ '%s' → choosing '%s'",
                                q_text[:50], chosen["text"][:50])

                    clicked = False

                    # Strategy 1: click visible wrapper (label/div around hidden input)
                    wrap_sel = chosen.get("wrapperSelector", "")
                    if wrap_sel and not clicked:
                        try:
                            el = self.page.locator(wrap_sel).first
                            if await el.count() > 0 and await el.is_visible():
                                await el.scroll_into_view_if_needed()
                                await el.click()
                                await asyncio.sleep(0.25)
                                clicked = True
                                logger.info("[CanvasMonitor] MCQ clicked via wrapper selector")
                        except Exception as e:
                            logger.debug("[CanvasMonitor] MCQ wrapper click failed: %s", e)

                    # Strategy 2: force-click the input by id (even if visually hidden)
                    sel_id = chosen.get("selectorById", "")
                    if sel_id and not clicked:
                        try:
                            el = self.page.locator(sel_id).first
                            if await el.count() > 0:
                                await el.scroll_into_view_if_needed()
                                await el.click(force=True)
                                await asyncio.sleep(0.25)
                                clicked = True
                                logger.info("[CanvasMonitor] MCQ clicked via id (force)")
                        except Exception as e:
                            logger.debug("[CanvasMonitor] MCQ id click failed: %s", e)

                    # Strategy 3: force-click by name+value
                    sel_val = chosen.get("selectorByVal", "")
                    if sel_val and not clicked:
                        try:
                            el = self.page.locator(sel_val).first
                            if await el.count() > 0:
                                await el.scroll_into_view_if_needed()
                                await el.click(force=True)
                                await asyncio.sleep(0.25)
                                clicked = True
                                logger.info("[CanvasMonitor] MCQ clicked via name+value (force)")
                        except Exception as e:
                            logger.debug("[CanvasMonitor] MCQ name+value click failed: %s", e)

                    # Strategy 4: text-based locators on visible wrappers
                    if not clicked:
                        text = chosen["text"]
                        for loc in [
                            f'label:has-text("{text}")',
                            f'[role="radio"]:has-text("{text}")',
                            f'[role="option"]:has-text("{text}")',
                            f'div:has-text("{text}")',
                            f'li:has-text("{text}")',
                        ]:
                            try:
                                el = self.page.locator(loc).first
                                if await el.count() > 0 and await el.is_visible():
                                    await el.scroll_into_view_if_needed()
                                    await el.click()
                                    await asyncio.sleep(0.25)
                                    clicked = True
                                    logger.info("[CanvasMonitor] MCQ clicked via text locator: %s", loc)
                                    break
                            except Exception:
                                continue

                    # Strategy 5: JS — walk DOM, find element whose text matches,
                    # click its input child or the wrapper itself, dispatch React events
                    if not clicked:
                        try:
                            result = await self.page.evaluate("""(text) => {
                                const norm = t => (t || '').replace(/\\s+/g,' ').trim().toLowerCase();
                                const target = norm(text);
                                const candidates = [
                                    ...document.querySelectorAll(
                                        'label, [role="radio"], [role="option"], li, div, span'
                                    )
                                ];
                                for (const el of candidates) {
                                    const elText = norm(el.innerText || '');
                                    if (!elText) continue;
                                    if (elText === target || elText.startsWith(target)) {
                                        // Prefer clicking the hidden input directly
                                        const inp = el.querySelector(
                                            'input[type="radio"],input[type="checkbox"]'
                                        );
                                        if (inp && !inp.disabled) {
                                            // Force-set checked state for React
                                            const proto = window.HTMLInputElement.prototype;
                                            const nv = Object.getOwnPropertyDescriptor(proto, 'checked');
                                            if (nv && nv.set) nv.set.call(inp, true);
                                            inp.click();
                                            inp.dispatchEvent(new Event('change', {bubbles:true}));
                                            inp.dispatchEvent(new Event('input',  {bubbles:true}));
                                            return 'input_clicked';
                                        }
                                        // Click the wrapper element itself
                                        if (el.offsetParent !== null) {
                                            el.click();
                                            return 'wrapper_clicked';
                                        }
                                    }
                                }
                                return 'not_found';
                            }""", chosen["text"])
                            if result in ("input_clicked", "wrapper_clicked"):
                                clicked = True
                                logger.info("[CanvasMonitor] MCQ JS click: %s for '%s'",
                                            result, chosen["text"][:50])
                        except Exception as e:
                            logger.debug("[CanvasMonitor] MCQ JS fallback: %s", e)

                    if not clicked:
                        logger.warning("[CanvasMonitor] MCQ: could not click option '%s'",
                                       chosen["text"][:60])
                    await asyncio.sleep(0.4)

                else:  # text / textarea
                    sel = q.get("selectorById", "")
                    current = (q.get("currentValue") or "").strip()
                    if current:
                        logger.debug("[CanvasMonitor] Skipping already-filled field: %s", q_text[:40])
                        continue

                    prompt = (
                        f"SAI is asking: {q_text}\n"
                        f"Hint: {q.get('placeholder','')}\n\n"
                        "Give a short, specific, persona-appropriate answer (1-2 sentences max)."
                    )
                    answer = self._answer_engine.generate(prompt)
                    logger.info("[CanvasMonitor] Text answer for '%s': %s",
                                q_text[:50], answer[:70])

                    filled = False

                    # Strategy 1: Playwright fill via id selector
                    if sel:
                        try:
                            el = self.page.locator(sel).first
                            if await el.count() > 0 and await el.is_visible():
                                await el.scroll_into_view_if_needed()
                                await el.click()
                                await asyncio.sleep(0.1)
                                await el.fill(answer)
                                filled = True
                        except Exception as e:
                            logger.debug("[CanvasMonitor] fill by id failed: %s", e)

                    # Strategy 2: React native setter (handles controlled inputs)
                    if not filled:
                        try:
                            await self.page.evaluate("""([sel, val]) => {
                                const el = (sel ? document.querySelector(sel) : null)
                                    || Array.from(document.querySelectorAll(
                                        'input[type="text"],input:not([type]),textarea'
                                    )).find(e => e.offsetParent && !e.disabled
                                                  && !e.readOnly && !e.value.trim());
                                if (!el) return false;
                                const proto = el.tagName === 'TEXTAREA'
                                    ? window.HTMLTextAreaElement.prototype
                                    : window.HTMLInputElement.prototype;
                                const nv = Object.getOwnPropertyDescriptor(proto, 'value');
                                if (nv && nv.set) nv.set.call(el, val);
                                el.dispatchEvent(new Event('input',  {bubbles:true}));
                                el.dispatchEvent(new Event('change', {bubbles:true}));
                                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
                                return true;
                            }""", [sel, answer])
                            filled = True
                        except Exception as e:
                            logger.debug("[CanvasMonitor] React setter fallback: %s", e)

                    await asyncio.sleep(0.3)

            # ── Step 3: Click Submit ──────────────────────────────────────────
            # Wait briefly for React validation to enable the button after filling fields
            await asyncio.sleep(0.8)

            # Poll up to 5 s for the Submit button to become enabled
            submit_btn = None
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                for btn_sel in [
                    'button:has-text("Submit answers")',
                    'button:has-text("Submit")',
                    '[type="submit"]',
                ]:
                    try:
                        el = self.page.locator(btn_sel).first
                        if (await el.count() > 0
                                and await el.is_visible()
                                and await el.is_enabled()):
                            submit_btn = el
                            break
                    except Exception:
                        pass
                if submit_btn:
                    break
                await asyncio.sleep(0.4)

            submitted = False
            if submit_btn:
                try:
                    await submit_btn.scroll_into_view_if_needed()
                    await submit_btn.hover()
                    await asyncio.sleep(0.15)
                    await submit_btn.click()
                    submitted = True
                    logger.info("[CanvasMonitor] Clicked Submit answers button")
                except Exception as e:
                    logger.warning("[CanvasMonitor] Submit click failed: %s", e)

            if not submitted:
                logger.warning("[CanvasMonitor] Submit button not found or not enabled")

            if submitted:
                # ── Step 4: Wait for Overview to appear, capture it ───────────
                await asyncio.sleep(1.5)
                await self.ss.capture_if_changed(
                    label="canvas_form_submitted", event_type="canvas_answer"
                )
                # Try to click Overview tab and capture its content for the judge
                await self._capture_overview_after_submit()
            else:
                logger.warning("[CanvasMonitor] Could not find Submit button")

        except Exception as e:
            logger.warning("[CanvasMonitor] _answer_visible_form error: %s", e)

    async def _capture_overview_after_submit(self) -> None:
        """
        After submitting a Questions form, click the Overview sub-tab,
        wait for content to load, capture a screenshot and store the text
        in the canvas timeline so the judge can evaluate it.
        """
        try:
            # Wait briefly for UI to update after submit
            await asyncio.sleep(1.0)

            # Try to click Overview tab
            overview_clicked = False
            for loc in [
                'button:has-text("Overview")',
                '[role="tab"]:has-text("Overview")',
                '[class*="tab"]:has-text("Overview")',
            ]:
                try:
                    el = self.page.locator(loc).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        overview_clicked = True
                        logger.info("[CanvasMonitor] Clicked Overview tab after submit")
                        break
                except Exception:
                    continue

            if not overview_clicked:
                return

            # Wait for content to stabilise
            await asyncio.sleep(2.0)
            await self._wait_sub_tab_content_ready(timeout_seconds=20)

            # Capture screenshot
            ss_path = await self.ss.capture(label="overview_after_submit", event_type="canvas")

            # Read Overview content
            from app.browser.canvas_zoomer import CanvasZoomer
            async with CanvasZoomer(self.page) as cz:
                content = await cz.read_text()
            if not content:
                content = await self.dom.read_canvas_content()

            if content:
                self.canvas_timeline.append(CanvasEvent(
                    event_type="overview_captured_after_submit",
                    agent="canvas_agent",
                    sub_tab="Overview",
                    content_preview=content[:400],
                    screenshot_path=ss_path or "",
                ).to_dict())
                # Store full overview text for judge — attach to most recent agent evidence
                logger.info("[CanvasMonitor] Overview captured after submit (%d words)",
                            len(content.split()))
                # Update the last agent's Overview sub_tab with fresh content
                if self._appeared_agents:
                    agent_name = self._appeared_agents[-1]
                    # This will be picked up when capture_agent_evidence runs
                    # Store on a temp attribute for capture_agent_evidence to merge
                    if not hasattr(self, '_post_submit_overview'):
                        self._post_submit_overview = {}
                    self._post_submit_overview[agent_name] = content

        except Exception as e:
            logger.debug("[CanvasMonitor] _capture_overview_after_submit error: %s", e)

    # ── AIA workflow graph capture ────────────────────────────────────────────

    async def _capture_aia_workflow_graph(self, evidence: dict) -> None:
        """
        Capture the AIA workflow graph — the visual build plan that shows
        the sequence of agents and steps SAI will execute.

        Sequence:
          1. Try to click the Workflow sub-tab (shows the graph explicitly).
          2. Wait for the graph to render.
          3. Zoom in × 3 on the canvas panel → screenshot → zoom out.
          4. Also take an un-zoomed full-panel screenshot for context.
          5. Store both in evidence["screenshots"] with label "aia_workflow_graph".
        """
        from app.browser.canvas_zoomer import CanvasZoomer
        logger.info("[CanvasMonitor] Capturing AIA workflow graph…")

        # Try to open the Workflow sub-tab first
        workflow_tab_clicked = False
        for sel in [
            'button:has-text("Workflow")',
            '[role="tab"]:has-text("Workflow")',
            '[class*="tab"]:has-text("Workflow")',
            'button:has-text("Overview")',  # fallback — graph often visible on Overview
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    workflow_tab_clicked = True
                    logger.info("[CanvasMonitor] Clicked AIA Workflow tab: %s", sel)
                    await asyncio.sleep(1.0)
                    break
            except Exception:
                pass

        if not workflow_tab_clicked:
            logger.debug("[CanvasMonitor] AIA Workflow tab not found — using current view")
            await asyncio.sleep(0.5)

        # Wait for the workflow graph / canvas content to stabilise
        await self._wait_sub_tab_content_ready(timeout_seconds=20)

        os.makedirs("screenshots", exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run = self.ss.run_id or "run"

        # Un-zoomed full-panel screenshot (full context)
        unzoomed_path = f"screenshots/{run}_AIA_workflow_graph_context_{ts}.png"
        unzoomed_ss = await self.ss.capture_agent_state("AIA", label="workflow_graph_context")
        if unzoomed_ss:
            evidence["screenshots"].append({
                "label":   "aia_workflow_graph_context",
                "path":    unzoomed_ss,
                "sub_tab": "Workflow",
            })

        # Zoomed screenshot — Ctrl+scroll × 3 on the canvas panel
        zoomed_path = f"screenshots/{run}_AIA_workflow_graph_zoomed_{ts}.png"
        async with CanvasZoomer(self.page) as cz:
            zoomed_ss = await cz.screenshot_panel_after_zoom(
                path=zoomed_path,
                zoom_ticks=3,
            )
        if zoomed_ss:
            evidence["screenshots"].append({
                "label":   "aia_workflow_graph_zoomed",
                "path":    zoomed_ss,
                "sub_tab": "Workflow",
            })
            logger.info("[CanvasMonitor] AIA workflow graph zoomed SS: %s", zoomed_ss)
        else:
            logger.warning("[CanvasMonitor] Could not capture AIA workflow graph")

        # Read workflow graph text content for judge
        workflow_text = await self._scroll_and_extract_full_content()
        if not workflow_text:
            async with CanvasZoomer(self.page) as cz:
                workflow_text = await cz.read_text()
        if workflow_text:
            evidence["sub_tabs"]["Workflow"] = workflow_text
            logger.info("[CanvasMonitor] AIA workflow text: %d chars", len(workflow_text))

    # ── Canvas Questions tab answering ────────────────────────────────────────

    async def _answer_canvas_questions_tab(self, agent_name: str) -> None:
        """
        Answer every question in the canvas right-panel Questions tab.

        Sequence:
          1. Wait for the form to render.
          2. Scroll to the TOP of the panel (so we start from question 1).
          3. Screenshot the visible form as evidence.
          4. SCROLL LOOP — scroll down in steps, answering every MCQ and
             text field that comes into view, top to bottom.
          5. After all questions are answered, scroll to Submit and click it.
          6. Retry Submit once if it was still disabled on first attempt.
          7. Screenshot after submit.
        """
        if self._answer_engine is None:
            return

        logger.info("[CanvasMonitor] Answering Questions tab for agent: %s", agent_name)
        safe_agent = agent_name.replace(" ", "_")

        # ── 1. Wait for form to render ────────────────────────────────────────
        try:
            await self.page.wait_for_function("""() => {
                const rightPanel = document.querySelector(
                    '[class*="right-panel"], [class*="RightPanel"], aside, ' +
                    '[class*="canvas-panel"], [class*="CanvasPanel"]'
                );
                const root = rightPanel || document.body;
                const hasSubmit = !!Array.from(root.querySelectorAll('button'))
                    .find(b => /submit/i.test(b.innerText || '') && b.offsetParent);
                const hasInput  = !!root.querySelector('input:not([disabled]), textarea:not([disabled])');
                return hasSubmit || hasInput;
            }""", timeout=10_000)
            logger.info("[CanvasMonitor] Questions form ready for %s", agent_name)
        except Exception:
            logger.warning("[CanvasMonitor] Questions form not detected within 10 s — trying anyway")

        await asyncio.sleep(0.4)

        # ── 2. Scroll to the very top of the right panel ─────────────────────
        await self.page.evaluate("""() => {
            const panel = document.querySelector(
                '[class*="right-panel"], [class*="RightPanel"], aside, ' +
                '[class*="canvas-panel"], [class*="CanvasPanel"]'
            );
            if (panel) { panel.scrollTop = 0; }
            else { window.scrollTo(0, 0); }
        }""")
        await asyncio.sleep(0.3)

        # ── 3. Screenshot top of form ─────────────────────────────────────────
        await self.ss.capture(
            label=f"questions_top_{safe_agent}",
            event_type="canvas",
        )

        # ── 4. Scroll-and-answer loop — top to bottom ─────────────────────────
        # Track which questions we have already answered (by question text) so
        # we don't re-answer the same field after scrolling reveals it again.
        answered_questions: set = set()
        SCROLL_STEP   = 300   # px per scroll step
        MAX_SCROLLS   = 25    # safety limit
        prev_scroll_y = -1

        for scroll_idx in range(MAX_SCROLLS):
            # Answer everything currently visible
            await self._answer_visible_form_incremental(answered_questions)

            # Screenshot each scroll position for evidence
            if scroll_idx % 3 == 0:   # every 3 scrolls to avoid too many screenshots
                await self.ss.capture(
                    label=f"questions_scroll_{scroll_idx}_{safe_agent}",
                    event_type="canvas",
                )

            # Scroll down one step
            new_scroll_y = await self.page.evaluate(f"""() => {{
                const panel = document.querySelector(
                    '[class*="right-panel"], [class*="RightPanel"], aside, ' +
                    '[class*="canvas-panel"], [class*="CanvasPanel"]'
                );
                if (panel) {{
                    panel.scrollBy(0, {SCROLL_STEP});
                    return panel.scrollTop;
                }}
                window.scrollBy(0, {SCROLL_STEP});
                return window.scrollY;
            }}""")

            if new_scroll_y == prev_scroll_y:
                logger.info("[CanvasMonitor] Reached bottom of Questions panel after %d scrolls",
                            scroll_idx + 1)
                break
            prev_scroll_y = new_scroll_y
            await asyncio.sleep(0.3)

        # Do one final answer pass at the bottom (catches last items)
        await self._answer_visible_form_incremental(answered_questions)

        logger.info("[CanvasMonitor] Answered %d question(s) for %s",
                    len(answered_questions), agent_name)

        # ── 5. Screenshot full form state before submitting ───────────────────
        await self.ss.capture(
            label=f"questions_before_submit_{safe_agent}",
            event_type="canvas",
        )

        # ── 6. Click Submit — poll up to 6 s for it to become enabled ─────────
        submitted = False
        deadline  = asyncio.get_event_loop().time() + 6.0
        while asyncio.get_event_loop().time() < deadline:
            for btn_sel in [
                'button:has-text("Submit answers")',
                'button:has-text("Submit")',
                '[type="submit"]',
            ]:
                try:
                    el = self.page.locator(btn_sel).first
                    if (await el.count() > 0
                            and await el.is_visible()
                            and await el.is_enabled()):
                        await el.scroll_into_view_if_needed()
                        await el.hover()
                        await asyncio.sleep(0.15)
                        await el.click()
                        submitted = True
                        logger.info("[CanvasMonitor] Submitted Questions form for %s", agent_name)
                        break
                except Exception:
                    pass
            if submitted:
                break
            await asyncio.sleep(0.4)

        if not submitted:
            logger.warning("[CanvasMonitor] Could not find enabled Submit button for %s", agent_name)

        # ── 7. Screenshot after submit ────────────────────────────────────────
        await asyncio.sleep(1.5)
        await self.ss.capture(
            label=f"questions_after_submit_{safe_agent}",
            event_type="canvas",
        )

    async def _answer_visible_form_incremental(self, already_answered: set) -> None:
        """
        Answer only questions that are currently visible in the viewport and
        have NOT been answered yet (tracked by question text in already_answered).
        Called repeatedly as the form is scrolled top-to-bottom.
        """
        try:
            questions = await self.page.evaluate("""() => {
                const rightPanel = document.querySelector(
                    '[class*="right-panel"], [class*="RightPanel"], aside, ' +
                    '[class*="canvas-panel"], [class*="CanvasPanel"], ' +
                    '[data-testid="canvas-panel"]'
                );
                const ROOT = (rightPanel && rightPanel.offsetParent) ? rightPanel : document.body;
                const vp = { top: 0, bottom: window.innerHeight };

                function inViewport(el) {
                    const r = el.getBoundingClientRect();
                    return r.bottom > vp.top && r.top < vp.bottom && r.width > 0 && r.height > 0;
                }
                function effectivelyVisible(el) {
                    if (!el) return false;
                    if (el.offsetParent !== null) return true;
                    let p = el.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        if (p.offsetParent !== null) return true;
                        p = p.parentElement;
                    }
                    return false;
                }
                function resolveLabel(el) {
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (lbl) return lbl.innerText.replace(/\\s+/g,' ').trim();
                    }
                    const wrap = el.closest('label');
                    if (wrap) {
                        const c = wrap.cloneNode(true);
                        c.querySelectorAll('input').forEach(n => n.remove());
                        return c.innerText.replace(/\\s+/g,' ').trim();
                    }
                    const sib = el.nextElementSibling || el.previousElementSibling;
                    if (sib && sib.tagName !== 'INPUT') return (sib.innerText||'').trim();
                    if (el.parentElement) {
                        const c = el.parentElement.cloneNode(true);
                        c.querySelectorAll('input').forEach(n => n.remove());
                        return c.innerText.replace(/\\s+/g,' ').trim().slice(0,80);
                    }
                    return '';
                }

                const results = [];
                const seen = new WeakSet();

                // MCQ groups
                const radios = Array.from(ROOT.querySelectorAll(
                    'input[type="radio"]:not([disabled]), input[type="checkbox"]:not([disabled])'
                )).filter(r => effectivelyVisible(r) && inViewport(r));

                const byName = {};
                for (const r of radios) {
                    const key = r.name || ('grp_' + results.length);
                    if (!byName[key]) byName[key] = [];
                    seen.add(r);
                    // question heading
                    let qText = '';
                    let anc = r.closest('fieldset,div,li,section') || r.parentElement;
                    for (let i = 0; i < 5 && anc && anc !== document.body; i++) {
                        const cand = anc.querySelector('legend,p,h3,h4,h5,span[class*="label"],span[class*="question"]');
                        if (cand && cand.offsetParent && !cand.contains(r)) {
                            const t = cand.innerText.replace(/\\s+/g,' ').trim();
                            if (t.length > 5 && t.length < 300) { qText = t; break; }
                        }
                        anc = anc.parentElement;
                    }
                    byName[key].push({
                        text: resolveLabel(r),
                        type: r.type,
                        id:   r.id || '',
                        name: r.name || '',
                        checked: r.checked,
                        selectorById:  r.id   ? '#' + CSS.escape(r.id)   : null,
                        selectorByVal: r.name ? 'input[name="' + CSS.escape(r.name) + '"][value="' + CSS.escape(r.value) + '"]' : null,
                        value: r.value || '',
                        qText,
                    });
                }
                for (const [key, opts] of Object.entries(byName)) {
                    if (opts.length) results.push({ type: 'mcq', questionText: opts[0].qText || key, options: opts });
                }

                // Text inputs / textareas
                const textEls = Array.from(ROOT.querySelectorAll(
                    'input[type="text"]:not([disabled]):not([readonly]),' +
                    'input:not([type]):not([disabled]):not([readonly]),' +
                    'textarea:not([disabled]):not([readonly])'
                )).filter(el => effectivelyVisible(el) && inViewport(el) && !seen.has(el));

                for (const el of textEls) {
                    seen.add(el);
                    let labelText = '';
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (lbl) labelText = lbl.innerText.replace(/\\s+/g,' ').trim();
                    }
                    if (!labelText) {
                        let anc = el.parentElement;
                        for (let i = 0; i < 5 && anc && anc !== ROOT; i++) {
                            for (const tag of ['label','p','h3','h4','h5','span','legend']) {
                                const f = anc.querySelector(tag);
                                if (f && f.offsetParent && !f.contains(el)) {
                                    const t = f.innerText.replace(/\\s+/g,' ').trim();
                                    if (t.length > 3 && t.length < 300) { labelText = t; break; }
                                }
                            }
                            if (labelText) break;
                            anc = anc.parentElement;
                        }
                    }
                    results.push({
                        type:         'text',
                        questionText: labelText || el.placeholder || el.name || 'field',
                        placeholder:  el.placeholder || '',
                        selectorById: el.id ? ('#' + CSS.escape(el.id)) : null,
                        tagName:      el.tagName.toLowerCase(),
                        currentValue: el.value || '',
                    });
                }
                return results;
            }""") or []

            for q in questions:
                q_text = q.get("questionText", "").strip()
                q_key  = q_text[:80]   # dedup key

                if q.get("type") == "mcq":
                    if q_key in already_answered:
                        continue
                    opts = [o for o in q.get("options", []) if o.get("text")]
                    if not opts:
                        continue

                    opt_texts = [o["text"] for o in opts]
                    raw = self._answer_engine.generate(
                        f"SAI is asking: {q_text or 'Which option?'}\n\n"
                        f"Options:\n" + "\n".join(f"- {t}" for t in opt_texts)
                        + "\n\nChoose the best option for this persona. "
                          "Reply ONLY with the exact option text."
                    )
                    raw_lower = raw.lower().strip()
                    chosen_idx = 0
                    for i, o in enumerate(opts):
                        ot = o["text"].lower().strip()
                        if ot == raw_lower or ot in raw_lower or raw_lower in ot:
                            chosen_idx = i
                            break
                    else:
                        for i, o in enumerate(opts):
                            if any(w in raw_lower for w in o["text"].lower().split() if len(w) > 3):
                                chosen_idx = i
                                break

                    chosen = opts[chosen_idx]
                    logger.info("[CanvasMonitor] MCQ '%s' → '%s'",
                                q_key[:40], chosen["text"][:40])
                    await self._click_mcq_option(chosen)
                    already_answered.add(q_key)
                    await asyncio.sleep(0.3)

                else:  # text / textarea
                    if q_key in already_answered:
                        continue
                    if (q.get("currentValue") or "").strip():
                        already_answered.add(q_key)
                        continue

                    answer = self._answer_engine.generate(
                        f"SAI is asking: {q_text}\n"
                        f"Hint: {q.get('placeholder', '')}\n\n"
                        "Give a short, specific, persona-appropriate answer (1-2 sentences max)."
                    )
                    logger.info("[CanvasMonitor] Text '%s' → '%s'",
                                q_key[:40], answer[:50])
                    await self._fill_text_field_by_q(q, answer)
                    already_answered.add(q_key)
                    await asyncio.sleep(0.3)

        except Exception as e:
            logger.debug("[CanvasMonitor] _answer_visible_form_incremental error: %s", e)

    async def _click_mcq_option(self, opt: dict) -> None:
        """Click a single MCQ option using multiple fallback strategies."""
        clicked = False

        # Strategy 1: by element id
        if opt.get("selectorById") and not clicked:
            try:
                el = self.page.locator(opt["selectorById"]).first
                if await el.count() > 0:
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True)
                    clicked = True
            except Exception:
                pass

        # Strategy 2: by name+value
        if opt.get("selectorByVal") and not clicked:
            try:
                el = self.page.locator(opt["selectorByVal"]).first
                if await el.count() > 0:
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True)
                    clicked = True
            except Exception:
                pass

        # Strategy 3: text-based locators
        text = opt.get("text", "")
        if text and not clicked:
            for loc in [
                f'label:has-text("{text}")',
                f'[role="radio"]:has-text("{text}")',
                f'[role="option"]:has-text("{text}")',
                f'li:has-text("{text}")',
            ]:
                try:
                    el = self.page.locator(loc).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        clicked = True
                        break
                except Exception:
                    continue

        # Strategy 4: JS React-native setter
        if text and not clicked:
            try:
                await self.page.evaluate("""(text) => {
                    const norm = t => (t||'').replace(/\\s+/g,' ').trim().toLowerCase();
                    const target = norm(text);
                    for (const el of document.querySelectorAll(
                        'label, [role="radio"], [role="option"], li, div, span'
                    )) {
                        if (!el.offsetParent) continue;
                        if (norm(el.innerText||'') === target ||
                            norm(el.innerText||'').startsWith(target)) {
                            const inp = el.querySelector('input[type="radio"],input[type="checkbox"]');
                            if (inp && !inp.disabled) {
                                const nv = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'checked');
                                if (nv && nv.set) nv.set.call(inp, true);
                                inp.click();
                                inp.dispatchEvent(new Event('change', {bubbles:true}));
                                return;
                            }
                            el.click();
                            return;
                        }
                    }
                }""", text)
                clicked = True
            except Exception:
                pass

        if not clicked:
            logger.warning("[CanvasMonitor] Could not click MCQ option: %s", text[:50])

    async def _fill_text_field_by_q(self, q: dict, answer: str) -> None:
        """Fill a text input or textarea identified by question dict."""
        sel = q.get("selectorById", "")
        filled = False

        if sel:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await asyncio.sleep(0.1)
                    await el.fill(answer)
                    filled = True
            except Exception:
                pass

        if not filled:
            try:
                await self.page.evaluate("""([sel, val]) => {
                    const el = (sel ? document.querySelector(sel) : null)
                        || Array.from(document.querySelectorAll(
                            'input[type="text"],input:not([type]),textarea'
                        )).find(e => e.offsetParent && !e.disabled && !e.readOnly && !e.value.trim());
                    if (!el) return;
                    const proto = el.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const nv = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (nv && nv.set) nv.set.call(el, val);
                    el.dispatchEvent(new Event('input',  {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
                }""", [sel, answer])
            except Exception:
                pass

    async def _wait_sub_tab_content_ready(self, timeout_seconds: int = 15) -> None:
        """Wait for sub-tab content area to have non-empty, stable text."""
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        prev_text = ""
        poll = 1.0

        while asyncio.get_event_loop().time() < deadline:
            loading = await self.dom.is_loading()
            if not loading:
                content = await self.dom.read_canvas_content()
                if content and content == prev_text and len(content) > 20:
                    return
                prev_text = content
            await asyncio.sleep(poll)

    async def _scroll_and_extract_full_content(self) -> str:
        """
        Scroll the active sub-tab content panel to the bottom in increments,
        collecting text at each step.  Merges duplicates and returns the full
        de-duplicated content string.

        This is necessary because many sub-tabs virtualise long outputs —
        content below the fold is only rendered once scrolled into view.
        """
        # Selectors for the scrollable content container (most-specific first)
        scroll_container_js = """() => {
            const candidates = [
                '[data-testid="subtab-content"]',
                '.subtab-content', '.agent-output', '.tab-content',
                '.panel-content', '[class*="output"]', '[class*="content"]',
                'main', '[role="main"]',
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent && el.scrollHeight > el.clientHeight + 20)
                    return sel;
            }
            // Fallback: rightmost tall scrollable div
            const divs = Array.from(document.querySelectorAll('div')).filter(el => {
                if (!el.offsetParent) return false;
                const r = el.getBoundingClientRect();
                return r.right > window.innerWidth * 0.5 && el.scrollHeight > el.clientHeight + 50;
            });
            if (divs.length) {
                divs.sort((a, b) => b.scrollHeight - a.scrollHeight);
                return null;  // use window scroll as fallback
            }
            return null;
        }"""

        # Collect text chunks across scroll positions
        chunks: list[str] = []
        prev_scroll = -1
        scroll_step = 600  # px per scroll increment

        try:
            container_sel = await self.page.evaluate(scroll_container_js)

            for _ in range(20):  # max 20 scroll steps = ~12 000 px
                # Read current visible text
                content = await self.dom.read_canvas_content()
                if content:
                    chunks.append(content)

                # Scroll one step
                if container_sel:
                    new_scroll = await self.page.evaluate(
                        """([sel, step]) => {
                            const el = document.querySelector(sel);
                            if (!el) return -1;
                            el.scrollBy(0, step);
                            return el.scrollTop;
                        }""",
                        [container_sel, scroll_step],
                    )
                else:
                    new_scroll = await self.page.evaluate(
                        """(step) => { window.scrollBy(0, step); return window.scrollY; }""",
                        scroll_step,
                    )

                if new_scroll == prev_scroll:
                    break  # reached the bottom
                prev_scroll = new_scroll
                await asyncio.sleep(0.3)  # let virtualised content render

        except Exception as e:
            logger.debug("[CanvasMonitor] scroll_and_extract error: %s", e)

        if not chunks:
            return ""

        # De-duplicate: merge all chunks, keep unique lines preserving order
        seen: set[str] = set()
        merged_lines: list[str] = []
        for chunk in chunks:
            for line in chunk.splitlines():
                stripped = line.strip()
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    merged_lines.append(line)

        full = "\n".join(merged_lines).strip()
        logger.info("[CanvasMonitor] scroll_and_extract: %d chars from %d scroll steps",
                    len(full), len(chunks))
        return full

    async def wait_for_all_agents_ready(
        self, expected_agents: list[str], timeout_seconds: int = 300
    ) -> dict[str, bool]:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        result = {a: False for a in expected_agents}
        while asyncio.get_event_loop().time() < deadline:
            all_ready = True
            for agent in expected_agents:
                ready = (agent in self._appeared_agents and
                         self._agent_states.get(agent, "unknown") == "ready")
                result[agent] = ready
                if not ready:
                    all_ready = False
            if all_ready:
                break
            await asyncio.sleep(2.0)
        return result
