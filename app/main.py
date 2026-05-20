"""
Entry point for the SAI Browser Testing Agent.

Usage:
  python -m app.main                                      # random persona + workflow
  python -m app.main --persona PM-1                       # specific persona
  python -m app.main --persona PM-1 --workflow WF-PM-001  # specific persona + workflow
  python -m app.main --all                                # run all 30+ workflows
  python -m app.main --headless                           # headless browser mode
"""

import asyncio
import sys
import os

# Ensure project root is on path when run as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.orchestrator import run_test


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="SAI Browser Testing Agent — runs personas against SAI workflows"
    )
    parser.add_argument("--persona", type=str, default=None,
                        help="Persona ID to use (e.g. PM-1, ST-1, AI-1). Random if omitted.")
    parser.add_argument("--workflow", type=str, default=None,
                        help="Workflow ID to run (e.g. WF-PM-001). Random if omitted.")
    parser.add_argument("--all", action="store_true",
                        help="Run all workflows sequentially")
    parser.add_argument("--headless", action="store_true",
                        help="Launch browser in headless mode (no window)")
    args = parser.parse_args()

    asyncio.run(run_test(
        persona_id=args.persona,
        workflow_id=args.workflow,
        run_all=args.all,
        headless=args.headless,
    ))
