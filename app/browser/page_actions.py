class PageActions:

    def __init__(self, page):
        self.page = page

    async def type_message(self, text):

        await self.page.locator("textarea").fill(text)

    async def submit(self):

        await self.page.keyboard.press("Enter")