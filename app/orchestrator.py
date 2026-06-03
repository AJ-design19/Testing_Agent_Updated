"""
Production-grade autonomous AI Browser Testing and Evaluation Agent for Adya SAI.

Architecture (multi-agent):
  ┌─ Planner        ── selects persona + workflow, plans the test run
  ├─ Browser Op     ── login, navigation, event-driven waiting
  ├─ SAI Handler    ── full conversational loop with SAI's copilot
  ├─ Screenshot Agt ── continuous background capture + on-demand evidence
  ├─ Canvas Monitor ── real-time canvas state tracking (background task)
  ├─ Self-Healer    ── detects and recovers from failures automatically
  ├─ LLM Judge      ── text evaluation of process + output quality
  ├─ Visual Judge   ── multimodal screenshot evaluation
  ├─ Report Gen     ── full run report + per-agent detailed reports
  └─ ESLL           ── derives insights, routes to HITL as needed

Usage:
  python -m app.main                           # random persona + workflow
  python -m app.main --persona PM-1            # specific persona, random workflow
  python -m app.main --persona PM-1 --workflow WF-PM-001
  python -m app.main --all                     # run all workflows sequentially
  python -m app.main --metrics                 # print metrics dashboard only
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from app.browser.browser_manager import BrowserManager
from app.browser.login import login
from app.browser.sai_navigator import SAINavigator
from app.browser.sai_handler import SAIHandler
from app.browser.screenshot_agent import ScreenshotAgent
from app.browser.canvas_monitor import CanvasMonitor
from app.browser.self_healer import SelfHealer
from app.browser.accessibility_checker import AccessibilityChecker
from app.browser.style_extractor import StyleExtractor
from app.browser.change_detector import ChangeDetector
from app.browser.brd_downloader import BRDDownloader
from app.browser.pdf_extractor import extract_text as extract_pdf_text, extract_sections
from app.conversation.answer_engine import AnswerEngine
from app.recording.action_recorder import ActionRecorder
from app.judge.llm_judge import LLMJudge
from app.judge.brd_validator import BRDValidator
from app.reporting.agent_report import generate_all_agent_reports
from app.workflows.workflow_registry import (
    get_all_workflows,
    get_workflow_by_id,
    get_random_workflow,
    load_persona,
)

# Infrastructure stores and services
from app.store.mongo_store import MongoStore
from app.store.postgres_store import PostgresStore
from app.store.audit_log import AuditLog
from app.store.object_store import ObjectStore
from app.store.models import (
    AnalysisRun,
    ESMEvent,
    ESMEventType,
)
from app.esll.esll_service import ESLLService
from app.governance.hitl_manager import HitlManager
from app.metrics.metrics_collector import MetricsCollector
from app.reporting.run_report import RunReportGenerator, generate_batch_index

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("reports/test_run.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"


# ── Infrastructure factory ─────────────────────────────────────────────────────

def _build_infrastructure():
    """
    Initialise all infrastructure stores. Every component gracefully degrades
    to dry-run / local-file mode when the backing service is unavailable.
    Returns (mongo, pg, audit, object_store, esll, hitl, metrics).
    """
    mongo   = MongoStore()
    pg      = PostgresStore()
    audit   = AuditLog(pg_conn=pg._conn)
    obj     = ObjectStore()
    esll    = ESLLService(mongo=mongo, pg=pg, audit=audit)
    hitl    = HitlManager(pg=pg, audit=audit)
    metrics = MetricsCollector(mongo_store=mongo, pg_store=pg, hitl_manager=hitl)
    return mongo, pg, audit, obj, esll, hitl, metrics


# ── ESM event helper ───────────────────────────────────────────────────────────

def _emit(mongo: MongoStore, event_type: ESMEventType, run_id: str,
          persona: dict, workflow: dict, **payload) -> str:
    """Write one ESM event and return its event_id."""
    evt = ESMEvent(
        event_type=event_type.value,
        run_id=run_id,
        session_id=run_id,
        persona_id=persona.get("id", ""),
        journey_id=workflow.get("id", ""),
        payload=payload,
    )
    mongo.write_event(evt)
    return evt.event_id


# ── Single workflow run ────────────────────────────────────────────────────────

async def run_single_workflow(
    persona: dict,
    workflow: dict,
    headless: bool = False,
    infra: Optional[tuple] = None,
) -> dict:
    """
    Execute one full test run: login → prompt → SAI loop → canvas capture → judge
    → persist to stores → route ESLL insights.
    Returns the full run record dict.

    Pass `infra` to reuse infrastructure across a batch run (avoids re-connecting
    on every iteration). When None a fresh set is created for this run only.
    """
    run_id = (
        f"{persona['id']}_{workflow['id']}"
        f"_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        f"_{uuid.uuid4().hex[:6]}"
    )
    logger.info("=" * 60)
    logger.info("STARTING RUN: %s", run_id)
    logger.info("Persona: %s (%s)", persona.get("name"), persona.get("id"))
    logger.info("Workflow: %s (%s)", workflow.get("title"), workflow.get("id"))
    logger.info("=" * 60)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)
    os.makedirs("artifacts", exist_ok=True)

    # Infrastructure (shared if batch, fresh if single run)
    _owns_infra = infra is None
    if _owns_infra:
        infra = _build_infrastructure()
    mongo, pg, audit, obj, esll, hitl, metrics = infra

    # Initialise recording and browser
    recorder        = ActionRecorder(run_id=run_id, persona=persona, workflow=workflow)
    browser_manager = BrowserManager()
    page = await browser_manager.start(headless=headless)

    # ESM: session start event
    session_start_id = _emit(mongo, ESMEventType.SAI_SESSION_START, run_id,
                             persona, workflow, workflow_title=workflow.get("title"))

    # Postgres: open analysis run
    analysis_run = AnalysisRun(
        run_id=run_id,
        persona_id=persona.get("id", ""),
        journey_id=workflow.get("id", ""),
        workflow_title=workflow.get("title", ""),
        agents_expected=workflow.get("expected_agents", []),
    )
    pg.upsert_analysis_run(analysis_run)

    # Agents initialised after login — declared here for except/finally access
    ss_agent: Optional[ScreenshotAgent]  = None
    canvas_monitor: Optional[CanvasMonitor] = None
    healer: Optional[SelfHealer]         = None
    change_detector: Optional[ChangeDetector] = None
    verdict: dict                        = {}
    a11y_report: dict                    = {}
    style_report: dict                   = {}
    brd_result: dict                     = {}   # BRD download + validation result

    try:
        # ── Step 1: Login ──────────────────────────────────────────────────────
        # Clear cookies before login so a previous session can't cause a
        # server-side redirect to a saved workspaceId URL.
        try:
            await page.context.clear_cookies()
            logger.info("[Orchestrator] Browser cookies cleared before login")
        except Exception as _ce:
            logger.debug("[Orchestrator] Could not clear cookies: %s", _ce)

        recorder.record("login", "Navigating to SAI and logging in")
        await login(page)
        recorder.record("login", "Login completed", success=True,
                        notes=f"URL: {page.url}")

        # Assert the post-login URL is the clean base orchestrator page
        if "workspaceId" in page.url or "workspaceid" in page.url.lower():
            raise RuntimeError(
                f"[Orchestrator] Login did not land on base orchestrator page. "
                f"Still on workspace URL: {page.url}"
            )

        # ── Initialise all agents ───────────────────────────────────────────────
        navigator      = SAINavigator(page)
        answer_eng     = AnswerEngine(persona=persona, workflow=workflow)
        ss_agent       = ScreenshotAgent(
            page, run_id=run_id, interval_seconds=5.0,
            workflow_id=workflow.get("id", run_id),
        )
        canvas_monitor = CanvasMonitor(page, screenshot_agent=ss_agent, answer_engine=answer_eng)
        healer          = SelfHealer(page, run_id=run_id)
        a11y_checker    = AccessibilityChecker(page)
        style_extractor = StyleExtractor(page)
        change_detector = ChangeDetector(page, ss_agent, run_id=run_id)
        sai_handler     = SAIHandler(
            page=page, persona=persona,
            answer_engine=answer_eng, navigator=navigator,
            run_id=run_id, screenshot_agent=ss_agent,
            esll_service=esll, journey=workflow,
        )
        judge           = LLMJudge()

        # Start continuous background screenshot capture
        ss_agent.start_background_capture()

        # ── Step 2: Health check — dismiss any modals ─────────────────────────
        health = await healer.full_health_check(login_fn=login)
        if not health["healthy"]:
            recorder.record("health_check", "Page health issues detected",
                            success=False, notes=str(health["issues"]))

        # ── Step 3: Send initial prompt ────────────────────────────────────────
        initial_prompt = workflow["initial_prompt"]
        recorder.record(
            "send_prompt",
            f"Sending initial workflow prompt as {persona['name']}",
            content_snapshot=initial_prompt,
            input_sent=initial_prompt,
            reasoning="Initial workflow prompt submitted to SAI to begin the conversation.",
        )
        sent = await sai_handler.send_initial_prompt(initial_prompt)

        if not sent:
            err_ss = await ss_agent.capture_error("prompt_send_failed")
            recorder.record(
                "send_prompt", "Failed to send initial prompt",
                success=False, screenshot_path=err_ss,
                reasoning="Prompt textarea or send button was not found/clickable. "
                          "SAI interface may not have loaded correctly.",
            )
            _emit(mongo, ESMEventType.SAI_STEP_FAILED, run_id, persona, workflow,
                  step="send_initial_prompt")
            result = recorder.finish(overall_status="error")
            _finalise_run(pg, analysis_run, result, verdict={}, screenshot_paths=[])
            return result

        recorder.record(
            "send_prompt", "Initial prompt submitted",
            input_sent=initial_prompt,
            reasoning="Prompt was typed into the textarea and the send button was clicked successfully.",
        )
        await ss_agent.capture(label="prompt_submitted", event_type="prompt")

        # ── Step 4: SAI conversation loop ──────────────────────────────────────
        recorder.record("sai_loop", "Starting SAI conversation loop",
                        reasoning="Entering the multi-round conversation loop with SAI.")
        sai_result = await sai_handler.run_conversation_loop(max_rounds=20)

        # Capture screenshot after each SAI exchange
        await ss_agent.capture(label="sai_loop_done", event_type="sai")

        # Record each conversation turn with full input/output
        conv_log = sai_result.get("conversation_log", [])
        for i, entry in enumerate(conv_log):
            role  = entry.get("role", "")
            text  = entry.get("text") or entry.get("content") or ""
            if role == "assistant":
                _emit(mongo, ESMEventType.SAI_PSI_QUESTION, run_id, persona, workflow,
                      message=text[:500])
                # Find the next user reply (if any) to pair input→output
                next_user = next(
                    (e.get("text") or e.get("content") or ""
                     for e in conv_log[i+1:]
                     if e.get("role") == "user"),
                    None,
                )
                recorder.record(
                    "sai_question",
                    f"SAI asked a clarifying question (turn {i+1})",
                    sai_output=text,
                    input_sent=next_user,
                    reasoning=(
                        "SAI responded with a clarifying question to gather more context "
                        "before building the solution."
                    ),
                )
            elif role == "user":
                _emit(mongo, ESMEventType.SAI_PSI_ANSWER, run_id, persona, workflow,
                      message=text[:500])
                # Find the preceding SAI question
                prev_sai = next(
                    (e.get("text") or e.get("content") or ""
                     for e in reversed(conv_log[:i])
                     if e.get("role") == "assistant"),
                    None,
                )
                recorder.record(
                    "sai_answer",
                    f"Agent answered SAI question (turn {i+1})",
                    input_sent=prev_sai,
                    sai_output=text,
                    reasoning=(
                        "Agent responded in persona voice to SAI's clarifying question."
                    ),
                )
            tokens = entry.get("tokens", 0)
            if tokens:
                metrics.record_token_usage(run_id, tokens)

        # Build a single-string summary of the full conversation for the loop step
        convo_summary = " | ".join(
            f"[{e.get('role','?')}] {(e.get('text') or e.get('content') or '')[:120]}"
            for e in conv_log
        )
        recorder.record(
            "sai_loop",
            f"SAI complete: {sai_result['question_count']} Qs answered | "
            f"completion_detected={sai_result['completion_detected']} | "
            f"rounds={sai_result['rounds']}",
            sai_output=convo_summary,       # full, untruncated
            reasoning=(
                f"Conversation loop ended after {sai_result['rounds']} round(s). "
                f"completion_detected={sai_result['completion_detected']}. "
                f"{sai_result['question_count']} question(s) answered by the agent."
            ),
        )
        recorder.set_psi_conversation(sai_result["conversation_log"])
        # Attach interaction log (full SAI output + derived reasoning per turn)
        # and learning summary to the run record
        recorder.record_obj.interaction_log   = sai_result.get("interaction_log", [])
        recorder.record_obj.learning_summary  = sai_result.get("learning_summary", {})

        # ── Step 5: Wait for canvas + start canvas monitor ────────────────────
        recorder.record("canvas_wait", "Waiting for canvas to appear after SAI handoff")
        canvas_appeared = await navigator.wait_for_canvas(timeout_ms=90000)

        if not canvas_appeared:
            # Self-healer: try to recover missing canvas
            recovered = await healer.recover_missing_canvas(navigator)
            recorder.record("canvas_wait", f"Canvas recovery attempted: {recovered}",
                            success=recovered)

        # Handoff screenshot
        handoff_ss = await ss_agent.capture(label="sai_handoff", event_type="handoff")
        handoff_uri = obj.store_screenshot(run_id, handoff_ss or "", label="sai_handoff")
        _emit(mongo, ESMEventType.SAI_STEP_COMPLETED, run_id, persona, workflow,
              step="sai_handoff", artifact_uri=handoff_uri)
        recorder.record("screenshot", "SAI→Canvas handoff screenshot",
                        screenshot_path=handoff_ss)

        # Start canvas background monitor + change detector AFTER handoff
        canvas_monitor.start()
        change_detector.start()

        # Baseline accessibility + layout check immediately after login/handoff
        try:
            a11y_report  = await a11y_checker.run_for_page(url=page.url)
            style_report = await style_extractor.run_all(context_label="post_handoff")
            recorder.record(
                "accessibility",
                f"Baseline a11y check: score={a11y_report.get('score')}/10 "
                f"issues={a11y_report.get('summary', {}).get('total', 0)}",
            )
            recorder.record(
                "layout",
                f"Baseline layout check: score={style_report.get('score')}/10 "
                f"overlaps={style_report.get('summary', {}).get('overlaps', 0)}",
            )
        except Exception as e:
            logger.warning("[Orchestrator] Baseline a11y/layout check failed (non-fatal): %s", e)

        # ── Step 6: Capture canvas — each agent with all sub-tabs ─────────────
        expected_agents = workflow.get("expected_agents", ["AIA", "AGP", "ETL", "App Studio"])
        recorder.record("canvas_capture",
                        f"Capturing canvas for agents: {expected_agents}")

        # Use CanvasMonitor for richer per-agent evidence
        agents_evidence: dict = {}
        for agent_name in expected_agents:
            recorder.record("canvas_agent", f"Starting capture: {agent_name}",
                            agent_tab=agent_name)
            try:
                # Navigate to the agent tab first, then wait until it has
                # finished loading before we read content or take screenshots.
                tab_appeared = await navigator.wait_for_agent_tab(
                    agent_name, timeout=120000
                )
                if tab_appeared:
                    await navigator.navigate_to_agent_tab(agent_name)
                    await navigator.wait_for_agent_loaded(agent_name, timeout_seconds=120)
                    logger.info("[Orchestrator] Agent '%s' ready — capturing", agent_name)
                else:
                    logger.warning("[Orchestrator] Agent tab '%s' never appeared", agent_name)

                agent_ev = await canvas_monitor.capture_agent_evidence(agent_name, navigator)

                # Per-agent accessibility and layout checks
                try:
                    agent_a11y  = await a11y_checker.run_for_agent(agent_name)
                    agent_style = await style_extractor.run_all(context_label=f"agent:{agent_name}")
                    agent_ev["a11y_report"]  = agent_a11y
                    agent_ev["style_report"] = agent_style
                    logger.info(
                        "[Orchestrator] Agent '%s' a11y score=%s/10 layout score=%s/10",
                        agent_name, agent_a11y.get("score"), agent_style.get("score"),
                    )
                    # Merge worst findings into top-level reports
                    a11y_report.setdefault("issues", []).extend(agent_a11y.get("issues", []))
                    style_report.setdefault("all_layout_issues", []).extend(
                        agent_style.get("all_layout_issues", [])
                    )
                except Exception as ae:
                    logger.debug("[Orchestrator] Per-agent a11y/layout check error for %s: %s", agent_name, ae)

                agents_evidence[agent_name] = agent_ev
                appeared = agent_ev.get("appeared", False)
                ss_list  = agent_ev.get("screenshots", [])

                for ss in ss_list:
                    uri = obj.store_screenshot(run_id, ss.get("path", ""),
                                               label=f"{agent_name}_{ss.get('sub_tab','')}")
                    if uri:
                        pass  # uris tracked below

                evt_type = (ESMEventType.SAI_CANVAS_AGENT_START if appeared
                            else ESMEventType.SAI_STEP_FAILED)
                _emit(mongo, evt_type, run_id, persona, workflow,
                      agent=agent_name, appeared=appeared, screenshots=len(ss_list))

                # Build a canvas summary from all sub-tabs for this agent
                sub_tabs = agent_ev.get("sub_tabs", {})
                canvas_summary = "; ".join(
                    f"{st}: {(txt or '').strip()[:120]}"
                    for st, txt in sub_tabs.items() if txt
                )
                recorder.record(
                    "canvas_agent",
                    f"Agent '{agent_name}': appeared={appeared}, "
                    f"screenshots={len(ss_list)}, state={agent_ev.get('state')}",
                    agent_tab=agent_name,
                    success=appeared,
                    screenshot_path=ss_list[-1]["path"] if ss_list else None,
                    canvas_result=canvas_summary[:1000] if canvas_summary else None,
                    reasoning=(
                        f"Agent tab '{agent_name}' {'appeared and was captured' if appeared else 'did not appear'}. "
                        f"{len(sub_tabs)} sub-tab(s) read: {', '.join(sub_tabs.keys()) or 'none'}."
                    ),
                )
                for sub_tab, content in sub_tabs.items():
                    has_content = bool((content or "").strip())
                    recorder.record(
                        "canvas_subtab",
                        f"Read sub-tab {sub_tab}",
                        agent_tab=agent_name,
                        sub_tab=sub_tab,
                        content_snapshot=(content or "")[:300],
                        canvas_result=(content or "")[:1000],
                        success=has_content,
                        reasoning=(
                            f"Sub-tab '{sub_tab}' on agent '{agent_name}' "
                            f"{'returned {len(content)} chars of content' if has_content else 'was empty or not found'}."
                        ),
                    )
            except Exception as ae:
                logger.error("[Orchestrator] Error capturing agent %s: %s", agent_name, ae)
                err_ss = await ss_agent.capture_error(f"agent_{agent_name}_error",
                                                       agent=agent_name)
                recorder.record("error", f"Agent {agent_name} capture error: {ae}",
                                agent_tab=agent_name, success=False,
                                screenshot_path=err_ss)

        # Stop change detector and collect its timeline before building evidence
        change_detector.stop()
        change_timeline  = change_detector.get_timeline()
        change_summary   = change_detector.get_summary()

        # Final page-level a11y + layout pass
        try:
            final_a11y   = await a11y_checker.run_for_page(url=page.url)
            final_style  = await style_extractor.run_all(context_label="final")
            # Merge with accumulated per-agent issues
            for iss in final_a11y.get("issues", []):
                if iss not in a11y_report.get("issues", []):
                    a11y_report.setdefault("issues", []).append(iss)
            for iss in final_style.get("all_layout_issues", []):
                if iss not in style_report.get("all_layout_issues", []):
                    style_report.setdefault("all_layout_issues", []).append(iss)
            # Update summary and score with final pass
            a11y_report["summary"] = final_a11y.get("summary", a11y_report.get("summary", {}))
            a11y_report["score"]   = final_a11y.get("score", a11y_report.get("score", 5))
            style_report["summary"] = final_style.get("summary", style_report.get("summary", {}))
            style_report["score"]   = final_style.get("score", style_report.get("score", 5))
        except Exception as e:
            logger.warning("[Orchestrator] Final a11y/layout check failed (non-fatal): %s", e)

        # Build canvas_evidence in the format CanvasReader / reports expect
        canvas_evidence = {
            "run_id":    run_id,
            "flow_view": await canvas_monitor.capture_flow_view_evidence(
                navigator,
                expected_agents=expected_agents,
                user_query=workflow.get("initial_prompt", ""),
            ),
            "agents":           agents_evidence,
            "canvas_timeline":  canvas_monitor.get_timeline(),
            "change_timeline":  change_timeline,
            "change_summary":   change_summary,
            "appeared_agents":  canvas_monitor.get_appeared_agents(),
            "final_screenshot": await ss_agent.capture(label="canvas_final", event_type="final"),
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        recorder.set_canvas_evidence(canvas_evidence)

        # ── Step 6b: Canvas Document Download + Validation ───────────────────
        # SAI generates downloadable documents (PRD, BRD, architecture doc,
        # runbook, etc.) in the canvas right-panel Output tab with a Download
        # button.  We always attempt the download — if no button appears within
        # the timeout, we skip gracefully.
        _doc_keywords = [
            "prd", "brd", "document", "spec", "runbook", "guide", "report",
            "literature review", "architecture", "requirements", "diagram",
        ]
        _title_lower    = workflow.get("title", "").lower()
        _prompt_lower   = workflow.get("initial_prompt", "").lower()
        _criteria_lower = str(workflow.get("evaluation_criteria", "")).lower()
        _wants_doc = any(
            kw in _title_lower or kw in _prompt_lower or kw in _criteria_lower
            for kw in _doc_keywords
        )

        if _wants_doc:
            # Detect document type from workflow title/prompt
            _doc_type = "Document"
            if "prd" in _title_lower or "product requirement" in _prompt_lower:
                _doc_type = "PRD"
            elif "brd" in _title_lower or "business requirement" in _prompt_lower:
                _doc_type = "BRD"
            elif "architecture" in _title_lower or "architecture" in _prompt_lower:
                _doc_type = "Architecture Doc"
            elif "runbook" in _prompt_lower:
                _doc_type = "Runbook"
            elif "literature" in _prompt_lower:
                _doc_type = "Literature Review"

            recorder.record(
                "doc_download",
                f"Attempting {_doc_type} download from canvas Output tab",
            )
            try:
                doc_downloader = BRDDownloader(
                    page, run_id=run_id, screenshot_agent=ss_agent
                )
                pdf_path = await doc_downloader.download(timeout_seconds=60)

                if pdf_path:
                    pdf_extraction = extract_pdf_text(pdf_path)
                    doc_sections   = extract_sections(pdf_extraction.get("text", ""))

                    doc_validator  = BRDValidator()
                    doc_validation = doc_validator.validate(
                        brd_text=pdf_extraction.get("text", ""),
                        workflow=workflow,
                        persona=persona,
                        pdf_extraction=pdf_extraction,
                        doc_type=_doc_type,
                    )
                    brd_result = {
                        "doc_type":         _doc_type,
                        "pdf_path":         pdf_path,
                        "pdf_pages":        pdf_extraction.get("pages", 0),
                        "pdf_chars":        pdf_extraction.get("char_count", 0),
                        "pdf_text":         pdf_extraction.get("text", ""),
                        "sections":         doc_sections,
                        "validation":       doc_validation,
                        "verdict":          doc_validation.get("verdict", "fail"),
                        "overall_score":    doc_validation.get("overall_score", 0),
                        "download_success": True,
                    }
                    recorder.record(
                        "doc_download",
                        f"{_doc_type} downloaded and validated: "
                        f"verdict={doc_validation.get('verdict')} "
                        f"score={doc_validation.get('overall_score')}/10 "
                        f"pages={pdf_extraction.get('pages')} "
                        f"chars={pdf_extraction.get('char_count')}",
                        success=(doc_validation.get("verdict") != "fail"),
                        sai_output=pdf_extraction.get("text", "")[:500],
                        reasoning=(
                            f"{_doc_type} downloaded successfully. "
                            f"LLM validator scored it {doc_validation.get('overall_score')}/10 "
                            f"({doc_validation.get('verdict')}). "
                            f"Completeness={doc_validation.get('completeness_score')}/10, "
                            f"Accuracy={doc_validation.get('accuracy_score')}/10."
                        ),
                    )
                    logger.info(
                        "[Orchestrator] %s validation: verdict=%s score=%s/10 "
                        "completeness=%s accuracy=%s",
                        _doc_type,
                        doc_validation.get("verdict"),
                        doc_validation.get("overall_score"),
                        doc_validation.get("completeness_score"),
                        doc_validation.get("accuracy_score"),
                    )
                else:
                    brd_result = {
                        "doc_type":         _doc_type,
                        "download_success": False,
                        "verdict":          "fail",
                        "error":            "Download button not found or timed out",
                    }
                    recorder.record(
                        "doc_download",
                        f"{_doc_type} download failed — Download button not found in canvas",
                        success=False,
                        reasoning="The Download button was not visible in the canvas Output tab within 30 s. The document may not have finished generating, or the Output tab was not active.",
                    )
                    logger.warning("[Orchestrator] %s download failed", _doc_type)

            except Exception as doc_err:
                brd_result = {
                    "doc_type": _doc_type,
                    "download_success": False,
                    "verdict": "fail",
                    "error": str(doc_err),
                }
                recorder.record(
                    "doc_download", f"{_doc_type} download error: {doc_err}",
                    success=False,
                )
                logger.error("[Orchestrator] %s download error: %s", _doc_type, doc_err)
        else:
            brd_result = {
                "download_success": False,
                "skipped": True,
                "reason": "No document-generating keywords detected in this workflow",
            }

        # ── Step 7: LLM Judge evaluation ──────────────────────────────────────
        recorder.record("judge", "Running LLM Judge + Visual Judge evaluation")
        verdict = judge.evaluate(
            persona=persona,
            workflow=workflow,
            psi_conversation=sai_result["conversation_log"],
            canvas_evidence=canvas_evidence,
            step_log=recorder.get_record()["steps"],
            recovery_log=healer.recovery_log,
            canvas_timeline=canvas_monitor.get_timeline(),
            a11y_report=a11y_report or None,
            style_report=style_report or None,
            change_summary=change_summary or None,
        )
        recorder.set_judge_verdict(verdict)
        metrics.record_token_usage(run_id, verdict.get("tokens_used", 2000))

        # ── Health checks: copilot working? canvas correct? ───────────────────
        from app.judge.copilot_health_checker import run_health_checks
        health_checks = run_health_checks(
            sai_result=sai_result,
            canvas_evidence=canvas_evidence,
            workflow=workflow,
            persona=persona,
            verdict=verdict,
        )
        recorder.record(
            "health_check",
            f"Copilot: {health_checks['copilot']['status']} ({health_checks['copilot']['score']}/10) | "
            f"Canvas: {health_checks['canvas']['status']} ({health_checks['canvas']['score']}/10)",
            success=(health_checks["overall_status"] != "fail"),
        )
        logger.info("[Orchestrator] Health checks — %s", health_checks["summary"])

        overall_status = LLMJudge.verdict_to_overall_status(verdict)

        # Attach a11y / style / change data to the run record for the report generator
        recorder.record(
            "accessibility",
            f"Final a11y score: {a11y_report.get('score','?')}/10 "
            f"issues={len(a11y_report.get('issues', []))}",
        )
        recorder.record(
            "layout",
            f"Final layout score: {style_report.get('score','?')}/10 "
            f"layout_issues={len(style_report.get('all_layout_issues', []))}",
        )
        recorder.record(
            "judge",
            f"Verdict: {verdict.get('correctness_verdict')} | "
            f"Process:{verdict.get('process_score')}/10 | "
            f"Output:{verdict.get('output_score')}/10 | "
            f"Visual:{verdict.get('visual_score')}/10",
            success=(overall_status == "pass"),
        )

        _emit(mongo, ESMEventType.SAI_JUDGE_VERDICT, run_id, persona, workflow,
              correctness_verdict=verdict.get("correctness_verdict"),
              process_score=verdict.get("process_score"),
              output_score=verdict.get("output_score"),
              visual_score=verdict.get("visual_score"),
              improvement_flags=verdict.get("improvement_flags", []),
              missing_elements=verdict.get("missing_elements", []))

        # Final screenshot
        final_ss = await ss_agent.capture(label="run_complete", event_type="final")
        recorder.record("screenshot", "Final screenshot", screenshot_path=final_ss)

        # ── Step 8: Finish & persist ───────────────────────────────────────────
        result = recorder.finish(overall_status=overall_status)
        screenshot_uris = [
            obj.store_screenshot(run_id, s["path"], label=s["label"])
            for s in ss_agent.manifest
            if s.get("path") and os.path.exists(s["path"])
        ]
        screenshot_uris = [u for u in screenshot_uris if u]
        _finalise_run(pg, analysis_run, result, verdict, screenshot_uris)

        # ── Step 9: Generate all reports ──────────────────────────────────────
        run_meta = {
            "start_time": result.get("start_time", ""),
            "end_time":   result.get("end_time", ""),
            "duration":   _duration(result.get("start_time"), result.get("end_time")),
        }

        # Attach enrichment data to the run result for the report generator
        result["a11y_report"]    = a11y_report
        result["style_report"]   = style_report
        result["change_timeline"] = change_timeline
        result["change_summary"]  = change_summary
        result["health_checks"]   = health_checks
        result["brd_result"]      = brd_result

        # 9a. Full run HTML report
        try:
            report_path = RunReportGenerator().generate(result, persona_data=persona)
            result["html_report_path"] = report_path
            logger.info("[Orchestrator] Full report: %s", report_path)
        except Exception as e:
            logger.warning("[Orchestrator] Full report failed (non-fatal): %s", e)

        # 9b. Per-agent detailed reports
        try:
            agent_report_paths = generate_all_agent_reports(
                run_id=run_id,
                persona=persona,
                workflow=workflow,
                canvas_evidence=canvas_evidence,
                psi_conversation=sai_result["conversation_log"],
                canvas_timeline=canvas_monitor.get_timeline(),
                verdict=verdict,
                run_metadata=run_meta,
            )
            result["agent_report_paths"] = agent_report_paths
            logger.info("[Orchestrator] Per-agent reports: %s", agent_report_paths)
        except Exception as e:
            logger.warning("[Orchestrator] Per-agent reports failed (non-fatal): %s", e)

        # 9c. Screenshot manifest
        try:
            ss_agent.save_manifest()
        except Exception:
            pass

        # ── Step 10: ESM session end + ESLL insights ──────────────────────────
        _emit(mongo, ESMEventType.SAI_SESSION_END, run_id, persona, workflow,
              overall_status=overall_status,
              artifact_run_record_path=result.get("html_report_path", ""))

        try:
            esll.process_run_verdict(
                run_id=run_id, persona=persona, workflow=workflow,
                verdict=verdict,
                conversation_log=sai_result["conversation_log"],
                canvas_evidence=canvas_evidence,
                recovery_log=healer.recovery_log,
            )
        except Exception as e:
            logger.warning("[Orchestrator] ESLL failed (non-fatal): %s", e)

        # ── Summary log ───────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("RUN COMPLETE: %s", run_id)
        logger.info("Status: %s | Process:%s/10 | Output:%s/10 | Visual:%s/10",
                    overall_status,
                    verdict.get("process_score"),
                    verdict.get("output_score"),
                    verdict.get("visual_score"))
        logger.info("Copilot health: %s (%s/10) | Canvas correctness: %s (%s/10)",
                    health_checks["copilot"]["status"], health_checks["copilot"]["score"],
                    health_checks["canvas"]["status"],  health_checks["canvas"]["score"])
        logger.info("Screenshots captured: %d", len(ss_agent.manifest))
        logger.info("Recovery events:      %d", len(healer.recovery_log))
        logger.info("Canvas timeline:      %d events", len(canvas_monitor.get_timeline()))
        if verdict.get("improvement_flags"):
            logger.info("Improvement flags:")
            for flag in verdict["improvement_flags"]:
                logger.info("  • %s", flag)
        logger.info("=" * 60)

        return result

    except Exception as e:
        logger.error("RUN ERROR in %s: %s", run_id, e, exc_info=True)
        err_ss_path = f"screenshots/{run_id}_fatal_error.png"
        try:
            await page.screenshot(path=err_ss_path)
        except Exception:
            pass
        recorder.record("error", f"Unexpected error: {e}", success=False,
                        screenshot_path=err_ss_path if os.path.exists(err_ss_path) else None)
        result = recorder.finish(overall_status="error")
        _finalise_run(pg, analysis_run, result, verdict={}, screenshot_paths=[])
        _emit(mongo, ESMEventType.SAI_STEP_FAILED, run_id, persona, workflow, error=str(e))
        return result

    finally:
        if change_detector is not None:
            change_detector.stop()
        if ss_agent is not None:
            ss_agent.stop()
        if canvas_monitor is not None:
            canvas_monitor.stop()
        await browser_manager.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _duration(start_iso: Optional[str], end_iso: Optional[str]) -> str:
    """Return human-readable duration string, e.g. '3m 42s'."""
    if not start_iso or not end_iso:
        return "N/A"
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        # strip timezone suffix for simple parsing
        start_iso = start_iso[:26].replace("Z", "")
        end_iso   = end_iso[:26].replace("Z", "")
        start_dt  = datetime.fromisoformat(start_iso)
        end_dt    = datetime.fromisoformat(end_iso)
        secs      = int((end_dt - start_dt).total_seconds())
        if secs < 0:
            return "N/A"
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if m else f"{s}s"
    except Exception:
        return "N/A"


# ── Postgres finaliser helper ──────────────────────────────────────────────────

def _finalise_run(
    pg: PostgresStore,
    run: AnalysisRun,
    result: dict,
    verdict: dict,
    screenshot_paths: list,
) -> None:
    """Update AnalysisRun with final scores and persist to Postgres."""
    run.finished_at        = datetime.now(timezone.utc).isoformat()
    run.overall_status     = result.get("overall_status", "error")
    run.correctness_score  = int(verdict.get("correctness_score", 0) or 0)
    run.process_score      = int(verdict.get("process_score", 0) or 0)
    run.output_score       = int(verdict.get("output_score", 0) or 0)
    run.psi_questions_rating = str(verdict.get("psi_questions_rating", ""))
    run.agents_that_ran    = verdict.get("agents_that_ran", [])
    run.improvement_flags  = verdict.get("improvement_flags", [])
    run.missing_elements   = verdict.get("missing_elements", [])
    run.judge_rationale    = str(verdict.get("rationale", ""))
    run.artifact_run_record_path = result.get("report_path", "")
    run.screenshot_paths   = screenshot_paths
    try:
        pg.upsert_analysis_run(run)
    except Exception as e:
        logger.warning("[Orchestrator] Could not persist analysis run to Postgres: %s", e)


# ── Batch run ─────────────────────────────────────────────────────────────────

async def run_all_workflows(headless: bool = False) -> list[dict]:
    """
    Run every workflow grouped by persona.

    Order of execution:
      For each persona (in registry order):
        For each workflow that belongs to that persona:
          1. Open a fresh browser + new workspace
          2. Complete the full SAI conversation loop
          3. Navigate to canvas, click every agent tab + all 7 sub-tabs
          4. LLM judge evaluates all canvas sub-tab content
          5. Generate full HTML report for this run
          6. Close the browser
        → Only after ALL of one persona's workflows are done do we move
          to the next persona.

    Shares infrastructure (DB connections) across the whole batch.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    infra = _build_infrastructure()
    mongo, pg, audit, obj, esll, hitl, metrics = infra

    all_workflows = get_all_workflows()

    # Build an ordered list of (persona_id, [workflows]) preserving registry order
    seen_personas: list[str] = []
    persona_workflows: dict[str, list[dict]] = {}
    for wf in all_workflows:
        pid = wf["personas"][0]
        if pid not in persona_workflows:
            persona_workflows[pid] = []
            seen_personas.append(pid)
        persona_workflows[pid].append(wf)

    total_workflows = len(all_workflows)
    total_personas  = len(seen_personas)
    logger.info(
        "Starting batch run: %d personas, %d workflows total",
        total_personas, total_workflows,
    )

    results: list[dict] = []
    run_counter = 0

    for persona_idx, persona_id in enumerate(seen_personas, 1):
        persona = load_persona(persona_id)
        if not persona:
            logger.warning("Persona %s not found — skipping all its workflows", persona_id)
            continue

        workflows_for_persona = persona_workflows[persona_id]
        logger.info(
            "━━━ Persona %d/%d: %s (%s) — %d workflow(s) ━━━",
            persona_idx, total_personas,
            persona.get("name", persona_id), persona_id,
            len(workflows_for_persona),
        )

        for wf_idx, workflow in enumerate(workflows_for_persona, 1):
            run_counter += 1
            logger.info(
                "  [%d/%d] Workflow %d/%d for %s: %s (%s)",
                run_counter, total_workflows,
                wf_idx, len(workflows_for_persona),
                persona_id, workflow["title"], workflow["id"],
            )

            # Every workflow run gets its own fresh browser instance, fresh
            # cookies, cleared localStorage, and a brand-new workspace.
            # run_single_workflow creates and destroys BrowserManager internally
            # so no session state leaks between runs.
            logger.info(
                "  ↳ Opening NEW browser + NEW workspace for %s / %s",
                persona_id, workflow["id"],
            )
            result = await run_single_workflow(
                persona, workflow, headless=headless, infra=infra
            )
            results.append(result)

            logger.info(
                "  ✓ Done: %s | status=%s | process=%s/10 | output=%s/10 | url_after_login=%s",
                workflow["id"],
                result.get("overall_status"),
                result.get("judge_verdict", {}).get("process_score", "?"),
                result.get("judge_verdict", {}).get("output_score", "?"),
                result.get("steps", [{}])[1].get("notes", "?") if result.get("steps") else "?",
            )

            # Wait for the browser process to fully terminate before launching
            # the next one — prevents port/GPU resource conflicts on Windows.
            if wf_idx < len(workflows_for_persona):
                logger.info("  … waiting 10s before next workflow run …")
                await asyncio.sleep(10)

        logger.info(
            "━━━ Persona %s complete (%d/%d workflows done) ━━━",
            persona_id, run_counter, total_workflows,
        )

        # Longer pause between personas — new persona = new browser = new workspace
        if persona_idx < total_personas:
            logger.info(
                "  … waiting 15s before starting next persona (%s) …",
                seen_personas[persona_idx] if persona_idx < len(seen_personas) else "done",
            )
            await asyncio.sleep(15)

    # ── Post-batch: HITL housekeeping ──────────────────────────────────
    try:
        hitl.process_overdue_items()
    except Exception as e:
        logger.warning("[Orchestrator] HITL overdue processing failed: %s", e)

    # ── Post-batch: metrics dashboard ─────────────────────────────────
    try:
        metrics.print_dashboard()
    except Exception as e:
        logger.warning("[Orchestrator] Metrics dashboard failed: %s", e)

    # ── Post-batch: HTML batch index ───────────────────────────────────
    try:
        # Build persona_map from loaded results for the index page
        persona_map: dict[str, dict] = {}
        for r in results:
            pid = r.get("persona_id", "")
            if pid and pid not in persona_map:
                p = load_persona(pid)
                if p:
                    persona_map[pid] = p
        index_path = generate_batch_index(results, persona_map)
        logger.info("[Orchestrator] Batch index: %s", index_path)
    except Exception as e:
        logger.warning("[Orchestrator] Batch index generation failed (non-fatal): %s", e)

    # ── Batch summary JSON ─────────────────────────────────────────────
    summary_path = os.path.join(REPORTS_DIR, "batch_summary.json")
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "pass":    sum(1 for r in results if r.get("overall_status") == "pass"),
        "partial": sum(1 for r in results if r.get("overall_status") == "partial"),
        "fail":    sum(1 for r in results if r.get("overall_status") == "fail"),
        "error":   sum(1 for r in results if r.get("overall_status") == "error"),
        "runs": [
            {
                "run_id":        r.get("run_id"),
                "persona":       r.get("persona_id"),
                "workflow":      r.get("workflow_id"),
                "status":        r.get("overall_status"),
                "process_score": r.get("judge_verdict", {}).get("process_score"),
                "output_score":  r.get("judge_verdict", {}).get("output_score"),
            }
            for r in results
        ],
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("Batch summary saved: %s", summary_path)
    return results


