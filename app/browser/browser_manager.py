import logging
import os
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Domains the agent must never navigate to
_BLOCKED_DOMAINS = (
    "facebook.com",
    "fb.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "accounts.google.com",
    "login.microsoftonline.com",
    "appleid.apple.com",
    "github.com/login",
    "auth0.com",
)


class BrowserManager:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def start(self, headless: bool = False):
        headless_env = os.getenv("HEADLESS", "false").lower() == "true"
        use_headless = headless or headless_env

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=use_headless,
            slow_mo=50,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,720",
            ],
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
            color_scheme="light",
            record_video_dir="screenshots/videos" if not use_headless else None,
            permissions=["notifications", "clipboard-read", "clipboard-write"],
            java_script_enabled=True,
            bypass_csp=True,
        )

        # Remove all automation fingerprints that sites use to detect Playwright
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        # Clear cookies so no previous session can cause a saved-workspace redirect.
        await self.context.clear_cookies()

        # Create the main page FIRST, then attach the new-tab blocker.
        # This way the handler never sees the initial page creation event
        # (which fires as about:blank) and doesn't accidentally close it.
        self.page = await self.context.new_page()

        # Clear localStorage/sessionStorage so the site can't restore a saved
        # workspace from the previous session.
        try:
            await self.page.goto("about:blank")
            await self.page.evaluate("""() => {
                try { localStorage.clear(); } catch(e) {}
                try { sessionStorage.clear(); } catch(e) {}
            }""")
        except Exception:
            pass

        # Now safe to block any subsequent new tabs (target="_blank" pop-ups)
        self.context.on("page", self._on_new_page)

        # Block social-media requests on the main page only.
        # Only intercept sub-resource requests (xhr, fetch, image, etc.) —
        # never intercept document-level navigations, which causes ERR_ABORTED.
        await self.page.route("**/*", self._block_external_route)

        self.page.set_default_timeout(30000)
        self.page.set_default_navigation_timeout(60000)

        return self.page

    # ── Route handler ─────────────────────────────────────────────────────────

    async def _block_external_route(self, route, request):
        try:
            # Never intercept top-level document navigations — that causes
            # ERR_ABORTED on page.goto(). Only block sub-resources.
            if request.resource_type == "document":
                await route.continue_()
                return

            url = request.url
            if any(d in url for d in _BLOCKED_DOMAINS):
                logger.warning("[BrowserManager] Blocked: %s", url)
                await route.abort()
                return

            await route.continue_()
        except Exception:
            # Never let a route handler error propagate — it would abort the request
            try:
                await route.continue_()
            except Exception:
                pass

    # ── New-tab handler ───────────────────────────────────────────────────────

    async def _on_new_page(self, new_page):
        try:
            url = new_page.url or ""
            logger.warning("[BrowserManager] New tab blocked (%s) — closing", url)
            await new_page.close()
        except Exception:
            pass

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def close(self):
        for obj, method in [
            (self.context,    "close"),
            (self.browser,    "close"),
            (self.playwright, "stop"),
        ]:
            try:
                if obj:
                    fn = getattr(obj, method, None)
                    if fn:
                        await fn()
            except Exception:
                pass
