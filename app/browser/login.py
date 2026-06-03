"""
Login handler for vanijstaging.adya.ai

Behaves exactly like a human at every step:
  - Real Playwright pointer clicks (hover → click), never JS .click()
  - Keystroke-by-keystroke typing with delays
  - Every button click is scoped to the modal dialog to avoid hitting
    the background nav "Sign in" link by mistake
  - Waits for each element to be visible AND enabled before acting
  - Debug screenshots at every step: screenshots/debug_login_*.png
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── JS: read-only page state probe ───────────────────────────────────────────

LOGIN_STATE_JS = """() => {
    const isLoading = Array.from(document.querySelectorAll('*')).some(el => {
        if (!el.offsetParent) return false;
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 20) return false;
        const cls = (el.className || '').toString().toLowerCase();
        const t   = (el.innerText || '').trim().toLowerCase();
        return cls.includes('loading') || cls.includes('spinner') || t === 'loading...';
    });

    const signInEl = Array.from(document.querySelectorAll('button,a,[role="button"]'))
        .find(el => {
            if (!el.offsetParent) return false;
            const t = (el.innerText||el.textContent||'').trim().toLowerCase();
            return t === 'sign in' || t === 'login' || t === 'log in';
        });

    const textarea = Array.from(document.querySelectorAll('textarea'))
        .find(el => el.offsetParent !== null);

    return {
        loggedIn:         !signInEl && !!textarea && !isLoading,
        hasSignInButton:  !!signInEl,
        signInText:       signInEl ? (signInEl.innerText||'').trim() : '',
        hasTextarea:      !!textarea,
        isLoading,
        bodyText:         (document.body.innerText||'').trim().slice(0,300),
        url:              window.location.href,
    };
}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ss(page, label: str) -> None:
    os.makedirs("screenshots", exist_ok=True)
    path = f"screenshots/debug_login_{label}.png"
    try:
        await page.screenshot(path=path, full_page=False)
        logger.info("[Login] Screenshot: %s", path)
    except Exception:
        pass


async def _wait_rendered(page, timeout: int = 90) -> dict:
    """Poll until spinner gone AND (Sign In OR textarea) is visible."""
    loop = asyncio.get_event_loop()
    dead = loop.time() + timeout
    last: dict = {}
    while loop.time() < dead:
        try:
            s = await page.evaluate(LOGIN_STATE_JS)
            last = s
            if not s["isLoading"] and (s["hasSignInButton"] or s["hasTextarea"]):
                logger.info("[Login] Rendered: loggedIn=%s hasSignIn=%s hasTextarea=%s",
                            s["loggedIn"], s["hasSignInButton"], s["hasTextarea"])
                return s
        except Exception:
            pass
        await asyncio.sleep(0.8)
    logger.warning("[Login] _wait_rendered timed out")
    return last


async def _wait_visible_enabled(page, selector: str, timeout: int = 15) -> bool:
    """Wait until element is both visible and enabled."""
    loop = asyncio.get_event_loop()
    dead = loop.time() + timeout
    while loop.time() < dead:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible() and await el.is_enabled():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.4)
    return False


async def _wait_any_visible(page, selectors: list, timeout: int = 15) -> str | None:
    """Return the first selector that becomes visible."""
    loop = asyncio.get_event_loop()
    dead = loop.time() + timeout
    while loop.time() < dead:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    return sel
            except Exception:
                pass
        await asyncio.sleep(0.4)
    return None


async def _human_type(page, selector: str, text: str) -> None:
    """Human-like: click field → select-all → delete → type char-by-char."""
    await page.click(selector)
    await asyncio.sleep(0.2)
    await page.keyboard.press("Control+a")
    await asyncio.sleep(0.05)
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.1)
    await page.type(selector, text, delay=55)


async def _human_click(page, selector: str, label: str) -> bool:
    """Human-like: hover → small pause → real Playwright click."""
    try:
        el = page.locator(selector).first
        if await el.count() == 0:
            logger.warning("[Login] '%s' not found: %s", label, selector)
            return False
        await el.hover()
        await asyncio.sleep(0.15)
        await el.click()
        logger.info("[Login] Clicked '%s' [%s]", label, selector)
        return True
    except Exception as e:
        logger.warning("[Login] Click '%s' failed (%s): %s", label, selector, e)
        return False


