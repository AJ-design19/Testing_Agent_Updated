from openai import OpenAI
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()


class AnswerEngine:

    def __init__(self, persona):

        self.persona = persona

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL")
        )

    def generate(self, question):

        response = self.client.chat.completions.create(
            model="grok-3-beta",
            messages=[
                {
                    "role": "system",
                    "content": f"""
                    You are acting as this persona:

                    {self.persona}

                    You are testing Adya AI platform.

                    Reply naturally like a real enterprise user.
                    Keep answers concise and realistic.
                    """
                },
                {
                    "role": "user",
                    "content": question
                }
            ],
            temperature=0.5,
            max_tokens=300
        )

        return response.choices[0].message.content
