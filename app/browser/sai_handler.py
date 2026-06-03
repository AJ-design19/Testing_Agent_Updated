"""
SAI copilot interaction handler.

Talks to SAI exactly like a human would:
  - Waits for SAI to finish generating before doing anything
  - Detects AGENT QUESTION cards and answers them (checkbox select + Submit)
  - After submitting a card, waits for that card to DISAPPEAR before moving on
  - Detects plain text questions and replies with LLM-generated answers
  - Tracks which cards/messages have already been handled to avoid re-answering
  - Stops when SAI signals workflow completion
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Optional

from app.conversation.answer_engine import AnswerEngine
from app.browser.sai_navigator import SAINavigator, _try_selectors
from app.browser.dom_intelligence import DOMIntelligence
from app.learning.sai_learning import SAILearning

logger = logging.getLogger(__name__)

# ESM step event type constants (BRD F3.8)
ESM_STEP_STARTED             = "sai.step.started"
ESM_STEP_ACTION_TAKEN        = "sai.step.action_taken"
ESM_STEP_OBSERVATION_CAPTURED = "sai.step.observation_captured"
ESM_STEP_JUDGED              = "sai.step.judged"
ESM_STEP_COMPLETED           = "sai.step.completed"
ESM_STEP_FAILED              = "sai.step.failed"

# ── Selectors ─────────────────────────────────────────────────────────────────

CHAT_INPUT_SELECTORS = [
    'textarea[aria-label="Write your prompt here"]',
    'textarea[placeholder*="Ask Orchestrator" i]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="build" i]',
    'textarea',
]

SEND_BUTTON_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[type="submit"][aria-label="Send message"]',
    'button[aria-label="Send"]',
    'button[type="submit"]',
]

# ── Conversation pattern matching ─────────────────────────────────────────────

QUESTION_PATTERNS = [
    r"\?",
    r"\bwhat\b", r"\bwhich\b", r"\bhow\b",
    r"\bwould you\b", r"\bdo you\b", r"\bcan you\b",
    r"\bplease (specify|confirm|provide|clarify|describe|choose|select)\b",
    r"\btell me\b", r"\blet me know\b",
    r"\bi need (to know|more information|clarification)\b",
    r"\bany preference\b", r"\bprefer\b",
]

BLOCKING_PATTERNS = [
    r"insufficient wallet balance",
    r"wallet balance",
    r"please recharge",
    r"minimum required.{0,30}coins",
    r"not enough (credits|coins|balance)",
    r"recharge your wallet",
    r"top.?up your (wallet|account|balance)",
    r"configure your api key",
]

def _is_blocking_error(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in BLOCKING_PATTERNS)


COMPLETION_PATTERNS = [
    r"workflow (has been |is )?(created|started|initiated|planned)",
    r"(starting|launching|spinning up|initializing) (the )?(agents|aia|agp|etl|app studio)",
    r"(building|generating|creating) your (application|solution|system|app)",
    r"canvas (is|should be) (ready|updating|loading)",
    r"(prototype|solution|application) (completed|ready|generated|built)",
    r"(all agents|agents are) (running|working|active)",
    r"(check|see) (the |your )?(canvas|right panel)",
    r"flow view",
    r"proceed with executing",
    r"would you like to proceed",
    r"reply with.{0,20}continue",
    r"i will now (start|begin|launch|initiate)",
    r"kicking off",
]


def _is_question(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in QUESTION_PATTERNS)


def _is_workflow_complete(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in COMPLETION_PATTERNS)


def _card_hash(card: dict) -> str:
    """
    Stable hash of a card's IDENTITY for deduplication.
    Based only on the sorted option texts — NOT the full card text or checked state,
    because clicking options changes the card's innerText (checkmarks), which would
    produce a different hash for the same card after answering it.
    """
    options = sorted(o["text"] for o in card.get("options", []))
    if not options:
        # Fallback: use first 60 chars of card text
        return hashlib.md5(card.get("cardText", "")[:60].encode()).hexdigest()[:12]
    return hashlib.md5(str(options).encode()).hexdigest()[:12]


class SAIHandler:
    """
    Full conversational agent that talks back and forth with SAI's copilot.
    """

    def __init__(self, page, persona: dict, answer_engine: AnswerEngine,
                 navigator: SAINavigator, run_id: str = "", screenshot_agent=None,
                 esll_service=None, journey: Optional[dict] = None):
        self.page = page
        self.persona = persona
        self.answer_engine = answer_engine
        self.navigator = navigator
        self.run_id = run_id
        self.ss = screenshot_agent
        self.dom = DOMIntelligence(page)
        self._seen_messages: set[str] = set()
        self._answered_card_hashes: set[str] = set()
        self.conversation_log: list[dict] = []
        self._chat_ss_index = 0
        # ESM per-step emission (BRD F3.8) — optional; no-ops if not provided
        self._esll = esll_service
        self._journey_id = (journey or {}).get("id", "") or run_id
        self._persona_id = persona.get("id", "")
        # Self-learning: tracks response times and question patterns per workflow
        self._learning = SAILearning(
            workflow_id=(journey or {}).get("id", run_id),
            workflow_title=(journey or {}).get("title", ""),
        )
        self._learning.start_run()
        self._round_start_time: float = time.monotonic()
        # Full conversation turns with timing — populated during the loop
        self.interaction_log: list[dict] = []

    # ── ESM step event emission (BRD F3.8) ───────────────────────────────────

    def _emit_step(
        self,
        event_type: str,
        step_index: int,
        step_label: str,
        payload: Optional[dict] = None,
        artifact_uris: Optional[list] = None,
    ) -> None:
        """Emit a per-step ESM event. No-op if no ESLLService was provided."""
        if self._esll is None:
            return
        try:
            self._esll.emit_step_event(
                event_type=event_type,
                run_id=self.run_id,
                persona_id=self._persona_id,
                journey_id=self._journey_id,
                step_index=step_index,
                step_label=step_label,
                payload=payload or {},
                artifact_uris=artifact_uris or [],
            )
        except Exception as e:
            logger.debug("[SAIHandler] ESM emit_step_event failed (non-fatal): %s", e)

    # ── Idle detection ────────────────────────────────────────────────────────

    async def _wait_for_sai_idle(self, timeout_seconds: int = 300,
                                   require_activity: bool = False) -> None:
        """
        Wait until SAI is done generating and ready for input.

        require_activity=True  → must observe SAI actually start generating
                                 before declaring idle. Use this right after
                                 sending a prompt so we don't return before
                                 SAI has begun its response.
        require_activity=False → fast-path: return immediately if already idle
                                 (use when re-entering after card answers etc.)
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds

        # ── Fast-path: already idle AND we don't need to see activity first ──
        if not require_activity:
            try:
                state = await self.dom.get_sai_state()
                if state.get("hasSubmitCard"):
                    logger.info("[SAIHandler] Idle: AGENT QUESTION card already present")
                    return
                if state.get("tabs"):
                    logger.info("[SAIHandler] Idle: canvas tabs already live")
                    return
                if state.get("textareaReady") and not state.get("isGenerating"):
                    logger.info("[SAIHandler] Idle: page already idle (no spinner, textarea ready)")
                    return
            except Exception:
                pass

        logger.info("[SAIHandler] Waiting for SAI idle (max=%ds, require_activity=%s)…",
                    timeout_seconds, require_activity)

        last_log_at = loop.time()
        was_generating = False
        activity_seen = False   # True once we've observed SAI start generating
        stable_text = ""
        stable_count = 0

        # Snapshot body text at the moment we start waiting — so we know when
        # new content actually appears even without a spinner
        try:
            baseline_body = await self.page.evaluate(
                "() => (document.body.innerText || '').slice(0, 800)"
            )
        except Exception:
            baseline_body = ""
        last_body = baseline_body

        while loop.time() < deadline:
            try:
                state = await self.dom.get_sai_state()

                # Signal 1: AGENT QUESTION card
                if state.get("hasSubmitCard"):
                    logger.info("[SAIHandler] Idle: AGENT QUESTION card appeared")
                    return

                # Signal 2: Canvas tabs appeared (SAI handed off)
                if state.get("tabs"):
                    logger.info("[SAIHandler] Idle: canvas tabs appeared %s",
                                [t["text"] for t in state["tabs"]])
                    return

                is_gen = state.get("isGenerating", False)
                if is_gen:
                    was_generating = True
                    activity_seen = True

                # Signal 3: was generating, now stopped
                if was_generating and not is_gen and state.get("textareaReady"):
                    logger.info("[SAIHandler] Idle: generation stopped, textarea ready")
                    await asyncio.sleep(0.5)
                    return

                # Signal 4: body text changed from baseline → SAI produced output
                # Use this when spinner detection misses (e.g. streaming without CSS class)
                try:
                    cur_body = await self.page.evaluate(
                        "() => (document.body.innerText || '').slice(0, 800)"
                    )
                except Exception:
                    cur_body = last_body

                if cur_body != baseline_body:
                    activity_seen = True
                    # Now wait for it to stabilise
                    if cur_body == stable_text:
                        stable_count += 1
                        if stable_count >= 3 and not is_gen:
                            logger.info("[SAIHandler] Idle: body text stable after change")
                            return
                    else:
                        stable_text = cur_body
                        stable_count = 0
                    last_body = cur_body
                elif not require_activity or activity_seen:
                    # No activity AND we're not requiring it — check stable idle
                    if not is_gen and state.get("textareaReady"):
                        if cur_body == stable_text:
                            stable_count += 1
                            if stable_count >= 3:
                                logger.info("[SAIHandler] Idle: stable, not generating")
                                return
                        else:
                            stable_text = cur_body
                            stable_count = 0

            except Exception:
                pass

            now = loop.time()
            if now - last_log_at >= 30:
                elapsed = int(now - (deadline - timeout_seconds))
                logger.info("[SAIHandler] Still waiting for SAI… (%ds elapsed)", elapsed)
                last_log_at = now

            await asyncio.sleep(0.5)

        logger.warning("[SAIHandler] _wait_for_sai_idle timed out after %ds", timeout_seconds)

    # ── Screenshots ───────────────────────────────────────────────────────────

    async def _screenshot_chat(self, label: str) -> Optional[str]:
        """Capture SAI chat panel only when content has changed since last capture."""
        if self.ss:
            return await self.ss.capture_if_changed(label=label, event_type="chat_interaction")
        # No screenshot agent — skip silently
        return None

    async def _debug_dump(self, label: str, *, error: bool = False) -> None:
        """Log page state; only save a screenshot file when error=True."""
        await self.dom.log_page_state(label)
        if error:
            os.makedirs("screenshots", exist_ok=True)
            path = f"screenshots/debug_psi_{label}.png"
            try:
                await self.page.screenshot(path=path, full_page=False)
            except Exception:
                pass

    # ── Chat input / send button ──────────────────────────────────────────────

    async def _get_active_chat_input(self) -> Optional[str]:
        sel = await self.dom.get_chat_input_selector()
        if sel:
            return sel
        return await _try_selectors(self.page, CHAT_INPUT_SELECTORS, timeout=5000)

    async def _get_send_button(self) -> Optional[str]:
        try:
            btn_sel = await self.page.evaluate("""() => {
                const ta = Array.from(document.querySelectorAll('textarea'))
                    .filter(el => el.offsetParent && !el.disabled).pop();
                if (!ta) return null;
                let p = ta.parentElement;
                for (let i = 0; i < 6 && p; i++) {
                    const btns = Array.from(p.querySelectorAll('button'))
                        .filter(b => b.offsetParent && !b.disabled);
                    for (const btn of btns) {
                        const al = btn.getAttribute('aria-label') || '';
                        const t  = (btn.innerText || '').trim();
                        if (/send|submit/i.test(al) || /send/i.test(t) || btn.type==='submit') {
                            if (btn.id) return '#' + CSS.escape(btn.id);
                            if (al) return '[aria-label="' + al + '"]';
                            return null;
                        }
                    }
                    p = p.parentElement;
                }
                return null;
            }""")
            if btn_sel:
                return btn_sel
        except Exception:
            pass
        return await _try_selectors(self.page, SEND_BUTTON_SELECTORS, timeout=3000)

    # ── Read latest SAI message ───────────────────────────────────────────────

    async def read_latest_psi_message(self) -> str:
        """Read the latest AI assistant message from the chat, excluding card content."""
        msg_js = """() => {
            // Find and exclude the agent question card
            const submitBtn = Array.from(document.querySelectorAll('button'))
                .find(b => /submit answers/i.test((b.innerText||'').trim()) && b.offsetParent);
            const cardEl = submitBtn
                ? (submitBtn.closest('[class*="card"],[class*="question"],[class*="Card"]') ||
                   submitBtn.parentElement)
                : null;

            // Look for message containers with AI/assistant content
            const msgSelectors = [
                '[class*="assistant"]', '[class*="ai-message"]', '[class*="bot-message"]',
                '[class*="sai-message"]', '[data-role="assistant"]', '[data-sender="assistant"]',
                '[class*="message-content"]', '[class*="chat-bubble"]',
            ];
            let found = '';
            for (const sel of msgSelectors) {
                const els = Array.from(document.querySelectorAll(sel))
                    .filter(el => {
                        if (!el.offsetParent) return false;
                        if (cardEl && cardEl.contains(el)) return false;
                        const t = (el.innerText || '').trim();
                        return t.length > 20;
                    });
                if (els.length > 0) {
                    const t = (els[els.length - 1].innerText || '').trim();
                    if (t.length > found.length) found = t;
                }
            }
            if (found.length > 20) return found.slice(0, 800);

            // Fallback: walk the chat container bottom-up
            const chatRoot = document.querySelector(
                '[class*="conversation"], [class*="messages"], [role="log"], main'
            ) || document.body;
            const allDivs = Array.from(chatRoot.querySelectorAll('p,div,span'))
                .filter(el => {
                    if (!el.offsetParent) return false;
                    if (cardEl && cardEl.contains(el)) return false;
                    if (['button','input','textarea'].includes(el.tagName.toLowerCase())) return false;
                    const t = (el.innerText || '').trim();
                    return t.length > 40 && el.children.length < 6;
                });
            for (let i = allDivs.length - 1; i >= 0; i--) {
                const t = (allDivs[i].innerText || '').trim();
                if (t.length > 40) return t.slice(0, 800);
            }
            return '';
        }"""
        try:
            text = await self.page.evaluate(msg_js)
            if text and len(text.strip()) > 20:
                return text.strip()
        except Exception as e:
            logger.debug("[SAIHandler] msg_js error: %s", e)

        # Fallback: DOM state
        try:
            state = await self.dom.get_sai_state()
            for m in reversed(state.get("messages", [])):
                t = m.get("text", "").strip()
                if len(t) > 20:
                    return t
        except Exception:
            pass

        return await self.navigator.get_sai_panel_text() or ""

    async def wait_for_new_psi_message(self, timeout_seconds: int = 300,
                                         require_activity: bool = False) -> Optional[str]:
        """
        Wait for SAI to reply and return the new message.
        Also returns None if SAI showed an AGENT QUESTION card instead of a text message
        (the caller should detect the card separately via _detect_agent_question_card).
        """
        await self._wait_for_sai_idle(timeout_seconds=timeout_seconds,
                                      require_activity=require_activity)

        # After idle, check for a card — if there is one, return None so the
        # caller's card detection path handles it
        try:
            state = await self.dom.get_sai_state()
            if state.get("hasSubmitCard"):
                logger.debug("[SAIHandler] wait_for_new_psi_message: card present — caller handles")
                return None
        except Exception:
            pass

        for _ in range(8):
            msg = await self.read_latest_psi_message()
            if msg and msg not in self._seen_messages:
                self._seen_messages.add(msg)
                self.conversation_log.append({"role": "assistant", "text": msg})
                logger.info("[SAIHandler] New SAI message (%d chars): %s…", len(msg), msg[:120])
                return msg
            await asyncio.sleep(1.0)
        logger.warning("[SAIHandler] No new SAI message after %ds", timeout_seconds)
        return None

    # ── Send prompt / reply ───────────────────────────────────────────────────

    async def send_initial_prompt(self, prompt: str) -> bool:
        logger.info("[SAIHandler] Sending initial prompt (persona=%s)", self.persona.get("id"))
        await self._debug_dump("before_prompt")  # log-only, no screenshot

        input_sel = await self._get_active_chat_input()
        if not input_sel:
            logger.error("[SAIHandler] No chat input found")
            await self._debug_dump("no_chat_input")
            return False

        logger.info("[SAIHandler] Chat input: %s", input_sel)
        try:
            await self.page.wait_for_selector(input_sel, state="visible", timeout=10000)
        except Exception:
            pass

        await self.page.click(input_sel)
        await asyncio.sleep(0.3)
        # Use keyboard select-all + type so React's onChange fires correctly
        await self.page.keyboard.press("Control+a")
        await self.page.keyboard.press("Delete")
        await asyncio.sleep(0.1)
        await self.page.type(input_sel, prompt, delay=20)
        await asyncio.sleep(0.5)
        await self._debug_dump("after_fill")

        send_sel = await self._get_send_button()
        if send_sel:
            try:
                el = self.page.locator(send_sel).first
                await el.hover()
                await asyncio.sleep(0.1)
                await el.click()
                logger.info("[SAIHandler] Clicked send: %s", send_sel)
            except Exception:
                await self.page.keyboard.press("Enter")
                logger.info("[SAIHandler] Send click failed — pressed Enter")
        else:
            logger.info("[SAIHandler] No send button — pressing Enter")
            await self.page.keyboard.press("Enter")

        logger.info("[SAIHandler] Prompt submitted")
        self.conversation_log.append({"role": "user", "text": prompt})
        await asyncio.sleep(1.5)

        # Quick check: did SAI immediately reply with a blocking error?
        try:
            quick_reply = await self.read_latest_psi_message()
            if quick_reply and _is_blocking_error(quick_reply):
                logger.error(
                    "[SAIHandler] BLOCKING ERROR on initial prompt: %s", quick_reply[:300]
                )
                print(f"\n⛔  SAI returned a blocking error:\n   {quick_reply[:300]}\n"
                      "   Check your wallet balance / API key at vanijstaging.adya.ai\n")
        except Exception:
            pass

        await self._screenshot_chat("initial_prompt_sent")
        return True

    async def send_reply(self, reply_text: str) -> bool:
        """Type a plain text reply into the chat input and send it."""
        await self._wait_for_sai_idle(timeout_seconds=120)

        input_sel = await self._get_active_chat_input()
        if not input_sel:
            logger.error("[SAIHandler] No chat input for reply")
            return False

        try:
            await self.page.wait_for_selector(input_sel, state="visible", timeout=6000)
        except Exception:
            pass

        await self.page.click(input_sel)
        await asyncio.sleep(0.3)
        await self.page.keyboard.press("Control+a")
        await self.page.keyboard.press("Delete")
        await asyncio.sleep(0.1)
        await self.page.type(input_sel, reply_text, delay=20)
        await asyncio.sleep(0.4)

        send_sel = await self._get_send_button()
        if send_sel:
            try:
                el = self.page.locator(send_sel).first
                await el.hover()
                await asyncio.sleep(0.1)
                await el.click()
                logger.info("[SAIHandler] Reply sent via send button")
            except Exception:
                await self.page.keyboard.press("Enter")
                logger.info("[SAIHandler] Reply send click failed — pressed Enter")
        else:
            await self.page.keyboard.press("Enter")
            logger.info("[SAIHandler] Reply sent via Enter")

        logger.info("[SAIHandler] Sent reply: %s", reply_text[:100])
        self.conversation_log.append({"role": "user", "text": reply_text})
        await asyncio.sleep(0.8)
        await self._screenshot_chat("reply_sent")
        return True

    # ── AGENT QUESTION card ───────────────────────────────────────────────────

    async def _detect_agent_question_card(self) -> Optional[dict]:
        """Detect an AGENT QUESTION card that has NOT been answered yet."""
        try:
            state = await self.dom.get_sai_state()
            if not state.get("hasSubmitCard"):
                return None
        except Exception:
            pass

        card = await self.dom.read_agent_card()
        if not card:
            logger.warning("[SAIHandler] hasSubmitCard=True but read_agent_card returned None — "
                           "card DOM may not match expected structure")
            await self._debug_dump("card_read_failed")
            return None

        if card.get("optionCount", 0) == 0:
            logger.warning("[SAIHandler] Card found but 0 options extracted — card text: %s",
                           card.get("cardText", "")[:200])
            await self._debug_dump("card_zero_options")
            # Return the card anyway so the caller can at least attempt to submit
            # with the first available option via a last-resort JS probe
            return card

        h = _card_hash(card)
        if h in self._answered_card_hashes:
            logger.debug("[SAIHandler] Card already answered (hash=%s) — skipping", h)
            return None

        logger.info("[SAIHandler] New AGENT QUESTION card detected: %d options (hash=%s)",
                    card["optionCount"], h)
        return card

    async def _answer_agent_question_card(self, card: dict) -> bool:
        """Select options and click Submit, then wait for the card to disappear."""
        card_text = card.get("cardText", "")
        card_hash = _card_hash(card)
        questions = card.get("questions", [])

        # Fallback: build a single question block from legacy flat fields
        if not questions:
            options = card.get("options", [])
            text_inputs = card.get("textInputs", [])
            if options:
                questions = [{"type": "mcq", "questionText": card_text, "options": options, "selector": ""}]
            for fi in text_inputs:
                questions.append({"type": "text", "questionText": fi.get("label", ""), "selector": fi.get("selector", ""), "placeholder": fi.get("placeholder", ""), "options": []})

        if not questions:
            logger.warning("[SAIHandler] Card has no questions — last-resort: click first checkbox + submit")
            clicked = await self.page.evaluate("""() => {
                const cb = Array.from(document.querySelectorAll(
                    'input[type="checkbox"], [role="checkbox"]'
                )).find(el => el.offsetParent !== null);
                if (cb) { cb.click(); return true; }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(0.4)
                submitted = await self.dom.click_submit_answers()
                if submitted:
                    self._answered_card_hashes.add(card_hash)
                    await self._wait_for_card_to_disappear(timeout_seconds=60)
                    return True
            logger.error("[SAIHandler] Could not answer card — no questions found")
            return False

        persona_ctx = f"Persona: {self.persona.get('name', self.persona.get('id', 'unknown'))}"

        # Answer each question block
        for q in questions:
            q_text = q.get("questionText", "").strip()
            q_type = q.get("type", "mcq")

            if q_type == "text":
                sel = q.get("selector", "")
                if not sel:
                    continue
                prompt = (
                    f"{persona_ctx}\n"
                    f"SAI is asking: {q_text or card_text}\n\n"
                    f"Write a short, relevant answer (1-2 sentences). "
                    f"If unsure, say 'Do as preferred by you'."
                )
                answer = self.answer_engine.generate(prompt)
                logger.info("[SAIHandler] Text answer for '%s': %s", q_text[:60], answer[:80])
                await self._fill_text_field(sel, answer)

            else:  # mcq
                opts = q.get("options", [])
                if not opts:
                    continue
                opt_texts = [o["text"] for o in opts]
                prompt = (
                    f"{persona_ctx}\n"
                    f"SAI is asking: {q_text}\n\n"
                    f"Available options:\n"
                    + "\n".join(f"- {t}" for t in opt_texts)
                    + "\n\nChoose the most relevant option(s). "
                      "Reply with ONLY the exact text(s), comma-separated. "
                      "If none fit well, reply with the first option."
                )
                raw = self.answer_engine.generate(prompt)
                logger.info("[SAIHandler] MCQ answer for '%s': %s", q_text[:60], raw[:100])

                raw_lower = raw.lower()
                chosen = [i for i, o in enumerate(opts) if o["text"].lower() in raw_lower]
                if not chosen:
                    chosen = [i for i, o in enumerate(opts)
                               if any(w in raw_lower for w in o["text"].lower().split() if len(w) > 3)]
                if not chosen:
                    chosen = [0]
                    logger.info("[SAIHandler] No match — defaulting to first option")

                for idx in chosen:
                    opt = opts[idx]
                    await self._click_option(opt["text"], opt.get("selector", ""))
                    await asyncio.sleep(0.4)

        await asyncio.sleep(0.5)

        # Click Submit answers
        submitted = False
        for sel in ['button:has-text("Submit answers")', 'button:has-text("Submit")']:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    submitted = True
                    logger.info("[SAIHandler] Clicked Submit answers: %s", sel)
                    break
            except Exception as e:
                logger.debug("[SAIHandler] Submit click failed (%s): %s", sel, e)
        if not submitted:
            submitted = await self.dom.click_submit_answers()
            if submitted:
                logger.info("[SAIHandler] Submit answers (JS fallback)")
        if not submitted:
            await self.page.keyboard.press("Enter")
            logger.info("[SAIHandler] Submit via Enter (last resort)")

        self.conversation_log.append({"role": "user", "text": f"[CARD ANSWERED] {card_text[:100]}"})
        await self._screenshot_chat("card_answered")
        await self._wait_for_card_to_disappear(timeout_seconds=60)
        self._answered_card_hashes.add(card_hash)
        return True

    async def _click_option(self, opt_text: str, selector: str = "") -> bool:
        """Click an MCQ option by its text. Tries Playwright then JS fallback."""
        # Use the DOM-extracted selector first if available
        if selector:
            try:
                el = self.page.locator(selector).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    logger.info("[SAIHandler] Clicked option via selector: '%s'", opt_text[:60])
                    return True
            except Exception:
                pass

        for loc_str in [f'label:has-text("{opt_text}")', f':text-is("{opt_text}")', f'text="{opt_text}"']:
            try:
                el = self.page.locator(loc_str).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    logger.info("[SAIHandler] Clicked option '%s'", opt_text[:60])
                    return True
            except Exception:
                continue

        # JS fallback
        try:
            result = await self.page.evaluate("""(text) => {
                for (const el of document.querySelectorAll('label, li, div, [role="option"]')) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || '').trim();
                    if (t === text || t.startsWith(text)) {
                        const cb = el.querySelector('input[type="checkbox"], input[type="radio"]');
                        if (cb && !cb.disabled) { cb.click(); return 'cb'; }
                        el.click(); return 'el';
                    }
                }
                return null;
            }""", opt_text)
            if result:
                logger.info("[SAIHandler] JS-clicked option '%s' (%s)", opt_text[:60], result)
                return True
        except Exception as e:
            logger.debug("[SAIHandler] JS click failed for '%s': %s", opt_text, e)

        logger.warning("[SAIHandler] Could not click option '%s'", opt_text[:60])
        return False

    async def _fill_text_field(self, selector: str, value: str) -> bool:
        """Fill a text input/textarea with a React-compatible event dispatch."""
        try:
            el = self.page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.1)
                await el.fill(value)
                logger.info("[SAIHandler] Filled field '%s'", selector[:60])
                return True
        except Exception:
            pass
        # React native setter fallback
        try:
            filled = await self.page.evaluate("""([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el || el.disabled) return false;
                const proto = el.tagName === 'TEXTAREA'
                    ? window.HTMLTextAreaElement.prototype
                    : window.HTMLInputElement.prototype;
                const nv = Object.getOwnPropertyDescriptor(proto, 'value');
                if (nv && nv.set) nv.set.call(el, val);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", [selector, value])
            if filled:
                logger.info("[SAIHandler] React-filled field '%s'", selector[:60])
                return True
        except Exception as e:
            logger.warning("[SAIHandler] Could not fill field '%s': %s", selector[:60], e)
        return False

    def _derive_reasoning(
        self,
        sai_output: str,
        input_sent: str,
        is_question: bool,
        is_completion: bool,
    ) -> str:
        """
        Derive reasoning strictly from the actual SAI output text.
        Never generic — always references what SAI said or didn't say.
        """
        if not sai_output:
            return (
                "SAI returned no message. This step failed because the output was empty — "
                "SAI may still be processing or an error occurred."
            )

        preview = sai_output[:300].replace('"', "'")

        if is_completion:
            return (
                f"Step passed. SAI signalled workflow completion. "
                f"SAI said: \"{preview}{'...' if len(sai_output) > 300 else ''}\""
            )

        if is_question:
            return (
                f"Step passed. SAI asked a clarifying question before proceeding. "
                f"SAI said: \"{preview}{'...' if len(sai_output) > 300 else ''}\". "
                f"Agent responded with: \"{input_sent[:150]}\" to address SAI's query."
            )

        if _is_blocking_error(sai_output):
            return (
                f"Step FAILED. SAI returned a blocking error. "
                f"SAI said: \"{preview}{'...' if len(sai_output) > 300 else ''}\""
            )

        return (
            f"Step passed. SAI provided an informational response. "
            f"SAI said: \"{preview}{'...' if len(sai_output) > 300 else ''}\""
        )

    async def _wait_for_card_to_disappear(self, timeout_seconds: int = 30) -> None:
        """
        Wait until the Submit answers button disappears (SAI processed our answer).
        Simply waits until hasSubmitCard=False or timeout.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            try:
                state = await self.dom.get_sai_state()
                if not state.get("hasSubmitCard"):
                    logger.info("[SAIHandler] Card gone — SAI processing answer")
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                pass
            await asyncio.sleep(1.0)
        logger.debug("[SAIHandler] Card did not disappear in %ds — SAI may still be processing",
                     timeout_seconds)

    # ── Main conversation loop ────────────────────────────────────────────────

    async def run_conversation_loop(self, max_rounds: int = 20) -> dict:
        """
        Full back-and-forth conversation with SAI SAI.

        Each round:
          1. Wait for SAI to be idle (up to 5 min — SAI can take a long time)
          2. Check for AGENT QUESTION card → answer it and wait for card to vanish
          3. Check for canvas tabs → workflow handed off to agents → stop
          4. Read latest SAI message
          5. If workflow complete → stop
          6. If another card appeared → answer it
          7. If plain question → generate reply → send
          8. If informational → wait 3s and loop
        """
        question_count = 0
        completion_detected = False
        round_num = 0
        consecutive_empty = 0

        for round_num in range(max_rounds):
            logger.info("[SAIHandler] ── Round %d/%d ──", round_num + 1, max_rounds)

            # BRD F3.8: step.started
            self._emit_step(ESM_STEP_STARTED, step_index=round_num,
                            step_label=f"round_{round_num + 1}",
                            payload={"round": round_num + 1})

            # Self-learning: mark when this round started so we can time SAI's response
            self._learning.start_round(round_num)
            self._round_start_time = time.monotonic()

            # Use learned wait time for rounds > 0 (first round always uses full timeout)
            adaptive_wait = int(self._learning.get_recommended_wait_s()) if round_num > 0 else 300

            # Step 1: wait until SAI is idle
            # Round 1: require_activity=True so we don't fast-path before SAI starts
            await self._wait_for_sai_idle(
                timeout_seconds=adaptive_wait,
                require_activity=(round_num == 0),
            )
            await asyncio.sleep(0.8)
            await self.dom.log_page_state(f"round{round_num + 1}")

            # Step 2: AGENT QUESTION card?
            card = await self._detect_agent_question_card()
            if card:
                question_count += 1
                consecutive_empty = 0
                logger.info("[SAIHandler] Answering AGENT QUESTION card #%d", question_count)

                # BRD F3.8: step.observation_captured (card detected)
                self._emit_step(ESM_STEP_OBSERVATION_CAPTURED, step_index=round_num,
                                step_label="agent_question_card_detected",
                                payload={"option_count": card.get("optionCount", 0),
                                         "question_count": len(card.get("questions", []))})

                await self._answer_agent_question_card(card)
                card_response_time = time.monotonic() - self._round_start_time

                # Record card interaction in learning log
                card_text = card.get("cardText", "")
                self._learning.record_sai_response(
                    round_num=round_num,
                    sai_message=card_text,
                    input_sent="[AGENT QUESTION CARD ANSWERED]",
                    response_time_s=card_response_time,
                    was_question=True,
                    was_completion=False,
                    was_card=True,
                )
                self.interaction_log.append({
                    "round":            round_num,
                    "response_time_s":  round(card_response_time, 2),
                    "input_sent":       "[AGENT QUESTION CARD ANSWERED]",
                    "sai_output":       card_text,
                    "is_question":      True,
                    "is_completion":    False,
                    "reasoning":        (
                        f"SAI presented an AGENT QUESTION card with "
                        f"{card.get('optionCount', 0)} option(s). "
                        f"Card text: \"{card_text[:200]}\". "
                        f"Agent selected the best-matching option(s) and submitted."
                    ),
                })

                # BRD F3.8: step.action_taken (card answered)
                self._emit_step(ESM_STEP_ACTION_TAKEN, step_index=round_num,
                                step_label="agent_question_card_answered",
                                payload={"card_text_preview": card.get("cardText", "")[:100]})

                logger.info("[SAIHandler] Waiting for SAI to process card answer…")
                await self._wait_for_sai_idle(timeout_seconds=180)
                continue

            # Step 3: canvas tabs already live? (SAI handed off without a text message)
            try:
                state = await self.dom.get_sai_state()
                if state.get("tabs"):
                    tab_names = [t["text"] for t in state["tabs"]]
                    logger.info("[SAIHandler] Canvas tabs live: %s — workflow started", tab_names)
                    completion_detected = True

                    # BRD F3.8: step.completed (workflow handoff)
                    self._emit_step(ESM_STEP_COMPLETED, step_index=round_num,
                                    step_label="canvas_handoff",
                                    payload={"canvas_tabs": tab_names})

                    await self._screenshot_chat("canvas_handoff")
                    break
            except Exception:
                pass

            # Step 4: read latest SAI message
            # Round 0: require_activity so we block until SAI actually responds
            sai_msg = await self.wait_for_new_psi_message(
                timeout_seconds=300,
                require_activity=(round_num == 0),
            )

            if not sai_msg:
                # Check for card one more time (sometimes message arrives as card)
                card = await self._detect_agent_question_card()
                if card:
                    question_count += 1
                    consecutive_empty = 0

                    self._emit_step(ESM_STEP_OBSERVATION_CAPTURED, step_index=round_num,
                                    step_label="agent_question_card_detected_retry",
                                    payload={"option_count": card.get("optionCount", 0)})

                    await self._answer_agent_question_card(card)

                    self._emit_step(ESM_STEP_ACTION_TAKEN, step_index=round_num,
                                    step_label="agent_question_card_answered_retry",
                                    payload={})

                    await self._wait_for_sai_idle(timeout_seconds=180)
                    continue

                # Check for canvas tabs one more time
                try:
                    state = await self.dom.get_sai_state()
                    if state.get("tabs"):
                        logger.info("[SAIHandler] Canvas tabs appeared — ending loop")
                        completion_detected = True

                        self._emit_step(ESM_STEP_COMPLETED, step_index=round_num,
                                        step_label="canvas_handoff_late",
                                        payload={"canvas_tabs": [t["text"] for t in state["tabs"]]})
                        break
                except Exception:
                    pass

                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.info("[SAIHandler] No new message for 2 rounds — ending loop")
                    self._emit_step(ESM_STEP_FAILED, step_index=round_num,
                                    step_label="no_message_timeout",
                                    payload={"consecutive_empty": consecutive_empty})
                    break
                logger.info("[SAIHandler] No new message — will retry (attempt %d/2)",
                            consecutive_empty)
                await asyncio.sleep(1.5)
                continue

            consecutive_empty = 0
            await self._screenshot_chat(f"sai_round{round_num + 1}")

            # Measure how long SAI took to respond
            response_time_s = time.monotonic() - self._round_start_time
            is_question   = _is_question(sai_msg)
            is_completion = _is_workflow_complete(sai_msg)

            # Record observation for self-learning
            last_user_input = next(
                (e.get("text", "") for e in reversed(self.conversation_log)
                 if e.get("role") == "user"),
                "",
            )
            self._learning.record_sai_response(
                round_num=round_num,
                sai_message=sai_msg,
                input_sent=last_user_input,
                response_time_s=response_time_s,
                was_question=is_question,
                was_completion=is_completion,
                was_card=False,
            )

            # Store full interaction turn (no truncation)
            self.interaction_log.append({
                "round":            round_num,
                "response_time_s":  round(response_time_s, 2),
                "input_sent":       last_user_input,
                "sai_output":       sai_msg,          # full — never truncated
                "is_question":      is_question,
                "is_completion":    is_completion,
                "reasoning":        self._derive_reasoning(
                                        sai_msg, last_user_input,
                                        is_question, is_completion,
                                    ),
            })

            # BRD F3.8: step.observation_captured (SAI message received)
            self._emit_step(ESM_STEP_OBSERVATION_CAPTURED, step_index=round_num,
                            step_label="sai_message_received",
                            payload={"message_preview": sai_msg[:200],
                                     "is_question": is_question,
                                     "is_completion": is_completion,
                                     "response_time_s": round(response_time_s, 2)})

            # Step 4.5: blocking error (e.g. insufficient wallet balance) → stop immediately
            if _is_blocking_error(sai_msg):
                logger.warning("[SAIHandler] Blocking error detected — stopping loop: %s", sai_msg[:200])
                self._emit_step(ESM_STEP_FAILED, step_index=round_num,
                                step_label="blocking_error",
                                payload={"message": sai_msg[:300]})
                break

            # Step 5: workflow complete?
            if _is_workflow_complete(sai_msg):
                logger.info("[SAIHandler] Workflow completion detected: %s", sai_msg[:100])
                completion_detected = True

                self._emit_step(ESM_STEP_COMPLETED, step_index=round_num,
                                step_label="workflow_completion_message",
                                payload={"message_preview": sai_msg[:200]})

                await self._screenshot_chat("workflow_complete")
                break

            # Step 6: card appeared alongside message?
            card = await self._detect_agent_question_card()
            if card:
                question_count += 1
                logger.info("[SAIHandler] Card appeared after message #%d", question_count)

                self._emit_step(ESM_STEP_OBSERVATION_CAPTURED, step_index=round_num,
                                step_label="card_after_message",
                                payload={"option_count": card.get("optionCount", 0)})

                await self._answer_agent_question_card(card)

                self._emit_step(ESM_STEP_ACTION_TAKEN, step_index=round_num,
                                step_label="card_after_message_answered",
                                payload={"card_text_preview": card.get("cardText", "")[:100]})

                await self._wait_for_sai_idle(timeout_seconds=180)
                continue

            # Step 7: plain question → reply
            if _is_question(sai_msg):
                question_count += 1
                reply = self.answer_engine.generate(sai_msg)
                logger.info("[SAIHandler] Replying to question: %s", reply[:100])

                # BRD F3.8: step.judged (answer generated for plain question)
                self._emit_step(ESM_STEP_JUDGED, step_index=round_num,
                                step_label="plain_question_answered",
                                payload={"question_preview": sai_msg[:150],
                                         "reply_preview": reply[:150],
                                         "question_number": question_count})

                await self.send_reply(reply)

                # BRD F3.8: step.action_taken (reply sent)
                self._emit_step(ESM_STEP_ACTION_TAKEN, step_index=round_num,
                                step_label="reply_sent",
                                payload={"reply_preview": reply[:150]})
            else:
                # Informational message — wait a beat and continue
                logger.info("[SAIHandler] Informational message — waiting for next SAI turn")
                await asyncio.sleep(1.5)

        logger.info("[SAIHandler] Loop ended: %d questions, completion=%s, rounds=%d",
                    question_count, completion_detected, round_num + 1)
        return {
            "question_count":      question_count,
            "completion_detected": completion_detected,
            "conversation_log":    self.conversation_log,
            "rounds":              min(round_num + 1, max_rounds),
            # Full interaction log — each turn has sai_output (untruncated) + reasoning
            "interaction_log":     self.interaction_log,
            # Self-learning summary for this run
            "learning_summary":    self._learning.get_summary(),
        }
