"""
Copilot Health Checker.

Answers exactly two questions after every test run:

  CHECK 1 — Is SAI Copilot (SAI) working properly?
    Did SAI understand the request, ask meaningful clarifying questions,
    stay on-topic, and correctly hand off to agents?

  CHECK 2 — Is the canvas making everything correctly?
    Did every expected agent appear, produce non-empty relevant output,
    and match what the original request asked for?

Each check returns a structured result:
  {
    "status":   "pass" | "partial" | "fail",
    "score":    0-10,
    "summary":  "<one sentence verdict>",
    "evidence": ["<specific observation>", ...],
    "issues":   [{"severity": "critical|major|minor",
                  "description": "...",
                  "fix": "..."}],
  }

These are computed locally (no LLM call) from the evidence already collected
by the orchestrator, so they add zero latency and never fail due to API issues.
The LLM judge's scores are still used when available — these checks provide an
always-on, deterministic fallback baseline.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_color(score: int) -> str:
    if score >= 8:
        return "pass"
    if score >= 5:
        return "partial"
    return "fail"


def _issue(severity: str, description: str, fix: str) -> dict:
    return {"severity": severity, "description": description, "fix": fix}


# ── Check 1: Copilot (SAI) Health ─────────────────────────────────────────────

def check_copilot(
    sai_result: dict,
    workflow: dict,
    persona: dict,
    verdict: Optional[dict] = None,
) -> dict:
    """
    Evaluate whether SAI Copilot handled the conversation correctly.

    Inputs
    ------
    sai_result  : dict returned by SAIHandler.run_conversation_loop()
                  Keys: question_count, completion_detected, conversation_log, rounds
    workflow    : workflow definition dict (initial_prompt, evaluation_criteria, expected_agents)
    persona     : persona dict (name, role, technical_depth, industry)
    verdict     : optional LLM judge verdict — used to pull process_score and
                  psi_questions_rating if available
    """
    score    = 10
    evidence = []
    issues   = []

    q_count   = sai_result.get("question_count", 0)
    completed = sai_result.get("completion_detected", False)
    rounds    = sai_result.get("rounds", 0)
    conv_log  = sai_result.get("conversation_log", [])

    assistant_msgs = [e for e in conv_log if e.get("role") == "assistant"]
    user_msgs      = [e for e in conv_log if e.get("role") == "user"]
    process_crit   = workflow.get("evaluation_criteria", {}).get("process", "")

    # ── Rule 1: Copilot must send at least one message ────────────────────────
    if not assistant_msgs:
        score -= 5
        issues.append(_issue(
            "critical",
            "SAI sent zero messages — the copilot never responded to the user prompt.",
            "Check whether the prompt was submitted correctly and whether the SAI textarea "
            "selector is still valid. Verify the page loaded and SAI is active.",
        ))
    else:
        evidence.append(f"SAI sent {len(assistant_msgs)} message(s) across {rounds} round(s).")

    # ── Rule 2: At least one clarifying question ──────────────────────────────
    if q_count == 0 and assistant_msgs:
        score -= 3
        issues.append(_issue(
            "major",
            "SAI asked zero clarifying questions — it skipped the requirements gathering phase.",
            "Review SAI's question generation logic. Ensure the workflow is complex enough to "
            "trigger clarification, and that question cards are being correctly detected.",
        ))
    elif q_count == 1:
        evidence.append("SAI asked 1 clarifying question (minimum acceptable).")
    else:
        evidence.append(f"SAI asked {q_count} clarifying question(s) — good coverage.")

    # ── Rule 3: Completion signal must be detected ────────────────────────────
    if not completed:
        score -= 3
        issues.append(_issue(
            "major",
            "Copilot completion was never detected — SAI may not have handed off to canvas agents.",
            "Check COMPLETION_PATTERNS in sai_handler.py against current SAI response text. "
            "Add new patterns if SAI's handoff message wording has changed.",
        ))
    else:
        evidence.append("SAI correctly signalled workflow completion / agent handoff.")

    # ── Rule 4: No one-word or empty user answers ─────────────────────────────
    empty_answers = sum(1 for m in user_msgs if len((m.get("text") or "").strip()) < 5)
    if empty_answers > 0:
        score -= 1
        issues.append(_issue(
            "minor",
            f"{empty_answers} user answer(s) were very short (< 5 chars) — "
            "may indicate the AnswerEngine failed to generate a proper response.",
            "Review AnswerEngine.answer() for edge cases where it returns empty strings. "
            "Add fallback answers for unanticipated question types.",
        ))

    # ── Rule 5: Conversation did not time out (max rounds hit) ────────────────
    max_rounds = 20
    if rounds >= max_rounds:
        score -= 2
        issues.append(_issue(
            "major",
            f"Conversation hit the maximum round limit ({max_rounds}) — "
            "SAI may be stuck in a loop or never reached a completion state.",
            "Increase max_rounds or investigate why completion was not detected. "
            "Check for repeated identical messages from SAI.",
        ))

    # ── Rule 6: SAI mentioned expected agents in handoff message ─────────────
    expected = [a.lower() for a in workflow.get("expected_agents", [])]
    if expected and assistant_msgs:
        last_msg = (assistant_msgs[-1].get("text") or "").lower()
        mentioned = [a for a in expected if a in last_msg]
        if not mentioned and completed:
            evidence.append(
                "Note: SAI's final message did not explicitly name expected agents "
                f"({', '.join(workflow.get('expected_agents', []))}) — this is acceptable "
                "if the canvas launched them."
            )

    # ── Rule 7: Pull process_score from LLM judge if available ───────────────
    if verdict:
        judge_ps = verdict.get("process_score")
        psi_rating = verdict.get("psi_questions_rating", "")
        if judge_ps is not None:
            if judge_ps < 5:
                score = min(score, judge_ps)
                issues.append(_issue(
                    "major",
                    f"LLM judge rated SAI process quality {judge_ps}/10 — "
                    f"{verdict.get('process_evaluation', '')}",
                    "Review SAI question generation for this workflow type and persona.",
                ))
                evidence.append(f"LLM judge process score: {judge_ps}/10 ({psi_rating}).")
            else:
                evidence.append(f"LLM judge process score: {judge_ps}/10 ({psi_rating}).")

    # ── Process criterion check ───────────────────────────────────────────────
    if process_crit:
        evidence.append(f"Expected process behaviour: {process_crit}")

    score = max(0, min(10, score))
    status = _score_color(score)

    summary = {
        "pass":    f"SAI Copilot is working correctly — {q_count} question(s) asked, clean handoff detected.",
        "partial": f"SAI Copilot partially functional — some issues detected ({len(issues)} warning(s)).",
        "fail":    f"SAI Copilot has critical failures — copilot did not behave as expected.",
    }[status]

    return {
        "check":    "copilot_health",
        "label":    "SAI Copilot (SAI) Working Correctly",
        "status":   status,
        "score":    score,
        "summary":  summary,
        "evidence": evidence,
        "issues":   issues,
    }


# ── Check 2: Canvas Output Correctness ────────────────────────────────────────

def check_canvas(
    canvas_evidence: dict,
    workflow: dict,
    sai_result: dict,
    verdict: Optional[dict] = None,
) -> dict:
    """
    Evaluate whether the canvas produced the correct output for the request.

    Inputs
    ------
    canvas_evidence : dict built by orchestrator — contains agents{}, flow_view{}
    workflow        : workflow definition (expected_agents, evaluation_criteria, steps)
    sai_result      : sai conversation result (used for context on what was asked)
    verdict         : optional LLM judge verdict
    """
    score    = 10
    evidence = []
    issues   = []

    agents_evidence = canvas_evidence.get("agents", {})
    expected_agents = workflow.get("expected_agents", [])
    output_crit     = workflow.get("evaluation_criteria", {}).get("output", "")
    flow_view       = canvas_evidence.get("flow_view", {})
    appeared_agents = canvas_evidence.get("appeared_agents", [])

    # ── Rule 1: Every expected agent must have appeared ───────────────────────
    missing_agents = [a for a in expected_agents if a not in appeared_agents]
    if missing_agents:
        penalty = 3 * len(missing_agents)
        score  -= penalty
        issues.append(_issue(
            "critical",
            f"Expected agent(s) never appeared on canvas: {', '.join(missing_agents)}.",
            "Verify that these agents are enabled in the SAI workspace. Check whether the "
            "workflow prompt is specific enough to trigger all required agents. "
            "Inspect the canvas_timeline for any tab_appeared events that were missed.",
        ))
    else:
        evidence.append(
            f"All {len(expected_agents)} expected agent(s) appeared: "
            f"{', '.join(expected_agents)}."
        )

    # ── Rule 2: Each agent must have non-empty Output sub-tab ─────────────────
    for agent_name in expected_agents:
        agent_data = agents_evidence.get(agent_name, {})
        if not agent_data.get("appeared"):
            continue  # already flagged above

        sub_tabs = agent_data.get("sub_tabs", {})
        output   = (sub_tabs.get("Output") or "").strip()
        overview = (sub_tabs.get("Overview") or "").strip()

        if not output and not overview:
            score -= 2
            issues.append(_issue(
                "major",
                f"{agent_name}: both Output and Overview sub-tabs are empty — "
                "agent appeared but produced no readable content.",
                f"Navigate to the {agent_name} tab manually and check if content is hidden "
                "behind a CSS overflow. Verify CanvasZoomer is active during text reads. "
                "Check if the agent is still loading when the capture runs.",
            ))
        elif not output:
            score -= 1
            issues.append(_issue(
                "minor",
                f"{agent_name}: Output sub-tab is empty (Overview has content).",
                f"Click the Output sub-tab explicitly after waiting for agent completion. "
                "Check SUBTAB_CONTENT_SELECTORS coverage for {agent_name}.",
            ))
        else:
            word_count = len(output.split())
            evidence.append(
                f"{agent_name} Output: {word_count} words — "
                f"{'good' if word_count > 50 else 'thin — may be incomplete'}."
            )

    # ── Rule 3: Agent state must be 'ready', not 'loading' ───────────────────
    still_loading = [
        a for a in expected_agents
        if agents_evidence.get(a, {}).get("state") == "loading"
    ]
    if still_loading:
        score -= 2
        issues.append(_issue(
            "major",
            f"Agent(s) still in 'loading' state when captured: {', '.join(still_loading)}.",
            "Increase wait timeout in _wait_agent_content_stable(). Check if the loading "
            "spinner selector matches the current SAI DOM — the 'loading' detection may be "
            "stuck returning True indefinitely.",
        ))

    # ── Rule 4: Flow View must appear and have phases ─────────────────────────
    if not flow_view.get("appeared"):
        score -= 2
        issues.append(_issue(
            "major",
            "Flow View (computational graph) never appeared on the canvas.",
            "Verify FLOW_VIEW_SELECTORS match the current DOM. Check if the Flow View "
            "panel renders after a delay — increase canvas wait timeout.",
        ))
    else:
        phase_count = flow_view.get("phase_count", 0)
        fv_val      = flow_view.get("validation", {})
        fv_verdict  = fv_val.get("verdict", "unknown")
        fv_metrics  = fv_val.get("metrics", {})
        errors      = fv_metrics.get("error", 0)
        cancelled   = fv_metrics.get("cancelled", 0)

        if fv_verdict == "fail":
            score -= 2
            issues.append(_issue(
                "critical",
                f"Flow graph validation FAILED — {errors} error phase(s), "
                f"{cancelled} cancelled phase(s) out of {phase_count} total.",
                "Check the Flow View for specific phase errors. Retry the workflow. "
                "Inspect SAI agent logs for the failing phases.",
            ))
        elif fv_verdict == "partial":
            score -= 1
            issues.append(_issue(
                "major",
                f"Flow graph has issues — {errors} error(s), {cancelled} cancelled, "
                f"{phase_count} total phases.",
                "Review the flow graph for incomplete phases. Check agent timeout settings.",
            ))
        else:
            evidence.append(
                f"Flow View OK: {phase_count} phase(s), "
                f"{fv_metrics.get('completed', 0)} completed, 0 errors."
            )

    # ── Rule 5: Screenshot evidence must exist for each agent ─────────────────
    for agent_name in expected_agents:
        agent_data  = agents_evidence.get(agent_name, {})
        screenshots = agent_data.get("screenshots", [])
        if agent_data.get("appeared") and not screenshots:
            score -= 1
            issues.append(_issue(
                "minor",
                f"{agent_name}: agent appeared but no screenshots were captured.",
                "Check ScreenshotAgent.capture_canvas_zoomed() error logs. Verify the "
                "CANVAS_PANEL_SELECTORS match the current DOM after the agent loads.",
            ))

    # ── Rule 6: Output must relate to the original request (keyword check) ────
    prompt_keywords = _extract_keywords(workflow.get("initial_prompt", ""))
    if prompt_keywords:
        all_output_text = " ".join(
            " ".join(
                (agents_evidence.get(a, {}).get("sub_tabs") or {}).values()
            )
            for a in expected_agents
        ).lower()
        matched = [k for k in prompt_keywords if k in all_output_text]
        coverage = len(matched) / len(prompt_keywords) if prompt_keywords else 1.0
        if coverage < 0.3:
            score -= 2
            issues.append(_issue(
                "major",
                f"Canvas output has low semantic coverage of the original request "
                f"({len(matched)}/{len(prompt_keywords)} keywords found: "
                f"{', '.join(matched[:5]) or 'none'}).",
                "Check whether the agent Output sub-tabs are actually being read. "
                "Verify CanvasZoomer un-clipping is active during inner_text reads. "
                "Re-run with debug logging to see raw sub-tab content.",
            ))
        else:
            evidence.append(
                f"Canvas output covers {len(matched)}/{len(prompt_keywords)} "
                f"keywords from the original request — "
                f"{'strong' if coverage >= 0.6 else 'acceptable'} alignment."
            )

    # ── Rule 7: Pull output_score from LLM judge if available ────────────────
    if verdict:
        judge_os = verdict.get("output_score")
        if judge_os is not None:
            if judge_os < 5:
                score = min(score, judge_os)
                issues.append(_issue(
                    "major",
                    f"LLM judge rated canvas output quality {judge_os}/10 — "
                    f"{verdict.get('output_evaluation', '')}",
                    "Review the agent output sub-tabs and compare against the expected "
                    "output in the workflow evaluation_criteria.",
                ))
            evidence.append(f"LLM judge output score: {judge_os}/10.")

        # Surface LLM judge empty_sections as canvas issues
        for empty in verdict.get("empty_sections", []):
            issues.append(_issue(
                "minor",
                f"LLM judge flagged empty section: {empty}",
                f"Ensure {empty} sub-tab is clicked and content is stable before capture.",
            ))

    # ── Output criterion reference ────────────────────────────────────────────
    if output_crit:
        evidence.append(f"Expected canvas output: {output_crit}")

    score = max(0, min(10, score))
    status = _score_color(score)

    n_appeared = len([a for a in expected_agents if a in appeared_agents])
    summary = {
        "pass":    f"Canvas is producing correct output — {n_appeared}/{len(expected_agents)} agents ran successfully.",
        "partial": f"Canvas has partial issues — {n_appeared}/{len(expected_agents)} agents ran, {len(issues)} issue(s) found.",
        "fail":    f"Canvas has critical failures — {n_appeared}/{len(expected_agents)} agents ran, output incomplete or missing.",
    }[status]

    return {
        "check":    "canvas_correctness",
        "label":    "Canvas Producing Correct Output",
        "status":   status,
        "score":    score,
        "summary":  summary,
        "evidence": evidence,
        "issues":   issues,
    }


# ── Combined entry point ───────────────────────────────────────────────────────

def run_health_checks(
    sai_result: dict,
    canvas_evidence: dict,
    workflow: dict,
    persona: dict,
    verdict: Optional[dict] = None,
) -> dict:
    """
    Run both health checks and return a combined result.

    Returns
    -------
    {
        "copilot":           <check_1_result>,
        "canvas":            <check_2_result>,
        "overall_status":    "pass" | "partial" | "fail",
        "overall_score":     0-10,
        "summary":           "<one-line overall verdict>",
    }
    """
    copilot = check_copilot(sai_result, workflow, persona, verdict)
    canvas  = check_canvas(canvas_evidence, workflow, sai_result, verdict)

    avg_score   = round((copilot["score"] + canvas["score"]) / 2)
    all_statuses = {copilot["status"], canvas["status"]}

    if "fail" in all_statuses:
        overall = "fail"
    elif "partial" in all_statuses:
        overall = "partial"
    else:
        overall = "pass"

    summary_map = {
        "pass":    "SAI Copilot is working correctly and the canvas is producing correct output.",
        "partial": "System is partially functional — see individual check results for details.",
        "fail":    "Critical failures detected — one or both core checks failed.",
    }

    logger.info(
        "[HealthChecks] Copilot: %s (%d/10) | Canvas: %s (%d/10) | Overall: %s",
        copilot["status"], copilot["score"],
        canvas["status"],  canvas["score"],
        overall,
    )

    return {
        "copilot":        copilot,
        "canvas":         canvas,
        "overall_status": overall,
        "overall_score":  avg_score,
        "summary":        summary_map[overall],
    }


# ── Keyword extraction helper ──────────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "that", "this", "it", "is", "are", "was",
    "be", "have", "has", "do", "does", "i", "me", "my", "we", "you", "your",
    "can", "will", "would", "should", "could", "build", "create", "make",
    "want", "need", "get", "use", "using", "based", "where", "so", "how",
    "all", "some", "any", "each", "into", "than", "more", "also",
}

def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from a prompt for semantic coverage check."""
    words = re.findall(r'\b[a-z]{4,}\b', text.lower())
    seen  = set()
    result = []
    for w in words:
        if w not in _STOP_WORDS and w not in seen:
            seen.add(w)
            result.append(w)
    return result[:20]