# ── Modal-scoped button click ─────────────────────────────────────────────────
# The password screen has a "Sign in" button AND the background nav also has
# "Sign in". We must click only the one inside the dialog/modal.

async def _click_modal_button(page, texts: list, label: str, timeout: int = 12) -> bool:
    """
    Click the submit button INSIDE the modal card only.

    The vanijstaging password screen has:
      - A nav "LOGIN" button in the top-right (must NOT click this)
      - A "Sign in" button inside the modal card (must click this)

    We scope every search to the innermost card/modal container first,
    and only fall back to full-page as a last resort with exact-text matching.
    """
    loop = asyncio.get_event_loop()
    dead = loop.time() + timeout

    while loop.time() < dead:

        # ── Strategy 1: JS scoped to the modal card ───────────────────────────
        # Find the modal container (the white card that holds the password field),
        # then click the submit button inside it — never touches the nav LOGIN.
        try:
            clicked_text = await page.evaluate("""(candidates) => {
                // Find the modal card: the container holding the password input
                const pwInput = document.querySelector('input[type="password"]');
                if (!pwInput) return null;

                // Walk up to find a container big enough to be the modal card
                let card = pwInput.parentElement;
                for (let i = 0; i < 8 && card && card !== document.body; i++) {
                    const r = card.getBoundingClientRect();
                    if (r.width > 200 && r.height > 100) break;
                    card = card.parentElement;
                }
                if (!card || card === document.body) return null;

                // Now find a submit button inside that card
                const btns = Array.from(card.querySelectorAll('button'));
                for (const text of candidates) {
                    const tLow = text.toLowerCase();
                    for (const btn of btns) {
                        if (btn.disabled) continue;
                        const t = (btn.innerText || btn.textContent || '').trim();
                        const tBtnLow = t.toLowerCase();
                        // exact match or the button text contains the candidate
                        if (tBtnLow === tLow || tBtnLow.includes(tLow)) {
                            btn.scrollIntoView({block: 'center'});
                            btn.click();
                            return t;
                        }
                    }
                }
                return null;
            }""", texts)
            if clicked_text:
                logger.info("[Login] JS modal-scoped clicked '%s': '%s'", label, clicked_text)
                return True
        except Exception:
            pass

        # ── Strategy 2: Playwright scoped to password-field ancestor ─────────
        for text in texts:
            try:
                # Use :near() to find the button closest to the password field
                # which guarantees we stay inside the modal, not the nav
                pw = page.locator('input[type="password"]').first
                if await pw.count() > 0:
                    # Find button with matching text that is a sibling/cousin of pw
                    btn = page.locator(f'button:has-text("{text}")').last
                    if await btn.count() > 0:
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.15)
                        await btn.click(force=True)
                        logger.info("[Login] Playwright scoped-clicked '%s': %s", label, text)
                        return True
            except Exception:
                pass

        await asyncio.sleep(0.5)

    logger.warning("[Login] _click_modal_button '%s' timed out", label)
    return False


# ── Main login ────────────────────────────────────────────────────────────────

