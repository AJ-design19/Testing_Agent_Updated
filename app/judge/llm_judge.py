"""
LLM-as-Judge evaluation module.

After the browser testing agent completes a workflow run, this module:
  1. Assembles full evidence: SAI conversation, canvas sub-tab content,
     action step log, screenshot paths, canvas timeline, recovery log
  2. Sends evidence to an LLM with a structured evaluation prompt
  3. Returns a structured verdict with per-agent scores
  4. Integrates with VisualJudge for multimodal evaluation

Scoring dimensions:
  - correctness_score   (0-10): Did SAI do the right thing?
  - process_score       (0-10): Did SAI ask good questions?
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

from app.judge.visual_judge import VisualJudge, VisualDiffReport

load_dotenv()
logger = logging.getLogger(__name__)


JUDGE_SYSTEM_PROMPT = """You are a senior QA engineer and AI evaluator for SAI (Adya AI's Super AI Agent Platform).

SAI has SEVEN core capabilities. For every run you must identify which capability was exercised
and evaluate it with the matching rubric below. A single run may exercise more than one capability.

━━━ SAI CAPABILITY RUBRICS ━━━

CAPABILITY 1 — FULL APPLICATION BUILDER
  Triggered when: the prompt asks to "build", "create an app", "develop a system"
  Evaluate:
  • Did App Studio appear and produce working code files?
  • Does the architecture (AIA) cover frontend + backend + database layers?
  • Are all features from the prompt represented in the code?
  • Is there a runnable entry point (main file, package.json, requirements.txt, etc.)?
  • Are there unit/integration tests or at minimum test stubs?
  Score (app_builder_score 0-10): 0=no code produced, 5=partial code missing layers, 10=complete runnable app

CAPABILITY 2 — AUTOMATED WORKFLOW BUILDER (no-code pipelines)
  Triggered when: the prompt asks for "workflow", "pipeline", "automation", "integration", "trigger"
  Evaluate:
  • Did AIA produce a step-by-step workflow graph with named nodes?
  • Are trigger conditions, actions, and outputs clearly defined?
  • Are integrations named (e.g. Slack, Stripe, HubSpot) and correctly wired?
  • Does the workflow handle error/retry paths?
  Score (workflow_builder_score 0-10): 0=no workflow, 5=partial steps, 10=complete end-to-end

CAPABILITY 3 — DATA PIPELINE DESIGNER (ELT/ETL)
  Triggered when: the prompt mentions "data", "ETL", "ELT", "pipeline", "transform", "schema", "connector"
  Evaluate:
  • Did ETL agent appear with source → transform → destination mapping?
  • Are data sources and destinations explicitly named with connection details?
  • Are transformation rules (filters, joins, aggregations) specified?
  • Is there a schema definition with column names and types?
  • Are incremental load / scheduling / error handling strategies defined?
  Score (data_pipeline_score 0-10): 0=no ETL, 5=source/dest but no transforms, 10=full pipeline spec

CAPABILITY 4 — DOCUMENT GENERATOR (PRDs, architecture docs, runbooks, guides)
  Triggered when: the prompt asks to "generate", "write", "create a document", "PRD", "spec", "runbook", "guide"
  Evaluate:
  • Did the Output sub-tab contain a structured document (not just bullet points)?
  • Does it have recognisable sections (Executive Summary, Requirements, Architecture, etc.)?
  • Are all features from the prompt covered with sufficient detail?
  • Are acceptance criteria testable and unambiguous?
  • Is priority labelling (Must Have / Should Have / Could Have) used correctly?
  Score (document_generator_score 0-10): 0=no doc, 5=outline only, 10=complete publication-ready document

CAPABILITY 5 — DIAGRAM CREATOR (flowcharts, sequence, ER, system diagrams)
  Triggered when: the prompt asks for "diagram", "chart", "flowchart", "sequence diagram", "ER diagram", "architecture diagram"
  Evaluate:
  • Did the canvas show a rendered diagram (not just text describing one)?
  • Are all entities/actors/components from the prompt represented as nodes?
  • Are relationships, data flows, or sequences correctly drawn?
  • Is the diagram type appropriate (sequence for interactions, ER for data models, etc.)?
  Score (diagram_creator_score 0-10): 0=no diagram, 5=partial nodes/missing edges, 10=complete accurate diagram

CAPABILITY 6 — GOVERNANCE RULE ENGINE
  Triggered when: the prompt mentions "governance", "security", "compliance", "rules", "policy", "GDPR", "SOX", "audit"
  Evaluate:
  • Did AGP agent appear and produce governance rules?
  • Are rules specific (not just "add security") with named standards, thresholds, or policies?
  • Do rules cover the regulatory frameworks mentioned in the prompt?
  • Are enforcement mechanisms (alerts, blocks, audit trails) defined?
  Score (governance_score 0-10): 0=no rules, 5=generic rules, 10=specific enforceable policies with standards

CAPABILITY 7 — IMAGE GENERATOR (illustrations, mockups, logos, concept art)
  Triggered when: the prompt asks for "image", "illustration", "mockup", "logo", "visual", "design", "UI design"
  Evaluate:
  • Did SAI produce image output or reference an image generation step?
  • Does the image match the described subject, style, and purpose?
  • Is the resolution/quality appropriate for the stated use case?
  Score (image_generator_score 0-10): 0=no image, 5=image produced but mismatched, 10=accurate high-quality image

━━━ UNIVERSAL DIMENSIONS (score every run on all of these) ━━━

1. PROCESS QUALITY (process_score 0-10)
   - Did SAI ask the right clarifying questions for this persona?
   - Were questions specific and relevant (not generic boilerplate)?
   - Did SAI understand the user's context, industry, and technical depth?

2. OUTPUT QUALITY (output_score 0-10)
   - Did the correct agents appear (matching expected_agents)?
   - Did each agent produce meaningful, non-empty output?
   - Does the solution match the persona's original request?

3. CORRECTNESS (correctness_score 0-10)
   - Is the generated solution architecturally correct?
   - Would it actually work for the persona's industry use case?

4. PER-AGENT SCORES (per_agent_scores) AND PER-SUBTAB SCORES (per_agent_subtab_scores)
   - Score each agent 0-10; 0 if never appeared
   - Score each sub-tab 0-10; 0 if empty or not clicked
   - Sub-tabs: Overview, Output, Questions, Thinking, Workflow, Architecture, Execution trace
   - For OUTPUT sub-tab: does it contain a real deliverable? Are acceptance criteria testable?
     Flag generic/placeholder content that is not specific to the user's request.

5. DOCUMENT QUALITY ANALYSIS (for Output sub-tabs containing documents)
   - Completeness, specificity, structure, acceptance criteria, priority labelling

6. RELIABILITY (reliability_score 0-10) — completed without errors/retries?

7. UX QUALITY (ux_score 0-10) — smooth transitions, no blank panels or stuck spinners?

8. REASONING QUALITY (reasoning_score 0-10) — Thinking sub-tab coherent and logical?

9. HALLUCINATION DETECTION — fabricated content, empty sections claiming content, mismatches

10. ACCESSIBILITY QUALITY (a11y_score 0-10) — ARIA violations, contrast, labels

11. LAYOUT QUALITY (layout_score 0-10) — overlaps, truncation, hidden content

12. FIX RECOMMENDATIONS — concrete developer-actionable fix for every issue

13. FLOW GRAPH VALIDATION (flow_graph_score 0-10)
    - Graph present? All expected agents as phases? All phases Completed?
    - Score 0 = graph missing, 10 = complete, all phases completed

Return ONLY valid JSON matching this schema exactly:
{
  "status": "PASS" | "FAIL" | "PARTIAL",
  "correctness_verdict": "pass" | "partial" | "fail",
  "confidence_score": <integer 0-100>,

  "correctness_score":   <integer 0-10>,
  "process_score":       <integer 0-10>,
  "output_score":        <integer 0-10>,
  "reliability_score":   <integer 0-10>,
  "ux_score":            <integer 0-10>,
  "reasoning_score":     <integer 0-10>,
  "a11y_score":          <integer 0-10>,
  "layout_score":        <integer 0-10>,
  "flow_graph_score":    <integer 0-10>,

  "capabilities_detected": ["app_builder"|"workflow_builder"|"data_pipeline"|"document_generator"|"diagram_creator"|"governance"|"image_generator"],

  "capability_scores": {
    "app_builder_score":        <integer 0-10 or null if not triggered>,
    "workflow_builder_score":   <integer 0-10 or null if not triggered>,
    "data_pipeline_score":      <integer 0-10 or null if not triggered>,
    "document_generator_score": <integer 0-10 or null if not triggered>,
    "diagram_creator_score":    <integer 0-10 or null if not triggered>,
    "governance_score":         <integer 0-10 or null if not triggered>,
    "image_generator_score":    <integer 0-10 or null if not triggered>
  },

  "capability_evaluations": {
    "app_builder":        "<2-3 sentences — was the app complete? what was missing?> or null",
    "workflow_builder":   "<2-3 sentences> or null",
    "data_pipeline":      "<2-3 sentences> or null",
    "document_generator": "<2-3 sentences> or null",
    "diagram_creator":    "<2-3 sentences> or null",
    "governance":         "<2-3 sentences> or null",
    "image_generator":    "<2-3 sentences> or null"
  },

  "per_agent_scores": {"AIA": <0-10>, "AGP": <0-10>, "ETL": <0-10>, "App Studio": <0-10>},
  "per_agent_subtab_scores": {
    "AIA":        {"Overview": <0-10>, "Output": <0-10>, "Questions": <0-10>, "Thinking": <0-10>, "Workflow": <0-10>, "Architecture": <0-10>, "Execution trace": <0-10>},
    "AGP":        {"Overview": <0-10>, "Output": <0-10>, "Questions": <0-10>, "Thinking": <0-10>, "Workflow": <0-10>, "Architecture": <0-10>, "Execution trace": <0-10>},
    "ETL":        {"Overview": <0-10>, "Output": <0-10>, "Questions": <0-10>, "Thinking": <0-10>, "Workflow": <0-10>, "Architecture": <0-10>, "Execution trace": <0-10>},
    "App Studio": {"Overview": <0-10>, "Output": <0-10>, "Questions": <0-10>, "Thinking": <0-10>, "Workflow": <0-10>, "Architecture": <0-10>, "Execution trace": <0-10>}
  },

  "process_evaluation":    "<2-3 sentences>",
  "output_evaluation":     "<2-3 sentences>",
  "reasoning_evaluation":  "<1-2 sentences>",
  "a11y_evaluation":       "<1-2 sentences>",
  "layout_evaluation":     "<1-2 sentences>",
  "flow_graph_evaluation": "<2-3 sentences>",

  "flow_graph_issues": [
    {"rule": "<rule_id>", "severity": "critical|major|minor", "description": "<issue>", "fix_recommendation": "<fix>"}
  ],
  "issues_found": [
    {
      "category": "missing_information|wrong_workflow|incorrect_answer|broken_ui|incorrect_navigation|empty_section|hallucination|formatting|accessibility|layout|overlap|contrast|responsiveness|missing_capability|wrong_capability",
      "description": "<specific issue>",
      "severity": "critical|major|minor",
      "affected_component": "<agent name, sub-tab, capability, or UI region>",
      "evidence": "<exact quote or observation — never generic>",
      "reasoning": "<1-2 sentences explaining WHY this matters for the persona/workflow>",
      "fix_recommendation": "<concrete developer action>"
    }
  ],
  "improvement_flags":               ["<actionable improvement referencing a specific part of the run>", ...],
  "missing_elements":                ["<component SAI should have built>", ...],
  "hallucination_flags":             ["<suspicious or incorrect content>", ...],
  "empty_sections":                  ["<agent/sub-tab that was blank>", ...],
  "a11y_issues":     [{"rule": "<rule_id>", "severity": "critical|major|minor", "description": "<issue>", "fix_recommendation": "<fix>"}],
  "layout_issues":   [{"rule": "<rule_id>", "severity": "critical|major|minor", "description": "<issue>", "fix_recommendation": "<fix>"}],
  "rationale": "<3-5 sentence overall narrative citing specific evidence>",
  "psi_questions_rating": "excellent" | "good" | "adequate" | "poor",
  "agents_that_ran":              ["<agent>", ...],
  "agents_that_should_have_run":  ["<agent>", ...],
  "recovery_needed":       true | false,
  "recovery_assessment":   "<how failures were handled>",
  "recommended_workflow_improvements": ["<improvement>", ...],
  "document_quality": {
    "agent": "<which agent's Output tab>",
    "doc_type": "<PRD|Architecture|Schema|Code|Workflow|Diagram|GovernancePolicy|Other>",
    "completeness_score":  <integer 0-10>,
    "specificity_score":   <integer 0-10>,
    "structure_score":     <integer 0-10>,
    "acceptance_criteria_quality": "excellent|good|adequate|poor|absent",
    "priority_labelling_correct":  true | false,
    "features_covered":  ["<feature>", ...],
    "features_missing":  ["<feature from prompt not found in doc>", ...],
    "doc_evaluation":    "<3-5 sentence analysis>"
  }
}

Rules:
- status = "PASS" only if correctness_score >= 7 AND output_score >= 7 AND no critical issues
- status = "FAIL" if correctness_score < 5 OR output_score < 5 OR any critical issues found
- status = "PARTIAL" otherwise
- Set capability score to null (not 0) for capabilities that were NOT triggered by this workflow
- For triggered capabilities, score 0 only if the capability was triggered but completely failed
- confidence_score: 100 = full canvas evidence + full conversation, 0 = no data
- Every issues_found entry MUST include: affected_component, evidence (exact quote), reasoning, fix_recommendation
- evidence MUST be a direct quote or specific observation — never "output was empty" without quoting what was there
- reasoning MUST explain impact for the specific persona and workflow
"""


def _build_judge_prompt(
    persona: dict,
    workflow: dict,
    psi_conversation: list[dict],
    canvas_evidence: dict,
    step_log: list[dict],
    recovery_log: Optional[list[dict]] = None,
    canvas_timeline: Optional[list[dict]] = None,
    a11y_report: Optional[dict] = None,
    style_report: Optional[dict] = None,
    change_summary: Optional[dict] = None,
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

    # Detect which SAI capabilities are triggered by this workflow's prompt
    prompt_lower = workflow.get("initial_prompt", "").lower()
    title_lower  = workflow.get("title", "").lower()
    combined     = prompt_lower + " " + title_lower

    _cap_triggers = {
        "app_builder":        any(w in combined for w in ["build", "create an app", "develop", "application", "system", "platform", "tool", "saas"]),
        "workflow_builder":   any(w in combined for w in ["workflow", "automation", "pipeline", "integration", "trigger", "no-code", "automate"]),
        "data_pipeline":      any(w in combined for w in ["etl", "elt", "data pipeline", "transform", "schema", "connector", "ingestion", "warehouse", "dbt", "kafka"]),
        "document_generator": any(w in combined for w in ["prd", "document", "spec", "runbook", "guide", "report", "literature review", "brd", "architecture doc"]),
        "diagram_creator":    any(w in combined for w in ["diagram", "flowchart", "chart", "sequence diagram", "er diagram", "visuali", "graph"]),
        "governance":         any(w in combined for w in ["governance", "compliance", "security", "policy", "gdpr", "sox", "hipaa", "audit", "rule"]),
        "image_generator":    any(w in combined for w in ["image", "illustration", "mockup", "logo", "visual design", "concept art", "ui design"]),
    }
    triggered = [cap for cap, hit in _cap_triggers.items() if hit]
    if not triggered:
        triggered = ["app_builder"]   # default if nothing matched

    capability_block = (
        f"SAI Capabilities triggered by this workflow: {', '.join(triggered)}\n"
        f"Judge MUST evaluate each triggered capability using its rubric and populate capability_scores + capability_evaluations.\n"
        f"Set score to null (not 0) for capabilities that are NOT in the triggered list above."
    )

    sai_log_text = "\n".join(
        f"  [{e.get('role','?').upper()}]: {e.get('text','')[:500]}"
        for e in psi_conversation
    ) or "  (no conversation recorded)"

    # Flow Graph evidence — structured phase data
    from app.browser.flow_graph_validator import FlowGraphValidator
    fv = canvas_evidence.get("flow_view", {})
    flow_graph_text = FlowGraphValidator.build_judge_context(fv)

    # Canvas evidence — all agents with all sub-tabs (full content, windowed only for very large outputs)
    canvas_lines = []

    for agent_name, agent_data in canvas_evidence.get("agents", {}).items():
        canvas_lines.append(f"\n{'='*60}")
        canvas_lines.append(f"AGENT: {agent_name}")
        canvas_lines.append(f"  Appeared: {agent_data.get('appeared', False)}")
        canvas_lines.append(f"  State: {agent_data.get('state', 'unknown')}")
        for sub_tab, content in agent_data.get("sub_tabs", {}).items():
            words = len((content or "").split())
            canvas_lines.append(f"\n  --- Sub-tab: {sub_tab} ({words} words) ---")
            if not content or not content.strip():
                canvas_lines.append("  (empty — nothing was rendered here)")
            else:
                # Send up to 3000 chars per sub-tab so the judge sees real content
                body = content.strip()
                if len(body) > 3000:
                    canvas_lines.append(body[:3000])
                    canvas_lines.append(f"  ... [{len(body)-3000} more chars truncated]")
                else:
                    canvas_lines.append(body)
        ss_count = len(agent_data.get("screenshots", []))
        ss_subtabs = [s.get("sub_tab", "") for s in agent_data.get("screenshots", []) if s.get("sub_tab")]
        canvas_lines.append(f"\n  Screenshots captured: {ss_count} sub-tabs: {', '.join(ss_subtabs) or 'none'}")

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

    # Accessibility report summary
    a11y_text = ""
    if a11y_report:
        summary = a11y_report.get("summary", {})
        issues  = a11y_report.get("issues", [])
        a11y_text = (
            f"\n=== ACCESSIBILITY AUDIT ===\n"
            f"  Score: {a11y_report.get('score', 'N/A')}/10 | "
            f"critical={summary.get('critical',0)} major={summary.get('major',0)} minor={summary.get('minor',0)}\n"
        )
        for iss in issues[:10]:
            a11y_text += (
                f"  [{iss.get('severity','?').upper()}] {iss.get('rule','')} — "
                f"{iss.get('description','')} (element: {iss.get('element','')})\n"
            )

    # Style / layout report summary
    style_text = ""
    if style_report:
        summary = style_report.get("summary", {})
        all_iss = style_report.get("all_layout_issues", [])
        style_text = (
            f"\n=== LAYOUT / STYLE AUDIT ===\n"
            f"  Score: {style_report.get('score', 'N/A')}/10 | "
            f"overlaps={summary.get('overlaps',0)} spacing={summary.get('spacing',0)} hidden={summary.get('hidden',0)}\n"
        )
        for iss in all_iss[:8]:
            style_text += (
                f"  [{iss.get('severity','?').upper()}] {iss.get('rule','')} — "
                f"{iss.get('description','')}\n"
            )

    # Change detector summary
    change_text = ""
    if change_summary:
        change_text = (
            f"\n=== CHANGE DETECTOR SUMMARY ===\n"
            f"  Total changes: {change_summary.get('total_changes', 0)} | "
            f"screenshots captured: {change_summary.get('screenshots_captured', 0)}\n"
            f"  Change breakdown: {change_summary.get('change_breakdown', {})}\n"
            f"  A11y issues during run: {change_summary.get('a11y_issues_total', 0)} | "
            f"Layout issues during run: {change_summary.get('layout_issues_total', 0)}\n"
        )

    return f"""
=== EVALUATION REQUEST ===

{persona_block}

{workflow_block}

=== SAI CAPABILITY CONTEXT ===
{capability_block}

=== PSI CONVERSATION LOG ({len(psi_conversation)} turns) ===
{sai_log_text}

=== FLOW GRAPH (Computational Graph Validation) ===
{flow_graph_text}

=== CANVAS EVIDENCE (Agent Sub-tabs) ===
{canvas_text}
{recovery_text}
{timeline_text}
{a11y_text}
{style_text}
{change_text}

=== STEP LOG (last {len(recent)} of {len(step_log)} steps) ===
{step_text}

=== YOUR TASK ===
Evaluate the above SAI test run. Return ONLY valid JSON as specified in your system prompt.

Key evaluation priorities:
1. READ THE FULL OUTPUT TAB CONTENT carefully — it contains the actual deliverable (PRD, architecture doc, etc.).
   Score the Output sub-tab and populate document_quality based on what you actually read there.
2. Check: do the feature requirements match the initial_prompt? Are acceptance criteria testable?
   Are priority tags (Must Have / Should Have / Could Have) used correctly?
3. FLOW GRAPH: Does the graph satisfy the user's query? Are all expected agents present and completed?
4. Include a11y_issues, layout_issues, and flow_graph_issues with fix_recommendation for each.
5. Every issues_found entry MUST name the specific affected_component (e.g. "PRD Output tab", "AIA Overview").
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
        a11y_report: Optional[dict] = None,
        style_report: Optional[dict] = None,
        change_summary: Optional[dict] = None,
    ) -> dict:
        """
        Run full evaluation (text + visual). Returns merged verdict dict.
        Accepts optional accessibility, layout, and change-detection data.
        """
        prompt = _build_judge_prompt(
            persona, workflow, psi_conversation, canvas_evidence,
            step_log, recovery_log, canvas_timeline,
            a11y_report, style_report, change_summary,
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
                max_tokens=4000,
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

        # ── Visual evaluation (9-dimension) ──────────────────────────────────────
        try:
            step_ss = [
                s.get("screenshot_path", "")
                for s in step_log
                if s.get("screenshot_path")
            ]
            visual_result = self.visual_judge.evaluate_full_run(
                canvas_evidence=canvas_evidence,
                step_screenshots=step_ss,
                change_timeline=canvas_evidence.get("change_timeline"),
                a11y_report=a11y_report,
                style_report=style_report,
            )
            verdict["visual_evaluation"] = visual_result
            overall_visual = visual_result.get("overall", {})
            verdict["visual_score"] = overall_visual.get("visual_score", 5)

            # Merge 9-dimension visual scores into verdict
            for dim in [
                "ui_alignment_score", "visual_consistency_score", "text_correctness_score",
                "hallucination_score", "responsiveness_score", "workflow_score",
                "accessibility_score", "overlap_score", "canvas_score",
            ]:
                if dim in overall_visual and dim not in verdict:
                    verdict[dim] = overall_visual[dim]

            # Merge visual anomalies into issues_found
            for anomaly in overall_visual.get("ui_anomalies", []):
                verdict.setdefault("issues_found", []).append({
                    "category":           "broken_ui",
                    "description":        anomaly.get("description", str(anomaly)),
                    "severity":           anomaly.get("severity", "minor"),
                    "affected_component": "UI",
                    "fix_recommendation": anomaly.get("fix_recommendation",
                                          "Review the visual anomaly and fix the underlying layout or state issue."),
                })
            for li in overall_visual.get("layout_issues", []):
                verdict.setdefault("layout_issues", []).append(li)

        except Exception as e:
            logger.warning("[LLMJudge] Visual evaluation failed (non-fatal): %s", e)
            verdict["visual_score"] = 0
            verdict["visual_evaluation"] = {"error": str(e)}

        # ── Merge accessibility checker results ────────────────────────────────
        if a11y_report:
            from app.browser.accessibility_checker import AccessibilityChecker
            verdict.setdefault("a11y_issues", [])
            for iss in a11y_report.get("issues", []):
                verdict["a11y_issues"].append({
                    "rule":               iss.get("rule", ""),
                    "severity":           iss.get("severity", "minor"),
                    "description":        iss.get("description", ""),
                    "element":            iss.get("element", ""),
                    "fix_recommendation": AccessibilityChecker.fix_recommendation(iss),
                })
            if "a11y_score" not in verdict:
                verdict["a11y_score"] = a11y_report.get("score", 5)

        # ── Merge style extractor results ──────────────────────────────────────
        if style_report:
            from app.browser.style_extractor import StyleExtractor
            verdict.setdefault("layout_issues", [])
            for iss in style_report.get("all_layout_issues", []):
                verdict["layout_issues"].append({
                    "rule":               iss.get("rule", ""),
                    "severity":           iss.get("severity", "minor"),
                    "description":        iss.get("description", ""),
                    "fix_recommendation": StyleExtractor.fix_recommendation(iss),
                })
            if "layout_score" not in verdict:
                verdict["layout_score"] = style_report.get("score", 5)

        # ── Visual Diff Report (pixel-level expected vs actual) ──────────────
        try:
            diff_pairs = VisualDiffReport.compare_run(
                canvas_evidence=canvas_evidence,
                step_log=step_log,
            )
            verdict["visual_diff_pairs"] = diff_pairs
            # Surface any FAIL-level diffs as issues_found
            for dp in diff_pairs:
                if dp.get("verdict") == "FAIL" and not dp.get("error"):
                    verdict.setdefault("issues_found", []).append({
                        "category":           "broken_ui",
                        "description":        (
                            f"Visual diff FAIL — {dp.get('label','')}: "
                            f"{dp.get('similarity_pct')}% similar, "
                            f"{dp.get('changed_regions')} changed region(s), "
                            f"UI drift: {dp.get('ui_drift')}"
                        ),
                        "severity":           "major",
                        "affected_component": dp.get("label", "screenshot pair"),
                        "fix_recommendation": (
                            "Review the diff image in the Visual Diff Report section "
                            "and identify which UI region regressed."
                        ),
                    })
        except Exception as e:
            logger.warning("[LLMJudge] VisualDiffReport failed (non-fatal): %s", e)
            verdict["visual_diff_pairs"] = []

        # ── Merge flow graph validation issues ────────────────────────────────
        fv = canvas_evidence.get("flow_view", {}) if canvas_evidence else {}
        flow_val = fv.get("validation", {})
        if flow_val.get("issues"):
            verdict.setdefault("flow_graph_issues", [])
            for iss in flow_val["issues"]:
                verdict["flow_graph_issues"].append({
                    "rule":               iss.get("rule", ""),
                    "severity":           iss.get("severity", "minor"),
                    "description":        iss.get("description", ""),
                    "fix_recommendation": iss.get("fix_recommendation", ""),
                })
            # Structural flow failures also surface as issues_found
            for iss in flow_val["issues"]:
                if iss.get("severity") in ("critical", "major"):
                    verdict.setdefault("issues_found", []).append({
                        "category":           "wrong_workflow",
                        "description":        iss.get("description", ""),
                        "severity":           iss.get("severity", "major"),
                        "affected_component": "Flow Graph",
                        "fix_recommendation": iss.get("fix_recommendation", ""),
                    })
        # Set flow_graph_score from structural verdict if judge didn't provide it
        if "flow_graph_score" not in verdict:
            fv_verdict = flow_val.get("verdict", "unknown")
            verdict["flow_graph_score"] = 10 if fv_verdict == "pass" else (5 if fv_verdict == "partial" else 0)

        return verdict

    @staticmethod
    def _fallback_verdict(error: str) -> dict:
        return {
            "status":                          "FAIL",
            "correctness_verdict":             "fail",
            "confidence_score":                0,
            "correctness_score":               0,
            "process_score":                   0,
            "output_score":                    0,
            "reliability_score":               0,
            "ux_score":                        0,
            "reasoning_score":                 0,
            "a11y_score":                      0,
            "layout_score":                    0,
            "flow_graph_score":                0,
            "capabilities_detected":           [],
            "capability_scores": {
                "app_builder_score":        None,
                "workflow_builder_score":   None,
                "data_pipeline_score":      None,
                "document_generator_score": None,
                "diagram_creator_score":    None,
                "governance_score":         None,
                "image_generator_score":    None,
            },
            "capability_evaluations": {
                "app_builder":        None,
                "workflow_builder":   None,
                "data_pipeline":      None,
                "document_generator": None,
                "diagram_creator":    None,
                "governance":         None,
                "image_generator":    None,
            },
            "per_agent_scores":                {},
            "per_agent_subtab_scores":         {},
            "process_evaluation":              "Evaluation failed — judge error.",
            "output_evaluation":               "Evaluation failed — judge error.",
            "reasoning_evaluation":            "Evaluation failed — judge error.",
            "a11y_evaluation":                 "Evaluation failed — judge error.",
            "layout_evaluation":               "Evaluation failed — judge error.",
            "flow_graph_evaluation":           "Evaluation failed — judge error.",
            "flow_graph_issues":               [],
            "issues_found":                    [{"category": "broken_ui", "description": f"Judge error: {error}", "severity": "critical", "affected_component": "Judge", "evidence": error, "reasoning": "Judge API call failed.", "fix_recommendation": "Check judge model API key and connectivity."}],
            "improvement_flags":               [f"Judge error: {error}"],
            "missing_elements":                [],
            "a11y_issues":                     [],
            "layout_issues":                   [],
            "hallucination_flags":             [],
            "empty_sections":                  [],
            "rationale":                       f"LLM judge could not complete evaluation: {error}",
            "psi_questions_rating":            "poor",
            "agents_that_ran":                 [],
            "agents_that_should_have_run":     [],
            "recovery_needed":                 False,
            "recovery_assessment":             "N/A",
            "recommended_workflow_improvements": [],
            "document_quality":                {},
        }

    @staticmethod
    def verdict_to_overall_status(verdict: dict) -> str:
        # Use the spec's PASS/FAIL/PARTIAL status field if present
        s = verdict.get("status", "").upper()
        if s == "PASS":    return "pass"
        if s == "PARTIAL": return "partial"
        if s == "FAIL":    return "fail"
        # Fall back to correctness_verdict
        v = verdict.get("correctness_verdict", "fail")
        if v == "pass":    return "pass"
        if v == "partial": return "partial"
        return "fail"
