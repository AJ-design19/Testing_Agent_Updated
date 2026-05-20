import os
import asyncio
from dotenv import load_dotenv

load_dotenv()


async def login(page):
    ADYA_URL = os.getenv("ADYA_URL")
    ADYA_EMAIL = os.getenv("ADYA_EMAIL")
    ADYA_PASSWORD = os.getenv("ADYA_PASSWORD")

    print("URL:", ADYA_URL)
    print("Opening Adya AI")

    await page.goto(ADYA_URL)
    await asyncio.sleep(8)

    print("Current URL:", page.url)

    # CHECK IF LOGIN PAGE EXISTS
    try:
        email_exists = await page.locator('input[type="email"]').count()

        if email_exists > 0:
            print("Login page detected")

            await page.fill('input[type="email"]', ADYA_EMAIL)
            await page.fill('input[type="password"]', ADYA_PASSWORD)
            await page.click('button[type="submit"]')

            await asyncio.sleep(10)
            print("Login successful")

        else:
            print("Already logged in")

    except Exception as error:
        print("LOGIN CHECK ERROR")
        print(error)

        await page.screenshot(path="screenshots/login_error.png")
        raise error