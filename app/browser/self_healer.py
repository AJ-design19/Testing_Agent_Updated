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
from typing import Optional, Callable, Any

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
    All recovery attempts are recorded in recovery_log.
    """

    def __init__(self, page, run_id: str):
        self.page = page
        self.run_id = run_id
        self.recovery_log: list[dict] = []

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
            result = await self.page.evaluate(
                "() => document.readyState", timeout=timeout_ms
            )
            return result in ("complete", "interactive")
        except Exception as e:
            self._log("page_freeze", "evaluate readyState", False, str(e))
            return False

    async def recover_stuck_page(self) -> bool:
        """Try to recover a frozen page by reloading."""
        try:
            await self._ss_error("stuck_page_before_reload")
            await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            self._log("page_stuck", "page_reload", True)
            return True
        except Exception as e:
            self._log("page_stuck", "page_reload", False, str(e))
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
