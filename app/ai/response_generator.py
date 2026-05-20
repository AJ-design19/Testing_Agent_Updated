def generate_answer(question, context):
    question = question.lower()

    if "frontend" in question:
        return "Use React with Tailwind CSS and reusable responsive components."

    if "backend" in question:
        return "Use FastAPI with scalable production architecture."

    if "database" in question:
        return "Use PostgreSQL with optimized schema."

    if "authentication" in question:
        return "Use JWT authentication with role-based access control."

    if "billing" in question:
        return "Integrate Stripe subscription billing."

    if "deployment" in question:
        return "Use Docker deployment on AWS."

    if "design" in question:
        return "Use modern SaaS UI with dark mode and responsive design."

    return "Use production-level scalable architecture with modern UI/UX and responsive design."