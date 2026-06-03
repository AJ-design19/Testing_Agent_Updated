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

# SAI copilot chat area — the orchestrator textarea IS the SAI interface
SAI_CHAT_SELECTORS = [
    'textarea[aria-label="Write your prompt here"]',
    'textarea[placeholder*="Ask Orchestrator" i]',
    'textarea',
]

# Canvas container — appears after SAI hands off
# Covers the "Flow View" right-panel panel confirmed in live screenshots
CANVAS_SELECTORS = [
    # Confirmed from live DOM (vanijstaging.adya.ai)
    '[data-testid="flow-view"]',
    'text=Flow View',
    '[class*="flow-view"]',
    '[class*="FlowView"]',
    '[class*="canvas"]',
    '[data-testid="canvas"]',
    ".canvas-panel",
    ".canvas-container",
    '[aria-label="Canvas"]',
    ".right-panel",
    ".agent-canvas",
    # Agent tab names — any of these appearing = canvas is live
    'button:has-text("AIA")',
    'button:has-text("AGP")',
    'button:has-text("ETL")',
    'button:has-text("App Studio")',
]

# Flow View — the right panel that shows the planned agent flow diagram
FLOW_VIEW_SELECTORS = [
    # Confirmed from live DOM text content
    'text=Flow View',
    '[data-testid="flow-view"]',
    '[class*="flow-view"]',
    '[class*="FlowView"]',
    '[aria-label="Flow View"]',
    # Broader fallbacks
    '[class*="workflow-plan"]',
    'text=Workflow Steps',
    'text=Planned Steps',
    'text=Agent Flow',
    '[class*="agent-flow"]',
]

# Canvas agent tabs — names confirmed from Vanij platform (live screenshots)
AGENT_TAB_SELECTORS: dict[str, list[str]] = {
    "AIA": [
        'button:has-text("AIA")',
        '[role="tab"]:has-text("AIA")',
        '[data-testid="tab-aia"]',
        '[aria-label="AIA"]',
        '[class*="tab"]:has-text("AIA")',
    ],
    "AGP": [
        'button:has-text("AGP")',
        '[role="tab"]:has-text("AGP")',
        '[data-testid="tab-agp"]',
        '[aria-label="AGP"]',
        '[class*="tab"]:has-text("AGP")',
    ],
    "ETL": [
        'button:has-text("ETL")',
        '[role="tab"]:has-text("ETL")',
        '[data-testid="tab-etl"]',
        '[aria-label="ETL"]',
        '[class*="tab"]:has-text("ETL")',
    ],
    "App Studio": [
        'button:has-text("App Studio")',
        '[role="tab"]:has-text("App Studio")',
        '[data-testid="tab-app-studio"]',
        '[aria-label="App Studio"]',
        '[class*="tab"]:has-text("App Studio")',
    ],
}

