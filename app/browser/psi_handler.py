"""
Psi copilot interaction handler.

Handles the full conversation loop with Psi (SAI's conversational AI):
  1. Reads Psi's latest message
  2. Detects whether it's a question requiring a response
  3. Generates a persona-appropriate reply via the AnswerEngine
  4. Sends the reply
  5. Waits — properly — for Psi's next message (no fixed sleeps)
  6. Detects workflow completion / hand-off to canvas agents
"""

import asyncio
import logging
import re
from typing import Optional

from app.conversation.answer_engine import AnswerEngine
from app.browser.sai_navigator import SAINavigator, PSI_CHAT_SELECTORS, _try_selectors

logger = logging.getLogger(__name__)

# ── Selectors (confirmed against vanijstaging.adya.ai DOM) ───────────────────

# The Vanij prompt textarea
CHAT_INPUT_SELECTORS = [
    'textarea[aria-label="Write your prompt here"]',
    'textarea[placeholder*="Ask Orchestrator" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="build" i]',
    'textarea',
]

# The Send message button
SEND_BUTTON_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[type="submit"][aria-label="Send message"]',
    'button[aria-label="Send"]',
]

# Psi / Orchestrator response messages in the chat thread.
# Vanij renders the conversation as a scrollable list; each assistant turn
# is the last element in the messages container.
PSI_LAST_MESSAGE_SELECTORS = [
    # Vanij-specific (to be confirmed once a message is sent)
    '[data-testid="message-assistant"]:last-child',
    '[data-testid="psi-message"]:last-child',
    '.message-assistant:last-child',
    '.assistant-message:last-child',
    '.psi-message:last-child',
    '.copilot-message:last-child',
    '.ai-bubble:last-child',
    '.message[data-role="assistant"]:last-child',
    '[role="log"] > div:last-child',        # generic chat log pattern
    '[aria-live="polite"] > div:last-child', # live region pattern
]

# Loading / typing indicator selectors — Vanij may not have these;
# text-stability fallback will be used instead.
PSI_LOADING_SELECTORS = [
    '[data-testid="psi-typing"]',
    '[data-testid="message-loading"]',
    ".typing-indicator",
    ".psi-thinking",
    ".ai-loading",
    ".message-loading",
    '[aria-label="Psi is thinking"]',
    '[aria-label="Thinking"]',
    ".dot-flashing",
    ".loading-dots",
    ".animate-pulse",      # Tailwind pulse used for skeletons
    '[data-state="loading"]',
]

QUESTION_PATTERNS = [
    r"\?",
    r"\bwhat\b",
    r"\bwhich\b",
    r"\bhow\b",
    r"\bwould you\b",
    r"\bdo you\b",
    r"\bcan you\b",
    r"\bplease (specify|confirm|provide|clarify|describe)\b",
    r"\bplease (choose|select)\b",
    r"\btell me\b",
    r"\blet me know\b",
    r"\bi need (to know|more information|clarification)\b",
    r"\bany preference\b",
    r"\bprefer\b",
]

COMPLETION_PATTERNS = [
    r"workflow (has been |is )?(created|started|initiated|planned)",
    r"(starting|launching|spinning up|initializing) (the )?(agents|aia|agp|etl|app studio)",
    r"(building|generating|creating) your (application|solution|system|app)",
    r"canvas (is|should be) (ready|updating|loading)",
    r"(prototype|solution|application) (completed|ready|generated|built)",
    r"(all agents|agents are) (running|working|active)",
    r"(check|see) (the |your )?(canvas|right panel)",
    r"flow view",
]


def _is_question(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in QUESTION_PATTERNS)


def _is_workflow_complete(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in COMPLETION_PATTERNS)


