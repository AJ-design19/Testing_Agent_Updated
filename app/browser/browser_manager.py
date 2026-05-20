from playwright.async_api import async_playwright

class BrowserManager:


 def __init__(self):

    self.playwright = None
    self.browser = None
    self.context = None
    self.page = None

 async def start(self):

    self.playwright = await async_playwright().start()

    self.browser = await self.playwright.chromium.launch(
        headless=False,
        slow_mo=300
    )

    self.context = await self.browser.new_context()

    self.page = await self.context.new_page()

    return self.page

 async def close(self):

    await self.browser.close()

    await self.playwright.stop()

