"""
Production-grade autonomous AI Browser Testing and Evaluation Agent for Adya SAI.

Architecture (multi-agent):
  ┌─ Planner        ── selects persona + workflow, plans the test run
  ├─ Browser Op     ── login, navigation, event-driven waiting
  ├─ Psi Handler    ── full conversational loop with SAI's copilot
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
from app.browser.psi_handler import PsiHandler
from app.browser.screenshot_agent import ScreenshotAgent
from app.browser.canvas_monitor import CanvasMonitor
from app.browser.self_healer import SelfHealer
from app.conversation.answer_engine import AnswerEngine
from app.recording.action_recorder import ActionRecorder
from app.judge.llm_judge import LLMJudge
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
    Execute one full test run: login → prompt → Psi loop → canvas capture → judge
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
    verdict: dict                        = {}

    try:
        # ── Step 1: Login ──────────────────────────────────────────────────────
        recorder.record("login", "Navigating to SAI and logging in")
        await login(page)
        recorder.record("login", "Login completed", success=True,
                        notes=f"URL: {page.url}")

        # ── Initialise all agents ───────────────────────────────────────────────
        navigator      = SAINavigator(page)
        answer_eng     = AnswerEngine(persona=persona, workflow=workflow)
        ss_agent       = ScreenshotAgent(page, run_id=run_id, interval_seconds=5.0)
        canvas_monitor = CanvasMonitor(page, screenshot_agent=ss_agent)
        healer         = SelfHealer(page, run_id=run_id)
        psi_handler    = PsiHandler(
            page=page, persona=persona,
            answer_engine=answer_eng, navigator=navigator,
        )
        judge         = LLMJudge()

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
        )
        sent = await psi_handler.send_initial_prompt(initial_prompt)

        if not sent:
            err_ss = await ss_agent.capture_error("prompt_send_failed")
            recorder.record("send_prompt", "Failed to send initial prompt",
                            success=False, screenshot_path=err_ss)
            _emit(mongo, ESMEventType.SAI_STEP_FAILED, run_id, persona, workflow,
                  step="send_initial_prompt")
            result = recorder.finish(overall_status="error")
            _finalise_run(pg, analysis_run, result, verdict={}, screenshot_paths=[])
            return result

        recorder.record("send_prompt", "Initial prompt submitted")
        await ss_agent.capture(label="prompt_submitted", event_type="prompt")

        # ── Step 4: Psi conversation loop ──────────────────────────────────────
        recorder.record("psi_loop", "Starting Psi conversation loop")
        psi_result = await psi_handler.run_conversation_loop(max_rounds=12)

        # Capture screenshot after each Psi exchange
        await ss_agent.capture(label="psi_loop_done", event_type="psi")

        for entry in psi_result.get("conversation_log", []):
            role  = entry.get("role", "")
            text  = entry.get("text") or entry.get("content") or ""
            if role == "assistant":
                _emit(mongo, ESMEventType.SAI_PSI_QUESTION, run_id, persona, workflow,
                      message=text[:500])
            elif role == "user":
                _emit(mongo, ESMEventType.SAI_PSI_ANSWER, run_id, persona, workflow,
                      message=text[:500])
            tokens = entry.get("tokens", 0)
            if tokens:
                metrics.record_token_usage(run_id, tokens)

        recorder.record(
            "psi_loop",
            f"Psi complete: {psi_result['question_count']} Qs answered | "
            f"completion_detected={psi_result['completion_detected']} | "
            f"rounds={psi_result['rounds']}",
        )
        recorder.set_psi_conversation(psi_result["conversation_log"])

        # ── Step 5: Wait for canvas + start canvas monitor ────────────────────
        recorder.record("canvas_wait", "Waiting for canvas to appear after Psi handoff")
        canvas_appeared = await navigator.wait_for_canvas(timeout_ms=30000)

        if not canvas_appeared:
            # Self-healer: try to recover missing canvas
            recovered = await healer.recover_missing_canvas(navigator)
            recorder.record("canvas_wait", f"Canvas recovery attempted: {recovered}",
                            success=recovered)

        # Handoff screenshot
        handoff_ss = await ss_agent.capture(label="psi_handoff", event_type="handoff")
        handoff_uri = obj.store_screenshot(run_id, handoff_ss or "", label="psi_handoff")
        _emit(mongo, ESMEventType.SAI_STEP_COMPLETED, run_id, persona, workflow,
              step="psi_handoff", artifact_uri=handoff_uri)
        recorder.record("screenshot", "Psi→Canvas handoff screenshot",
                        screenshot_path=handoff_ss)

        # Start canvas background monitor AFTER handoff
        canvas_monitor.start()

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
                agent_ev = await canvas_monitor.capture_agent_evidence(agent_name, navigator)
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

                recorder.record(
                    "canvas_agent",
                    f"Agent '{agent_name}': appeared={appeared}, "
                    f"screenshots={len(ss_list)}, state={agent_ev.get('state')}",
                    agent_tab=agent_name,
                    success=appeared,
                    screenshot_path=ss_list[-1]["path"] if ss_list else None,
                )
                for sub_tab, content in agent_ev.get("sub_tabs", {}).items():
                    recorder.record(
                        "canvas_subtab",
                        f"Read sub-tab {sub_tab}",
                        agent_tab=agent_name,
                        sub_tab=sub_tab,
                        content_snapshot=(content or "")[:300],
                    )
            except Exception as ae:
                logger.error("[Orchestrator] Error capturing agent %s: %s", agent_name, ae)
                err_ss = await ss_agent.capture_error(f"agent_{agent_name}_error",
                                                       agent=agent_name)
                recorder.record("error", f"Agent {agent_name} capture error: {ae}",
                                agent_tab=agent_name, success=False,
                                screenshot_path=err_ss)

        # Build canvas_evidence in the format CanvasReader / reports expect
        canvas_evidence = {
            "run_id":           run_id,
            "flow_view":        await _capture_flow_view(navigator, ss_agent),
            "agents":           agents_evidence,
            "canvas_timeline":  canvas_monitor.get_timeline(),
            "appeared_agents":  canvas_monitor.get_appeared_agents(),
            "final_screenshot": await ss_agent.capture(label="canvas_final", event_type="final"),
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        recorder.set_canvas_evidence(canvas_evidence)

        # ── Step 7: LLM Judge evaluation ──────────────────────────────────────
        recorder.record("judge", "Running LLM Judge + Visual Judge evaluation")
        verdict = judge.evaluate(
            persona=persona,
            workflow=workflow,
            psi_conversation=psi_result["conversation_log"],
            canvas_evidence=canvas_evidence,
            step_log=recorder.get_record()["steps"],
            recovery_log=healer.recovery_log,
            canvas_timeline=canvas_monitor.get_timeline(),
        )
        recorder.set_judge_verdict(verdict)
        metrics.record_token_usage(run_id, verdict.get("tokens_used", 2000))

        overall_status = LLMJudge.verdict_to_overall_status(verdict)
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
                psi_conversation=psi_result["conversation_log"],
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
                conversation_log=psi_result["conversation_log"],
                canvas_evidence=canvas_evidence,
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
        if ss_agent is not None:
            ss_agent.stop()
        if canvas_monitor is not None:
            canvas_monitor.stop()
        await browser_manager.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _capture_flow_view(navigator: "SAINavigator", ss_agent: "ScreenshotAgent") -> dict:
    """
    Capture the Flow View panel state (the overall workflow plan shown by SAI
    before individual canvas agents start).  Returns a dict compatible with
    the canvas_evidence["flow_view"] schema expected by LLMJudge and reports.
    """
    flow_data: dict = {"appeared": False, "planned_steps": "", "screenshot": None}
    try:
        flow_selectors = [
            '[data-testid="flow-view"]',
            '[class*="flow-view"]',
            '[class*="FlowView"]',
            '[aria-label*="flow" i]',
            '[class*="workflow-plan"]',
        ]
        page = navigator.page
        appeared = False
        for sel in flow_selectors:
            try:
                await page.wait_for_selector(sel, timeout=3000)
                appeared = True
                break
            except Exception:
                continue

        flow_data["appeared"] = appeared
        if appeared:
            # Try to read textual plan from the flow view
            for sel in flow_selectors:
                try:
                    text = await page.inner_text(sel)
                    if text.strip():
                        flow_data["planned_steps"] = text.strip()[:800]
                        break
                except Exception:
                    continue

        # Always capture a screenshot of the current state
        ss_path = await ss_agent.capture(label="flow_view", event_type="canvas")
        flow_data["screenshot"] = ss_path
    except Exception as e:
        logger.warning("[Orchestrator] _capture_flow_view error (non-fatal): %s", e)

    return flow_data


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
    Run every workflow in the registry sequentially.
    Shares infrastructure (DB connections) across the whole batch.
    Prints metrics dashboard and processes overdue HITL items at the end.
    Returns all run records.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    infra = _build_infrastructure()
    mongo, pg, audit, obj, esll, hitl, metrics = infra

    workflows = get_all_workflows()
    logger.info("Starting batch run: %d workflows", len(workflows))

    results = []
    for i, workflow in enumerate(workflows, 1):
        persona_id = workflow["personas"][0]
        persona = load_persona(persona_id)
        if not persona:
            logger.warning("Persona %s not found, skipping workflow %s",
                           persona_id, workflow["id"])
            continue

        logger.info("[%d/%d] Running: %s with persona %s",
                    i, len(workflows), workflow["title"], persona_id)
        result = await run_single_workflow(
            persona, workflow, headless=headless, infra=infra
        )
        results.append(result)

        # Brief pause to avoid session overlap
        await asyncio.sleep(5)

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
        import random
        personas_dir = os.path.join(os.path.dirname(__file__), "personas")
        files = [f for f in os.listdir(personas_dir) if f.endswith(".json")]
        if not files:
            logger.error("No persona files found")
            return
        persona_id = random.choice(files).replace(".json", "")
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
