QUESTION_PATTERNS = [
    "?",
    "which",
    "what",
    "do you want",
    "please specify",
    "choose",
    "select",
    "would you like",
    "can you clarify"
]


def is_question(text):
    text = text.lower()

    for pattern in QUESTION_PATTERNS:
        if pattern in text:
            return True

    return False