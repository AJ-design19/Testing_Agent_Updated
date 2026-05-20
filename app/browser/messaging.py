import asyncio


async def send_message(page, message):
    print("Searching for chat input")

    selectors = [
        "textarea",
        '[contenteditable="true"]',
        'input[type="text"]',
        '[placeholder*="Message"]',
        '[placeholder*="message"]',
        '[placeholder*="Ask"]',
        '[placeholder*="Type"]'
    ]

    found = False

    for selector in selectors:
        try:
            print(f"Trying selector: {selector}")

            await page.wait_for_selector(selector, timeout=5000)
            await page.click(selector)
            await asyncio.sleep(1)

            # TYPE MESSAGE
            await page.fill(selector, message)
            await asyncio.sleep(2)

            # SEND
            await page.keyboard.press("Enter")

            print(f"SUCCESS USING: {selector}")
            found = True
            break

        except Exception:
            continue

    if not found:
        await page.screenshot(path="screenshots/no_input_found.png")
        raise Exception("Could not find chat input")