# ── Entry points ──────────────────────────────────────────────────────────────

async def run_test(
    persona_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    run_all: bool = False,
    headless: bool = False,
    metrics_only: bool = False,
) -> None:
    """Top-level entry point called from main.py."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)

    if metrics_only:
        _, pg, _, _, _, hitl, metrics = _build_infrastructure()
        metrics.print_dashboard()
        return

    if run_all:
        await run_all_workflows(headless=headless)
        return

    # Resolve persona
    if persona_id:
        persona = load_persona(persona_id)
        if not persona:
            logger.error("Persona '%s' not found in personas directory", persona_id)
            return
    else:
        import random, re as _re
        personas_dir = os.path.join(os.path.dirname(__file__), "personas")
        files = [f for f in os.listdir(personas_dir)
                 if f.endswith((".json", ".yaml", ".yml"))]
        if not files:
            logger.error("No persona files found")
            return
        persona_id = _re.sub(r'\.(json|yaml|yml)$', '', random.choice(files))
        persona = load_persona(persona_id)

    # Resolve workflow
    if workflow_id:
        workflow = get_workflow_by_id(workflow_id)
        if not workflow:
            logger.error("Workflow '%s' not found", workflow_id)
            return
    else:
        workflow = get_random_workflow(persona_id=persona.get("id"))

    await run_single_workflow(persona=persona, workflow=workflow, headless=headless)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAI Browser Testing Agent")
    parser.add_argument("--persona",  type=str,        help="Persona ID (e.g. PM-1)")
    parser.add_argument("--workflow", type=str,        help="Workflow ID (e.g. WF-PM-001)")
    parser.add_argument("--all",      action="store_true", help="Run all workflows sequentially")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--metrics",  action="store_true", help="Print metrics dashboard and exit")
    args = parser.parse_args()

    asyncio.run(run_test(
        persona_id=args.persona,
        workflow_id=args.workflow,
        run_all=args.all,
        headless=args.headless,
        metrics_only=args.metrics,
    ))
