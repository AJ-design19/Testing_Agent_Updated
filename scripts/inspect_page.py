"""
Detailed page inspector — captures every page state including after Sign In click.
Run once to map the full UI flow.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import async_playwright


async def dump_page(page, label: str):
    os.makedirs("screenshots", exist_ok=True)
    path = f"screenshots/inspect_{label}.png"
    await page.screenshot(path=path, full_page=True)
    print(f"\n[Screenshot: {path}]  URL: {page.url}  Title: {await page.title()}")

    elements = await page.evaluate("""() =>
        Array.from(document.querySelectorAll(
            'input, textarea, select, button, [contenteditable="true"], a[href]'))
        .filter(el => el.offsetParent !== null)
        .slice(0, 80)
        .map(el => ({
            tag:         el.tagName,
            type:        el.type || '',
            name:        el.name || '',
            id:          el.id   || '',
            placeholder: el.placeholder || '',
            ariaLabel:   el.getAttribute('aria-label') || '',
            text:        (el.innerText||el.value||'').trim().slice(0, 70),
            class:       el.className.slice(0, 100),
            dataTestId:  el.getAttribute('data-testid') || '',
            href:        el.href || '',
        }))
    """)
    print(f"  --- {len(elements)} visible interactive elements ---")
    for el in elements:
        parts = []
        if el['type']:       parts.append(f"type={el['type']}")
        if el['id']:         parts.append(f"id={el['id']}")
        if el['name']:       parts.append(f"name={el['name']}")
        if el['placeholder']:parts.append(f"ph='{el['placeholder'][:40]}'")
        if el['ariaLabel']:  parts.append(f"aria='{el['ariaLabel'][:40]}'")
        if el['dataTestId']: parts.append(f"testid={el['dataTestId']}")
        if el['text']:       parts.append(f"text='{el['text'][:60]}'")
        if el['href'] and el['tag']=='A': parts.append(f"href={el['href'][:60]}")
        print(f"    {el['tag']:<10} {' | '.join(parts)}")


async def main():
    adya_url      = os.getenv("ADYA_URL", "")
    adya_email    = os.getenv("ADYA_EMAIL", "")
    adya_password = os.getenv("ADYA_PASSWORD", "")

    print(f"URL: {adya_url}  |  Email: {adya_email}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        ctx     = await browser.new_context(viewport={"width": 1600, "height": 900})
        page    = await ctx.new_page()

        # 1 ── Navigate
        print("\n=== STEP 1: Navigate ===")
        await page.goto(adya_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)
        await dump_page(page, "01_landing")

        # 2 ── Click Sign In (top-right button / link)
        print("\n=== STEP 2: Click Sign In ===")
        signin_clicked = False
        for sel in [
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
            'button:has-text("Sign In")',
            'a:has-text("Sign In")',
            'button:has-text("Login")',
            'a:has-text("Login")',
        ]:
            try:
                el = page.locator(sel).last  # top-right is usually last
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    print(f"  Clicked: {sel}")
                    signin_clicked = True
                    break
            except Exception:
                continue
        if not signin_clicked:
            print("  WARNING: Could not find Sign In button")

        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(2)
        await dump_page(page, "02_after_signin_click")

        # 3 ── Fill login form
        print("\n=== STEP 3: Fill login form ===")
        email_sel = None
        for sel in ['input[type="email"]', 'input[name="email"]',
                    'input[placeholder*="email" i]', 'input[autocomplete="email"]',
                    'input[type="text"]']:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=5000)
                email_sel = sel
                print(f"  Email field: {sel}")
                break
            except Exception:
                continue

        if email_sel:
            await page.fill(email_sel, adya_email)
            await asyncio.sleep(0.5)
            await dump_page(page, "03_email_filled")

            # Password
            for sel in ['input[type="password"]', 'input[name="password"]',
                        'input[placeholder*="password" i]']:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.fill(sel, adya_password)
                        print(f"  Password field: {sel}")
                        break
                except Exception:
                    continue

            await asyncio.sleep(0.3)
            await dump_page(page, "04_credentials_filled")

            # Submit
            for sel in ['button[type="submit"]', 'button:has-text("Sign in")',
                        'button:has-text("Continue")', 'button:has-text("Log in")',
                        'input[type="submit"]']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click()
                        print(f"  Submit: {sel}")
                        break
                except Exception:
                    continue

            print("  Waiting for post-login redirect (up to 30s)...")
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(3)
            await dump_page(page, "05_post_login")
        else:
            print("  No email field found — maybe already logged in or different auth flow")
            await dump_page(page, "03_no_email_field")

        # 4 ── Post-login: dump full page state
        print("\n=== STEP 4: Final state ===")
        await asyncio.sleep(3)
        await dump_page(page, "06_final")

        # 5 ── Try to find the Psi/Orchestrator chat input
        print("\n=== STEP 5: Looking for chat/prompt input ===")
        chat_found = False
        for sel in [
            'textarea[aria-label="Write your prompt here"]',
            'textarea[placeholder*="Ask Orchestrator" i]',
            'textarea[placeholder*="Ask" i]',
            'textarea[placeholder*="Message" i]',
            '[contenteditable="true"]',
            'textarea',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    print(f"  FOUND chat input: {sel}")
                    chat_found = True
                    # Try typing a test message
                    await el.click()
                    await el.fill("Hello, this is a test message")
                    await asyncio.sleep(1)
                    await dump_page(page, "07_chat_input_filled")
                    # Clear it
                    await el.fill("")
                    break
            except Exception:
                continue
        if not chat_found:
            print("  WARNING: No chat input found on post-login page")
            await dump_page(page, "07_no_chat_input")

        print("\n\nDone! Check screenshots/inspect_*.png")
        print("Keeping browser open 30s for manual inspection...")
        await asyncio.sleep(30)
        await browser.close()


asyncio.run(main())
