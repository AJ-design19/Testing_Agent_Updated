import os
from playwright.async_api import async_playwright


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
            slow_mo=200,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir="screenshots/videos" if not use_headless else None,
        )

        self.page = await self.context.new_page()
        return self.page

    async def close(self):
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass
