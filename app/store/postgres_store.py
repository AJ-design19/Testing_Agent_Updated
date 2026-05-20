"""
Postgres store (§12.1).

Tables:
  analysis_runs       — per-run pass/fail by persona × journey, signed artifacts
  candidate_insights  — staging area for project/enterprise/global writes pending HITL review
  hitl_queue          — Human-in-the-Loop review queue items

Falls back to JSON-file storage if psycopg2 / DATABASE_URL not available,
so the harness always runs even without a Postgres instance.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.store.models import (
    AnalysisRun, CandidateInsight, HitlQueueItem,
    ReviewStatus, ESMTier,
)

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False
    logger.warning("[PostgresStore] psycopg2 not installed — falling back to JSON file storage")

FALLBACK_DIR = "reports/pg_fallback"

# DDL — tables are created on first connect if they don't exist
_DDL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id               TEXT PRIMARY KEY,
    persona_id           TEXT NOT NULL,
    journey_id           TEXT NOT NULL,
    workflow_title       TEXT,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at          TIMESTAMPTZ,
    overall_status       TEXT NOT NULL DEFAULT 'in_progress',
    correctness_score    INTEGER DEFAULT 0,
    process_score        INTEGER DEFAULT 0,
    output_score         INTEGER DEFAULT 0,
    psi_questions_rating TEXT,
    agents_that_ran      JSONB DEFAULT '[]',
    agents_expected      JSONB DEFAULT '[]',
    improvement_flags    JSONB DEFAULT '[]',
    missing_elements     JSONB DEFAULT '[]',
    judge_rationale      TEXT,
    artifact_run_record_path TEXT,
    screenshot_paths     JSONB DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS candidate_insights (
    candidate_id              TEXT PRIMARY KEY,
    insight_id                TEXT NOT NULL,
    target_tier               TEXT NOT NULL,
    persona_id                TEXT,
    journey_id                TEXT,
    proposed_action           TEXT,
    pattern_description       TEXT,
    confidence_score          FLOAT DEFAULT 0,
    evidence_summary          TEXT,
    agp_review_template_id    TEXT,
    review_status             TEXT NOT NULL DEFAULT 'pending',
    reviewer_id               TEXT,
    reviewer_role             TEXT,
    review_deadline           TIMESTAMPTZ,
    reviewed_at               TIMESTAMPTZ,
    review_notes              TEXT,
    counter_proposal          TEXT,
    is_anonymised             BOOLEAN DEFAULT FALSE,
    anonymisation_verified    BOOLEAN DEFAULT FALSE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hitl_queue (
    item_id        TEXT PRIMARY KEY,
    item_type      TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    persona_id     TEXT,
    journey_id     TEXT,
    target_tier    TEXT,
    description    TEXT,
    evidence       JSONB DEFAULT '{}',
    agp_template_id TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    assigned_to    TEXT,
    deadline       TIMESTAMPTZ,
    resolved_at    TIMESTAMPTZ,
    resolution     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_persona_journey
    ON analysis_runs(persona_id, journey_id);
CREATE INDEX IF NOT EXISTS idx_candidate_insights_status
    ON candidate_insights(review_status, target_tier);
CREATE INDEX IF NOT EXISTS idx_hitl_queue_status_deadline
    ON hitl_queue(status, deadline);
"""

HITL_DEFAULT_DEADLINE_HOURS = 48


