"""
Agentic Executor — executes each surface test step.

Mode selection (per step):
  1. AGENTIC  — LLM reasons over a live DOM snapshot to decide the action.
                Used for: sai_prompt, sai_conversation_loop, detect_and_interact,
                          visit_agent_tabs, any step where fallback_playwright=False.
  2. PLAYWRIGHT — deterministic selector-driven execution.
                Used for: navigate, click, fill, scroll_capture, detect,
                          screenshot, detect_all_tabs, nav_click, ensure_workspace,
                          navigate_back, visit_subtab.

When the agentic loop fails to converge (max retries exceeded, exception), it
logs the escalation reason and falls back to Playwright mode automatically.

SAI INPUT / OUTPUT capture:
  - Every action is logged before + after via IOLogger.
  - For sai_prompt steps the exact prompt text is logged as SAI INPUT.
  - For sai_conversation_loop steps the full conversation_log is attached as SAI OUTPUT.
  - For any DOM action the visible DOM snapshot is saved as dom_snapshot_uri.
"""

import asyncio
import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from app.browser.dom_intelligence import DOMIntelligence, FIND_AGENT_TABS_JS
from app.surfaces.io_logger import IOLogger
from app.surfaces.viewport_scroller import ViewportScroller

load_dotenv()
logger = logging.getLogger(__name__)


# ── LLM client for agentic reasoning ─────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )


AGENTIC_SYSTEM = """You are an autonomous browser test agent operating the Adya SAI platform.

You receive:
  - A GOAL describing what action to take.
  - A DOM SNAPSHOT (list of visible interactive elements).
  - The CURRENT URL.

Respond with ONLY a JSON object:
{
  "reasoning": "<1-2 sentence rationale>",
  "action": "click" | "fill" | "navigate" | "scroll" | "screenshot" | "done" | "fail",
  "selector": "<CSS selector or text locator to act on, if applicable>",
  "value": "<text value to fill, if action is fill>",
  "url": "<URL to navigate to, if action is navigate>",
  "done_reason": "<explanation if action is done or fail>"
}

Rules:
- Choose 'done' when the goal is achieved.
- Choose 'fail' only after all reasonable options are exhausted.
- Prefer aria-label, data-testid, and role selectors over class names.
- For SAI chat input always use: textarea[aria-label="Write your prompt here"]
- For sub-tabs use: button:has-text("<tab_name>") or [role="tab"]:has-text("<tab_name>")
"""


