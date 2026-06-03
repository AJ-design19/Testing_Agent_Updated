"""
Self-Healing Browser Recovery Agent.

Detects and recovers from:
  - Selector failures (element not found)
  - Modal/dialog interruptions (cookie banners, alerts, overlays)
  - Page load failures and stuck states
  - Login session expiry (redirect to login)
  - Broken workflows (canvas never appears)
  - UI freezes (page stops responding)
  - Conversational misunderstandings (Psi gives error responses)

Every recovery attempt is logged with its strategy and outcome.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Selectors for common interruptions
MODAL_DISMISS_SELECTORS = [
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'button:has-text("Close")',
    'button:has-text("Dismiss")',
    'button:has-text("Got it")',
    'button:has-text("OK")',
    'button:has-text("Accept")',
    'button:has-text("Continue")',
    'button[aria-label="close modal"]',
    '[role="dialog"] button:last-child',
]

OVERLAY_SELECTORS = [
    '.modal-backdrop',
    '.overlay',
    '[data-testid="modal-overlay"]',
    '[role="dialog"]',
    '.toast-container',
    '.notification-banner',
]

# Patterns in page text that indicate a session/auth problem
AUTH_ERROR_PATTERNS = [
    "session expired",
    "please sign in",
    "unauthorized",
    "401",
    "not authenticated",
    "login required",
]

# Patterns that indicate a Psi error response
PSI_ERROR_PATTERNS = [
    "i'm sorry, i couldn't",
    "i encountered an error",
    "something went wrong",
    "i'm unable to process",
    "please try again",
    "an error occurred",
]


class RecoveryEvent:
    def __init__(self, issue: str, strategy: str, success: bool, notes: str = ""):
        self.issue      = issue
        self.strategy   = strategy
        self.success    = success
        self.notes      = notes
        self.timestamp  = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "issue":     self.issue,
            "strategy":  self.strategy,
            "success":   self.success,
            "notes":     self.notes,
            "timestamp": self.timestamp,
        }


class SelfHealer:
    """
    Autonomous recovery agent. Monitors the browser state and heals automatically.

    Error categories handled (per spec):
      - timeout              : element / page load timed out
      - stale_locator        : element detached from DOM (React re-render)
      - missing_element      : selector matched nothing
      - rendering_failure    : content area blank / still loading after timeout
      - invalid_ai_response  : Psi returned an error message
      - modal_blocked        : overlay / modal blocking interaction
      - session_expired      : redirected to login
      - page_stuck           : JS freeze or unresponsive page
      - canvas_missing       : canvas never appeared after Psi handoff

    All recovery attempts are recorded in recovery_log.
    """

    def __init__(self, page, run_id: str):
        self.page = page
        self.run_id = run_id
        self.recovery_log: list[dict] = []

    # ── Stale element / locator retry ─────────────────────────────────────────

    async def retry_stale_action(
        self,
        selectors: list[str],
        action: str = "click",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> bool:
        """
        Retry an action against a list of selectors, re-evaluating the DOM each
        time to handle stale locators caused by React re-renders.

        action: "click" | "focus" | "check_visible"
        Returns True on success.
        """
        for attempt in range(max_retries):
            for sel in selectors:
                try:
                    el = self.page.locator(sel).first
                    # Re-evaluate whether element is attached and visible
                    if await el.count() == 0:
                        continue
                    if not await el.is_visible():
                        continue
                    if action == "click":
                        await el.click()
                    elif action == "focus":
                        await el.focus()
                    self._log(
                        "stale_locator",
                        f"retry_attempt_{attempt + 1}:{sel}",
                        True,
                        f"action={action}",
                    )
                    return True
                except Exception as e:
                    if "detached" in str(e).lower() or "stale" in str(e).lower():
                        logger.debug(
                            "[SelfHealer] Stale element on attempt %d/%d (%s): %s",
                            attempt + 1, max_retries, sel, e,
                        )
                    continue
            await asyncio.sleep(retry_delay)

        self._log("stale_locator", "all_retries_failed", False,
                  f"selectors={selectors[:2]}")
        return False

    async def retry_with_timeout(
        self,
        coroutine_factory,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        label: str = "action",
    ) -> Optional[Any]:
        """
        Retry an async coroutine on timeout or element-not-found errors.
        coroutine_factory: a zero-arg callable returning a coroutine.
        Returns the coroutine result or None if all retries fail.
        """
        for attempt in range(max_retries):
            try:
                result = await coroutine_factory()
                return result
            except Exception as e:
                err_lower = str(e).lower()
                is_timeout = "timeout" in err_lower or "timed out" in err_lower
                is_missing = "not found" in err_lower or "no element" in err_lower
                if is_timeout or is_missing:
                    self._log(
                        "timeout" if is_timeout else "missing_element",
                        f"retry_{attempt + 1}/{max_retries}:{label}",
                        False,
                        str(e)[:120],
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    continue
                raise  # non-retriable error — re-raise immediately

        self._log(label, "all_retries_exhausted", False)
        return None

    async def wait_for_dom_stable(self, timeout_seconds: float = 10.0) -> bool:
        """
        Wait until the DOM stops mutating (React has finished rendering).
        Uses a JS MutationObserver to detect when the DOM is quiescent.
        Returns True when stable, False if timeout exceeded.
        """
        js = """(timeout_ms) => new Promise((resolve) => {
            let timer = null;
            const reset = () => {
                if (timer) clearTimeout(timer);
                timer = setTimeout(() => {
                    observer.disconnect();
                    resolve(true);
                }, 300);   // 300 ms of no mutations = stable
            };
            const observer = new MutationObserver(reset);
            observer.observe(document.body, {
                childList: true, subtree: true,
                attributes: true, characterData: true,
            });
            reset();  // start the timer immediately
            // Hard deadline
            setTimeout(() => {
                observer.disconnect();
                resolve(false);
            }, timeout_ms);
        })"""
        try:
            return await self.page.evaluate(js, int(timeout_seconds * 1000))
        except Exception as e:
            logger.debug("[SelfHealer] wait_for_dom_stable error: %s", e)
            return True  # assume stable on error

    async def check_rendering_failure(self, content_selectors: list[str]) -> bool:
        """
        Detect a rendering failure: the content area is present but blank/still loading.
        Returns True if a rendering failure is detected.
        """
        for sel in content_selectors:
            try:
                el = await self.page.query_selector(sel)
                if not el or not await el.is_visible():
                    continue
                text = (await el.inner_text()).strip()
                # Blank or only whitespace = rendering failure
                if not text:
                    self._log(
                        "rendering_failure",
                        f"blank_content_area:{sel}",
                        False,
                        "content area visible but empty",
                    )
                    return True
                # Still has loading indicator text
                if any(w in text.lower() for w in ["loading", "please wait", "generating"]):
                    self._log(
                        "rendering_failure",
                        f"still_loading:{sel}",
                        False,
                        f"text starts with: {text[:60]}",
                    )
                    return True
            except Exception:
                continue
        return False

    def _log(self, issue: str, strategy: str, success: bool, notes: str = "") -> None:
        event = RecoveryEvent(issue, strategy, success, notes)
        self.recovery_log.append(event.to_dict())
        level = logging.INFO if success else logging.WARNING
        logger.log(level, "[SelfHealer] %s | strategy=%s | success=%s | %s",
                   issue, strategy, success, notes)

    async def _ss_error(self, label: str) -> None:
        os.makedirs("screenshots", exist_ok=True)
        try:
            await self.page.screenshot(
                path=f"screenshots/recovery_{self.run_id}_{label}.png"
            )
        except Exception:
            pass

    # ── Modal / overlay dismissal ─────────────────────────────────────────────

    async def dismiss_modals(self) -> bool:
        """Dismiss any blocking modal or overlay. Returns True if something was dismissed."""
        dismissed = False
        for sel in MODAL_DISMISS_SELECTORS:
            try:
                el = self.page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(0.5)
                    self._log("modal_blocking", f"click {sel}", True)
                    dismissed = True
                    break
            except Exception:
                continue

        # Try pressing Escape as fallback
        if not dismissed:
            for sel in OVERLAY_SELECTORS:
                try:
                    el = self.page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await self.page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                        self._log("modal_overlay", "press Escape", True)
                        dismissed = True
                        break
                except Exception:
                    continue
        return dismissed

    # ── Session / auth recovery ───────────────────────────────────────────────

    async def check_and_recover_session(self, login_fn: Callable) -> bool:
        """
        Check if the session has expired. If so, re-login.
        Returns True if session is OK (or was recovered).
        """
        try:
            page_text = await self.page.evaluate("() => document.body.innerText")
            for pattern in AUTH_ERROR_PATTERNS:
                if pattern in page_text.lower():
                    logger.warning("[SelfHealer] Session expired detected — re-logging in")
                    await self._ss_error("session_expired")
                    try:
                        await login_fn(self.page)
                        self._log("session_expired", "re_login", True)
                        return True
                    except Exception as e:
                        self._log("session_expired", "re_login", False, str(e))
                        return False
        except Exception:
            pass
        return True

    # ── Page freeze / stuck state ─────────────────────────────────────────────

    async def check_page_responsive(self, timeout_ms: int = 5000) -> bool:
        """Check that the page JS is still responsive."""
        try:
            # NOTE: page.evaluate() does NOT accept a timeout kwarg in Playwright Python.
            # Use asyncio.wait_for to apply a timeout externally.
            result = await asyncio.wait_for(
                self.page.evaluate("() => document.readyState"),
                timeout=timeout_ms / 1000.0,
            )
            return result in ("complete", "interactive")
        except Exception as e:
            self._log("page_freeze", "evaluate readyState", False, str(e))
            return False

    async def recover_stuck_page(self) -> bool:
        """
        Try to recover a frozen page.
        DOES NOT reload — a reload destroys the active workspace/conversation.
        Instead tries: dismiss modals → scroll to top → wait for DOM to settle.
        """
        try:
            await self._ss_error("stuck_page_before_reload")
            # Step 1: dismiss any blocking overlay
            await self.dismiss_modals()
            await asyncio.sleep(1.0)
            # Step 2: scroll to top to reveal any hidden content
            try:
                await self.page.evaluate("() => window.scrollTo(0, 0)")
            except Exception:
                pass
            await asyncio.sleep(1.0)
            # Step 3: verify page is now responsive
            responsive = await self.check_page_responsive()
            self._log("page_stuck", "dismiss_and_scroll", responsive)
            return responsive
        except Exception as e:
            self._log("page_stuck", "dismiss_and_scroll", False, str(e))
            return False

    # ── Element not found recovery ────────────────────────────────────────────

    async def recover_missing_element(
        self,
        selector: str,
        alternatives: list[str],
        action: str = "click",
    ) -> Optional[str]:
        """
        Try alternative selectors when the primary one fails.
        Returns the working selector, or None.
        """
        for alt in alternatives:
            try:
                el = self.page.locator(alt).first
                if await el.count() > 0 and await el.is_visible():
                    self._log(
                        f"element_not_found:{selector}",
                        f"fallback:{alt}",
                        True,
                    )
                    return alt
            except Exception:
                continue

        # Last resort: take a screenshot and log
        await self._ss_error(f"element_not_found")
        self._log(f"element_not_found:{selector}", "all_fallbacks_failed", False)
        return None

    # ── Canvas never appeared ─────────────────────────────────────────────────

    async def recover_missing_canvas(self, navigator) -> bool:
        """
        If the canvas never appeared after Psi handoff, try scrolling,
        dismissing modals, and waiting a bit longer.
        """
        await self._ss_error("canvas_missing")

        # Step 1: dismiss any blocking modal
        dismissed = await self.dismiss_modals()
        if dismissed:
            self._log("canvas_missing", "dismiss_modal_then_wait", True, "modal was blocking")
            return True

        # Step 2: scroll right (canvas may be off-screen)
        try:
            await self.page.evaluate("window.scrollTo(document.body.scrollWidth, 0)")
            await asyncio.sleep(1)
        except Exception:
            pass

        # Step 3: wait for canvas one more time
        appeared = await navigator.wait_for_canvas(timeout_ms=20000)
        self._log("canvas_missing", "scroll_and_wait", appeared)
        return appeared

    # ── Psi error response recovery ───────────────────────────────────────────

    async def handle_psi_error_response(
        self, psi_message: str, retry_prompt: str
    ) -> Optional[str]:
        """
        If Psi returned an error/confused response, send a clarifying retry.
        Returns the retry prompt used, or None if no retry needed.
        """
        lower = psi_message.lower()
        for pattern in PSI_ERROR_PATTERNS:
            if pattern in lower:
                self._log(
                    "psi_error_response",
                    f"send_retry_prompt",
                    True,
                    f"pattern={pattern}",
                )
                return retry_prompt
        return None

    # ── Comprehensive health check ────────────────────────────────────────────

    async def full_health_check(self, login_fn: Optional[Callable] = None) -> dict:
        """
        Run all health checks. Returns a report dict.
        Attempts automatic recovery for each issue found.
        """
        report = {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "issues":     [],
            "recoveries": [],
            "healthy":    True,
        }

        # 1. Page responsiveness
        responsive = await self.check_page_responsive()
        if not responsive:
            report["issues"].append("page_unresponsive")
            recovered = await self.recover_stuck_page()
            report["recoveries"].append({"issue": "page_unresponsive", "recovered": recovered})
            if not recovered:
                report["healthy"] = False

        # 2. Blocking modals
        dismissed = await self.dismiss_modals()
        if dismissed:
            report["issues"].append("modal_blocked")
            report["recoveries"].append({"issue": "modal_blocked", "recovered": True})

        # 3. Session check
        if login_fn:
            session_ok = await self.check_and_recover_session(login_fn)
            if not session_ok:
                report["issues"].append("session_expired")
                report["healthy"] = False

        return report
