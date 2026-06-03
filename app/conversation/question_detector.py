class QuestionDetector:

    def is_question(self, text):

        keywords = [
            "what",
            "which",
            "how",
            "please provide",
            "do you want"
        ]

        lower = text.lower()

        return any(k in lower for k in keywords)