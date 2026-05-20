"""
LLM-as-Judge evaluation module.

After the browser testing agent completes a workflow run, this module:
  1. Assembles full evidence: Psi conversation, canvas sub-tab content,
     action step log, screenshot paths, canvas timeline, recovery log
  2. Sends evidence to an LLM with a structured evaluation prompt
  3. Returns a structured verdict with per-agent scores
  4. Integrates with VisualJudge for multimodal evaluation

Scoring dimensions:
  - correctness_score   (0-10): Did SAI do the right thing?
  - process_score       (0-10): Did Psi ask good questions?
  - output_score        (0-10): Are canvas outputs correct and complete?
  - per_agent_scores    {agent: 0-10}: Individual agent quality
  - visual_score        (0-10): Visual quality from VisualJudge
  - reliability_score   (0-10): Did it work without errors/retries?
  - ux_score            (0-10): UI/UX smoothness
"""

import json
import logging
import os
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

from app.browser.canvas_reader import CanvasReader
from app.judge.visual_judge import VisualJudge

load_dotenv()
logger = logging.getLogger(__name__)


JUDGE_SYSTEM_PROMPT = """You are an expert QA evaluator and product analyst for an AI development platform called SAI (part of Adya AI).

You are evaluating a full browser test run where an autonomous agent impersonated a real enterprise user persona and executed a specific workflow through SAI's browser interface.

You have access to:
- The persona profile (who the user is, their goals, technical depth)
- The workflow definition (what they wanted to build)
- The complete Psi conversation log (how SAI's copilot handled the conversation)
- Canvas agent evidence (what each agent produced, including sub-tab content)
- The full step-by-step action log
- Recovery events (any failures and how they were handled)

You must evaluate SEVEN dimensions:

1. PROCESS QUALITY (process_score 0-10)
   - Did Psi ask the right clarifying questions?
   - Did Psi understand the persona's context and needs?
   - Were questions specific and relevant (not generic)?
   - Did Psi troubleshoot ambiguities effectively?

2. OUTPUT QUALITY (output_score 0-10)
   - Did the correct agents start (matching expected_agents)?
   - Did each agent produce meaningful, complete output?
   - Does the solution match the persona's request?
   - Are all required components present?

3. CORRECTNESS (correctness_score 0-10)
   - Is the generated solution architecturally correct?
   - Would it actually work for the persona's use case?
   - Is it appropriate for the persona's industry and technical depth?

4. PER-AGENT SCORES (per_agent_scores {agent_name: 0-10})
   - Score each agent that ran based on its output quality
   - Agents that didn't appear get 0

5. RELIABILITY (reliability_score 0-10)
   - Did the workflow complete without errors or retries?
   - Were recovery events needed? (fewer = better)
   - Did the UI behave predictably?

6. UX QUALITY (ux_score 0-10)
   - Were UI transitions smooth and logical?
   - Did Psi respond at appropriate speed?
   - Were there any confusing UI states?

7. REASONING QUALITY (reasoning_score 0-10)
   - Did SAI's reasoning (Thinking sub-tabs) appear coherent?
   - Was the workflow plan logical and well-structured?
   - Did the agent decisions make sense?

Return ONLY valid JSON matching this schema exactly:
{
  "correctness_verdict": "pass" | "partial" | "fail",
  "correctness_score": <integer 0-10>,
  "process_score": <integer 0-10>,
  "output_score": <integer 0-10>,
  "reliability_score": <integer 0-10>,
  "ux_score": <integer 0-10>,
  "reasoning_score": <integer 0-10>,
  "per_agent_scores": {"AIA": <0-10>, "AGP": <0-10>, "ETL": <0-10>, "App Studio": <0-10>},
  "process_evaluation": "<2-3 sentence evaluation of Psi conversation quality>",
  "output_evaluation": "<2-3 sentence evaluation of canvas agent output quality>",
  "reasoning_evaluation": "<1-2 sentence evaluation of agent reasoning quality>",
  "improvement_flags": ["<specific actionable improvement 1>", ...],
  "missing_elements": ["<thing SAI should have built but didn't>", ...],
  "hallucination_flags": ["<suspicious/incorrect content>", ...],
  "rationale": "<overall narrative in 3-5 sentences>",
  "psi_questions_rating": "excellent" | "good" | "adequate" | "poor",
  "agents_that_ran": ["<agent name>", ...],
  "agents_that_should_have_run": ["<agent name>", ...],
  "recovery_needed": true | false,
  "recovery_assessment": "<assessment of how failures were handled, or 'no failures'>",
  "recommended_workflow_improvements": ["<improvement 1>", ...]
}

Be specific and actionable. Never give vague feedback. Cite exact evidence from the logs.
"""