class AgenticExecutor:
    """
    Executes surface test steps in either agentic (LLM-driven) or
    Playwright (deterministic) mode, with automatic mode escalation.
    """

    def __init__(
        self,
        page,
        run_id: str,
        persona: dict,
        io_logger: IOLogger,
        sai_handler=None,
        answer_engine=None,
    ):
        self.page = page
        self.run_id = run_id
        self.persona = persona
        self.io_logger = io_logger
        self.sai_handler = sai_handler
        self.answer_engine = answer_engine
        self.dom = DOMIntelligence(page)
        self.scroller = ViewportScroller(page, io_logger=io_logger, run_id=run_id)
        self._llm = _llm_client()
        self._model = os.getenv("ANSWER_MODEL", "grok-3-beta")

    # ── Public: execute one step ──────────────────────────────────────────────

    async def execute_step(self, step: dict, surface_id: str) -> dict:
        """
        Execute one step dict. Returns the IOLogger step record.
        Automatically picks agentic vs Playwright mode.
        """
        step_id   = step["step_id"]
        action    = step["action"]
        selectors = step.get("selectors", [])
        sai_prompt = step.get("sai_prompt")
        use_playwright = step.get("fallback_playwright", True)
        scroll_after   = step.get("scroll_after", False)

        self.io_logger.begin_step(
            step_id=step_id,
            action=action,
            sai_prompt=sai_prompt,
            selectors=selectors,
        )
        logger.info("[AgenticExec] Step %s — action=%s", step_id, action)

        # Save DOM snapshot for every step
        await self._capture_dom_snapshot(step_id)

        status = "pass"
        try:
            if action == "sai_prompt":
                status = await self._exec_sai_prompt(step_id, sai_prompt, selectors)

            elif action == "sai_conversation_loop":
                status = await self._exec_sai_loop(step_id)

            elif action in ("navigate", "nav_click"):
                status = await self._exec_navigate_or_click(step_id, action, selectors, use_playwright)

            elif action == "click":
                status = await self._exec_click(step_id, selectors, use_playwright)

            elif action == "click_first":
                status = await self._exec_click_first(step_id, selectors)

            elif action == "fill":
                status = await self._exec_fill(step_id, selectors)

            elif action == "detect":
                status = await self._exec_detect(step_id, selectors)

            elif action == "detect_and_interact":
                status = await self._exec_detect_and_interact(step_id, selectors, use_playwright)

            elif action == "detect_all_tabs":
                status = await self._exec_detect_all_tabs(step_id, selectors)

            elif action == "visit_agent_tabs":
                status = await self._exec_visit_agent_tabs(step_id, selectors, surface_id)

            elif action == "visit_subtab":
                status = await self._exec_visit_subtab(step_id, selectors)

            elif action == "scroll_capture":
                status = await self._exec_scroll_capture(step_id)

            elif action == "screenshot":
                status = await self._exec_screenshot(step_id, selectors)

            elif action == "screenshot_region":
                status = await self._exec_screenshot_region(step_id, selectors)

            elif action == "ensure_workspace":
                status = await self._exec_ensure_workspace(step_id, selectors)

            elif action == "navigate_back":
                status = await self._exec_navigate_back(step_id)

            else:
                logger.warning("[AgenticExec] Unknown action '%s' — skipping", action)
                status = "skip"

            # Post-step scroll capture if requested
            if scroll_after and action not in ("scroll_capture",):
                await self.scroller.scroll_and_capture(step_id, label=f"{action}_post")

        except Exception as e:
            logger.error("[AgenticExec] Step %s exception: %s", step_id, e, exc_info=True)
            self.io_logger.set_notes(f"Exception: {e}")
            status = "fail"

        # Final screenshot for the step
        await self._take_step_screenshot(step_id, action)

        return self.io_logger.finish_step(status)

    # ── SAI prompt ─────────────────────────────────────────────────────────────

    async def _exec_sai_prompt(self, step_id: str, prompt: str, selectors: list) -> str:
        if not prompt:
            logger.warning("[AgenticExec] sai_prompt step has no prompt text")
            return "skip"
        if not self.sai_handler:
            logger.warning("[AgenticExec] No sai_handler — cannot send prompt")
            return "skip"

        logger.info("[AgenticExec] Sending SAI prompt: %s", prompt[:80])
        self.io_logger.set_sai_output(result="sending_prompt")

        sent = await self.sai_handler.send_initial_prompt(prompt)
        if not sent:
            self.io_logger.set_sai_output(result="prompt_send_failed")
            return "fail"

        self.io_logger.set_sai_output(
            full_text=prompt,
            result="prompt_sent",
        )
        return "pass"

    # ── SAI conversation loop ─────────────────────────────────────────────────

    async def _exec_sai_loop(self, step_id: str) -> str:
        if not self.sai_handler:
            logger.warning("[AgenticExec] No sai_handler — cannot run loop")
            return "skip"

        logger.info("[AgenticExec] Running SAI conversation loop")
        sai_result = await self.sai_handler.run_conversation_loop(max_rounds=20)

        conv_log  = sai_result.get("conversation_log", [])
        full_text = "\n".join(
            f"[{e.get('role','?').upper()}] {e.get('text','')}"
            for e in conv_log
        )
        # Detect sub-agents from DOM
        try:
            tabs = await self.page.evaluate(FIND_AGENT_TABS_JS) or []
            sub_agents = [t["name"] for t in tabs]
        except Exception:
            sub_agents = []

        self.io_logger.set_sai_output(
            full_text=full_text,
            sub_agents=sub_agents,
            result=(
                f"questions={sai_result.get('question_count',0)} "
                f"completion={sai_result.get('completion_detected')} "
                f"rounds={sai_result.get('rounds',0)}"
            ),
        )

        # Capture DOM after loop
        await self._capture_dom_snapshot(step_id + "_post_loop")
        return "pass" if sai_result.get("completion_detected") else "partial"

    # ── Navigate / nav-click ──────────────────────────────────────────────────

    async def _exec_navigate_or_click(
        self, step_id: str, action: str, selectors: list, use_playwright: bool
    ) -> str:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    await asyncio.sleep(1.0)
                    self.io_logger.set_sai_output(result=f"clicked:{sel}")
                    return "pass"
            except Exception:
                continue

        # Agentic fallback
        if not use_playwright:
            return await self._agentic_action(
                step_id,
                goal=f"Navigate or click to reach target. Selectors tried: {selectors}",
                escalation_reason="nav_click selectors not found",
            )

        self.io_logger.set_escalation("nav_click: all selectors failed")
        self.io_logger.set_sai_output(result="not_found")
        return "partial"

    # ── Click ─────────────────────────────────────────────────────────────────

    async def _exec_click(self, step_id: str, selectors: list, use_playwright: bool) -> str:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible() and await el.is_enabled():
                    await el.hover()
                    await asyncio.sleep(0.12)
                    await el.click()
                    await asyncio.sleep(0.5)
                    self.io_logger.set_sai_output(result=f"clicked:{sel}")
                    return "pass"
            except Exception:
                continue

        if not use_playwright:
            return await self._agentic_action(
                step_id,
                goal=f"Click element. Selectors tried: {selectors}",
                escalation_reason="click selectors not matched",
            )

        self.io_logger.set_sai_output(result="element_not_found")
        return "partial"

    async def _exec_click_first(self, step_id: str, selectors: list) -> str:
        for sel in selectors:
            try:
                els = self.page.locator(sel)
                count = await els.count()
                if count > 0:
                    el = els.first
                    if await el.is_visible():
                        await el.click()
                        await asyncio.sleep(0.8)
                        self.io_logger.set_sai_output(result=f"clicked_first:{sel}")
                        return "pass"
            except Exception:
                continue
        self.io_logger.set_sai_output(result="no_matching_element")
        return "partial"

    # ── Fill ──────────────────────────────────────────────────────────────────

    async def _exec_fill(self, step_id: str, selectors: list) -> str:
        # The fill action for search fields — fill with a default test query
        search_query = "analytics dashboard"
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.2)
                    await el.fill(search_query)
                    await asyncio.sleep(0.5)
                    self.io_logger.set_sai_output(result=f"filled:{sel} value={search_query!r}")
                    return "pass"
            except Exception:
                continue
        self.io_logger.set_sai_output(result="fill_target_not_found")
        return "partial"

    # ── Detect ────────────────────────────────────────────────────────────────

    async def _exec_detect(self, step_id: str, selectors: list) -> str:
        found_sels = []
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    found_sels.append(sel)
            except Exception:
                continue

        if found_sels:
            self.io_logger.set_sai_output(result=f"found:{found_sels[:3]}")
            return "pass"

        # Try text-based detection via DOM
        dom_snap = await self.dom.snapshot()
        found_texts = [
            el["text"] for el in dom_snap
            if any(
                kw.lower() in el["text"].lower()
                for kw in [s.replace('button:has-text("', '').replace('")', '').replace('text=', '') for s in selectors[:4]]
            )
        ]
        if found_texts:
            self.io_logger.set_sai_output(result=f"dom_found:{found_texts[:3]}")
            return "pass"

        self.io_logger.set_sai_output(result="not_found")
        self.io_logger.set_notes(f"Selectors tried: {selectors}")
        return "partial"

    # ── Detect + interact ─────────────────────────────────────────────────────

    async def _exec_detect_and_interact(
        self, step_id: str, selectors: list, use_playwright: bool
    ) -> str:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.3)
                    self.io_logger.set_sai_output(result=f"interacted:{sel}")
                    return "pass"
            except Exception:
                continue

        if not use_playwright:
            return await self._agentic_action(
                step_id,
                goal=f"Find and interact with element. Selectors: {selectors}",
                escalation_reason="detect_and_interact: no selector matched",
            )
        self.io_logger.set_sai_output(result="not_found")
        return "partial"

    # ── Detect all tabs (live DOM re-detection) ───────────────────────────────

    async def _exec_detect_all_tabs(self, step_id: str, selectors: list) -> str:
        # First try known agent tabs
        try:
            agent_tabs = await self.page.evaluate(FIND_AGENT_TABS_JS) or []
        except Exception:
            agent_tabs = []

        # Broader scan — all visible [role="tab"] elements
        all_tabs: list[dict] = []
        for sel in selectors + ['[role="tab"]', '[class*="tab"]']:
            try:
                els = self.page.locator(sel)
                count = await els.count()
                for i in range(count):
                    el = els.nth(i)
                    if await el.is_visible():
                        text = (await el.inner_text()).strip()
                        if text and len(text) < 60:
                            all_tabs.append({"selector": sel, "text": text})
            except Exception:
                continue

        seen = set()
        unique_tabs = []
        for t in all_tabs:
            if t["text"] not in seen:
                seen.add(t["text"])
                unique_tabs.append(t)

        combined = agent_tabs + unique_tabs
        self.io_logger.set_sai_output(
            full_text=json.dumps(combined, default=str),
            result=f"detected {len(combined)} tabs",
        )

        # Visit each detected tab
        for tab in unique_tabs[:12]:
            try:
                sel = f'[role="tab"]:has-text("{tab["text"]}")'
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.8)
                    await self.scroller.scroll_and_capture(
                        step_id, label=f"tab_{tab['text'].replace(' ','_')}"
                    )
            except Exception as e:
                logger.debug("[AgenticExec] Could not visit tab '%s': %s", tab["text"], e)

        return "pass" if combined else "partial"

    # ── Visit agent tabs + all sub-tabs ──────────────────────────────────────

    async def _exec_visit_agent_tabs(
        self, step_id: str, selectors: list, surface_id: str
    ) -> str:
        """
        Click the agent tab, wait for it to load, then visit every detected sub-tab.
        Screenshots at every viewport increment for each sub-tab.
        """
        # Click agent tab
        clicked = False
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    await asyncio.sleep(1.5)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Try JS fallback
            agent_name = self._agent_name_from_selectors(selectors)
            if agent_name:
                from app.browser.dom_intelligence import click_agent_tab_js
                try:
                    clicked = await self.page.evaluate(click_agent_tab_js(agent_name))
                    if clicked:
                        await asyncio.sleep(1.5)
                except Exception:
                    pass

        if not clicked:
            self.io_logger.set_escalation("agent tab not found; skipping sub-tab traversal")
            self.io_logger.set_sai_output(result="tab_not_found")
            return "partial"

        # Wait for agent to load
        await self._wait_for_agent_loaded(timeout_seconds=60)

        # Capture the tab's initial state
        agent_name = self._agent_name_from_selectors(selectors) or step_id
        await self.scroller.scroll_and_capture(step_id, label=f"{agent_name}_initial")

        # Detect sub-tabs from live DOM
        sub_tabs = await self._detect_sub_tabs()
        logger.info("[AgenticExec] Sub-tabs detected: %s", sub_tabs)

        all_sub_content: dict = {}
        for sub_tab in sub_tabs[:8]:  # cap at 8 to avoid infinite loops
            content = await self._visit_sub_tab(step_id, sub_tab, agent_name)
            all_sub_content[sub_tab] = content

        # Capture DOM content
        canvas_text = await self.dom.read_canvas_content()
        self.io_logger.set_sai_output(
            full_text=canvas_text,
            sub_agents=[agent_name],
            result=f"visited {len(sub_tabs)} sub-tabs: {sub_tabs}",
        )
        return "pass"

    async def _visit_sub_tab(
        self, step_id: str, sub_tab_name: str, agent_name: str
    ) -> str:
        """Click a sub-tab, scroll it, screenshot every viewport increment."""
        from app.browser.dom_intelligence import click_sub_tab_js

        clicked = False
        for sel in [
            f'button:has-text("{sub_tab_name}")',
            f'[role="tab"]:has-text("{sub_tab_name}")',
        ]:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.8)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            try:
                clicked = await self.page.evaluate(click_sub_tab_js(sub_tab_name))
                if clicked:
                    await asyncio.sleep(0.8)
            except Exception:
                pass

        label = f"{agent_name}_{sub_tab_name.replace(' ', '_')}"
        if clicked:
            await self._wait_for_content_stable()
            await self.scroller.scroll_and_capture(step_id, label=label)
        else:
            logger.debug("[AgenticExec] Sub-tab '%s' not found", sub_tab_name)

        content = await self.dom.read_canvas_content()
        return content

    async def _detect_sub_tabs(self) -> list[str]:
        """Return list of visible sub-tab names from live DOM."""
        known = ["Overview", "Output", "Questions", "Thinking",
                 "Workflow", "Architecture", "Execution trace"]
        found = []
        for name in known:
            for sel in [f'button:has-text("{name}")', f'[role="tab"]:has-text("{name}")']:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        if name not in found:
                            found.append(name)
                        break
                except Exception:
                    continue

        # Also scan for any unlisted sub-tabs
        try:
            els = self.page.locator('[role="tab"]')
            count = await els.count()
            for i in range(count):
                el = els.nth(i)
                if await el.is_visible():
                    text = (await el.inner_text()).strip()
                    if text and text not in known and text not in found and len(text) < 40:
                        found.append(text)
        except Exception:
            pass

        return found

    # ── Visit single sub-tab ──────────────────────────────────────────────────

    async def _exec_visit_subtab(self, step_id: str, selectors: list) -> str:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.8)
                    await self.scroller.scroll_and_capture(step_id, label="subtab")
                    content = await self.dom.read_canvas_content()
                    self.io_logger.set_sai_output(full_text=content, result="subtab_visited")
                    return "pass"
            except Exception:
                continue
        return "partial"

    # ── Scroll capture ────────────────────────────────────────────────────────

    async def _exec_scroll_capture(self, step_id: str) -> str:
        paths = await self.scroller.scroll_and_capture(step_id, label="page")
        self.io_logger.set_sai_output(result=f"{len(paths)} viewport screenshots")
        return "pass"

    # ── Screenshot ────────────────────────────────────────────────────────────

    async def _exec_screenshot(self, step_id: str, selectors: list) -> str:
        path = await self.scroller.capture_region(step_id, "capture", selectors)
        if path:
            self.io_logger.set_screenshot(path)
            return "pass"
        return "partial"

    async def _exec_screenshot_region(self, step_id: str, selectors: list) -> str:
        return await self._exec_screenshot(step_id, selectors)

    # ── Ensure workspace ──────────────────────────────────────────────────────

    async def _exec_ensure_workspace(self, step_id: str, selectors: list) -> str:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    await asyncio.sleep(1.0)
                    self.io_logger.set_sai_output(result=f"workspace_opened:{sel}")
                    return "pass"
            except Exception:
                continue

        # Try JS fallback
        try:
            result = await self.page.evaluate("""() => {
                const keywords = ['new workspace', 'new chat', '+ new'];
                const btn = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                    .find(el => el.offsetParent && keywords.some(k =>
                        (el.innerText || '').toLowerCase().includes(k)));
                if (btn) { btn.click(); return btn.innerText || 'clicked'; }
                return null;
            }""")
            if result:
                await asyncio.sleep(1.0)
                self.io_logger.set_sai_output(result=f"workspace_opened_js:{result}")
                return "pass"
        except Exception:
            pass

        self.io_logger.set_sai_output(result="workspace_button_not_found")
        return "partial"

    # ── Navigate back ─────────────────────────────────────────────────────────

    async def _exec_navigate_back(self, step_id: str) -> str:
        try:
            await self.page.go_back()
            await asyncio.sleep(0.8)
            self.io_logger.set_sai_output(result="navigated_back")
            return "pass"
        except Exception as e:
            self.io_logger.set_sai_output(result=f"go_back_error:{e}")
            return "partial"

    # ── Agentic mode (LLM over DOM) ───────────────────────────────────────────

    async def _agentic_action(
        self,
        step_id: str,
        goal: str,
        escalation_reason: str,
        max_retries: int = 3,
    ) -> str:
        self.io_logger.set_escalation(escalation_reason)
        url = self.page.url

        for attempt in range(max_retries):
            try:
                dom_snap = await self.dom.snapshot()
                dom_text = json.dumps(dom_snap[:50], default=str)

                messages = [
                    {"role": "system", "content": AGENTIC_SYSTEM},
                    {"role": "user", "content": (
                        f"GOAL: {goal}\n"
                        f"CURRENT URL: {url}\n"
                        f"DOM SNAPSHOT (first 50 elements):\n{dom_text}"
                    )},
                ]
                resp = self._llm.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=400,
                    response_format={"type": "json_object"},
                )
                decision = json.loads(resp.choices[0].message.content)
                logger.info("[AgenticExec] Agentic decision (attempt %d): %s",
                            attempt + 1, decision.get("reasoning", "")[:100])

                act      = decision.get("action", "fail")
                selector = decision.get("selector", "")
                value    = decision.get("value", "")
                nav_url  = decision.get("url", "")

                if act == "done":
                    self.io_logger.set_sai_output(result=f"agentic_done:{decision.get('done_reason','')}")
                    return "pass"

                if act == "fail":
                    self.io_logger.set_sai_output(result=f"agentic_fail:{decision.get('done_reason','')}")
                    return "partial"

                if act == "click" and selector:
                    el = self.page.locator(selector).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(0.5)
                        self.io_logger.set_sai_output(result=f"agentic_clicked:{selector}")
                        return "pass"

                if act == "fill" and selector and value:
                    el = self.page.locator(selector).first
                    if await el.count() > 0:
                        await el.fill(value)
                        await asyncio.sleep(0.3)
                        self.io_logger.set_sai_output(result=f"agentic_filled:{selector}")
                        return "pass"

                if act == "navigate" and nav_url:
                    await self.page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(1.0)
                    self.io_logger.set_sai_output(result=f"agentic_navigated:{nav_url}")
                    return "pass"

                if act == "scroll":
                    await self.scroller.scroll_and_capture(step_id, label=f"agentic_{attempt}")
                    return "pass"

                if act == "screenshot":
                    path = await self.scroller.capture_full_page(step_id, label=f"agentic_{attempt}")
                    self.io_logger.set_screenshot(path)
                    return "pass"

            except Exception as e:
                logger.warning("[AgenticExec] Agentic attempt %d failed: %s", attempt + 1, e)

        logger.warning("[AgenticExec] Agentic mode exhausted %d retries for step %s", max_retries, step_id)
        self.io_logger.set_sai_output(result="agentic_exhausted")
        return "partial"

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _capture_dom_snapshot(self, step_id: str) -> None:
        try:
            snap = await self.dom.snapshot()
            self.io_logger.set_dom_snapshot({"step": step_id, "elements": snap})
        except Exception as e:
            logger.debug("[AgenticExec] DOM snapshot error: %s", e)

    async def _take_step_screenshot(self, step_id: str, label: str) -> None:
        try:
            ss_dir = os.path.join("screenshots", "surfaces", self.run_id)
            os.makedirs(ss_dir, exist_ok=True)
            path = os.path.join(ss_dir, f"{step_id}_{label}_final.png")
            await self.page.screenshot(path=path, full_page=False)
            self.io_logger.set_screenshot(path)
        except Exception as e:
            logger.debug("[AgenticExec] Step screenshot error: %s", e)

    async def _wait_for_agent_loaded(self, timeout_seconds: int = 60) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            loading = await self.dom.is_loading()
            if not loading:
                return
            await asyncio.sleep(1.0)

    async def _wait_for_content_stable(self, timeout_seconds: int = 15) -> None:
        """Wait until visible text stops changing."""
        prev = ""
        stable_count = 0
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                cur = await self.page.evaluate(
                    "() => (document.body.innerText || '').slice(0, 300)"
                )
                if cur == prev:
                    stable_count += 1
                    if stable_count >= 2:
                        return
                else:
                    stable_count = 0
                prev = cur
            except Exception:
                pass
            await asyncio.sleep(0.5)

    @staticmethod
    def _agent_name_from_selectors(selectors: list) -> Optional[str]:
        """Extract agent name (AIA/AGP/ETL/App Studio) from selector list."""
        for sel in selectors:
            for name in ["App Studio", "AIA", "AGP", "ETL"]:
                if name in sel:
                    return name
        return None
