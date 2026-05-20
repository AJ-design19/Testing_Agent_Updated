COMPLETION_PATTERNS = [
    "prototype completed",
    "project completed",
    "deployment ready",
    "completed successfully",
    "final implementation",
    "application generated"
]


def is_completed(response):
    response = response.lower()

    for pattern in COMPLETION_PATTERNS:
        if pattern in response:
            return True

    return False