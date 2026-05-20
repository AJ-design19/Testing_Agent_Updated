import asyncio
from app.browser.browser_manager import BrowserManager
from app.browser.login import login
from app.browser.chatbot import open_chatbot
from app.browser.messaging import send_message


async def run_test():
    browser = BrowserManager()
    page = await browser.start()

    try:
        print("\n===================")
        print("STARTING SAI SYSTEM")
        print("===================\n")

        # LOGIN
        await login(page)

        print("\nWAITING AFTER LOGIN\n")

        # IMPORTANT WAIT
        await asyncio.sleep(10)

        print("Current URL After Login:", page.url)

        # OPEN CHATBOT
        await open_chatbot(page)
        await asyncio.sleep(5)

        # SEND PROMPT
        prompt = """
        Build a production-level SaaS AI dashboard.

        Features:
        - Authentication
        - Billing
        - Analytics
        - Admin Panel
        - Responsive UI
        - Modern UX
        - Deployment Ready
        - FastAPI Backend
        - React Frontend
        """

        await send_message(page, prompt)

        print("\nPROMPT SENT TO SAI\n")

        await asyncio.sleep(20)

        await page.screenshot(path="screenshots/final_chat.png")

        print("\nAUTOMATION COMPLETED")
        input("\nPress Enter To Close Browser")

    except Exception as error:
        print("\nSYSTEM ERROR\n")
        print(error)

        await page.screenshot(path="screenshots/system_error.png")

    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_test())