"""
Persona-aware answer engine.

Generates answers to Psi's questions in the voice of the selected persona.
Uses the LLM to produce natural, contextually appropriate responses that
match the persona's technical depth, communication style, and goals.
"""

import logging
import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _build_persona_system_prompt(persona: dict, workflow: dict) -> str:
    """Build a system prompt that fully describes the persona to the LLM."""
    # Support both legacy (name) and BRD YAML (display_name) field names
    name = persona.get("display_name") or persona.get("name", "User")
    vocab_signals = persona.get("vocabulary_signals", [])
    vocab_hint = ""
    if vocab_signals:
        vocab_hint = f"\n  Vocabulary signals (use naturally): {', '.join(vocab_signals[:5])}"

    return f"""You are roleplaying as a real user testing the SAI platform (by Adya AI).

Your persona:
  Name: {name}
  Role: {persona.get('role', 'Business User')}
  Segment: {persona.get('segment', 'N/A')}
  Industry: {persona.get('industry', 'N/A')}
  Technical depth: {persona.get('technical_depth', 'medium')}
  Communication style: {persona.get('communication_style', 'Professional and direct')}
  Goals: {', '.join(persona.get('goals', []))}
  Pain points: {', '.join(persona.get('pain_points', []))}{vocab_hint}

Your current workflow: {workflow.get('title', 'N/A')}
Your original request: {workflow.get('initial_prompt', '')}

Reply rules:
- Stay in character at ALL times — answer as this persona would
- Match the technical depth: {persona.get('technical_depth', 'medium')}
  - "very_high" / "very high": use technical jargon freely, reference specific tools/frameworks
  - "high": comfortable with technical terms but focuses on architecture
  - "medium": understands concepts, prefers plain explanations over jargon
  - "medium_low" / "medium-low": business-focused, avoids deep technical details
  - "low": non-technical, describe things in business/process terms only
  - "very_low" / "very low": purely business language, no technical terms whatsoever
- Be concise: 1-3 sentences per answer
- Provide specific answers (not "whatever you think is best")
- If asked about preferences, give a real preference that fits the persona
- If you don't know or don't care about a technical detail, say so naturally
- Reference the persona's industry, goals, and pain points when relevant
- Use vocabulary signals naturally if they match the question context
"""


