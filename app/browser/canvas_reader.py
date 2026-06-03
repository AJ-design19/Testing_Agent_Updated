"""
Canvas reader module.

After SAI hands off to the canvas agents, this module:
  - Detects which agent tabs appeared (AIA / AGP / ETL / App Studio)
  - For each agent tab, reads all sub-tabs: Overview · Output · Questions · Thinking
  - Captures screenshots of workflow graphs, code generation, and final solutions
  - Records canvas state as a structured evidence package for the LLM Judge
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.browser.sai_navigator import SAINavigator

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = "screenshots"

# How long to wait (seconds) for an agent tab to appear after SAI hands off
AGENT_WAIT_TIMEOUT = 120  # seconds — agents can be slow to start

# Selectors that indicate a canvas agent is still loading / processing
AGENT_LOADING_SELECTORS = [
    '[data-testid="agent-loading"]',
    ".agent-spinner",
    ".agent-thinking",
    ".canvas-loading",
    '[aria-label="Agent is working"]',
    ".loading-skeleton",
    ".progress-bar[aria-valuenow]",
]

# Selectors that indicate agent output is ready / settled
AGENT_READY_SELECTORS = [
    '[data-testid="agent-complete"]',
    ".agent-complete",
    ".agent-done",
    '[aria-label="Agent complete"]',
]

# Agent-specific notes for evaluation context
AGENT_DESCRIPTIONS = {
    "AIA": "AI Application agent — builds the application workflow graph",
    "AGP": "AI Governance Protocols agent — defines compliance and governance rules",
    "ETL": "Data Mapping / ETL agent — configures data sources and transformations",
    "App Studio": "Code generation agent with internal adversarial reviewer",
}


class CanvasReader:
    """
    Reads the SAI canvas after SAI hands off to agents.
    Produces a structured evidence dict consumed by the LLM Judge.
    """

    def __init__(self, page, navigator: SAINavigator, run_id: str):
        self.page = page
        self.navigator = navigator
        self.run_id = run_id
        self._screenshot_index = 0

    # ── Wait helpers ──────────────────────────────────────────────────────

    async def _wait_for_agent_idle(self, agent_name: str, timeout_seconds: int = 120) -> None:
        """
        Wait until the agent tab stops showing a loading/processing indicator.
        Strategy:
          1. If a known loading selector is visible, wait for it to disappear.
          2. Fall back to text-stability polling (same content 2 checks apart).
        """
        # Step 1 — loading indicator
        for sel in AGENT_LOADING_SELECTORS:
            try:
                await self.page.wait_for_selector(sel, state="visible", timeout=4000)
                await self.page.wait_for_selector(
                    sel, state="hidden", timeout=timeout_seconds * 1000
                )
                logger.debug("[CanvasReader] %s loading indicator gone (%s)", agent_name, sel)
                return
            except Exception:
                continue

        # Step 2 — ready indicator
        for sel in AGENT_READY_SELECTORS:
            try:
                await self.page.wait_for_selector(
                    sel, state="visible", timeout=timeout_seconds * 1000
                )
                logger.debug("[CanvasReader] %s ready indicator found (%s)", agent_name, sel)
                return
            except Exception:
                continue

        # Step 3 — text stability fallback
        content_selectors = [
            '[data-testid="subtab-content"]',
            ".subtab-content",
            ".agent-output",
            ".tab-content",
            ".panel-content",
        ]
        prev_text = ""
        stable_count = 0
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            for sel in content_selectors:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text and text == prev_text:
                            stable_count += 1
                            if stable_count >= 2:
                                logger.debug("[CanvasReader] %s content stabilised", agent_name)
                                return
                        else:
                            stable_count = 0
                        prev_text = text
                        break
                except Exception:
                    continue
            await asyncio.sleep(2.0)

        logger.warning("[CanvasReader] _wait_for_agent_idle timed out for %s", agent_name)

    async def _wait_for_sub_tab_content(self, timeout_seconds: int = 30) -> None:
        """Wait for a sub-tab's content area to have non-empty, stable text."""
        content_selectors = [
            '[data-testid="subtab-content"]',
            ".subtab-content",
            ".agent-output",
            ".tab-content",
            ".panel-content",
        ]
        prev_text = ""
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            for sel in content_selectors:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text and text == prev_text:
                            return
                        prev_text = text
                        break
                except Exception:
                    continue
            await asyncio.sleep(1.5)

    # ── Screenshot helper ─────────────────────────────────────────────────

    async def _screenshot(self, label: str) -> Optional[str]:
        """Take a screenshot and return its path."""
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        filename = f"{self.run_id}_{label}_{self._screenshot_index:03d}.png"
        self._screenshot_index += 1
        path = os.path.join(SCREENSHOTS_DIR, filename)
        try:
            await self.page.screenshot(path=path, full_page=False)
            logger.info("[CanvasReader] Screenshot saved: %s", path)
            return path
        except Exception as e:
            logger.warning("[CanvasReader] Screenshot failed for %s: %s", label, e)
            return None

    # ── Flow View capture ─────────────────────────────────────────────────

    async def capture_flow_view(self) -> dict:
        """Wait for Flow View to appear and stabilise, then read and screenshot it."""
        logger.info("[CanvasReader] Waiting for Flow View...")
        appeared = await self.navigator.wait_for_flow_view(timeout=40000)
        # Wait for Flow View content to stabilise before reading
        if appeared:
            await self._wait_for_sub_tab_content(timeout_seconds=20)
        text = await self.navigator.read_flow_view()
        screenshot = await self._screenshot("flow_view")
        return {
            "appeared": appeared,
            "planned_steps": text,
            "screenshot": screenshot,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Single agent tab capture ──────────────────────────────────────────

    async def capture_agent_tab(self, agent_name: str) -> dict:
        """
        Navigate to an agent tab, read all sub-tabs, and take screenshots
        at key moments (graph loading, code generation, final output).
        Returns a structured evidence dict for this agent.
        """
        logger.info("[CanvasReader] Capturing agent tab: %s", agent_name)

        # Wait for the tab to appear (agents start sequentially)
        appeared = await self.navigator.wait_for_agent_tab(
            agent_name, timeout=AGENT_WAIT_TIMEOUT * 1000
        )

        if not appeared:
            logger.warning("[CanvasReader] Agent tab '%s' never appeared", agent_name)
            return {
                "agent": agent_name,
                "appeared": False,
                "sub_tabs": {},
                "screenshots": [],
                "description": AGENT_DESCRIPTIONS.get(agent_name, ""),
            }

        await self.navigator.navigate_to_agent_tab(agent_name)

        # Wait for agent to finish processing before reading
        await self._wait_for_agent_idle(agent_name, timeout_seconds=AGENT_WAIT_TIMEOUT)

        # Screenshot: agent tab settled
        screenshots = []
        ss = await self._screenshot(f"{agent_name.replace(' ', '_')}_opened")
        if ss:
            screenshots.append({"label": "tab_opened", "path": ss, "sub_tab": "opened"})

        # Read all sub-tabs — wait for each to have stable content
        sub_tabs: dict[str, str] = {}
        for sub_tab in ["Overview", "Output", "Questions", "Thinking"]:
            navigated = await self.navigator.navigate_to_sub_tab(sub_tab)
            if navigated:
                await self._wait_for_sub_tab_content(timeout_seconds=30)
            content = await self.navigator.read_sub_tab_content(sub_tab)
            sub_tabs[sub_tab] = content

            # Screenshot the Output and Thinking sub-tabs (most evidence-rich)
            if sub_tab in ("Output", "Thinking") and navigated:
                ss = await self._screenshot(f"{agent_name.replace(' ', '_')}_{sub_tab.lower()}")
                if ss:
                    screenshots.append({"label": sub_tab.lower(), "path": ss, "sub_tab": sub_tab})

        # For App Studio: wait for code gen to finish, then screenshot final solution
        if agent_name == "App Studio":
            await self.navigator.navigate_to_sub_tab("Output")
            await self._wait_for_sub_tab_content(timeout_seconds=60)
            ss = await self._screenshot("app_studio_final_solution")
            if ss:
                screenshots.append({"label": "final_solution", "path": ss, "sub_tab": "Output"})

        return {
            "agent": agent_name,
            "appeared": True,
            "description": AGENT_DESCRIPTIONS.get(agent_name, ""),
            "sub_tabs": sub_tabs,
            "screenshots": screenshots,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ── Full canvas capture ────────────────────────────────────────────────

    async def capture_full_canvas(self, expected_agents: list[str]) -> dict:
        """
        Capture the full canvas for a test run:
          1. Flow View (planning phase)
          2. Each expected agent tab + all sub-tabs
          3. Final canvas overview screenshot

        Returns a complete evidence package.
        """
        logger.info("[CanvasReader] Starting full canvas capture. Expected agents: %s", expected_agents)

        evidence = {
            "run_id": self.run_id,
            "flow_view": {},
            "agents": {},
            "final_screenshot": None,
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Step 1: Flow View
        evidence["flow_view"] = await self.capture_flow_view()

        # Step 2: Each agent tab (no fixed sleep — each waits internally)
        for agent in expected_agents:
            evidence["agents"][agent] = await self.capture_agent_tab(agent)

        # Step 3: Final canvas overview screenshot
        ss = await self._screenshot("canvas_final_overview")
        evidence["final_screenshot"] = ss

        appeared_count = sum(1 for a in evidence["agents"].values() if a.get("appeared"))
        logger.info(
            "[CanvasReader] Canvas capture complete. %d/%d agents appeared",
            appeared_count,
            len(expected_agents),
        )

        return evidence

    # ── Evidence summary for LLM Judge ────────────────────────────────────

    @staticmethod
    def build_evidence_text(canvas_evidence: dict, sai_log: list[dict]) -> str:
        """
        Flatten canvas evidence + SAI conversation log into a single text block
        suitable for sending to the LLM Judge.
        """
        lines = []

        lines.append("=== FLOW VIEW ===")
        fv = canvas_evidence.get("flow_view", {})
        lines.append(f"Appeared: {fv.get('appeared')}")
        lines.append(f"Planned steps:\n{fv.get('planned_steps', '(none)')}")
        lines.append("")

        lines.append("=== PSI CONVERSATION LOG ===")
        for entry in sai_log:
            role = entry.get("role", "?").upper()
            text = entry.get("text", "")
            lines.append(f"[{role}]: {text}")
        lines.append("")

        for agent_name, agent_data in canvas_evidence.get("agents", {}).items():
            lines.append(f"=== AGENT: {agent_name} ===")
            lines.append(f"Appeared: {agent_data.get('appeared')}")
            lines.append(f"Description: {agent_data.get('description', '')}")
            for sub_tab, content in agent_data.get("sub_tabs", {}).items():
                lines.append(f"  [{sub_tab}]: {content[:500] if content else '(empty)'}")
            lines.append("")

        return "\n".join(lines)
