class ChatMonitor:

    def __init__(self, page):
        self.page = page

    async def wait_for_response(self):

        await self.page.wait_for_timeout(5000)

        return await self.page.locator(
            "div"
        ).all_inner_texts()