# Sub-tabs inside each agent tab
# Vanij renders these as tab buttons; also covers "Execution trace" and "Architecture"
SUB_TAB_SELECTORS: dict[str, list[str]] = {
    "Overview": [
        'button:has-text("Overview")',
        '[role="tab"]:has-text("Overview")',
        '[data-testid="subtab-overview"]',
        '[class*="subtab"]:has-text("Overview")',
    ],
    "Output": [
        'button:has-text("Output")',
        '[role="tab"]:has-text("Output")',
        '[data-testid="subtab-output"]',
        '[class*="subtab"]:has-text("Output")',
    ],
    "Questions": [
        'button:has-text("Questions")',
        '[role="tab"]:has-text("Questions")',
        '[data-testid="subtab-questions"]',
        '[class*="subtab"]:has-text("Questions")',
    ],
    "Thinking": [
        'button:has-text("Thinking")',
        '[role="tab"]:has-text("Thinking")',
        '[data-testid="subtab-thinking"]',
        'button:has-text("Steps")',
        '[class*="subtab"]:has-text("Thinking")',
    ],
    "Workflow": [
        'button:has-text("Workflow")',
        '[role="tab"]:has-text("Workflow")',
        '[data-testid="subtab-workflow"]',
    ],
    "Architecture": [
        'button:has-text("Architecture")',
        '[role="tab"]:has-text("Architecture")',
        '[data-testid="subtab-architecture"]',
    ],
    "Execution trace": [
        'button:has-text("Execution trace")',
        '[role="tab"]:has-text("Execution trace")',
        'button:has-text("Execution Trace")',
        '[data-testid="subtab-execution-trace"]',
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
    """
    Click the first matching visible+enabled selector with real pointer events.
    hover → small pause → click (behaves like a human).
    """
    loop = asyncio.get_event_loop()
    dead = loop.time() + timeout / 1000.0
    while loop.time() < dead:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible() and await el.is_enabled():
                    await el.hover()
                    await asyncio.sleep(0.12)
                    await el.click()
                    logger.info("Clicked '%s' via: %s", label, sel)
                    await asyncio.sleep(0.4)
                    return True
            except Exception:
                pass
        await asyncio.sleep(0.4)
    logger.warning("Could not click '%s' with any selector", label)
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

    # ── SAI copilot (convenience wrappers used by sai_handler) ───────────────

    async def get_sai_panel_text(self) -> str:
        """Return visible text in the SAI / orchestrator chat area."""
        return await _read_text(self.page, SAI_CHAT_SELECTORS, "SAI panel", timeout=5000)

    async def is_psi_visible(self) -> bool:
        sel = await _try_selectors(self.page, SAI_CHAT_SELECTORS, timeout=3000)
        return sel is not None

    # ── Canvas ────────────────────────────────────────────────────────────────

    async def wait_for_canvas(self, timeout_ms: int = 60000) -> bool:
        """
        Wait for the canvas right panel or any agent tab to appear.
        All selectors are polled concurrently against a single shared deadline
        so we don't waste the full timeout on each selector sequentially.
        """
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0

        all_sels = CANVAS_SELECTORS + [
            sel for tab_sels in AGENT_TAB_SELECTORS.values() for sel in tab_sels
        ]

        # First: nudge the page to reveal the right panel (scroll right + JS reveal)
        try:
            await self.page.evaluate("""() => {
                // Scroll the rightmost scrollable container into view
                const candidates = Array.from(document.querySelectorAll(
                    'aside, [class*="right"], [class*="panel"], [class*="canvas"], [class*="sidebar"]'
                )).filter(el => el.scrollWidth > el.clientWidth || el.offsetParent);
                if (candidates.length) candidates[candidates.length - 1].scrollIntoView();
                window.scrollTo(window.scrollMaxX || 9999, 0);
            }""")
        except Exception:
            pass

        logger.info("[SAINavigator] Waiting for canvas (timeout=%dms)…", timeout_ms)

        while asyncio.get_event_loop().time() < deadline:
            remaining_ms = max(500, int((deadline - asyncio.get_event_loop().time()) * 1000))
            for sel in all_sels:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        logger.info("[SAINavigator] Canvas appeared: %s", sel)
                        return True
                except Exception:
                    pass

            # Also check via DOM intelligence (catches dynamic class names)
            try:
                found = await self.page.evaluate("""() => {
                    const agents = ['AIA', 'AGP', 'ETL', 'App Studio'];
                    for (const name of agents) {
                        const els = Array.from(document.querySelectorAll(
                            'button, [role="tab"], [class*="tab"]'
                        )).filter(el => el.offsetParent &&
                            (el.innerText||'').trim() === name);
                        if (els.length) return name;
                    }
                    // Canvas/flow view container
                    const panel = document.querySelector(
                        '[class*="canvas"],[class*="Canvas"],[class*="flow-view"],[data-testid="flow-view"]'
                    );
                    return panel && panel.offsetParent ? 'panel' : null;
                }""")
                if found:
                    logger.info("[SAINavigator] Canvas detected via JS: %s", found)
                    return True
            except Exception:
                pass

            await asyncio.sleep(1.0)

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

    async def wait_for_agent_tab(self, agent_name: str, timeout: int = 60000) -> bool:
        """Wait for an agent tab using a shared deadline across all selectors."""
        selectors = AGENT_TAB_SELECTORS.get(agent_name, [f'button:has-text("{agent_name}")'])
        deadline = asyncio.get_event_loop().time() + timeout / 1000.0

        while asyncio.get_event_loop().time() < deadline:
            for sel in selectors:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        logger.info("[SAINavigator] Agent tab '%s' appeared: %s", agent_name, sel)
                        return True
                except Exception:
                    pass
            # JS fallback: find by exact text match
            try:
                found = await self.page.evaluate("""(name) => {
                    return !!Array.from(document.querySelectorAll(
                        'button, [role="tab"], [class*="tab"], a'
                    )).find(el => el.offsetParent &&
                        (el.innerText||'').trim() === name);
                }""", agent_name)
                if found:
                    logger.info("[SAINavigator] Agent tab '%s' found via JS", agent_name)
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)

        logger.warning("[SAINavigator] Agent tab '%s' did not appear within %dms", agent_name, timeout)
        return False

    async def navigate_to_agent_tab(self, agent_name: str) -> bool:
        selectors = AGENT_TAB_SELECTORS.get(agent_name, [f'button:has-text("{agent_name}")'])
        return await _click_selector(self.page, selectors, f"agent tab '{agent_name}'", timeout=10000)

    async def wait_for_agent_loaded(
        self, agent_name: str, timeout_seconds: int = 120
    ) -> bool:
        """
        After navigating to an agent tab, wait until the agent finishes loading
        (spinner gone, content non-empty) before we read or screenshot it.

        Returns True when the agent appears ready, False on timeout.
        """
        # JS that returns True while the agent is still in a loading state.
        agent_loading_js = """() => {
            // 1. Any visible spinner / loading animation in the canvas area
            const canvasArea = document.querySelector(
                '[data-testid="canvas"], [class*="canvas"], [class*="Canvas"], main'
            );
            const root = canvasArea || document.body;

            const spinners = Array.from(root.querySelectorAll(
                '.animate-spin, .animate-pulse, [class*="spinner"], [class*="Spinner"], ' +
                '[class*="loading"], [class*="Loading"], [aria-label*="loading" i]'
            )).filter(el => el.offsetParent !== null);
            if (spinners.length > 0) return true;

            // 2. "Loading…" / "Generating…" text inside the canvas panel
            const text = (root.innerText || '').toLowerCase();
            if (/\\bloading\\b|\\bgenerating\\b|\\bprocessing\\b|\\bbuilding\\b/.test(text)) {
                // Only flag if there are no substantial content blocks yet
                const contentBlocks = root.querySelectorAll('pre, code, table, [class*="output"], p');
                const hasContent = Array.from(contentBlocks).some(el => {
                    return el.offsetParent !== null && (el.innerText || '').trim().length > 40;
                });
                if (!hasContent) return true;
            }

            return false;
        }"""

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        poll = 1.0
        prev_loading = None

        while asyncio.get_event_loop().time() < deadline:
            try:
                still_loading = await self.page.evaluate(agent_loading_js)
            except Exception:
                still_loading = False

            if still_loading != prev_loading:
                state = "loading" if still_loading else "ready"
                logger.info("[SAINavigator] Agent '%s' state: %s", agent_name, state)
                prev_loading = still_loading

            if not still_loading:
                return True

            await asyncio.sleep(poll)

        logger.warning(
            "[SAINavigator] Agent '%s' still loading after %ds — proceeding anyway",
            agent_name, timeout_seconds,
        )
        return False

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
