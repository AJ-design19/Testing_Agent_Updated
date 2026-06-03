"""
Surface Runner — drives the full 8-surface SAI web UI test.

Execution flow per surface:
  1. Login (or reuse existing session if keep_session=True)
  2. Navigate to the surface's starting URL
  3. Execute nav_sequence (top-nav clicks to reach the surface)
  4. For each step in surface.steps:
       a. Begin IOLogger entry
       b. Execute via AgenticExecutor (agentic or Playwright as appropriate)
       c. Finish IOLogger entry
  5. Collect all viewport screenshots + DOM snapshots
  6. Produce per-surface report

After all surfaces:
  - Save combined io_log.json
  - Generate cross-surface HTML index
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from app.browser.browser_manager import BrowserManager
from app.browser.login import login
from app.browser.sai_navigator import SAINavigator
from app.browser.sai_handler import SAIHandler
from app.browser.screenshot_agent import ScreenshotAgent
from app.conversation.answer_engine import AnswerEngine
from app.surfaces.surface_definitions import ALL_SURFACES, get_surface_by_id
from app.surfaces.io_logger import IOLogger
from app.surfaces.agentic_executor import AgenticExecutor
from app.surfaces.surface_report import SurfaceReportGenerator

load_dotenv()
logger = logging.getLogger(__name__)

REPORTS_DIR = "reports/surface_runs"


# ── Single surface run ────────────────────────────────────────────────────────

async def run_surface(
    surface: dict,
    persona: dict,
    page,
    run_id: str,
    screenshot_agent: Optional[ScreenshotAgent] = None,
) -> dict:
    """
    Execute all steps for one surface. Returns a result dict with
    surface_id, status, step_records, io_log_path, report_path.
    """
    surface_id = surface["id"]
    logger.info("=" * 60)
    logger.info("SURFACE: %s — %s", surface_id, surface["name"])
    logger.info("=" * 60)

    io_log = IOLogger(run_id=run_id, surface_id=surface_id,
                      persona_id=persona.get("id", ""))

    # Build SAI handler + answer engine for this surface run
    answer_engine = AnswerEngine(persona=persona, workflow={
        "id": surface_id,
        "title": surface["name"],
        "initial_prompt": "",
        "expected_agents": surface.get("tabs", []),
        "steps": [s["description"] for s in surface.get("steps", [])],
        "evaluation_criteria": {},
    })
    navigator   = SAINavigator(page)
    sai_handler = SAIHandler(
        page=page,
        persona=persona,
        answer_engine=answer_engine,
        navigator=navigator,
        run_id=run_id,
        screenshot_agent=screenshot_agent,
        esll_service=None,
        journey=None,
    )

    executor = AgenticExecutor(
        page=page,
        run_id=run_id,
        persona=persona,
        io_logger=io_log,
        sai_handler=sai_handler,
        answer_engine=answer_engine,
    )

    # ── Nav sequence ──────────────────────────────────────────────────────────
    for nav_item in surface.get("nav_sequence", []):
        nav_sels = nav_item.get("selectors", [])
        nav_label = nav_item.get("label", "nav")
        for sel in nav_sels:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.hover()
                    await asyncio.sleep(0.15)
                    await el.click()
                    await asyncio.sleep(1.0)
                    logger.info("[SurfaceRunner] Nav click: %s via %s", nav_label, sel)
                    break
            except Exception:
                continue

    # ── Execute each step ─────────────────────────────────────────────────────
    step_records: list[dict] = []
    for step in surface.get("steps", []):
        try:
            record = await executor.execute_step(step, surface_id)
            step_records.append(record)
            logger.info("[SurfaceRunner] %s → %s", step["step_id"], record.get("status"))
        except Exception as e:
            logger.error("[SurfaceRunner] Step %s crashed: %s", step.get("step_id"), e, exc_info=True)
            step_records.append({
                "step_id": step.get("step_id"),
                "status": "fail",
                "notes": str(e),
            })

        # Pause between steps
        await asyncio.sleep(0.5)

    # ── Persist IO log ────────────────────────────────────────────────────────
    io_log_path = io_log.save_to_file()
    summary = io_log.get_summary()

    # ── Generate per-surface HTML report ──────────────────────────────────────
    report_path = ""
    try:
        report_path = SurfaceReportGenerator().generate(
            surface=surface,
            persona=persona,
            run_id=run_id,
            step_records=step_records,
            summary=summary,
        )
    except Exception as e:
        logger.warning("[SurfaceRunner] Report generation failed: %s", e)

    overall = (
        "pass"    if summary["failed"] == 0
        else "partial" if summary["passed"] > 0
        else "fail"
    )

    logger.info(
        "[SurfaceRunner] %s done — pass=%d fail=%d partial=%d escalated=%d",
        surface_id, summary["passed"], summary["failed"],
        summary["partial"], summary["escalated"],
    )

    return {
        "surface_id":    surface_id,
        "surface_name":  surface["name"],
        "run_id":        run_id,
        "persona_id":    persona.get("id", ""),
        "status":        overall,
        "summary":       summary,
        "step_records":  step_records,
        "io_log_path":   io_log_path,
        "report_path":   report_path,
    }


# ── Full 8-surface run ────────────────────────────────────────────────────────

async def run_all_surfaces(
    persona: Optional[dict] = None,
    surface_ids: Optional[list] = None,
    headless: bool = False,
) -> list[dict]:
    """
    Execute all (or a subset of) surfaces sequentially sharing one browser
    session after login.

    Returns list of surface result dicts.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs("screenshots/surfaces", exist_ok=True)

    if persona is None:
        persona = {
            "id":   "QA-SURFACE-1",
            "name": "QA Surface Tester",
            "role": "QA Engineer",
            "goals": ["Validate all SAI web UI surfaces"],
            "background": "Automated surface testing agent",
        }

    run_id = (
        f"SURF_RUN_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        f"_{uuid.uuid4().hex[:6]}"
    )
    logger.info("Starting surface test run: %s", run_id)

    surfaces = (
        [get_surface_by_id(sid) for sid in surface_ids]
        if surface_ids
        else ALL_SURFACES
    )

    browser_manager = BrowserManager()
    page = await browser_manager.start(headless=headless)

    ss_agent = ScreenshotAgent(page, run_id=run_id, interval_seconds=5.0,
                               workflow_id=run_id)
    ss_agent.start_background_capture()

    results: list[dict] = []

    try:
        # Login once
        logger.info("[SurfaceRunner] Logging in…")
        await login(page)
        logger.info("[SurfaceRunner] Login complete — url=%s", page.url)

        base_url = os.getenv("ADYA_URL", "").rstrip("/")

        for idx, surface in enumerate(surfaces, 1):
            logger.info(
                "[SurfaceRunner] ━━━ Surface %d/%d: %s ━━━",
                idx, len(surfaces), surface["name"],
            )

            # Navigate to surface starting URL
            surface_url = base_url + surface.get("url_path", "/orchestrator")
            try:
                await page.goto(surface_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.warning("[SurfaceRunner] goto %s failed: %s", surface_url, e)

            result = await run_surface(
                surface=surface,
                persona=persona,
                page=page,
                run_id=run_id,
                screenshot_agent=ss_agent,
            )
            results.append(result)

            # Pause between surfaces
            if idx < len(surfaces):
                await asyncio.sleep(3.0)

    except Exception as e:
        logger.error("[SurfaceRunner] Fatal error in surface run: %s", e, exc_info=True)
    finally:
        ss_agent.stop()
        await browser_manager.close()

    # ── Write combined results index ──────────────────────────────────────────
    _write_run_index(run_id, results)

    logger.info(
        "[SurfaceRunner] All surfaces complete — pass=%d fail=%d partial=%d",
        sum(1 for r in results if r.get("status") == "pass"),
        sum(1 for r in results if r.get("status") == "fail"),
        sum(1 for r in results if r.get("status") == "partial"),
    )
    return results


# ── Run index ─────────────────────────────────────────────────────────────────

def _write_run_index(run_id: str, results: list[dict]) -> str:
    import json
    index_path = os.path.join(REPORTS_DIR, f"{run_id}_index.json")
    payload = {
        "run_id":    run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total":     len(results),
        "pass":      sum(1 for r in results if r.get("status") == "pass"),
        "fail":      sum(1 for r in results if r.get("status") == "fail"),
        "partial":   sum(1 for r in results if r.get("status") == "partial"),
        "surfaces":  [
            {
                "surface_id":   r.get("surface_id"),
                "surface_name": r.get("surface_name"),
                "status":       r.get("status"),
                "report_path":  r.get("report_path"),
                "io_log_path":  r.get("io_log_path"),
                "summary":      r.get("summary"),
            }
            for r in results
        ],
    }
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info("[SurfaceRunner] Run index: %s", index_path)
    return index_path
