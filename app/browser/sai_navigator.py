"""
SAI / Vanij UI Navigation module.

All selectors confirmed against vanijstaging.adya.ai via live DOM inspection.

Page structure (post-login):
  Top nav:      <button>Workspaces</button>  <button>Apps</button>
                <button>Workflows</button>   <button>Models</button>
                <button>Agents</button>
  Prompt area:  <textarea aria-label="Write your prompt here"
                           placeholder="Ask Orchestrator to build something…">
  Send button:  <button type="submit" aria-label="Send message">
  More actions: <button id="radix-:r0:" aria-label="More actions">

  After a workflow is submitted, canvas agents appear as tabs.
  Sub-tabs within each agent: Overview · Output · Questions · Thinking/Steps
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Confirmed selectors ───────────────────────────────────────────────────────

# The main chat/prompt textarea (always visible on orchestrator page)
PROMPT_TEXTAREA_SELECTORS = [
    'textarea[aria-label="Write your prompt here"]',
    'textarea[placeholder*="Ask Orchestrator" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="build" i]',
    'textarea',
]

# The send / submit button for the prompt
SEND_BUTTON_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[type="submit"][aria-label="Send message"]',
    'button[aria-label="Send"]',
]

# Top navigation buttons (post-login)
NAV_BUTTON_SELECTORS = {
    "Workspaces": ['button:has-text("Workspaces")', '[aria-label="Workspaces"]'],
    "Apps":       ['button:has-text("Apps")',       '[aria-label="Apps"]'],
    "Workflows":  ['button:has-text("Workflows")',  '[aria-label="Workflows"]'],
    "Models":     ['button:has-text("Models")',     '[aria-label="Models"]'],
    "Agents":     ['button:has-text("Agents")',     '[aria-label="Agents"]'],
}

# "More actions" button next to the textarea
MORE_ACTIONS_SELECTORS = [
    'button[aria-label="More actions"]',
    'button[id^="radix-"]',
]

# Psi copilot chat area — the orchestrator textarea IS the Psi interface
PSI_CHAT_SELECTORS = [
    'textarea[aria-label="Write your prompt here"]',
    'textarea[placeholder*="Ask Orchestrator" i]',
    'textarea',
]

# Canvas container — appears after Psi hands off
CANVAS_SELECTORS = [
    '[data-testid="canvas"]',
    ".canvas-panel",
    ".canvas-container",
    '[aria-label="Canvas"]',
    ".right-panel",
    ".agent-canvas",
]

# Flow View
FLOW_VIEW_SELECTORS = [
    '[data-testid="flow-view"]',
    ".flow-view",
    ".workflow-steps",
    '[aria-label="Flow View"]',
    'text=Flow View',
    'text=Workflow Steps',
    'text=Planned Steps',
]

# Canvas agent tabs — names confirmed from Vanij platform
AGENT_TAB_SELECTORS: dict[str, list[str]] = {
    "AIA": [
        'button:has-text("AIA")',
        '[data-testid="tab-aia"]',
        '[aria-label="AIA"]',
        'text=AIA',
        '.tab-aia',
    ],
    "AGP": [
        'button:has-text("AGP")',
        '[data-testid="tab-agp"]',
        '[aria-label="AGP"]',
        'text=AGP',
        '.tab-agp',
    ],
    "ETL": [
        'button:has-text("ETL")',
        '[data-testid="tab-etl"]',
        '[aria-label="ETL"]',
        'text=ETL',
        '.tab-etl',
    ],
    "App Studio": [
        'button:has-text("App Studio")',
        '[data-testid="tab-app-studio"]',
        '[aria-label="App Studio"]',
        'text=App Studio',
        '.tab-app-studio',
    ],
}

# Sub-tabs inside each agent tab
SUB_TAB_SELECTORS: dict[str, list[str]] = {
    "Overview": [
        'button:has-text("Overview")',
        '[data-testid="subtab-overview"]',
        'text=Overview',
        '.subtab-overview',
    ],
    "Output": [
        'button:has-text("Output")',
        '[data-testid="subtab-output"]',
        'text=Output',
        '.subtab-output',
    ],
    "Questions": [
        'button:has-text("Questions")',
        '[data-testid="subtab-questions"]',
        'text=Questions',
        '.subtab-questions',
    ],
    "Thinking": [
        'button:has-text("Thinking")',
        '[data-testid="subtab-thinking"]',
        'text=Thinking',
        'text=Steps',
        '.subtab-thinking',
    ],
}

# Content area inside a sub-tab
SUBTAB_CONTENT_SELECTORS = [
    '[data-testid="subtab-content"]',
    ".subtab-content",
    ".agent-output",
    ".tab-content",
    ".panel-content",
    "main",
    '[role="main"]',
]

# Workflow navigation panel (sidebar)
WORKFLOW_NAV_SELECTORS = [
    '[data-testid="workflow-nav"]',
    ".workflow-navigation",
    ".workflow-list",
    '[aria-label="Workflows"]',
    ".nav-panel",
    'button:has-text("Workflows")',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _try_selectors(page, selectors: list[str], timeout: int = 4000) -> Optional[str]:
    """Try each selector; return the first one visible within timeout."""
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout, state="visible")
            return sel
        except Exception:
            continue
    return None


async def _click_selector(page, selectors: list[str], label: str, timeout: int = 4000) -> bool:
    """Click the first matching visible selector. Returns True on success."""
    sel = await _try_selectors(page, selectors, timeout=timeout)
    if sel:
        await page.click(sel)
        logger.info("Clicked '%s' via: %s", label, sel)
        # Let the DOM settle after click
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        return True
    logger.warning("Could not find '%s' with any selector", label)
    return False


async def _read_text(page, selectors: list[str], label: str, timeout: int = 4000) -> str:
    """Return inner text of the first matching element, or empty string."""
    sel = await _try_selectors(page, selectors, timeout=timeout)
    if sel:
        try:
            return (await page.inner_text(sel)).strip()
        except Exception as e:
            logger.warning("Could not read text from '%s': %s", label, e)
    return ""


# ── SAINavigator ──────────────────────────────────────────────────────────────

class SAINavigator:
    """
    All Vanij / SAI UI navigation.
    Uses confirmed selectors from live DOM inspection of vanijstaging.adya.ai.
    Every method is safe: logs a warning rather than raising on failure.
    """

    def __init__(self, page):
        self.page = page

    # ── Top nav ───────────────────────────────────────────────────────────────

    async def click_nav_button(self, name: str) -> bool:
        """Click a top-nav button: Workspaces / Apps / Workflows / Models / Agents."""
        selectors = NAV_BUTTON_SELECTORS.get(name, [f'button:has-text("{name}")'])
        return await _click_selector(self.page, selectors, f"nav '{name}'", timeout=8000)

    # ── Prompt textarea ───────────────────────────────────────────────────────

    async def wait_for_prompt_ready(self, timeout_ms: int = 20000) -> bool:
        """Wait until the prompt textarea is visible and enabled."""
        sel = await _try_selectors(self.page, PROMPT_TEXTAREA_SELECTORS, timeout=timeout_ms)
        if not sel:
            logger.warning("[SAINavigator] Prompt textarea not found within %dms", timeout_ms)
            return False
        # Also confirm it's not disabled
        try:
            await self.page.wait_for_function(
                """document.querySelector('textarea[aria-label="Write your prompt here"]')
                   && !document.querySelector('textarea[aria-label="Write your prompt here"]').disabled""",
                timeout=5000,
            )
        except Exception:
            pass
        return True

    async def fill_prompt(self, text: str) -> bool:
        """Click the textarea and fill with text."""
        sel = await _try_selectors(self.page, PROMPT_TEXTAREA_SELECTORS, timeout=10000)
        if not sel:
            logger.error("[SAINavigator] Could not find prompt textarea")
            return False
        await self.page.click(sel)
        await asyncio.sleep(0.3)
        await self.page.fill(sel, text)
        logger.info("[SAINavigator] Filled prompt (%d chars)", len(text))
        return True

    async def click_send(self) -> bool:
        """Click the Send message button."""
        success = await _click_selector(
            self.page, SEND_BUTTON_SELECTORS, "Send message", timeout=5000
        )
        if not success:
            # Fallback: press Enter in the textarea
            logger.info("[SAINavigator] Send button not found — pressing Enter")
            await self.page.keyboard.press("Enter")
            return True
        return True

    # ── Psi copilot (convenience wrappers used by psi_handler) ───────────────

    async def get_psi_panel_text(self) -> str:
        """Return visible text in the Psi / orchestrator chat area."""
        return await _read_text(self.page, PSI_CHAT_SELECTORS, "Psi panel", timeout=5000)

    async def is_psi_visible(self) -> bool:
        sel = await _try_selectors(self.page, PSI_CHAT_SELECTORS, timeout=3000)
        return sel is not None

    # ── Canvas ────────────────────────────────────────────────────────────────

    async def wait_for_canvas(self, timeout_ms: int = 30000) -> bool:
        """
        Wait for the canvas panel or any agent tab to appear after Psi handoff.
        Returns True when found.
        """
        all_sels = CANVAS_SELECTORS + [
            sel for tab_sels in AGENT_TAB_SELECTORS.values() for sel in tab_sels
        ]
        for sel in all_sels:
            try:
                await self.page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
                logger.info("[SAINavigator] Canvas appeared: %s", sel)
                return True
            except Exception:
                continue
        logger.warning("[SAINavigator] Canvas did not appear within %dms", timeout_ms)
        return False

    # ── Flow View ─────────────────────────────────────────────────────────────

    async def wait_for_flow_view(self, timeout: int = 20000) -> bool:
        sel = await _try_selectors(self.page, FLOW_VIEW_SELECTORS, timeout=timeout)
        if sel:
            logger.info("[SAINavigator] Flow View detected: %s", sel)
            return True
        logger.warning("[SAINavigator] Flow View did not appear within %dms", timeout)
        return False

    async def read_flow_view(self) -> str:
        text = await _read_text(self.page, FLOW_VIEW_SELECTORS, "flow view", timeout=8000)
        logger.info("[SAINavigator] Flow View text: %s", text[:200] if text else "(empty)")
        return text

    # ── Canvas agent tabs ─────────────────────────────────────────────────────

    async def wait_for_agent_tab(self, agent_name: str, timeout: int = 30000) -> bool:
        selectors = AGENT_TAB_SELECTORS.get(agent_name, [f'button:has-text("{agent_name}")'])
        sel = await _try_selectors(self.page, selectors, timeout=timeout)
        if sel:
            logger.info("[SAINavigator] Agent tab '%s' appeared: %s", agent_name, sel)
            return True
        logger.warning("[SAINavigator] Agent tab '%s' did not appear within %dms", agent_name, timeout)
        return False

    async def navigate_to_agent_tab(self, agent_name: str) -> bool:
        selectors = AGENT_TAB_SELECTORS.get(agent_name, [f'button:has-text("{agent_name}")'])
        return await _click_selector(self.page, selectors, f"agent tab '{agent_name}'", timeout=10000)

    async def get_active_agent_tab(self) -> str:
        active_selectors = [
            '[data-testid^="tab-"][aria-selected="true"]',
            '.tab-active',
            '.canvas-tab.active',
            '[role="tab"][aria-selected="true"]',
        ]
        return await _read_text(self.page, active_selectors, "active tab", timeout=3000)

    # ── Sub-tabs inside agent tabs ────────────────────────────────────────────

    async def navigate_to_sub_tab(self, sub_tab_name: str) -> bool:
        selectors = SUB_TAB_SELECTORS.get(sub_tab_name, [f'button:has-text("{sub_tab_name}")'])
        return await _click_selector(self.page, selectors, f"sub-tab '{sub_tab_name}'", timeout=6000)

    async def read_sub_tab_content(self, sub_tab_name: str) -> str:
        await self.navigate_to_sub_tab(sub_tab_name)
        return await _read_text(self.page, SUBTAB_CONTENT_SELECTORS,
                                 f"sub-tab content ({sub_tab_name})", timeout=5000)

    async def read_all_sub_tabs(self, agent_name: str) -> dict[str, str]:
        await self.navigate_to_agent_tab(agent_name)
        result: dict[str, str] = {}
        for sub_tab in ["Overview", "Output", "Questions", "Thinking"]:
            content = await self.read_sub_tab_content(sub_tab)
            result[sub_tab] = content
            logger.info("[SAINavigator] %s / %s: %d chars", agent_name, sub_tab, len(content))
        return result

    # ── Workflow nav ──────────────────────────────────────────────────────────

    async def is_workflow_nav_visible(self) -> bool:
        sel = await _try_selectors(self.page, WORKFLOW_NAV_SELECTORS, timeout=3000)
        return sel is not None

    async def get_visible_workflows(self) -> list[str]:
        try:
            sel = await _try_selectors(self.page, WORKFLOW_NAV_SELECTORS, timeout=3000)
            if not sel:
                return []
            items = await self.page.query_selector_all(
                f"{sel} .workflow-item, {sel} li, {sel} [role='listitem']"
            )
            return [await item.inner_text() for item in items if item]
        except Exception as e:
            logger.warning("[SAINavigator] Could not list workflows: %s", e)
            return []

    async def click_workflow(self, workflow_name: str) -> bool:
        selectors = [f'text={workflow_name}', f'[aria-label="{workflow_name}"]']
        return await _click_selector(self.page, selectors, f"workflow '{workflow_name}'")

    # ── Full canvas snapshot ──────────────────────────────────────────────────

    async def capture_canvas_state(self, expected_agents: list[str]) -> dict:
        canvas_state: dict = {}
        for agent in expected_agents:
            appeared = await self.wait_for_agent_tab(agent, timeout=15000)
            if appeared:
                canvas_state[agent] = await self.read_all_sub_tabs(agent)
            else:
                canvas_state[agent] = {"error": f"Tab '{agent}' never appeared"}
        return canvas_state
