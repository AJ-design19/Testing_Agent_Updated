"""
Standalone report generator.

Reads existing run record JSON files from reports/ and regenerates HTML reports
without running any browser sessions.

Usage:
  # Regenerate all reports in reports/
  python scripts/generate_reports.py

  # Regenerate a specific run
  python scripts/generate_reports.py --run-id PM-1_WF-PM-001_20240101_120000_abc123

  # Also regenerate the batch index
  python scripts/generate_reports.py --index
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.reporting.run_report import RunReportGenerator, generate_batch_index
from app.workflows.workflow_registry import load_persona

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"


def load_run_record(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Regenerate HTML reports from JSON run records")
    parser.add_argument("--run-id", type=str, help="Specific run_id to regenerate")
    parser.add_argument("--index",  action="store_true", help="Also regenerate batch index")
    args = parser.parse_args()

    gen = RunReportGenerator()
    results = []

    if args.run_id:
        json_path = os.path.join(REPORTS_DIR, f"{args.run_id}.json")
        if not os.path.exists(json_path):
            logger.error("Run record not found: %s", json_path)
            sys.exit(1)
        record = load_run_record(json_path)
        persona = load_persona(record.get("persona_id", "")) or {}
        path = gen.generate(record, persona_data=persona)
        logger.info("Report: %s", path)
        results = [record]
    else:
        # Regenerate all run records (exclude batch_summary.json and existing _report.html sources)
        json_files = [
            f for f in os.listdir(REPORTS_DIR)
            if f.endswith(".json") and not f.startswith("batch_") and not f.startswith("metrics_")
            and not f.startswith("calibration_")
        ]
        logger.info("Found %d run record files", len(json_files))
        for fname in sorted(json_files):
            try:
                record = load_run_record(os.path.join(REPORTS_DIR, fname))
                persona = load_persona(record.get("persona_id", "")) or {}
                path = gen.generate(record, persona_data=persona)
                logger.info("  ✓ %s", path)
                results.append(record)
            except Exception as e:
                logger.warning("  ✗ %s: %s", fname, e)

    if args.index or not args.run_id:
        persona_map = {}
        for r in results:
            pid = r.get("persona_id", "")
            if pid and pid not in persona_map:
                p = load_persona(pid)
                if p:
                    persona_map[pid] = p
        index_path = generate_batch_index(results, persona_map)
        logger.info("Index: %s", index_path)


if __name__ == "__main__":
    main()