def _build_judge_prompt(
    persona: dict,
    workflow: dict,
    psi_conversation: list[dict],
    canvas_evidence: dict,
    step_log: list[dict],
    recovery_log: Optional[list[dict]] = None,
    canvas_timeline: Optional[list[dict]] = None,
) -> str:
    """Assemble all evidence into one evaluation prompt."""

    persona_block = (
        f"Persona: {persona.get('name')} ({persona.get('role')})\n"
        f"Segment: {persona.get('segment', 'N/A')} | Industry: {persona.get('industry', 'N/A')}\n"
        f"Technical depth: {persona.get('technical_depth', 'N/A')}\n"
        f"Goals: {', '.join(persona.get('goals', []))}\n"
        f"Pain points: {', '.join(persona.get('pain_points', []))}"
    )

    workflow_block = (
        f"Workflow: {workflow.get('title')} ({workflow.get('id')})\n"
        f"Initial prompt: {workflow.get('initial_prompt', '')}\n"
        f"Expected agents: {', '.join(workflow.get('expected_agents', []))}\n"
        f"Process criteria: {workflow.get('evaluation_criteria', {}).get('process', 'N/A')}\n"
        f"Output criteria: {workflow.get('evaluation_criteria', {}).get('output', 'N/A')}"
    )

    psi_log_text = "\n".join(
        f"  [{e.get('role','?').upper()}]: {e.get('text','')[:500]}"
        for e in psi_conversation
    ) or "  (no conversation recorded)"

    # Canvas evidence — all agents with all sub-tabs
    canvas_lines = []
    fv = canvas_evidence.get("flow_view", {})
    canvas_lines.append(f"Flow View appeared: {fv.get('appeared', False)}")
    if fv.get("planned_steps"):
        canvas_lines.append(f"Planned steps: {fv.get('planned_steps','')[:400]}")

    for agent_name, agent_data in canvas_evidence.get("agents", {}).items():
        canvas_lines.append(f"\n--- Agent: {agent_name} ---")
        canvas_lines.append(f"  Appeared: {agent_data.get('appeared', False)}")
        canvas_lines.append(f"  State: {agent_data.get('state', 'unknown')}")
        for sub_tab, content in agent_data.get("sub_tabs", {}).items():
            preview = (content or "(empty)")[:500]
            canvas_lines.append(f"  [{sub_tab}]: {preview}")
        ss_count = len(agent_data.get("screenshots", []))
        canvas_lines.append(f"  Screenshots captured: {ss_count}")

    canvas_text = "\n".join(canvas_lines)

    # Step log (last 25 steps)
    recent = step_log[-25:] if len(step_log) > 25 else step_log
    step_text = "\n".join(
        f"  Step {s.get('step_number')}: [{s.get('action_type')}] "
        f"{'✓' if s.get('success',True) else '✗'} {s.get('description','')}"
        for s in recent
    )

    # Recovery log
    recovery_text = ""
    if recovery_log:
        recovery_text = "\n=== RECOVERY EVENTS ===\n"
        recovery_text += "\n".join(
            f"  [{r.get('timestamp','')}] issue={r.get('issue')} "
            f"strategy={r.get('strategy')} success={r.get('success')}"
            for r in recovery_log[-10:]
        )

    # Canvas timeline summary
    timeline_text = ""
    if canvas_timeline:
        timeline_text = "\n=== CANVAS TIMELINE ===\n"
        timeline_text += "\n".join(
            f"  [{e.get('timestamp','')}] {e.get('event_type')} agent={e.get('agent')}"
            for e in canvas_timeline[-20:]
        )

    return f"""
=== EVALUATION REQUEST ===

{persona_block}

{workflow_block}

=== PSI CONVERSATION LOG ({len(psi_conversation)} turns) ===
{psi_log_text}

=== CANVAS EVIDENCE ===
{canvas_text}
{recovery_text}
{timeline_text}

=== STEP LOG (last {len(recent)} of {len(step_log)} steps) ===
{step_text}

=== YOUR TASK ===
Evaluate the above SAI test run. Return ONLY valid JSON as specified in your system prompt.
"""


