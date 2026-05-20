"""
Continuous Screenshot Agent.

Runs as a background coroutine alongside the main test run.
Captures screenshots at configurable intervals AND on-demand for specific events.
Every screenshot is timestamped, labelled, and indexed in a manifest.

Features:
- Background polling: screenshot every N seconds automatically
- On-demand capture: call capture(label) at any point
- Canvas-aware: captures each agent tab + sub-tab automatically
- Annotated filenames: {run_id}_{timestamp}_{agent}_{label}.png
- Manifest JSON written at end of session for report embedding
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = "screenshots"


class ScreenshotAgent:
    """
    Continuous screenshot capture agent.
    Start with start_background_capture() and stop with stop().
    """

    def __init__(self, page, run_id: str, interval_seconds: float = 5.0):
        self.page = page
        self.run_id = run_id
        self.interval = interval_seconds
        self._index = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.manifest: list[dict] = []   # all captured screenshots
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    # ── Core capture ──────────────────────────────────────────────────────────

    async def capture(
        self,
        label: str,
        agent: str = "",
        sub_tab: str = "",
        event_type: str = "manual",
        full_page: bool = False,
    ) -> Optional[str]:
        """
        Capture one screenshot. Returns the file path or None on failure.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        parts = [self.run_id, ts]
        if agent:
            parts.append(agent.replace(" ", "_"))
        if sub_tab:
            parts.append(sub_tab.replace(" ", "_"))
        parts.append(label.replace(" ", "_").replace("/", "_"))
        parts.append(f"{self._index:04d}")
        filename = "_".join(parts) + ".png"
        path = os.path.join(SCREENSHOTS_DIR, filename)
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
            }
            self.manifest.append(entry)
            logger.info("[ScreenshotAgent] Captured: %s", filename)
            return path
        except Exception as e:
            logger.warning("[ScreenshotAgent] Capture failed for '%s': %s", label, e)
            return None

    # ── Background polling ────────────────────────────────────────────────────

    async def _background_loop(self) -> None:
        """Continuously capture screenshots at self.interval seconds."""
        while self._running:
            await self.capture(label="auto_poll", event_type="background_poll")
            await asyncio.sleep(self.interval)

    def start_background_capture(self) -> None:
        """Start background screenshot polling as an asyncio Task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._background_loop())
        logger.info("[ScreenshotAgent] Background capture started (interval=%.1fs)", self.interval)

    def stop(self) -> None:
        """Stop background capture."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[ScreenshotAgent] Background capture stopped. Total: %d screenshots", self._index)

    # ── Canvas-aware captures ─────────────────────────────────────────────────

    async def capture_agent_state(self, agent_name: str, sub_tab: str = "", label: str = "state") -> Optional[str]:
        return await self.capture(
            label=label, agent=agent_name, sub_tab=sub_tab, event_type="agent_state"
        )

    async def capture_error(self, context: str, agent: str = "") -> Optional[str]:
        return await self.capture(
            label=f"ERROR_{context}", agent=agent, event_type="error", full_page=False
        )

    async def capture_workflow_transition(self, from_state: str, to_state: str) -> Optional[str]:
        label = f"transition_{from_state}_to_{to_state}"
        return await self.capture(label=label, event_type="transition")

    async def capture_canvas_full(self, agent_name: str) -> list[str]:
        """Capture all sub-tabs for one canvas agent."""
        paths = []
        for sub_tab in ["Overview", "Output", "Questions", "Thinking"]:
            p = await self.capture(
                label="subtab", agent=agent_name, sub_tab=sub_tab, event_type="canvas_subtab"
            )
            if p:
                paths.append(p)
        return paths

    # ── Manifest persistence ──────────────────────────────────────────────────

    def save_manifest(self) -> str:
        """Write manifest JSON. Returns path."""
        path = os.path.join(SCREENSHOTS_DIR, f"{self.run_id}_screenshot_manifest.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "run_id":          self.run_id,
                    "total_count":     self._index,
                    "generated_at":    datetime.now(timezone.utc).isoformat(),
                    "screenshots":     self.manifest,
                }, f, indent=2)
            logger.info("[ScreenshotAgent] Manifest saved: %s", path)
        except Exception as e:
            logger.warning("[ScreenshotAgent] Could not save manifest: %s", e)
        return path

    def get_screenshots_for_agent(self, agent_name: str) -> list[dict]:
        return [s for s in self.manifest if s.get("agent") == agent_name]

    def get_screenshots_by_event(self, event_type: str) -> list[dict]:
        return [s for s in self.manifest if s.get("event_type") == event_type]

    def get_error_screenshots(self) -> list[dict]:
        return [s for s in self.manifest if "ERROR" in s.get("label", "")]
