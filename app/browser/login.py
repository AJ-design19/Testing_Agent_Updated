"""
Login handler for vanijstaging.adya.ai

Exact flow (confirmed by DOM inspection):
  1. Navigate to ADYA_URL  (lands on orchestrator page — already has chat box)
  2. Click the top-right  <button type="submit">Sign in</button>
  3. Auth modal appears:
       <input id="email" name="email" placeholder="name@example.com">
       <button id="login-continue-button">Continue</button>
  4. Password step (may appear on same modal or next screen):
       <input type="password"> + submit button
  5. After redirect: page shows "What should we build today?"
     with  <textarea aria-label="Write your prompt here">

Debug screenshots are always saved to screenshots/debug_login_*.png
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


async def _ss(page, label: str) -> None:
    """Save a debug screenshot (never raises)."""
    os.makedirs("screenshots", exist_ok=True)
    path = f"screenshots/debug_login_{label}.png"
    try:
        await page.screenshot(path=path, full_page=False)
        logger.info("[Login] Screenshot: %s  url=%s", path, page.url)
    except Exception:
        pass


async def _networkidle(page, timeout_ms: int = 15000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


async def login(page) -> None:
    """
    Full login sequence for vanijstaging.adya.ai.
    Raises RuntimeError / re-raises Playwright errors on unrecoverable failure.
    """
    adya_url      = os.getenv("ADYA_URL", "").strip()
    adya_email    = os.getenv("ADYA_EMAIL", "").strip()
    adya_password = os.getenv("ADYA_PASSWORD", "").strip()

    if not adya_url:
        raise RuntimeError("ADYA_URL not set in .env")
    if not adya_email or not adya_password:
        raise RuntimeError("ADYA_EMAIL or ADYA_PASSWORD not set in .env")

    # ── 1. Navigate ───────────────────────────────────────────────────────────
    logger.info("[Login] Navigating to %s", adya_url)
    await page.goto(adya_url, wait_until="domcontentloaded", timeout=60_000)
    await _networkidle(page, 15_000)
    await _ss(page, "01_after_goto")
    logger.info("[Login] Page loaded. url=%s", page.url)

    # ── 2. Wait for page to fully render (textarea must appear first) ─────────
    # The orchestrator page has the chat textarea even before login.
    # We wait for it so we know the JS bundle has loaded.
    try:
        await page.wait_for_selector(
            'textarea[aria-label="Write your prompt here"]',
            state="visible", timeout=20_000,
        )
        logger.info("[Login] Orchestrator page rendered (textarea visible)")
    except Exception:
        logger.warning("[Login] Textarea not visible yet — continuing anyway")

    # ── 3. Check if already authenticated ────────────────────────────────────
    # After login the nav shows user-specific buttons; "Sign In" button disappears.
    sign_in_visible = False
    try:
        sign_in_btn = page.locator('button[type="submit"]:has-text("Sign in")').first
        sign_in_visible = await sign_in_btn.is_visible()
    except Exception:
        pass

    if not sign_in_visible:
        logger.info("[Login] Already authenticated — skipping login")
        await _ss(page, "02_already_authed")
        return

    # ── 4. Click Sign In (top-right button) ───────────────────────────────────
    logger.info("[Login] Clicking Sign In button")
    await page.locator('button[type="submit"]:has-text("Sign in")').first.click()
    await asyncio.sleep(0.8)   # let modal animate in
    await _ss(page, "02_signin_clicked")

    # ── 5. Wait for auth modal ────────────────────────────────────────────────
    try:
        await page.wait_for_selector('#email', state="visible", timeout=10_000)
        logger.info("[Login] Auth modal appeared")
    except Exception:
        logger.warning("[Login] Auth modal email field not found — trying fallbacks")

    await _ss(page, "03_auth_modal")

    # ── 6. Fill email ─────────────────────────────────────────────────────────
    email_selectors = [
        '#email',
        'input[name="email"]',
        'input[placeholder="name@example.com"]',
        'input[type="email"]',
    ]
    email_filled = False
    for sel in email_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await el.fill(adya_email)
                logger.info("[Login] Filled email via %s", sel)
                email_filled = True
                break
        except Exception:
            continue

    if not email_filled:
        await _ss(page, "error_email_not_found")
        raise RuntimeError("[Login] Could not fill email — check screenshots/debug_login_error_email_not_found.png")

    await asyncio.sleep(0.3)
    await _ss(page, "04_email_filled")

    # ── 7. Click Continue ─────────────────────────────────────────────────────
    continue_selectors = [
        '#login-continue-button',
        'button:has-text("Continue")',
        'button[type="submit"]:has-text("Continue")',
    ]
    for sel in continue_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                logger.info("[Login] Clicked Continue via %s", sel)
                break
        except Exception:
            continue

    await asyncio.sleep(1.0)
    await _networkidle(page, 10_000)
    await _ss(page, "05_after_continue")

    # ── 8. Fill password (may appear in same modal or new screen) ────────────
    password_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[placeholder*="password" i]',
        'input[autocomplete="current-password"]',
    ]
    password_filled = False
    for attempt in range(3):          # wait up to ~6 s for password field
        for sel in password_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await el.fill(adya_password)
                    logger.info("[Login] Filled password via %s", sel)
                    password_filled = True
                    break
            except Exception:
                continue
        if password_filled:
            break
        await asyncio.sleep(2.0)

    if not password_filled:
        # Some SSO flows (Google/Microsoft) don't show a password field here.
        # If we're on an external IdP page, we have less control.
        logger.warning("[Login] Password field not found — may be SSO-only login")
        await _ss(page, "warning_no_password_field")
    else:
        await asyncio.sleep(0.3)
        await _ss(page, "06_password_filled")

        # ── 9. Submit password ────────────────────────────────────────────────
        submit_selectors = [
            'button[type="submit"]:not(:has-text("Sign in"))',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'input[type="submit"]',
        ]
        for sel in submit_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    logger.info("[Login] Submitted password via %s", sel)
                    break
            except Exception:
                continue

    # ── 10. Wait for post-login dashboard ─────────────────────────────────────
    logger.info("[Login] Waiting for post-login dashboard...")
    await _networkidle(page, 20_000)

    # The definitive sign of a logged-in orchestrator page:
    # the "Sign In" button is gone and the textarea is present.
    dashboard_confirmed = False
    deadline_ms = 45_000
    poll_interval = 2.0
    elapsed = 0.0
    while elapsed * 1000 < deadline_ms:
        try:
            # Sign-in button should be gone
            si_gone = not await page.locator(
                'button[type="submit"]:has-text("Sign in")'
            ).first.is_visible()
            # Textarea should still be there
            ta_present = await page.locator(
                'textarea[aria-label="Write your prompt here"]'
            ).first.is_visible()
            if si_gone and ta_present:
                dashboard_confirmed = True
                break
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    await _ss(page, "07_post_login_final")

    if dashboard_confirmed:
        logger.info("[Login] Login successful — dashboard ready. url=%s", page.url)
        print("[Login] Login successful!")
    else:
        logger.warning("[Login] Dashboard not confirmed after login. url=%s", page.url)
        print("[Login] Warning: login may have failed — check screenshots/debug_login_07_post_login_final.png")