class PostgresStore:
    """
    Wraps analysis_runs, candidate_insights, and hitl_queue tables.
    Falls back to JSON file writes when Postgres is unavailable.
    """

    def __init__(self):
        self._conn = None
        self._dry_run = not _PG_AVAILABLE

        if _PG_AVAILABLE:
            dsn = os.getenv("DATABASE_URL", "")
            if not dsn:
                logger.warning("[PostgresStore] DATABASE_URL not set — JSON fallback mode")
                self._dry_run = True
            else:
                try:
                    self._conn = psycopg2.connect(dsn)
                    self._conn.autocommit = True
                    self._bootstrap()
                    logger.info("[PostgresStore] Connected to Postgres")
                except Exception as e:
                    logger.warning("[PostgresStore] Cannot connect (%s) — JSON fallback mode", e)
                    self._dry_run = True

        if self._dry_run:
            os.makedirs(FALLBACK_DIR, exist_ok=True)

    def _bootstrap(self):
        with self._conn.cursor() as cur:
            cur.execute(_DDL)

    # ── Fallback helpers ──────────────────────────────────────────────────

    def _fallback_write(self, table: str, record: dict) -> None:
        os.makedirs(FALLBACK_DIR, exist_ok=True)
        path = os.path.join(FALLBACK_DIR, f"{table}_{record.get('run_id') or record.get('candidate_id') or record.get('item_id', 'unknown')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str)

    # ── Analysis runs ─────────────────────────────────────────────────────

    def upsert_analysis_run(self, run: AnalysisRun) -> bool:
        d = run.to_dict()
        if self._dry_run:
            self._fallback_write("analysis_runs", d)
            return True
        try:
            sql = """
            INSERT INTO analysis_runs
              (run_id, persona_id, journey_id, workflow_title, started_at, finished_at,
               overall_status, correctness_score, process_score, output_score,
               psi_questions_rating, agents_that_ran, agents_expected,
               improvement_flags, missing_elements, judge_rationale,
               artifact_run_record_path, screenshot_paths)
            VALUES
              (%(run_id)s, %(persona_id)s, %(journey_id)s, %(workflow_title)s,
               %(started_at)s, %(finished_at)s, %(overall_status)s,
               %(correctness_score)s, %(process_score)s, %(output_score)s,
               %(psi_questions_rating)s,
               %(agents_that_ran)s::jsonb, %(agents_expected)s::jsonb,
               %(improvement_flags)s::jsonb, %(missing_elements)s::jsonb,
               %(judge_rationale)s, %(artifact_run_record_path)s,
               %(screenshot_paths)s::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
              finished_at           = EXCLUDED.finished_at,
              overall_status        = EXCLUDED.overall_status,
              correctness_score     = EXCLUDED.correctness_score,
              process_score         = EXCLUDED.process_score,
              output_score          = EXCLUDED.output_score,
              psi_questions_rating  = EXCLUDED.psi_questions_rating,
              agents_that_ran       = EXCLUDED.agents_that_ran,
              improvement_flags     = EXCLUDED.improvement_flags,
              missing_elements      = EXCLUDED.missing_elements,
              judge_rationale       = EXCLUDED.judge_rationale,
              screenshot_paths      = EXCLUDED.screenshot_paths;
            """
            params = dict(d)
            for list_field in ("agents_that_ran", "agents_expected",
                               "improvement_flags", "missing_elements", "screenshot_paths"):
                params[list_field] = json.dumps(params.get(list_field, []))
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            return True
        except Exception as e:
            logger.error("[PostgresStore] upsert_analysis_run failed: %s", e)
            return False

    def get_run_summary(self, persona_id: Optional[str] = None,
                        journey_id: Optional[str] = None) -> list[dict]:
        if self._dry_run:
            return []
        try:
            conditions, params = [], []
            if persona_id:
                conditions.append("persona_id = %s")
                params.append(persona_id)
            if journey_id:
                conditions.append("journey_id = %s")
                params.append(journey_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            sql = f"SELECT * FROM analysis_runs {where} ORDER BY started_at DESC LIMIT 200"
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("[PostgresStore] get_run_summary failed: %s", e)
            return []

    # ── Candidate insights ────────────────────────────────────────────────

    def insert_candidate_insight(self, candidate: CandidateInsight) -> bool:
        d = candidate.to_dict()
        if not d.get("review_deadline"):
            deadline = datetime.now(timezone.utc) + timedelta(hours=HITL_DEFAULT_DEADLINE_HOURS)
            d["review_deadline"] = deadline.isoformat()
        if self._dry_run:
            self._fallback_write("candidate_insights", d)
            return True
        try:
            cols = ", ".join(d.keys())
            placeholders = ", ".join(f"%({k})s" for k in d.keys())
            sql = f"INSERT INTO candidate_insights ({cols}) VALUES ({placeholders}) ON CONFLICT (candidate_id) DO NOTHING"
            with self._conn.cursor() as cur:
                cur.execute(sql, d)
            return True
        except Exception as e:
            logger.error("[PostgresStore] insert_candidate_insight failed: %s", e)
            return False

    def update_candidate_status(self, candidate_id: str, status: str,
                                reviewer_id: Optional[str] = None,
                                review_notes: Optional[str] = None) -> bool:
        if self._dry_run:
            return True
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE candidate_insights
                       SET review_status=%s, reviewer_id=%s, review_notes=%s,
                           reviewed_at=%s, updated_at=%s
                       WHERE candidate_id=%s""",
                    (status, reviewer_id, review_notes, now, now, candidate_id),
                )
            return True
        except Exception as e:
            logger.error("[PostgresStore] update_candidate_status failed: %s", e)
            return False

    def get_pending_candidates(self, target_tier: Optional[str] = None) -> list[dict]:
        if self._dry_run:
            return []
        try:
            if target_tier:
                sql = "SELECT * FROM candidate_insights WHERE review_status='pending' AND target_tier=%s ORDER BY review_deadline ASC"
                params = (target_tier,)
            else:
                sql = "SELECT * FROM candidate_insights WHERE review_status='pending' ORDER BY review_deadline ASC"
                params = ()
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("[PostgresStore] get_pending_candidates failed: %s", e)
            return []

    # ── HITL queue ────────────────────────────────────────────────────────

    def enqueue_hitl(self, item: HitlQueueItem) -> bool:
        d = item.to_dict()
        if not d.get("deadline"):
            deadline = datetime.now(timezone.utc) + timedelta(hours=HITL_DEFAULT_DEADLINE_HOURS)
            d["deadline"] = deadline.isoformat()
        if self._dry_run:
            self._fallback_write("hitl_queue", d)
            return True
        try:
            cols = ", ".join(d.keys())
            placeholders = ", ".join(f"%({k})s" for k in d.keys())
            params = dict(d)
            params["evidence"] = json.dumps(params.get("evidence", {}))
            sql = f"INSERT INTO hitl_queue ({cols}) VALUES ({placeholders}) ON CONFLICT (item_id) DO NOTHING"
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            return True
        except Exception as e:
            logger.error("[PostgresStore] enqueue_hitl failed: %s", e)
            return False

    def get_open_hitl_items(self) -> list[dict]:
        if self._dry_run:
            return []
        try:
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hitl_queue WHERE status='open' ORDER BY deadline ASC")
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("[PostgresStore] get_open_hitl_items failed: %s", e)
            return []

    def resolve_hitl_item(self, item_id: str, resolution: str,
                          resolver_id: str = "system") -> bool:
        if self._dry_run:
            return True
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE hitl_queue SET status='resolved', resolution=%s, resolved_at=%s WHERE item_id=%s",
                    (resolution, now, item_id),
                )
            return True
        except Exception as e:
            logger.error("[PostgresStore] resolve_hitl_item failed: %s", e)
            return False

    def get_overdue_hitl_items(self) -> list[dict]:
        if self._dry_run:
            return []
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM hitl_queue WHERE status='open' AND deadline < %s ORDER BY deadline ASC",
                    (now,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("[PostgresStore] get_overdue_hitl_items failed: %s", e)
            return []

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