class AnswerEngine:
    """
    Generates persona-aware answers to Psi's questions.
    Falls back to rule-based answers if the LLM call fails.
    """

    def __init__(self, persona: dict, workflow: dict):
        self.persona = persona
        self.workflow = workflow
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model = os.getenv("ANSWER_MODEL", "grok-3-beta")
        self._system_prompt = _build_persona_system_prompt(persona, workflow)
        self._conversation_history: list[dict] = []

    def generate(self, psi_question: str) -> str:
        """
        Generate a persona-appropriate answer to a Psi question.
        Maintains conversation history for context continuity.
        """
        self._conversation_history.append({"role": "user", "content": psi_question})

        try:
            messages = [
                {"role": "system", "content": self._system_prompt},
                *self._conversation_history[-8:],  # keep last 4 exchange pairs
            ]
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.5,
                max_tokens=200,
            )
            answer = response.choices[0].message.content.strip()
            self._conversation_history.append({"role": "assistant", "content": answer})
            logger.info("[AnswerEngine] Generated answer for persona %s: %s",
                        self.persona.get("id"), answer[:80])
            return answer

        except Exception as e:
            logger.warning("[AnswerEngine] LLM call failed, using rule-based fallback: %s", e)
            return self._rule_based_fallback(psi_question)

    def _rule_based_fallback(self, question: str) -> str:
        """
        Rule-based fallback answers keyed to common Psi question patterns.
        Uses persona context to pick appropriate answers.
        """
        q = question.lower()
        raw_td = str(self.persona.get("technical_depth", "medium")).lower()
        tech_depth = raw_td.replace("-", "_").replace(" ", "_")
        industry = self.persona.get("industry", "")

        _high_tech = tech_depth in ("very_high", "very high", "high")

        if any(w in q for w in ["frontend", "ui", "interface", "react", "vue"]):
            if _high_tech:
                return "Use React with TypeScript and Tailwind CSS."
            return "A clean, modern web interface that works on desktop and mobile."

        if any(w in q for w in ["backend", "api", "server", "framework"]):
            if _high_tech:
                return "FastAPI with async SQLAlchemy and PostgreSQL."
            return "Whatever is standard and easy to maintain."

        if any(w in q for w in ["database", "storage", "data store"]):
            if _high_tech:
                return "PostgreSQL with Redis for caching."
            return "A standard relational database is fine."

        if any(w in q for w in ["auth", "login", "authentication", "sso"]):
            if "enterprise" in industry.lower() or _high_tech:
                return "SSO with Okta, and role-based access control."
            return "Email and password login with JWT tokens."

        if any(w in q for w in ["deploy", "hosting", "cloud", "infrastructure"]):
            if _high_tech:
                return "Docker containers on AWS ECS with a CI/CD pipeline."
            return "Cloud hosted, automated deployment — I don't want to manage servers."

        if any(w in q for w in ["scale", "users", "volume", "load"]):
            return f"Starting small — {self.persona.get('org_size', 'a few hundred users')}."

        if any(w in q for w in ["compliance", "gdpr", "hipaa", "sox", "regulation"]):
            if "financial" in industry.lower() or "healthcare" in industry.lower():
                return f"Yes, we need full compliance — this is a regulated industry ({industry})."
            return "Standard data protection practices are fine for now."

        if any(w in q for w in ["timeline", "deadline", "when", "how soon"]):
            if self.persona.get("id") in ("FN-1", "ST-1"):
                return "As fast as possible — I need this in a few days."
            return "In the next 2-3 weeks ideally."

        if any(w in q for w in ["existing", "current", "already have", "integrate"]):
            pain_points = self.persona.get("pain_points", [])
            if pain_points:
                return f"We currently have: {pain_points[0]}. Start fresh where possible."
            return "We have some existing tools but are open to a fresh approach."

        # App name questions — derive from workflow title
        if any(w in q for w in ["name your app", "app name", "name the app",
                                  "what would you like to name", "name of your app",
                                  "what to name"]):
            title = self.workflow.get("title", "")
            if title:
                # Convert "Real-Time SaaS Analytics Dashboard" → "SaaSMetrics"
                words = [w for w in title.split() if w not in
                         ("A", "An", "The", "and", "with", "for", "of", "the")]
                return "".join(w.capitalize() for w in words[:3]) + "App"
            return f"{self.persona.get('role', 'My').split('/')[0].strip()}App"

        # App description / "what does your app do" questions
        if any(w in q for w in ["describe", "what does", "what your app does",
                                  "briefly describe", "what it does", "purpose"]):
            prompt_text = self.workflow.get("initial_prompt", "")
            if prompt_text:
                # Return the first sentence of the initial prompt (up to 120 chars)
                first = prompt_text.split(".")[0].strip()
                return first[:120] if first else prompt_text[:120]
            goals = self.persona.get("goals", [])
            if goals:
                return goals[0]
            return f"A {self.persona.get('role', 'business')}-focused productivity tool."

        # Tech stack / "which application type" — pick the most fitting option based on workflow
        if any(w in q for w in ["application type", "tech stack", "which stack",
                                  "technology stack", "framework"]):
            if _high_tech:
                return "Next.js"
            return "React"

        # "Proceed / modify" — always continue
        if any(w in q for w in ["proceed", "continue", "modification", "execute",
                                  "begin execution"]):
            return "Continue and begin execution."

        # Generic fallback — use actual persona content, not meta-text
        goals = self.persona.get("goals", [])
        role  = self.persona.get("role", "business user").split("/")[0].strip()
        ind   = self.persona.get("industry", "")
        if goals:
            return f"{goals[0]}. Please keep it practical and production-ready."
        return f"Please build a solution suitable for a {role}{' in ' + ind if ind else ''}. Keep it simple and production-ready."
