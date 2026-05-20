"""
Shared data models for all durable stores (§12.1).

New event types are namespaced  sai.*  and  step.*
New projection types:  five SAI projections (see ESMProjection)
New insight types:     sai.persona.*, sai.journey.*, sai.failure_mode.*

Existing ORCH / MAN-ESM fields and types are NEVER changed — we only add.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ── Enumerations ──────────────────────────────────────────────────────────────

class ESMEventType(str, Enum):
    # SAI-namespaced events (new; do not touch existing ORCH types)
    SAI_SESSION_START       = "sai.session.start"
    SAI_SESSION_END         = "sai.session.end"
    SAI_STEP_STARTED        = "sai.step.started"
    SAI_STEP_COMPLETED      = "sai.step.completed"
    SAI_STEP_FAILED         = "sai.step.failed"
    SAI_STEP_AMBIGUOUS      = "sai.step.ambiguous"        # → HITL queue
    SAI_PSI_QUESTION        = "sai.psi.question"
    SAI_PSI_ANSWER          = "sai.psi.answer"
    SAI_CANVAS_AGENT_START  = "sai.canvas.agent_start"
    SAI_CANVAS_AGENT_END    = "sai.canvas.agent_end"
    SAI_JUDGE_VERDICT       = "sai.judge.verdict"
    SAI_INSIGHT_PROPOSED    = "sai.insight.proposed"
    SAI_INSIGHT_PROMOTED    = "sai.insight.promoted"
    SAI_INSIGHT_REJECTED    = "sai.insight.rejected"


class ESMProjectionType(str, Enum):
    # Five SAI projections (new)
    SAI_PERSONA_HEALTH          = "sai.projection.persona_health"
    SAI_JOURNEY_REACHABILITY    = "sai.projection.journey_reachability"
    SAI_CAPABILITY_COVERAGE     = "sai.projection.capability_coverage"
    SAI_FAILURE_MODE_FREQUENCY  = "sai.projection.failure_mode_frequency"
    SAI_CONTEXT_EFFECTIVENESS   = "sai.projection.context_effectiveness"


class ESMInsightType(str, Enum):
    SAI_PERSONA_PATTERN         = "sai.persona.pattern"
    SAI_JOURNEY_DIVERGENCE      = "sai.journey.divergence"
    SAI_FAILURE_MODE_AVOID      = "sai.failure_mode.avoid"
    SAI_FAILURE_MODE_RECOVER    = "sai.failure_mode.recover"
    SAI_JOURNEY_CANDIDATE       = "sai.journey.candidate"


class ESMTier(str, Enum):
    SESSION    = "session"
    USER       = "user"
    PROJECT    = "project"
    ENTERPRISE = "enterprise"
    GLOBAL     = "global"


class ReviewStatus(str, Enum):
    PENDING    = "pending"
    APPROVED   = "approved"
    REJECTED   = "rejected"
    MODIFIED   = "modified"
    ESCALATED  = "escalated"
    AUTO_COMMITTED = "auto_committed"


class AuditAction(str, Enum):
    INSIGHT_PROPOSED   = "insight.proposed"
    INSIGHT_APPROVED   = "insight.approved"
    INSIGHT_REJECTED   = "insight.rejected"
    INSIGHT_MODIFIED   = "insight.modified"
    INSIGHT_PROMOTED   = "insight.promoted"
    ANONYMISED         = "anonymisation.applied"
    ANON_VERIFIED      = "anonymisation.verified"
    ANON_BLOCKED       = "anonymisation.blocked"
    GLOBAL_APPROVED    = "global.approved"
    HITL_ESCALATED     = "hitl.escalated"
    HITL_AUTO_REJECTED = "hitl.auto_rejected"


# ── MongoDB models ────────────────────────────────────────────────────────────

@dataclass
class ESMEvent:
    """
    Written to  app_studio_esm_events  (MongoDB).
    Extends the existing ORCH / MAN-ESM schema by adding sai.* namespaced types.
    Existing fields and types are never mutated.
    """
    event_id: str = field(default_factory=_uid)
    event_type: str = ""                    # ESMEventType value
    session_id: str = ""
    run_id: str = ""
    persona_id: str = ""
    journey_id: str = ""
    step_index: int = 0
    step_label: str = ""
    payload: dict = field(default_factory=dict)  # event-specific data
    timestamp: str = field(default_factory=_now)
    # Object-store references (screenshots / DOM / traces)
    artifact_uris: list[str] = field(default_factory=list)
    # Existing ORCH fields kept intact (never removed)
    source: str = "sai_test_harness"
    schema_version: str = "1.0"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ESMProjection:
    """
    Written to  app_studio_esm_projections  (MongoDB).
    Five new projection_type values; never changes existing projection types.
    """
    projection_id: str = field(default_factory=_uid)
    projection_type: str = ""               # ESMProjectionType value
    scope_persona_id: Optional[str] = None
    scope_journey_id: Optional[str] = None
    scope_tier: str = ESMTier.SESSION.value
    state: dict = field(default_factory=dict)   # projection-specific state
    last_updated: str = field(default_factory=_now)
    run_count: int = 0
    source: str = "sai_test_harness"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ESMInsight:
    """
    Written to  app_studio_esm_learning  (MongoDB).
    New insight_type values under sai.* namespace.
    """
    insight_id: str = field(default_factory=_uid)
    insight_type: str = ""                   # ESMInsightType value
    target_tier: str = ESMTier.SESSION.value # ESMTier value
    persona_id: Optional[str] = None
    journey_id: Optional[str] = None
    failure_mode: Optional[str] = None
    pattern_description: str = ""           # human-readable pattern
    proposed_action: str = ""               # what SAI should do differently
    evidence_event_ids: list[str] = field(default_factory=list)
    confidence_score: float = 0.0           # 0.0 – 1.0
    proposer: str = "sai_test_harness"
    proposed_at: str = field(default_factory=_now)
    review_status: str = ReviewStatus.PENDING.value
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    source: str = "sai_test_harness"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Postgres models ───────────────────────────────────────────────────────────

@dataclass
class AnalysisRun:
    """
    Per-run analysis record in  analysis_runs  (Postgres §12.1).
    Tracks pass/fail per persona × journey, candidate buckets, signed artifacts.
    """
    run_id: str = field(default_factory=_uid)
    persona_id: str = ""
    journey_id: str = ""
    workflow_title: str = ""
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    overall_status: str = "in_progress"     # pass | partial | fail | error
    correctness_score: int = 0              # 0-10 from LLM judge
    process_score: int = 0
    output_score: int = 0
    psi_questions_rating: str = ""
    agents_that_ran: list[str] = field(default_factory=list)
    agents_expected: list[str] = field(default_factory=list)
    improvement_flags: list[str] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    judge_rationale: str = ""
    artifact_run_record_path: str = ""     # path to reports/<run_id>.json
    screenshot_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateInsight:
    """
    Staging area for project / enterprise / global tier writes (§12.1, §13.2).
    Sits in  candidate_insights  (Postgres) pending human review.
    """
    candidate_id: str = field(default_factory=_uid)
    insight_id: str = ""                   # FK → ESMInsight.insight_id
    target_tier: str = ESMTier.PROJECT.value
    persona_id: Optional[str] = None
    journey_id: Optional[str] = None
    proposed_action: str = ""
    pattern_description: str = ""
    confidence_score: float = 0.0
    evidence_summary: str = ""
    agp_review_template_id: Optional[str] = None   # AGP-supplied template ref
    review_status: str = ReviewStatus.PENDING.value
    reviewer_id: Optional[str] = None
    reviewer_role: str = ""
    review_deadline: Optional[str] = None          # ISO-8601; default +48h
    reviewed_at: Optional[str] = None
    review_notes: Optional[str] = None
    counter_proposal: Optional[str] = None
    is_anonymised: bool = False            # required before global promotion
    anonymisation_verified: bool = False
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Audit log ─────────────────────────────────────────────────────────────────

@dataclass
class AuditLogEntry:
    """
    Append-only audit log entry (§13, §13.4).
    Every approval, rejection, anonymisation, and global-tier promotion is logged.
    The log is itself immutable — no retroactive editing.
    """
    audit_id: str = field(default_factory=_uid)
    action: str = ""                        # AuditAction value
    actor_id: str = ""                      # human reviewer or "system"
    actor_role: str = ""
    target_id: str = ""                     # candidate_id or insight_id
    target_tier: str = ""
    payload: dict = field(default_factory=dict)   # action-specific details
    timestamp: str = field(default_factory=_now)
    # Immutability marker — set once on write, never updated
    immutable: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# ── HITL queue item ───────────────────────────────────────────────────────────

@dataclass
class HitlQueueItem:
    """
    Item in the Human-in-the-Loop review queue (§13.4).
    Two source types: ambiguous step verdicts, and candidate insights.
    Each item carries a deadline; after deadline it is escalated or auto-rejected.
    """
    item_id: str = field(default_factory=_uid)
    item_type: str = ""          # "ambiguous_verdict" | "candidate_insight"
    source_id: str = ""          # run_id or candidate_id
    persona_id: Optional[str] = None
    journey_id: Optional[str] = None
    target_tier: Optional[str] = None
    description: str = ""
    evidence: dict = field(default_factory=dict)
    agp_template_id: Optional[str] = None
    status: str = "open"         # open | in_review | resolved | escalated | auto_rejected
    assigned_to: Optional[str] = None
    deadline: Optional[str] = None   # ISO-8601; default now + 48h
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)