class LLMJudge:
    """
    Comprehensive LLM evaluator combining text analysis and visual inspection.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model        = os.getenv("JUDGE_MODEL", "grok-3-beta")
        self.visual_judge = VisualJudge()

    def evaluate(
        self,
        persona: dict,
        workflow: dict,
        psi_conversation: list[dict],
        canvas_evidence: dict,
        step_log: list[dict],
        recovery_log: Optional[list[dict]] = None,
        canvas_timeline: Optional[list[dict]] = None,
    ) -> dict:
        """
        Run full evaluation (text + visual). Returns merged verdict dict.
        """
        prompt = _build_judge_prompt(
            persona, workflow, psi_conversation, canvas_evidence,
            step_log, recovery_log, canvas_timeline,
        )

        logger.info("[LLMJudge] Sending evidence to judge model: %s", self.model)

        # ── Text evaluation ───────────────────────────────────────────────────
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            raw     = response.choices[0].message.content
            verdict = json.loads(raw)
            logger.info(
                "[LLMJudge] Text verdict: %s | Process: %s/10 | Output: %s/10",
                verdict.get("correctness_verdict"),
                verdict.get("process_score"),
                verdict.get("output_score"),
            )
        except json.JSONDecodeError as e:
            logger.error("[LLMJudge] JSON parse error: %s", e)
            verdict = self._fallback_verdict(f"JSON parse error: {e}")
        except Exception as e:
            logger.error("[LLMJudge] LLM call failed: %s", e)
            verdict = self._fallback_verdict(str(e))

        # ── Visual evaluation ─────────────────────────────────────────────────
        try:
            step_ss = [
                s.get("screenshot_path", "")
                for s in step_log
                if s.get("screenshot_path")
            ]
            visual_result = self.visual_judge.evaluate_full_run(canvas_evidence, step_ss)
            verdict["visual_evaluation"] = visual_result
            # Merge visual score into verdict
            overall_visual = visual_result.get("overall", {})
            verdict["visual_score"] = overall_visual.get("visual_score", 5)
        except Exception as e:
            logger.warning("[LLMJudge] Visual evaluation failed (non-fatal): %s", e)
            verdict["visual_score"] = 0
            verdict["visual_evaluation"] = {"error": str(e)}

        return verdict

    @staticmethod
    def _fallback_verdict(error: str) -> dict:
        return {
            "correctness_verdict":             "error",
            "correctness_score":               0,
            "process_score":                   0,
            "output_score":                    0,
            "reliability_score":               0,
            "ux_score":                        0,
            "reasoning_score":                 0,
            "per_agent_scores":                {},
            "process_evaluation":              "Evaluation failed.",
            "output_evaluation":               "Evaluation failed.",
            "reasoning_evaluation":            "Evaluation failed.",
            "improvement_flags":               [f"Judge error: {error}"],
            "missing_elements":                [],
            "hallucination_flags":             [],
            "rationale":                       f"LLM judge could not complete evaluation: {error}",
            "psi_questions_rating":            "unknown",
            "agents_that_ran":                 [],
            "agents_that_should_have_run":     [],
            "recovery_needed":                 False,
            "recovery_assessment":             "N/A",
            "recommended_workflow_improvements": [],
        }

    @staticmethod
    def verdict_to_overall_status(verdict: dict) -> str:
        v = verdict.get("correctness_verdict", "error")
        if v == "pass":    return "pass"
        if v == "partial": return "partial"
        return "fail"
