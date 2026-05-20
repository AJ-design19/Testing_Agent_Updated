class ContextManager:


 def __init__(self):

    self.context = {
        "frontend": "React + Tailwind CSS",
        "backend": "FastAPI",
        "database": "PostgreSQL",
        "authentication": "JWT Authentication",
        "deployment": "Docker + AWS",
        "billing": "Stripe",
        "design": "Modern SaaS UI",
        "responsive": True
    }

 def get_context(self):

    return self.context

 def update_context(
    self,
    key,
    value
):

    self.context[key] = value
