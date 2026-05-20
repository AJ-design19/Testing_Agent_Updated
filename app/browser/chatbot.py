import asyncio


async def open_chatbot(page):
    print("Opening chatbot")

    try:
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(5)

        possible_buttons = [
            "text=SAI",
            "text=Chat",
            "text=AI Agent",
            "text=Assistant",
            "text=New Chat"
        ]

        opened = False

        for button in possible_buttons:
            try:
                await page.click(button, timeout=3000)
                print(f"Clicked: {button}")
                opened = True
                break
            except:
                continue

        if not opened:
            print("No chatbot button found")

        await asyncio.sleep(5)
        print("Chatbot opened")

    except Exception as error:
        print("CHATBOT ERROR")
        print(error)

        await page.screenshot(path="screenshots/chatbot_error.png")
        raise error