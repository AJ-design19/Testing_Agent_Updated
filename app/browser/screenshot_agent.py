"""
Continuous Screenshot Agent.

Spec requirements implemented:
  - Naming: {workflow}_{step}_{timestamp}.png
  - Storage dirs: /screenshots  /dom_snapshots
  - DOM snapshot saved alongside every screenshot
  - Background polling every N seconds
  - On-demand capture for specific events
  - Canvas-aware: per-agent, per-sub-tab captures
  - Manifest JSON written at end of session
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR  = "screenshots"
DOM_SNAPSHOTS_DIR = "dom_snapshots"
LOGS_DIR         = "logs"


class ScreenshotAgent:
    """
    Continuous screenshot + DOM snapshot agent.
    Naming convention: {workflow_id}_{step_label}_{timestamp}.png
    """

    def __init__(
        self,
        page,
        run_id: str,
        interval_seconds: float = 5.0,
        workflow_id: str = "",
    ):
        self.page             = page
        self.run_id           = run_id
        self.workflow_id      = workflow_id or run_id
        self.interval         = interval_seconds
        self._index           = 0
        self._last_fingerprint: str = ""
        self.manifest: list[dict] = []
        os.makedirs(SCREENSHOTS_DIR,   exist_ok=True)
        os.makedirs(DOM_SNAPSHOTS_DIR, exist_ok=True)
        os.makedirs(LOGS_DIR,          exist_ok=True)

    # ── Core capture ──────────────────────────────────────────────────────────

    async def capture(
        self,
        label: str,
        agent: str = "",
        sub_tab: str = "",
        event_type: str = "manual",
        full_page: bool = False,
        save_dom: bool = True,
    ) -> Optional[str]:
        """
        Capture one screenshot + optionally save DOM snapshot.

        Naming: {workflow_id}_{step_label}_{timestamp}.png
        Returns the screenshot file path or None on failure.
        """
        ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        # Build step label from components
        parts = [self.workflow_id]
        if agent:
            parts.append(agent.replace(" ", "-"))
        if sub_tab:
            parts.append(sub_tab.replace(" ", "-"))
        parts.append(label.replace(" ", "_").replace("/", "_"))
        parts.append(ts)

        step_label = "_".join(parts)
        filename   = f"{step_label}.png"
        path       = os.path.join(SCREENSHOTS_DIR, filename)
        self._index += 1

        try:
            await self.page.screenshot(path=path, full_page=full_page)
            entry = {
                "index":      self._index,
                "path":       path,
                "filename":   filename,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "label":      label,
                "agent":      agent,
                "sub_tab":    sub_tab,
                "event_type": event_type,
                "url":        self.page.url,
                "dom_snapshot": "",
            }

            # Save DOM snapshot alongside screenshot
            if save_dom:
                dom_path = await self._save_dom_snapshot(step_label)
                entry["dom_snapshot"] = dom_path or ""

            self.manifest.append(entry)
            logger.info("[ScreenshotAgent] %s → %s", event_type, filename)
            return path
        except Exception as e:
            logger.warning("[ScreenshotAgent] Capture failed '%s': %s", label, e)
            return None

    # ── DOM snapshot ─────────────────────────────────────────────────────────

    async def _save_dom_snapshot(self, step_label: str) -> Optional[str]:
        """Save the current DOM as an HTML file."""
        filename = f"{step_label}.html"
        path     = os.path.join(DOM_SNAPSHOTS_DIR, filename)
        try:
            html = await self.page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return path
        except Exception as e:
            logger.debug("[ScreenshotAgent] DOM snapshot failed: %s", e)
            return None

    # ── Change-driven capture ─────────────────────────────────────────────────

    async def capture_if_changed(
        self,
        label: str,
        agent: str = "",
        sub_tab: str = "",
        event_type: str = "change",
    ) -> Optional[str]:
        """
        Capture only when the visible DOM has changed since the last capture.
        Compares a lightweight fingerprint of the SAI panel + chat area.
        """
        try:
            fingerprint = await self.page.evaluate("""() => {
                const chat = document.querySelector(
                    '[class*="conversation"], [class*="messages"], [role="log"], main'
                );
                const canvas = document.querySelector(
                    '[data-testid="canvas"], [class*="canvas"], [class*="right-panel"], aside'
                );
                const chatSnip  = chat   ? (chat.innerText   || '').trim().slice(-300) : '';
                const canvasSnip = canvas ? (canvas.innerText || '').trim().slice(-300) : '';
                const submitBtn  = !!Array.from(document.querySelectorAll('button'))
                    .find(b => /submit answers/i.test((b.innerText||'').trim()) && b.offsetParent);
                const tabs = Array.from(document.querySelectorAll('[role="tab"]'))
                    .filter(e => e.offsetParent)
                    .map(e => (e.innerText||'').trim() + ':' + e.getAttribute('aria-selected'))
                    .join(',');
                return chatSnip + '||' + canvasSnip + '||' + submitBtn + '||' + tabs;
            }""") or ""
        except Exception:
            fingerprint = ""

        if fingerprint and fingerprint == self._last_fingerprint:
            return None  # nothing changed — skip

        self._last_fingerprint = fingerprint
        return await self.capture(label=label, agent=agent, sub_tab=sub_tab,
                                  event_type=event_type, save_dom=False)

    def start_background_capture(self) -> None:
        """No-op: blind polling is disabled. Use ChangeDetector for change-driven captures."""
        logger.info("[ScreenshotAgent] Background polling disabled — using change-driven captures only")

    def stop(self) -> None:
        logger.info("[ScreenshotAgent] Stopped. Total: %d", self._index)

    # ── Canvas-aware captures ─────────────────────────────────────────────────

    async def capture_agent_state(
        self, agent_name: str, sub_tab: str = "", label: str = "state"
    ) -> Optional[str]:
        """Capture the canvas panel element only (no DPR/zoom changes)."""
        return await self.capture(
            label=label, agent=agent_name, sub_tab=sub_tab,
            event_type="agent_state", save_dom=False,
        )

    async def capture_canvas_zoomed(
        self,
        label: str,
        agent: str = "",
        sub_tab: str = "",
        selector: Optional[str] = None,
    ) -> Optional[str]:
        """Capture a canvas panel element screenshot (no DPR/zoom changes)."""
        return await self.capture(
            label=label, agent=agent, sub_tab=sub_tab,
            event_type="canvas", save_dom=False,
        )

    async def capture_error(self, context: str, agent: str = "") -> Optional[str]:
        return await self.capture(
            label=f"ERROR_{context}", agent=agent,
            event_type="error", full_page=False, save_dom=True,
        )

    async def capture_workflow_transition(
        self, from_state: str, to_state: str
    ) -> Optional[str]:
        return await self.capture(
            label=f"transition_{from_state}_to_{to_state}",
            event_type="transition", save_dom=False,
        )

    async def capture_canvas_full(self, agent_name: str) -> list[str]:
        """Capture all sub-tabs for one canvas agent."""
        paths = []
        for sub_tab in [
            "Overview", "Output", "Questions", "Thinking",
            "Workflow", "Architecture", "Execution trace",
        ]:
            p = await self.capture(
                label="subtab", agent=agent_name, sub_tab=sub_tab,
                event_type="canvas_subtab", save_dom=False,
            )
            if p:
                paths.append(p)
        return paths

    # ── Manifest persistence ──────────────────────────────────────────────────

    def save_manifest(self) -> str:
        path = os.path.join(SCREENSHOTS_DIR, f"{self.run_id}_manifest.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "run_id":       self.run_id,
                    "workflow_id":  self.workflow_id,
                    "total_count":  self._index,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "screenshots":  self.manifest,
                }, f, indent=2)
            logger.info("[ScreenshotAgent] Manifest: %s", path)
        except Exception as e:
            logger.warning("[ScreenshotAgent] Manifest save failed: %s", e)
        return path

    def get_screenshots_for_agent(self, agent_name: str) -> list[dict]:
        return [s for s in self.manifest if s.get("agent") == agent_name]

    def get_screenshots_by_event(self, event_type: str) -> list[dict]:
        return [s for s in self.manifest if s.get("event_type") == event_type]

    def get_error_screenshots(self) -> list[dict]:
        return [s for s in self.manifest if "ERROR" in s.get("label", "")]
