"""
SAI Web UI Surface Test — entry point.

Runs the 8-surface SAI web UI test suite (or a targeted subset).

Usage:
  python -m app.main_surface                          # run all 8 surfaces
  python -m app.main_surface --surfaces SURF-01,SURF-03  # specific surfaces
  python -m app.main_surface --area agent_studio      # by area name
  python -m app.main_surface --headless               # headless browser
  python -m app.main_surface --list                   # list available surfaces

Surfaces:
  SURF-01  onboarding           Login, workspace creation, SAI first chat
  SURF-02  ai_marketplace       Browse, search, clone, fork, submit for review
  SURF-03  agent_studio         Kick off network run, view all agent/sub-tab results
  SURF-04  ai_elt_analytics     Ingest CSV, analysis, chart, export report
  SURF-05  agp_governance       Policy check, audit trail, export report
  SURF-06  admin                Provision workspace, AGP rules, credentials, budget
  SURF-07  content_generation   Draft messages, KB search, executive summary
  SURF-08  esll_lab             Review learnings, approve/reject, redact, publish
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.surfaces.surface_definitions import ALL_SURFACES, get_surface_by_id, get_surface_by_area
from app.surfaces.surface_runner import run_all_surfaces

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("reports/surface_test.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def _main():
    import argparse

    os.makedirs("reports", exist_ok=True)

    parser = argparse.ArgumentParser(
        description="SAI Web UI Surface Test — validates all 8 capability areas",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--surfaces", type=str, default=None,
        help="Comma-separated surface IDs to run, e.g. SURF-01,SURF-03",
    )
    parser.add_argument(
        "--area", type=str, default=None,
        help="Run a single surface by area name, e.g. agent_studio",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available surfaces and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable SAI surfaces:\n")
        for s in ALL_SURFACES:
            print(f"  {s['id']:10s}  {s['area']:25s}  {s['name']}")
        print()
        return

    # Resolve surface IDs
    surface_ids = None
    if args.area:
        try:
            surface = get_surface_by_area(args.area)
            surface_ids = [surface["id"]]
        except KeyError as e:
            logger.error("Unknown area: %s", args.area)
            sys.exit(1)
    elif args.surfaces:
        surface_ids = [s.strip() for s in args.surfaces.split(",") if s.strip()]

    results = await run_all_surfaces(
        surface_ids=surface_ids,
        headless=args.headless,
    )

    # Print summary table
    print("\n" + "=" * 72)
    print(f"  SAI SURFACE TEST COMPLETE")
    print("=" * 72)
    print(f"  {'Surface':<12} {'Name':<32} {'Status':<10} {'Pass%':<8}")
    print(f"  {'-'*12} {'-'*32} {'-'*10} {'-'*8}")
    for r in results:
        s = r.get("summary", {})
        print(
            f"  {r.get('surface_id',''):<12} "
            f"{r.get('surface_name','')[:31]:<32} "
            f"{r.get('status','?'):<10} "
            f"{s.get('pass_rate',0):.0f}%"
        )
    total_pass    = sum(1 for r in results if r.get("status") == "pass")
    total_fail    = sum(1 for r in results if r.get("status") == "fail")
    total_partial = sum(1 for r in results if r.get("status") == "partial")
    print("=" * 72)
    print(f"  TOTAL: {len(results)}  PASS: {total_pass}  FAIL: {total_fail}  PARTIAL: {total_partial}")
    print("=" * 72)

    # Print report paths
    print("\nReports:")
    for r in results:
        if r.get("report_path"):
            print(f"  {r['surface_id']}: {r['report_path']}")
    print()


if __name__ == "__main__":
    asyncio.run(_main())
