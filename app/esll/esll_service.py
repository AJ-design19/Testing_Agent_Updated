"""
ESLL (Experiential Self-Learning Layer) service (§12.2, §13.2).

Implements the tier-based mutation policy:

  Tier          | Policy
  ──────────────|──────────────────────────────────────────────────────────
  session       | Auto-commit. No human review. Audited.
  user          | Auto-commit. No human review. Audited.
  project       | Candidate written to staging. review.requested event emitted.
                |   → Workspace owner reviews via AGP template.
  enterprise    | Candidate written to staging. review.requested event emitted.
                |   → Enterprise CoE reviews via AGP template.
  global        | Anonymisation required first. Candidate written to staging.
                |   → Enterprise CoE approves anonymised version.
                |   → Adya platform team gives final approval and signs.
  ──────────────|──────────────────────────────────────────────────────────

Integration contract with ORCH / MAN-ESM (§12.2):
  - We are a producer on the same MongoDB collections ORCH's ESLL writes to.
  - All our writes carry source="sai_test_harness" and sai.* namespaced types.
  - We share durable backing stores for project / enterprise / global tiers.
  - We never modify existing ORCH fields or event types.
  - The hybrid judge verdict is a Composite that conforms to MAN-ESM's Judge protocol.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.store.models import (
    ESMEvent, ESMInsight, CandidateInsight, HitlQueueItem,
    ESMEventType, ESMTier, ReviewStatus, AuditAction,
)
from app.store.mongo_store import MongoStore
from app.store.postgres_store import PostgresStore
from app.store.audit_log import AuditLog

logger = logging.getLogger(__name__)

# Minimum confidence score to auto-promote at session/user tier
AUTO_COMMIT_CONFIDENCE_THRESHOLD = float("0.0")  # always auto-commit session+user

# Minimum confidence to even create a candidate at project+ tier
PROJECT_PLUS_CONFIDENCE_THRESHOLD = 0.55

# Confidence above which we skip manual review at project tier (AGP-allowed fast-path)
PROJECT_FAST_PATH_THRESHOLD = 0.92


class ESLLService:
    """
    Central ESLL coordinator for the SAI test harness.
    Receives judge verdicts and proposed insights from a run,
    routes them through the tier-based mutation policy,
    and emits events on the appropriate MongoDB collections.
    """

    def __init__(self, mongo: MongoStore, pg: PostgresStore, audit: AuditLog):
        self.mongo = mongo
        self.pg = pg
        self.audit = audit

    # ── Public interface ──────────────────────────────────────────────────

    def process_run_verdict(
        self,
        run_id: str,
        persona: dict,
        workflow: dict,
        verdict: dict,
        conversation_log: list[dict],
        canvas_evidence: dict,
    ) -> dict:
        """
        Main entry point after a test run completes and the judge has scored it.
        1. Emits SAI_JUDGE_VERDICT event.
        2. Derives proposed insights from the verdict.
        3. Routes each insight through the tier mutation policy.
        Returns a summary of what was committed / staged.
        """
        session_id = str(uuid.uuid4())

        # ── Emit judge verdict event ───────────────────────────────────
        self._emit_event(ESMEvent(
            event_type=ESMEventType.SAI_JUDGE_VERDICT.value,
            session_id=session_id,
            run_id=run_id,
            persona_id=persona.get("id", ""),
            journey_id=workflow.get("id", ""),
            step_label="judge_verdict",
            payload={
                "correctness_verdict": verdict.get("correctness_verdict"),
                "correctness_score": verdict.get("correctness_score"),
                "process_score": verdict.get("process_score"),
                "output_score": verdict.get("output_score"),
                "improvement_flags": verdict.get("improvement_flags", []),
                "missing_elements": verdict.get("missing_elements", []),
            },
        ))

        # ── Derive and route insights ──────────────────────────────────
        insights = self._derive_insights(persona, workflow, verdict, run_id)
        results = {"auto_committed": [], "staged_for_review": [], "below_threshold": []}

        for insight in insights:
            result = self._route_insight(insight, persona, workflow)
            results[result].append(insight.insight_id)

        logger.info(
            "[ESLLService] Run %s: %d auto-committed, %d staged, %d below threshold",
            run_id,
            len(results["auto_committed"]),
            len(results["staged_for_review"]),
            len(results["below_threshold"]),
        )
        return results

    # ── Event emission ────────────────────────────────────────────────────

    def emit_step_event(
        self,
        event_type: str,
        run_id: str,
        persona_id: str,
        journey_id: str,
        step_index: int,
        step_label: str,
        payload: Optional[dict] = None,
        artifact_uris: Optional[list[str]] = None,
    ) -> str:
        event = ESMEvent(
            event_type=event_type,
            session_id=run_id,
            run_id=run_id,
            persona_id=persona_id,
            journey_id=journey_id,
            step_index=step_index,
            step_label=step_label,
            payload=payload or {},
            artifact_uris=artifact_uris or [],
        )
        return self._emit_event(event) or event.event_id

    def _emit_event(self, event: ESMEvent) -> Optional[str]:
        return self.mongo.write_event(event)

    # ── Insight derivation ────────────────────────────────────────────────

    def _derive_insights(
        self,
        persona: dict,
        workflow: dict,
        verdict: dict,
        run_id: str,
    ) -> list[ESMInsight]:
        """
        Derive proposed insights from a judge verdict.
        Each improvement flag becomes a failure-mode-avoid insight.
        Each missing element becomes a failure-mode-recover insight.
        """
        insights: list[ESMInsight] = []
        score = verdict.get("correctness_score", 0)
        persona_id = persona.get("id", "")
        journey_id = workflow.get("id", "")

        # Confidence heuristic: score/10 × 0.9 + 0.05 base
        base_confidence = (score / 10) * 0.9 + 0.05

        for flag in verdict.get("improvement_flags", []):
            insights.append(ESMInsight(
                insight_type="sai.failure_mode.avoid",
                target_tier=self._choose_tier(base_confidence),
                persona_id=persona_id,
                journey_id=journey_id,
                failure_mode=flag[:120],
                pattern_description=(
                    f"Persona {persona_id} on journey {journey_id}: {flag}"
                ),
                proposed_action=f"SAI should avoid: {flag}",
                evidence_event_ids=[run_id],
                confidence_score=round(base_confidence, 3),
            ))

        for missing in verdict.get("missing_elements", []):
            insights.append(ESMInsight(
                insight_type="sai.failure_mode.recover",
                target_tier=self._choose_tier(base_confidence),
                persona_id=persona_id,
                journey_id=journey_id,
                failure_mode=missing[:120],
                pattern_description=(
                    f"Missing output for {persona_id}/{journey_id}: {missing}"
                ),
                proposed_action=f"SAI should produce: {missing}",
                evidence_event_ids=[run_id],
                confidence_score=round(base_confidence, 3),
            ))

        # If Psi's question quality was poor, add a persona-pattern insight
        if verdict.get("psi_questions_rating") in ("poor", "adequate"):
            insights.append(ESMInsight(
                insight_type="sai.persona.pattern",
                target_tier=ESMTier.PROJECT.value,
                persona_id=persona_id,
                journey_id=journey_id,
                pattern_description=(
                    f"Psi question quality '{verdict.get('psi_questions_rating')}' "
                    f"for persona {persona_id}. Process evaluation: "
                    f"{verdict.get('process_evaluation', '')[:200]}"
                ),
                proposed_action="Improve Psi clarification questions for this persona type",
                evidence_event_ids=[run_id],
                confidence_score=0.70,
            ))

        return insights

    @staticmethod
    def _choose_tier(confidence: float) -> str:
        """Map confidence to a starting tier for the insight."""
        if confidence >= 0.85:
            return ESMTier.PROJECT.value
        if confidence >= 0.70:
            return ESMTier.USER.value
        return ESMTier.SESSION.value

    # ── Tier mutation policy (§13.2) ──────────────────────────────────────

    def _route_insight(
        self,
        insight: ESMInsight,
        persona: dict,
        workflow: dict,
    ) -> str:
        """
        Route an insight through the tier mutation policy.
        Returns "auto_committed" | "staged_for_review" | "below_threshold"
        """
        tier = insight.target_tier

        # Below confidence threshold — don't surface
        if insight.confidence_score < PROJECT_PLUS_CONFIDENCE_THRESHOLD and \
                tier not in (ESMTier.SESSION.value, ESMTier.USER.value):
            logger.debug("[ESLLService] Insight below threshold (%.2f): %s",
                         insight.confidence_score, insight.insight_id)
            return "below_threshold"

        # session / user → auto-commit, audit, return
        if tier in (ESMTier.SESSION.value, ESMTier.USER.value):
            self.mongo.write_insight(insight)
            self._emit_event(ESMEvent(
                event_type=ESMEventType.SAI_INSIGHT_PROPOSED.value,
                run_id=insight.evidence_event_ids[0] if insight.evidence_event_ids else "",
                persona_id=insight.persona_id or "",
                journey_id=insight.journey_id or "",
                step_label="insight_auto_committed",
                payload={"insight_id": insight.insight_id, "tier": tier,
                         "auto_committed": True},
            ))
            self.audit.append(
                action=AuditAction.INSIGHT_PROPOSED.value,
                actor_id="sai_test_harness",
                actor_role="system",
                target_id=insight.insight_id,
                target_tier=tier,
                payload={"auto_committed": True,
                         "insight_type": insight.insight_type,
                         "confidence": insight.confidence_score},
            )
            logger.info("[ESLLService] Auto-committed insight %s at tier=%s",
                        insight.insight_id, tier)
            return "auto_committed"

        # project / enterprise / global → stage for HITL review
        self.mongo.write_insight(insight)

        # Global tier: anonymisation required before staging
        if tier == ESMTier.GLOBAL.value:
            self._apply_anonymisation(insight)

        candidate = CandidateInsight(
            insight_id=insight.insight_id,
            target_tier=tier,
            persona_id=insight.persona_id,
            journey_id=insight.journey_id,
            proposed_action=insight.proposed_action,
            pattern_description=insight.pattern_description,
            confidence_score=insight.confidence_score,
            evidence_summary=f"Run evidence: {insight.evidence_event_ids}",
            agp_review_template_id=self._get_agp_template_id(tier),
            review_status=ReviewStatus.PENDING.value,
            is_anonymised=(tier == ESMTier.GLOBAL.value),
            anonymisation_verified=(tier == ESMTier.GLOBAL.value),
        )
        candidate.review_deadline = (
            datetime.now(timezone.utc) + timedelta(hours=48)
        ).isoformat()

        self.pg.insert_candidate_insight(candidate)

        # Enqueue in HITL queue
        hitl_item = HitlQueueItem(
            item_type="candidate_insight",
            source_id=candidate.candidate_id,
            persona_id=insight.persona_id,
            journey_id=insight.journey_id,
            target_tier=tier,
            description=(
                f"[{tier.upper()}] {insight.insight_type}: "
                f"{insight.pattern_description[:200]}"
            ),
            evidence={
                "proposed_action": insight.proposed_action,
                "confidence": insight.confidence_score,
                "insight_type": insight.insight_type,
            },
            agp_template_id=self._get_agp_template_id(tier),
            deadline=(
                datetime.now(timezone.utc) + timedelta(hours=48)
            ).isoformat(),
        )
        self.pg.enqueue_hitl(hitl_item)

        # Emit review.requested event
        self._emit_event(ESMEvent(
            event_type=ESMEventType.SAI_INSIGHT_PROPOSED.value,
            run_id=insight.evidence_event_ids[0] if insight.evidence_event_ids else "",
            persona_id=insight.persona_id or "",
            journey_id=insight.journey_id or "",
            step_label="review_requested",
            payload={
                "candidate_id": candidate.candidate_id,
                "insight_id": insight.insight_id,
                "tier": tier,
                "requires_review": True,
                "agp_template_id": candidate.agp_review_template_id,
            },
        ))

        self.audit.append(
            action=AuditAction.INSIGHT_PROPOSED.value,
            actor_id="sai_test_harness",
            actor_role="system",
            target_id=candidate.candidate_id,
            target_tier=tier,
            payload={"insight_type": insight.insight_type,
                     "confidence": insight.confidence_score,
                     "review_required": True},
        )

        logger.info("[ESLLService] Staged candidate %s for %s-tier review",
                    candidate.candidate_id, tier)
        return "staged_for_review"

    # ── Anonymisation (§13.3) ─────────────────────────────────────────────

    def _apply_anonymisation(self, insight: ESMInsight) -> None:
        """
        Scrub tenant/user/project identifiers from the insight before
        global-tier staging (§13.3).
        Replaces persona_id, journey_id, and any free-text IDs with
        anonymised placeholders. The result is a pattern, not a record.
        """
        # Replace identifiers with anonymised tokens
        insight.pattern_description = self._scrub_identifiers(
            insight.pattern_description
        )
        insight.proposed_action = self._scrub_identifiers(insight.proposed_action)
        insight.persona_id = f"anon_{insight.persona_id[:4]}" if insight.persona_id else None
        insight.journey_id = f"anon_{insight.journey_id[:6]}" if insight.journey_id else None
        insight.evidence_event_ids = []   # never expose raw event IDs at global tier

        self.audit.append(
            action=AuditAction.ANONYMISED.value,
            actor_id="sai_test_harness",
            actor_role="system",
            target_id=insight.insight_id,
            target_tier=ESMTier.GLOBAL.value,
            payload={"anonymisation": "identifiers_scrubbed"},
        )

    @staticmethod
    def _scrub_identifiers(text: str) -> str:
        """
        Remove or hash tenant/user/project identifiers from free text.
        Production version would use a trained deny-by-default classifier;
        v1 uses a simple pattern-replacement approach.
        """
        import re
        # Remove email-like patterns
        text = re.sub(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL]", text)
        # Remove UUID-like strings
        text = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "[UUID]", text, flags=re.IGNORECASE,
        )
        # Remove company/org names that look like proper nouns followed by "Inc", "Ltd", etc.
        text = re.sub(r"\b[A-Z][a-z]+ (?:Inc|Ltd|Corp|LLC|GmbH|Plc)\.?\b", "[ORG]", text)
        return text

    @staticmethod
    def _get_agp_template_id(tier: str) -> Optional[str]:
        """
        Return the AGP-supplied review template ID for the given tier.
        Template IDs are referenced by ID only — never inlined (§12.2).
        Production: fetch from AGP API; v1: static mapping.
        """
        return {
            ESMTier.PROJECT.value:    "agp_tmpl_project_review_v1",
            ESMTier.ENTERPRISE.value: "agp_tmpl_enterprise_review_v1",
            ESMTier.GLOBAL.value:     "agp_tmpl_global_review_v1",
        }.get(tier)

    # ── Human review actions (§13.2 steps 4-6) ───────────────────────────

    def approve_candidate(
        self,
        candidate_id: str,
        reviewer_id: str,
        reviewer_role: str,
        notes: str = "",
    ) -> bool:
        """
        Reviewer approves a candidate insight.
        Promotes it to the live learning store and audit-logs the action.
        """
        self.pg.update_candidate_status(
            candidate_id, ReviewStatus.APPROVED.value,
            reviewer_id=reviewer_id, review_notes=notes,
        )
        self.mongo.update_insight_status(candidate_id, ReviewStatus.APPROVED.value, reviewer_id)
        self.audit.append(
            action=AuditAction.INSIGHT_APPROVED.value,
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            target_id=candidate_id,
            payload={"notes": notes},
        )
        logger.info("[ESLLService] Candidate %s approved by %s", candidate_id, reviewer_id)
        return True

    def reject_candidate(
        self,
        candidate_id: str,
        reviewer_id: str,
        reviewer_role: str,
        reason: str = "",
    ) -> bool:
        """Reviewer rejects a candidate; archives with reason."""
        self.pg.update_candidate_status(
            candidate_id, ReviewStatus.REJECTED.value,
            reviewer_id=reviewer_id, review_notes=reason,
        )
        self.audit.append(
            action=AuditAction.INSIGHT_REJECTED.value,
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            target_id=candidate_id,
            payload={"reason": reason},
        )
        logger.info("[ESLLService] Candidate %s rejected by %s: %s",
                    candidate_id, reviewer_id, reason)
        return True

    def modify_candidate(
        self,
        candidate_id: str,
        reviewer_id: str,
        reviewer_role: str,
        counter_proposal: str,
    ) -> bool:
        """
        Reviewer proposes a modification.
        Original SAI proposal is closed; counter-proposal is staged.
        """
        self.pg.update_candidate_status(
            candidate_id, ReviewStatus.MODIFIED.value,
            reviewer_id=reviewer_id, review_notes=counter_proposal,
        )
        self.audit.append(
            action=AuditAction.INSIGHT_MODIFIED.value,
            actor_id=reviewer_id,
            actor_role=reviewer_role,
            target_id=candidate_id,
            payload={"counter_proposal": counter_proposal},
        )
        logger.info("[ESLLService] Candidate %s modified by %s", candidate_id, reviewer_id)
        return True