class PsiHandler:
    """
    Manages the full conversation between the testing agent and Psi.
    All waits are event-driven: we poll for DOM changes rather than
    sleeping for fixed durations.
    """

    def __init__(self, page, persona: dict, answer_engine: AnswerEngine, navigator: SAINavigator):
        self.page = page
        self.persona = persona
        self.answer_engine = answer_engine
        self.navigator = navigator
        self._seen_messages: set[str] = set()
        self.conversation_log: list[dict] = []

    # ── Internal wait helpers ──────────────────────────────────────────────

    async def _wait_for_psi_idle(self, timeout_seconds: int = 90) -> None:
        """
        Wait until Psi stops generating.
        Strategy:
          1. If a loading/typing indicator is visible, wait for it to disappear.
          2. Then poll the last message text: wait until it stabilises
             (same content on two consecutive polls 1.5 s apart).
        Falls back gracefully if no loading selector is found.
        """
        # Step 1 — wait for any visible loading indicator to disappear
        for sel in PSI_LOADING_SELECTORS:
            try:
                await self.page.wait_for_selector(
                    sel, state="visible", timeout=3000
                )
                # It appeared — now wait for it to go away
                await self.page.wait_for_selector(
                    sel, state="hidden", timeout=timeout_seconds * 1000
                )
                logger.debug("[PsiHandler] Loading indicator gone (%s)", sel)
                return
            except Exception:
                continue

        # Step 2 — no indicator found; poll for text stability
        prev_text = ""
        stable_count = 0
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            current = await self.read_latest_psi_message()
            if current and current == prev_text:
                stable_count += 1
                if stable_count >= 2:
                    logger.debug("[PsiHandler] Message stabilised")
                    return
            else:
                stable_count = 0
            prev_text = current
            await asyncio.sleep(1.5)

        logger.warning("[PsiHandler] _wait_for_psi_idle timed out after %ds", timeout_seconds)

    async def _wait_for_page_ready(self, timeout_ms: int = 20000) -> None:
        """Wait for the page network to settle after navigation/action."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass  # networkidle can time out on SPAs — that's fine

    # ── DOM debug helper ───────────────────────────────────────────────────────

    async def _debug_dump(self, label: str) -> None:
        """Save a screenshot + log all visible inputs. Helps diagnose missing selectors."""
        import os
        os.makedirs("screenshots", exist_ok=True)
        path = f"screenshots/debug_psi_{label}.png"
        try:
            await self.page.screenshot(path=path, full_page=False)
            logger.info("[PsiHandler] Debug screenshot: %s", path)
        except Exception:
            pass
        try:
            inputs = await self.page.evaluate("""() =>
                Array.from(document.querySelectorAll(
                    'input, textarea, [contenteditable="true"], button'))
                .filter(el => el.offsetParent !== null)
                .map(el => ({
                    tag:         el.tagName,
                    type:        el.type || '',
                    name:        el.name || '',
                    id:          el.id   || '',
                    placeholder: el.placeholder || '',
                    ariaLabel:   el.getAttribute('aria-label') || '',
                    text:        (el.innerText||'').trim().slice(0,50),
                    class:       el.className || '',
                }))
            """)
            logger.info("[PsiHandler] Visible inputs/buttons at '%s':", label)
            for el in inputs:
                logger.info("  %s type=%-10s name=%-15s placeholder='%s' aria='%s' text='%s' class='%s'",
                            el["tag"], el["type"], el["name"], el["placeholder"],
                            el["ariaLabel"], el["text"], el["class"][:60])
        except Exception as e:
            logger.warning("[PsiHandler] Could not dump inputs: %s", e)

    # ── Send initial workflow prompt ───────────────────────────────────────

    async def send_initial_prompt(self, prompt: str) -> bool:
        """
        Type and submit the initial workflow prompt into the SAI chat box.
        Waits for the chat input to be ready before typing.
        Saves debug screenshots + DOM dump if input not found.
        """
        logger.info("[PsiHandler] Sending initial prompt for persona %s", self.persona.get("id"))

        # Wait for the page to be in a ready state first
        await self._wait_for_page_ready(timeout_ms=30000)

        # Debug: capture state right before we search for the chat input
        await self._debug_dump("before_prompt")

        # Wait for chat input to appear (SAI may still be loading)
        input_sel = await _try_selectors(self.page, CHAT_INPUT_SELECTORS, timeout=20000)
        if not input_sel:
            logger.error("[PsiHandler] No chat input found on page after 20s — saving debug dump")
            await self._debug_dump("no_chat_input")
            return False

        logger.info("[PsiHandler] Chat input found: %s", input_sel)

        # Wait for the input to be enabled (not disabled/readonly)
        try:
            safe_sel = input_sel.replace('"', "'")
            await self.page.wait_for_function(
                f'document.querySelector("{safe_sel}") && '
                f'!document.querySelector("{safe_sel}").disabled',
                timeout=10000,
            )
        except Exception:
            pass  # best-effort

        await self.page.click(input_sel)
        await asyncio.sleep(0.3)
        await self.page.fill(input_sel, prompt)
        await asyncio.sleep(0.5)

        # Debug: confirm text was entered
        await self._debug_dump("after_fill")

        # Try send button first, fall back to Enter key
        send_sel = await _try_selectors(self.page, SEND_BUTTON_SELECTORS, timeout=3000)
        if send_sel:
            logger.info("[PsiHandler] Send button found: %s", send_sel)
            await self.page.click(send_sel)
        else:
            logger.info("[PsiHandler] No send button found — pressing Enter")
            await self.page.keyboard.press("Enter")

        logger.info("[PsiHandler] Initial prompt submitted")
        self.conversation_log.append({"role": "user", "text": prompt})

        # Small settle delay so the DOM registers the sent message
        await asyncio.sleep(1.0)
        return True

    # ── Read latest Psi message ────────────────────────────────────────────

    async def read_latest_psi_message(self) -> str:
        """Return the latest visible message from Psi."""
        for sel in PSI_LAST_MESSAGE_SELECTORS:
            try:
                await self.page.wait_for_selector(sel, timeout=2000, state="visible")
                text = await self.page.inner_text(sel)
                return text.strip()
            except Exception:
                continue

        # Fallback: full panel text, last paragraph
        panel_text = await self.navigator.get_psi_panel_text()
        if panel_text:
            paragraphs = [p.strip() for p in panel_text.split("\n") if p.strip()]
            return paragraphs[-1] if paragraphs else ""
        return ""

    # ── Wait for new Psi message ───────────────────────────────────────────

    async def wait_for_new_psi_message(self, timeout_seconds: int = 90) -> Optional[str]:
        """
        Wait for Psi to finish generating, then return its new message.
        Properly waits for the response — no fixed sleep.
        Returns None if timeout exceeded without a new message.
        """
        # Wait until Psi stops generating
        await self._wait_for_psi_idle(timeout_seconds=timeout_seconds)

        # Now read the message; poll briefly in case DOM hasn't updated yet
        for _ in range(5):
            msg = await self.read_latest_psi_message()
            if msg and msg not in self._seen_messages:
                self._seen_messages.add(msg)
                self.conversation_log.append({"role": "assistant", "text": msg})
                logger.info("[PsiHandler] New Psi message (%d chars): %s…", len(msg), msg[:120])
                return msg
            await asyncio.sleep(1.0)

        logger.warning("[PsiHandler] No new Psi message after waiting %ds", timeout_seconds)
        return None

    # ── Send a reply to Psi ────────────────────────────────────────────────

    async def send_reply(self, reply_text: str) -> bool:
        """Type and submit a reply to Psi. Waits for the input to be ready."""
        # Wait for any ongoing generation to finish before typing
        await self._wait_for_psi_idle(timeout_seconds=30)

        input_sel = await _try_selectors(self.page, CHAT_INPUT_SELECTORS, timeout=8000)
        if not input_sel:
            logger.error("[PsiHandler] No chat input for reply")
            return False

        await self.page.click(input_sel)
        await asyncio.sleep(0.3)
        await self.page.fill(input_sel, reply_text)
        await asyncio.sleep(0.4)

        send_sel = await _try_selectors(self.page, SEND_BUTTON_SELECTORS, timeout=3000)
        if send_sel:
            await self.page.click(send_sel)
        else:
            await self.page.keyboard.press("Enter")

        logger.info("[PsiHandler] Reply sent: %s", reply_text[:80])
        self.conversation_log.append({"role": "user", "text": reply_text})

        # Small settle delay so the DOM registers the sent message
        await asyncio.sleep(0.8)
        return True

    # ── Main conversation loop ─────────────────────────────────────────────

    async def run_conversation_loop(self, max_rounds: int = 10) -> dict:
        """
        Run the full Psi conversation loop.
        After the initial prompt is sent (caller's responsibility), this:
          - Waits (event-driven) for Psi to reply
          - Answers questions in persona voice
          - Stops when Psi signals agents are starting or workflow is complete
        Returns a summary dict.
        """
        question_count = 0
        completion_detected = False
        round_num = 0

        for round_num in range(max_rounds):
            logger.info("[PsiHandler] Conversation round %d/%d", round_num + 1, max_rounds)

            psi_msg = await self.wait_for_new_psi_message(timeout_seconds=90)
            if not psi_msg:
                logger.info("[PsiHandler] No new message — assuming Psi is done")
                break

            if _is_workflow_complete(psi_msg):
                logger.info("[PsiHandler] Workflow completion detected")
                completion_detected = True
                break

            if _is_question(psi_msg):
                question_count += 1
                logger.info("[PsiHandler] Question #%d detected", question_count)
                reply = self.answer_engine.generate(psi_msg)
                logger.info("[PsiHandler] Generated reply: %s", reply[:100])
                await self.send_reply(reply)
            else:
                # Psi is providing information — wait for the next message
                logger.info("[PsiHandler] Informational message, waiting for follow-up")

        return {
            "question_count": question_count,
            "completion_detected": completion_detected,
            "conversation_log": self.conversation_log,
            "rounds": min(round_num + 1, max_rounds),
        }
