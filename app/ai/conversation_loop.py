import asyncio
from app.ai.question_detector import is_question
from app.ai.response_generator import generate_answer
from app.ai.context_manager import ContextManager
from app.ai.completion_detector import is_completed
from app.browser.messaging import send_message
from app.browser.response_reader import get_latest_response


async def autonomous_conversation(page):
    context_manager = ContextManager()
    context = context_manager.get_context()

    while True:
        response = await get_latest_response(page)

        print("\n===================")
        print("SAI RESPONSE")
        print("===================\n")
        print(response)

        # CHECK IF COMPLETED
        if is_completed(response):
            print("\nPrototype Completed")
            break

        # CHECK IF SAI ASKED QUESTION
        if is_question(response):
            answer = generate_answer(response, context)

            print("\n===================")
            print("AUTO ANSWER")
            print("===================\n")
            print(answer)

            await send_message(page, answer)
            await asyncio.sleep(5)

        else:
            await asyncio.sleep(5)