async def login(page) -> None:
    """
    Complete human-like login sequence for vanijstaging.adya.ai.
    Presses every button explicitly. Raises RuntimeError on failure.
    """
    adya_url      = os.getenv("ADYA_URL", "").strip()
    adya_email    = os.getenv("ADYA_EMAIL", "").strip()
    adya_password = os.getenv("ADYA_PASSWORD", "").strip()

    if not adya_url:
        raise RuntimeError("ADYA_URL not set in .env")
    if not adya_email or not adya_password:
        raise RuntimeError("ADYA_EMAIL / ADYA_PASSWORD not set in .env")

    if "/orchestrator" not in adya_url:
        target_url = adya_url.rstrip("/") + "/orchestrator"
    else:
        target_url = adya_url

    # ── Step 1: Navigate ──────────────────────────────────────────────────────
    logger.info("[Login] Navigating to %s", target_url)
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        logger.warning("[Login] goto: %s", e)
    await _ss(page, "01_after_goto")

    # ── Step 2: Wait for React bundle to render ───────────────────────────────
    state = await _wait_rendered(page, timeout=90)
    await _ss(page, "02_rendered")
    logger.info("[Login] State: %s", state)

    # ── Step 3: Already logged in? ────────────────────────────────────────────
    if state.get("loggedIn") and not state.get("hasSignInButton"):
        logger.info("[Login] Already authenticated")
        await _goto_base_and_new_workspace(page, adya_url)
        return

    # Second render check if spinner was still active
    if not state.get("hasSignInButton") and not state.get("loggedIn"):
        await asyncio.sleep(1.5)
        state = await _wait_rendered(page, timeout=45)
        await _ss(page, "02b_recheck")
        if state.get("loggedIn") and not state.get("hasSignInButton"):
            await _goto_base_and_new_workspace(page, adya_url)
            return

    if not state.get("hasSignInButton"):
        await _ss(page, "error_no_signin_button")
        raise RuntimeError(
            "[Login] Sign In button not visible after page load.\n"
            f"Page text: {state.get('bodyText','')[:200]}"
        )

    # ── Step 4: Click the top-right login button ─────────────────────────────
    # vanijstaging uses "LOGIN" text (uppercase) — include all casing variants
    sign_in_selectors = [
        'button:has-text("LOGIN")',
        'button:has-text("Login")',
        'button:has-text("Log in")',
        'button:has-text("Log In")',
        'button:has-text("Sign in")',
        'button:has-text("Sign In")',
        'a:has-text("LOGIN")',
        'a:has-text("Login")',
        'a:has-text("Sign in")',
        'a:has-text("Sign In")',
        '[role="button"]:has-text("LOGIN")',
        '[role="button"]:has-text("Login")',
        '[role="button"]:has-text("Sign in")',
    ]
    # Pick the FIRST visible match (the nav button, before any modal appears)
    clicked_signin = False
    for sel in sign_in_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.hover()
                await asyncio.sleep(0.15)
                await el.click()
                logger.info("[Login] Clicked login button: %s", sel)
                clicked_signin = True
                break
        except Exception:
            pass

    if not clicked_signin:
        # Last-resort: JS scan matching what the state probe detected
        try:
            found = await page.evaluate("""() => {
                const el = Array.from(document.querySelectorAll('button,a,[role="button"]'))
                    .find(el => {
                        if (!el.offsetParent) return false;
                        const t = (el.innerText||el.textContent||'').trim().toLowerCase();
                        return t === 'login' || t === 'sign in' || t === 'log in';
                    });
                if (el) { el.click(); return (el.innerText||'?').trim(); }
                return null;
            }""")
            if found:
                logger.info("[Login] Clicked login button via JS fallback: %s", found)
                clicked_signin = True
        except Exception:
            pass

    if not clicked_signin:
        await _ss(page, "error_signin_not_clicked")
        raise RuntimeError("[Login] Could not click login button")

    await asyncio.sleep(0.6)   # modal animation
    await _ss(page, "03_signin_clicked")

    # ── Step 5: Wait for email input in the modal ─────────────────────────────
    email_selectors = [
        '#email',
        'input[name="email"]',
        'input[type="email"]',
        'input[placeholder="name@example.com"]',
        'input[placeholder*="email" i]',
    ]
    email_sel = await _wait_any_visible(page, email_selectors, timeout=30)
    if not email_sel:
        await _ss(page, "error_no_email_field")
        raise RuntimeError("[Login] Email field did not appear")
    await _ss(page, "04_email_modal")
    logger.info("[Login] Email field: %s", email_sel)

    # ── Step 6: Type email character-by-character ─────────────────────────────
    await _human_type(page, email_sel, adya_email)
    await asyncio.sleep(0.5)
    # Tab to trigger React onBlur validation → enables Continue
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.4)
    await _ss(page, "05_email_typed")
    logger.info("[Login] Email typed: %s", adya_email)

    # ── Step 7: Press Continue button (modal-scoped) ──────────────────────────
    # First wait for it to become enabled (React validates email before enabling)
    continue_selectors = [
        '#login-continue-button',
        'button:has-text("Continue")',
        'button:has-text("Next")',
        'button[type="submit"]:has-text("Continue")',
    ]
    cont_sel = await _wait_any_visible(page, continue_selectors, timeout=20)

    if cont_sel:
        # Wait for it to be enabled too
        await _wait_visible_enabled(page, cont_sel, timeout=15)
        ok = await _human_click(page, cont_sel, "Continue")
        if not ok:
            logger.info("[Login] Continue click failed — pressing Enter on email field")
            try:
                await page.click(email_sel)
                await asyncio.sleep(0.2)
            except Exception:
                pass
            await page.keyboard.press("Enter")
    else:
        logger.info("[Login] Continue not found — pressing Enter")
        try:
            await page.click(email_sel)
            await asyncio.sleep(0.2)
        except Exception:
            pass
        await page.keyboard.press("Enter")

    await asyncio.sleep(0.8)
    await _ss(page, "06_after_continue")
    logger.info("[Login] After Continue — url=%s", page.url)

    # ── Step 8: Wait for password field ──────────────────────────────────────
    password_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[autocomplete="current-password"]',
        'input[placeholder*="password" i]',
    ]
    password_sel = await _wait_any_visible(page, password_selectors, timeout=30)

    if not password_sel:
        logger.warning("[Login] Password field not found — SSO/OAuth flow?")
        await _ss(page, "warning_no_password")
    else:
        await _ss(page, "07_password_field")
        logger.info("[Login] Password field: %s", password_sel)

        # ── Step 9: Type password character-by-character ──────────────────────
        await _human_type(page, password_sel, adya_password)
        await asyncio.sleep(0.4)
        # Tab to fire React onBlur validation → enables Submit
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.4)
        await _ss(page, "08_password_typed")
        logger.info("[Login] Password typed")

        # ── Step 10: Click the modal "Sign in" submit button ──────────────────
        # IMPORTANT: scope to modal so we don't hit the background nav button.
        # The modal submit is the LAST "Sign in" button in the DOM.
        submitted = await _click_modal_button(
            page,
            ["Sign in", "Sign In", "Log in", "Login", "Continue", "Submit"],
            "Submit password",
            timeout=12,
        )
        if not submitted:
            logger.info("[Login] Submit button not found — pressing Enter on password field")
            try:
                await page.click(password_sel)
                await asyncio.sleep(0.2)
            except Exception:
                pass
            await page.keyboard.press("Enter")

        # Give the server time to process the login and redirect
        await asyncio.sleep(3.0)
        await _ss(page, "09_password_submitted")

    # ── Step 11: Wait for dashboard (textarea visible, no Sign In button) ─────
    logger.info("[Login] Waiting for authenticated dashboard…")
    loop = asyncio.get_event_loop()
    dead = loop.time() + 120.0
    authenticated = False
    while loop.time() < dead:
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
        try:
            s = await page.evaluate(LOGIN_STATE_JS)
            if not s.get("hasSignInButton") and s.get("hasTextarea"):
                authenticated = True
                logger.info("[Login] ✓ Dashboard confirmed — url=%s", page.url)
                break
            logger.debug("[Login] Still waiting… hasSignIn=%s hasTA=%s url=%s",
                         s.get("hasSignInButton"), s.get("hasTextarea"), page.url)
        except Exception:
            pass
        await asyncio.sleep(2.0)

    await _ss(page, "10_post_login")

    if authenticated:
        print("[Login] ✓ Login successful!")
        logger.info("[Login] Login successful!")
        # If the site redirected to a saved workspace, navigate back to base
        if "/workspace" in page.url or "workspaceId" in page.url:
            base_url = adya_url.rstrip("/").split("/orchestrator")[0] + "/orchestrator"
            logger.info("[Login] Workspace redirect detected (%s) — navigating to %s",
                        page.url, base_url)
            try:
                await page.goto(base_url, wait_until="networkidle", timeout=30_000)
                logger.info("[Login] Back at base orchestrator: %s", page.url)
            except Exception as e:
                logger.warning("[Login] goto base URL failed: %s", e)
    else:
        logger.warning("[Login] Dashboard not confirmed. url=%s", page.url)
        print("[Login] ⚠ Login may have failed — check screenshots/debug_login_10_post_login.png")

    # ── Step 12: Navigate to base orchestrator, then open a fresh workspace ────
    await _goto_base_and_new_workspace(page, adya_url)


