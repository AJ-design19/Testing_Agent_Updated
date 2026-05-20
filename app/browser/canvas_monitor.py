"""
Canvas State Monitor.

Runs continuously while canvas agents are executing.
Tracks every tab transition, sub-tab change, loading state, streaming output,
and UI anomaly. Maintains a full timeline of canvas events.

This is the "UI Observer Agent" — it watches the right panel in real time
and records everything that happens without requiring explicit navigation calls.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.browser.sai_navigator import (
    AGENT_TAB_SELECTORS,
    SUB_TAB_SELECTORS,
    SUBTAB_CONTENT_SELECTORS,
    _try_selectors,
)
from app.browser.screenshot_agent import ScreenshotAgent

logger = logging.getLogger(__name__)

# How often to poll the canvas for state changes (seconds)
POLL_INTERVAL = 2.0

# Selectors that indicate any agent is still loading/processing
CANVAS_LOADING_SELECTORS = [
    ".loading-skeleton",
    ".animate-pulse",
    '[data-state="loading"]',
    '[aria-busy="true"]',
    ".spinner",
    ".progress-bar",
    '[data-testid="agent-loading"]',
    ".agent-spinner",
    ".agent-thinking",
]

# Selectors that indicate output is ready / completed
CANVAS_DONE_SELECTORS = [
    '[data-testid="agent-complete"]',
    ".agent-complete",
    ".agent-done",
    '[data-state="complete"]',
    '[data-state="done"]',
]


class CanvasEvent:
    """Single canvas state change event."""
    def __init__(self, event_type: str, agent: str = "", sub_tab: str = "",
                 content_preview: str = "", screenshot_path: str = ""):
        self.event_type     = event_type
        self.agent          = agent
        self.sub_tab        = sub_tab
        self.content_preview = content_preview[:400] if content_preview else ""
        self.screenshot_path = screenshot_path
        self.timestamp      = datetime.now(timezone.utc).isoformat()

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
    Continuously monitors the Vanij canvas.
    Call start() to begin background monitoring, stop() to end.
    Produces a full canvas_timeline list of CanvasEvent dicts.
    """

    def __init__(self, page, screenshot_agent: ScreenshotAgent):
        self.page             = page
        self.ss               = screenshot_agent
        self._running         = False
        self._task: Optional[asyncio.Task] = None
        self.canvas_timeline: list[dict] = []
        self._prev_active_agent = ""
        self._prev_content_hash: dict[str, str] = {}   # agent → content hash
        self._agent_states: dict[str, str] = {}        # agent → "loading"|"ready"|"error"|"unknown"
        self._appeared_agents: list[str] = []

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
        logger.info("[CanvasMonitor] Stopped. %d canvas events recorded", len(self.canvas_timeline))

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
                logger.debug("[CanvasMonitor] Poll error (non-fatal): %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_canvas(self) -> None:
        """One poll cycle: check for new agents, tab changes, content changes."""
        # 1. Check which agent tabs are now visible
        for agent_name, selectors in AGENT_TAB_SELECTORS.items():
            if agent_name in self._appeared_agents:
                continue
            sel = await _try_selectors(self.page, selectors, timeout=500)
            if sel:
                self._appeared_agents.append(agent_name)
                ss_path = await self.ss.capture_agent_state(agent_name, label="tab_appeared")
                event = CanvasEvent(
                    event_type="agent_tab_appeared",
                    agent=agent_name,
                    screenshot_path=ss_path or "",
                )
                self.canvas_timeline.append(event.to_dict())
                logger.info("[CanvasMonitor] Agent tab appeared: %s", agent_name)
                self._agent_states[agent_name] = "loading"

        # 2. Detect loading vs ready state for each known agent
        for agent_name in self._appeared_agents:
            is_loading = await self._is_canvas_loading()
            new_state = "loading" if is_loading else "ready"
            prev_state = self._agent_states.get(agent_name, "unknown")
            if new_state != prev_state and prev_state == "loading" and new_state == "ready":
                ss_path = await self.ss.capture_agent_state(agent_name, label="agent_ready")
                event = CanvasEvent(
                    event_type="agent_output_ready",
                    agent=agent_name,
                    screenshot_path=ss_path or "",
                )
                self.canvas_timeline.append(event.to_dict())
                logger.info("[CanvasMonitor] Agent output ready: %s", agent_name)
            self._agent_states[agent_name] = new_state

        # 3. Detect content changes in the active panel
        for sel in SUBTAB_CONTENT_SELECTORS:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    # Use hash of first 200 chars as change detector
                    h = str(hash(text[:200]))
                    agent_guess = self._prev_active_agent or "unknown"
                    prev_h = self._prev_content_hash.get(agent_guess, "")
                    if h != prev_h and text:
                        self._prev_content_hash[agent_guess] = h
                        event = CanvasEvent(
                            event_type="content_updated",
                            agent=agent_guess,
                            content_preview=text[:400],
                        )
                        self.canvas_timeline.append(event.to_dict())
                    break
            except Exception:
                continue

    async def _is_canvas_loading(self) -> bool:
        """Return True if any canvas loading indicator is visible."""
        for sel in CANVAS_LOADING_SELECTORS:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def wait_for_all_agents_ready(
        self, expected_agents: list[str], timeout_seconds: int = 300
    ) -> dict[str, bool]:
        """
        Wait until all expected agents have appeared and are in 'ready' state.
        Returns {agent_name: appeared_and_ready}.
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        result = {a: False for a in expected_agents}

        while asyncio.get_event_loop().time() < deadline:
            all_ready = True
            for agent in expected_agents:
                appeared = agent in self._appeared_agents
                state = self._agent_states.get(agent, "unknown")
                ready = appeared and state == "ready"
                result[agent] = ready
                if not ready:
                    all_ready = False
            if all_ready:
                break
            await asyncio.sleep(2.0)

        return result

    # ── Per-agent detailed evidence ───────────────────────────────────────────

    async def capture_agent_evidence(self, agent_name: str, navigator) -> dict:
        """
        Navigate to an agent tab and capture all sub-tabs with content + screenshots.
        Returns a structured evidence dict for this specific agent.
        """
        evidence = {
            "agent":       agent_name,
            "appeared":    agent_name in self._appeared_agents,
            "state":       self._agent_states.get(agent_name, "unknown"),
            "sub_tabs":    {},
            "screenshots": [],
            "timeline_events": [
                e for e in self.canvas_timeline if e.get("agent") == agent_name
            ],
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

        if not evidence["appeared"]:
            ss = await self.ss.capture_agent_state(agent_name, label="never_appeared")
            evidence["screenshots"].append({
                "label": "never_appeared",
                "path":  ss or "",
                "sub_tab": "",
            })
            return evidence

        # Navigate to the agent tab
        await navigator.navigate_to_agent_tab(agent_name)
        await asyncio.sleep(0.5)

        # Capture opened state
        ss = await self.ss.capture_agent_state(agent_name, label="tab_opened")
        if ss:
            evidence["screenshots"].append({"label": "tab_opened", "path": ss, "sub_tab": ""})

        # Read each sub-tab
        for sub_tab in ["Overview", "Output", "Questions", "Thinking"]:
            navigated = await navigator.navigate_to_sub_tab(sub_tab)
            if navigated:
                await asyncio.sleep(0.5)  # let content render
            content = await navigator.read_sub_tab_content(sub_tab)
            evidence["sub_tabs"][sub_tab] = content

            # Screenshot Output and Thinking (most evidence-rich)
            if sub_tab in ("Output", "Thinking") and navigated:
                ss = await self.ss.capture_agent_state(
                    agent_name, sub_tab=sub_tab, label="subtab_content"
                )
                if ss:
                    evidence["screenshots"].append({
                        "label": sub_tab.lower(),
                        "path":  ss,
                        "sub_tab": sub_tab,
                    })

        # Extra screenshot for App Studio final output
        if agent_name == "App Studio":
            await navigator.navigate_to_sub_tab("Output")
            await asyncio.sleep(1.0)
            ss = await self.ss.capture_agent_state(agent_name, label="final_solution")
            if ss:
                evidence["screenshots"].append({
                    "label": "final_solution",
                    "path":  ss,
                    "sub_tab": "Output",
                })

        return evidence
