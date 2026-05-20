"""
Metrics collector and dashboard (§14 — all four metric categories).

§14.1 Coverage metrics
  - Number of validated personas in the library
  - Number of validated journeys; v1 target = 200
  - Step-level reachability: % of journey steps completing to validation; target ≥ 95%
  - Capability coverage: % of SAI capability areas exercised; target = all areas

§14.2 Quality metrics
  - Hybrid-judge agreement with human adjudication; target ≥ 90%
  - False-positive rate (LLM-pass, human-fail); target ≤ 3%
  - HITL queue resolution time (median); target ≤ 24 hours

§14.3 Learning metrics
  - Insights in SAI's per-agent ESLL scope by insight_type
  - Insight injection hit rate
  - Tier-promotion velocity (session → user → project → enterprise → global)
  - Failure-mode reduction rate (post-fix recurrence; target measurable within 30 days)

§14.4 Cost metrics
  - Inference cost per nightly run (tokens × cost/token)
  - HITL adjudication cost (human-hours × loaded cost)
  - Storage cost growth per month

§14.5 Operational metrics
  - Nightly-run completion rate (trailing 30 days); target ≥ 95%
  - HITL queue depth (open items)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

METRICS_FILE = "reports/metrics_snapshot.json"

# v1 targets
JOURNEY_COUNT_TARGET = 200
STEP_REACHABILITY_TARGET = 0.95
JUDGE_AGREEMENT_TARGET = 0.90
FALSE_POSITIVE_TARGET = 0.03
HITL_RESOLUTION_HOURS_TARGET = 24
NIGHTLY_COMPLETION_RATE_TARGET = 0.95

# Cost constants (estimates; update for real hardware)
COST_PER_1K_TOKENS_USD = 0.002   # placeholder; override with COST_PER_1K_TOKENS env var


class MetricsCollector:
    """
    Aggregates metrics from all stores and produces a snapshot dict
    suitable for dashboards, CI checks, and Slack alerts.
    """

    def __init__(self, mongo_store=None, pg_store=None, hitl_manager=None):
        self.mongo = mongo_store
        self.pg = pg_store
        self.hitl = hitl_manager
        self._token_log: list[dict] = []   # [{run_id, tokens}]

    # ── §14.1 Coverage ────────────────────────────────────────────────────

    def coverage_metrics(self) -> dict:
        from app.workflows.journey_loader import load_all_journeys, list_capability_areas
        import glob

        # Validated personas
        personas_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "app", "personas",
        )
        persona_files = [f for f in os.listdir(personas_dir) if f.endswith(".json")] \
            if os.path.exists(personas_dir) else []

        # Validated journeys
        journeys = load_all_journeys(include_drafts=False)
        capability_areas_covered = list_capability_areas(journeys)

        # Step reachability: from analysis_runs
        total_steps = 0
        completed_steps = 0
        if self.pg and not self.pg._dry_run:
            runs = self.pg.get_run_summary()
            for r in runs:
                # Count overall as one step block
                total_steps += 1
                if r.get("overall_status") in ("pass", "partial"):
                    completed_steps += 1
        step_reachability = (completed_steps / total_steps) if total_steps else None

        return {
            "validated_persona_count": len(persona_files),
            "validated_journey_count": len(journeys),
            "journey_count_target": JOURNEY_COUNT_TARGET,
            "journey_count_pct": round(len(journeys) / JOURNEY_COUNT_TARGET * 100, 1),
            "step_reachability": step_reachability,
            "step_reachability_target": STEP_REACHABILITY_TARGET,
            "step_reachability_meets_target": (
                step_reachability >= STEP_REACHABILITY_TARGET if step_reachability else None
            ),
            "capability_areas_covered": sorted(capability_areas_covered),
            "capability_area_count": len(capability_areas_covered),
        }

    # ── §14.2 Quality ─────────────────────────────────────────────────────

    def quality_metrics(self) -> dict:
        # Load latest calibration result
        calibration = {}
        calib_file = "reports/calibration_results.json"
        if os.path.exists(calib_file):
            try:
                with open(calib_file) as f:
                    results = json.load(f)
                if results:
                    latest = results[-1]
                    calibration = {
                        "agreement_rate": latest.get("agreement_rate"),
                        "false_positive_rate": latest.get("false_positive_rate"),
                        "meets_agreement_target": latest.get("meets_agreement_target"),
                        "meets_fp_target": latest.get("meets_fp_target"),
                        "calibration_run_timestamp": latest.get("run_timestamp"),
                        "total_calibration_steps": latest.get("total_steps"),
                    }
            except Exception as e:
                logger.warning("[Metrics] Could not load calibration results: %s", e)

        # HITL resolution time
        hitl_depth = self.hitl.get_depth_metric() if self.hitl else {}

        return {
            "calibration": calibration,
            "agreement_target": JUDGE_AGREEMENT_TARGET,
            "fp_rate_target": FALSE_POSITIVE_TARGET,
            "hitl_queue_depth": hitl_depth,
            "hitl_resolution_hours_target": HITL_RESOLUTION_HOURS_TARGET,
        }

    # ── §14.3 Learning ────────────────────────────────────────────────────

    def learning_metrics(self) -> dict:
        if not self.mongo:
            return {"error": "mongo not available"}

        # Insight counts by type
        insight_counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}

        # We query the learning collection
        col = self.mongo._col(self.mongo.LEARNING_COLLECTION)
        if col is not None:
            try:
                pipeline = [
                    {"$match": {"source": "sai_test_harness"}},
                    {"$group": {"_id": {"type": "$insight_type", "tier": "$target_tier"},
                                "count": {"$sum": 1}}}
                ]
                for doc in col.aggregate(pipeline):
                    k = doc["_id"]
                    t = k.get("type", "unknown")
                    tier = k.get("tier", "unknown")
                    insight_counts[t] = insight_counts.get(t, 0) + doc["count"]
                    tier_counts[tier] = tier_counts.get(tier, 0) + doc["count"]
            except Exception as e:
                logger.warning("[Metrics] Aggregation failed: %s", e)

        return {
            "insight_counts_by_type": insight_counts,
            "insight_counts_by_tier": tier_counts,
            "total_insights": sum(insight_counts.values()),
        }

    # ── §14.4 Cost ────────────────────────────────────────────────────────

    def cost_metrics(self) -> dict:
        cost_per_1k = float(os.getenv("COST_PER_1K_TOKENS", str(COST_PER_1K_TOKENS_USD)))
        total_tokens = sum(e.get("tokens", 0) for e in self._token_log)
        total_cost_usd = (total_tokens / 1000) * cost_per_1k

        # Storage size of reports/ and artifacts/
        def dir_size_mb(path: str) -> float:
            total = 0
            if os.path.exists(path):
                for dp, _, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
            return round(total / (1024 * 1024), 2)

        return {
            "total_tokens_logged": total_tokens,
            "estimated_inference_cost_usd": round(total_cost_usd, 4),
            "cost_per_1k_tokens": cost_per_1k,
            "storage_reports_mb": dir_size_mb("reports"),
            "storage_artifacts_mb": dir_size_mb("artifacts"),
            "storage_screenshots_mb": dir_size_mb("screenshots"),
        }

    def record_token_usage(self, run_id: str, tokens: int) -> None:
        self._token_log.append({"run_id": run_id, "tokens": tokens,
                                "ts": datetime.now(timezone.utc).isoformat()})

    # ── §14.5 Operational ─────────────────────────────────────────────────

    def operational_metrics(self) -> dict:
        if not self.pg or self.pg._dry_run:
            return {"error": "postgres not available"}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        try:
            with self.pg._conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM analysis_runs WHERE started_at > %s", (cutoff,)
                )
                total_runs = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM analysis_runs WHERE started_at > %s AND overall_status != 'error'",
                    (cutoff,),
                )
                completed_runs = cur.fetchone()[0]
        except Exception as e:
            logger.warning("[Metrics] operational query failed: %s", e)
            return {"error": str(e)}

        completion_rate = round(completed_runs / total_runs, 4) if total_runs else None
        return {
            "runs_last_30_days": total_runs,
            "completed_last_30_days": completed_runs,
            "completion_rate": completion_rate,
            "completion_rate_target": NIGHTLY_COMPLETION_RATE_TARGET,
            "completion_rate_meets_target": (
                completion_rate >= NIGHTLY_COMPLETION_RATE_TARGET if completion_rate else None
            ),
        }

    # ── Full snapshot ─────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Collect all metric categories and return a single dict."""
        snap = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coverage": self.coverage_metrics(),
            "quality": self.quality_metrics(),
            "learning": self.learning_metrics(),
            "cost": self.cost_metrics(),
            "operational": self.operational_metrics(),
        }
        self._save_snapshot(snap)
        return snap

    def _save_snapshot(self, snap: dict) -> None:
        os.makedirs("reports", exist_ok=True)
        try:
            with open(METRICS_FILE, "w", encoding="utf-8") as f:
                json.dump(snap, f, indent=2, default=str)
        except Exception as e:
            logger.error("[Metrics] Could not save snapshot: %s", e)

    def print_dashboard(self) -> None:
        """Print a human-readable metrics dashboard to stdout."""
        snap = self.snapshot()
        c = snap.get("coverage", {})
        q = snap.get("quality", {})
        l = snap.get("learning", {})
        cost = snap.get("cost", {})
        op = snap.get("operational", {})

        print("\n" + "=" * 60)
        print("SAI TESTING HARNESS — METRICS DASHBOARD")
        print(f"As of: {snap['timestamp']}")
        print("=" * 60)

        print("\n§14.1 COVERAGE")
        print(f"  Personas in library:   {c.get('validated_persona_count')} / 15 target")
        print(f"  Journeys validated:    {c.get('validated_journey_count')} / {c.get('journey_count_target')} target ({c.get('journey_count_pct')}%)")
        sr = c.get('step_reachability')
        sr_str = f"{sr * 100:.1f}%" if sr else "N/A"
        print(f"  Step reachability:     {sr_str} (target ≥{STEP_REACHABILITY_TARGET*100:.0f}%)")
        print(f"  Capability areas:      {c.get('capability_area_count')} areas: {', '.join(c.get('capability_areas_covered', []))}")

        print("\n§14.2 QUALITY")
        calib = q.get("calibration", {})
        ar = calib.get('agreement_rate')
        fp = calib.get('false_positive_rate')
        print(f"  Judge agreement rate:  {f'{ar*100:.1f}%' if ar else 'N/A'} (target ≥{JUDGE_AGREEMENT_TARGET*100:.0f}%) {'✓' if calib.get('meets_agreement_target') else '✗' if ar else ''}")
        print(f"  False-positive rate:   {f'{fp*100:.1f}%' if fp else 'N/A'} (target ≤{FALSE_POSITIVE_TARGET*100:.0f}%) {'✓' if calib.get('meets_fp_target') else '✗' if fp else ''}")
        hitl = q.get("hitl_queue_depth", {})
        print(f"  HITL queue depth:      {hitl.get('open', 'N/A')} open ({hitl.get('overdue', 0)} overdue) {'⚠ ALERT' if hitl.get('alert') else ''}")

        print("\n§14.3 LEARNING")
        print(f"  Total insights:        {l.get('total_insights', 0)}")
        for t, cnt in l.get("insight_counts_by_type", {}).items():
            print(f"    {t}: {cnt}")
        for tier, cnt in l.get("insight_counts_by_tier", {}).items():
            print(f"    tier={tier}: {cnt}")

        print("\n§14.4 COST")
        print(f"  Inference cost (USD):  ${cost.get('estimated_inference_cost_usd', 0):.4f}")
        print(f"  Storage — reports:     {cost.get('storage_reports_mb', 0)} MB")
        print(f"  Storage — artifacts:   {cost.get('storage_artifacts_mb', 0)} MB")

        print("\n§14.5 OPERATIONAL")
        cr = op.get("completion_rate")
        cr_str = f"{cr*100:.1f}%" if cr else "N/A"
        print(f"  Nightly completion:    {cr_str} (target ≥{NIGHTLY_COMPLETION_RATE_TARGET*100:.0f}%) {'✓' if op.get('completion_rate_meets_target') else '✗' if cr else ''}")
        print(f"  Runs last 30 days:     {op.get('runs_last_30_days', 'N/A')}")
        print("=" * 60 + "\n")