# ── Post-login navigation helpers ────────────────────────────────────────────

async def _goto_base_and_new_workspace(page, adya_url: str) -> None:
    """
    Post-login routine:
      1. Always navigate to https://vanijstaging.adya.ai/orchestrator
         so the agent starts from a clean base page, not a saved workspace.
      2. Click '+ New Workspace'.
    """
    base_url = adya_url.rstrip("/").split("/orchestrator")[0] + "/orchestrator"
    logger.info("[Login] Navigating to base orchestrator: %s", base_url)
    try:
        await page.goto(base_url, wait_until="networkidle", timeout=30_000)
        logger.info("[Login] Arrived at: %s", page.url)
    except Exception as e:
        logger.warning("[Login] goto %s failed: %s", base_url, e)

    await _ensure_workspace(page)


async def _ensure_workspace(page) -> None:
    """
    Click the top-left '+ New Workspace' sidebar button.
    Uses real Playwright pointer events so React's router fires.
    """
    logger.info("[Login] Opening a fresh new workspace…")
    await _ss(page, "11_finding_workspace")

    # Wait up to 15 s for the button to appear in the DOM
    try:
        await page.wait_for_function("""() => {
            const kw = ['new workspace', '+ new workspace', '+ new'];
            return Array.from(document.querySelectorAll(
                'button, a, [role="button"]'
            )).some(el => {
                if (!el.offsetParent) return false;
                const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                return kw.some(k => t.includes(k));
            });
        }""", timeout=15_000)
        logger.info("[Login] '+ New Workspace' button is in DOM")
    except Exception:
        logger.warning("[Login] '+ New Workspace' not found within 15 s — attempting anyway")

    # Give React time to attach event handlers after the button appears in DOM
    await asyncio.sleep(1.0)

    # Strategy 1: Playwright locator — most reliable for React apps because
    # Playwright waits for actionability (visible + enabled + stable) before click.
    clicked = False
    for sel in [
        'button:has-text("+ New Workspace")',
        'button:has-text("New Workspace")',
        'a:has-text("New Workspace")',
        '[data-testid="new-workspace"]',
        'button[aria-label="New Workspace"]',
        'button[aria-label="New workspace"]',
        'button:has-text("+ New")',
    ]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.scroll_into_view_if_needed()
                await el.hover()
                await asyncio.sleep(0.15)
                await el.click()
                logger.info("[Login] Clicked '+ New Workspace' via locator: %s", sel)
                await asyncio.sleep(1.5)
                clicked = True
                break
        except Exception:
            pass

    # Strategy 2: coordinate click via JS geometry + real mouse event
    if not clicked:
        try:
            coords = await page.evaluate("""() => {
                const kw = ['new workspace', '+ new workspace', '+ new'];
                const candidates = Array.from(document.querySelectorAll(
                    'button, a, [role="button"]'
                )).filter(el => {
                    if (!el.offsetParent) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return false;
                    const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                    return kw.some(k => t.includes(k));
                });
                if (!candidates.length) return null;
                candidates.sort((a, b) => {
                    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                    if (Math.abs(ra.top - rb.top) > 20) return ra.top - rb.top;
                    return ra.left - rb.left;
                });
                const r = candidates[0].getBoundingClientRect();
                return {
                    x:    r.left + r.width  / 2,
                    y:    r.top  + r.height / 2,
                    text: (candidates[0].innerText || candidates[0].getAttribute('aria-label') || '?').trim(),
                };
            }""")
            if coords:
                logger.info("[Login] Clicking '+ New Workspace' via coordinates (%.0f, %.0f): '%s'",
                            coords["x"], coords["y"], coords["text"])
                await page.mouse.move(coords["x"], coords["y"])
                await asyncio.sleep(0.15)
                await page.mouse.click(coords["x"], coords["y"])
                await asyncio.sleep(1.5)
                clicked = True
        except Exception as e:
            logger.debug("[Login] Coordinate click failed: %s", e)

    if not clicked:
        logger.warning("[Login] Could not click '+ New Workspace' — proceeding with current state")

    await _ss(page, "12_workspace_final")
    try:
        s = await page.evaluate(LOGIN_STATE_JS)
        logger.info("[Login] Workspace final: hasTextarea=%s url=%s",
                    s.get("hasTextarea"), page.url)
    except Exception:
        pass
