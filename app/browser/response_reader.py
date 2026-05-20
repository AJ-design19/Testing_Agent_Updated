import asyncio


async def get_latest_response(page):
    print("Waiting for AI response")

    await asyncio.sleep(10)

    responses = await page.locator('.assistant-message').all_inner_texts()

    if responses:
        latest_response = responses[-1]
        return latest_response

    return "No response found"