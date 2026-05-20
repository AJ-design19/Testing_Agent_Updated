"""
Human-in-the-Loop (HITL) queue manager (§13.4).

Responsibilities:
  - Surface open items to reviewers
  - Enforce the 48-hour deadline; escalate or auto-reject overdue items
  - Track queue depth as a published metric
  - Process ambiguous step verdicts (from LLM judge) AND candidate insights
    (from ESLL service) — both land in the same queue

Queue depth above a configurable threshold triggers a process review alert.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.store.postgres_store import PostgresStore
from app.store.models import HitlQueueItem, AuditAction, ReviewStatus
from app.store.audit_log import AuditLog

logger = logging.getLogger(__name__)

HITL_DEPTH_ALERT_THRESHOLD = int(50)   # sustained backlog above this → process review alert
HITL_DEFAULT_DEADLINE_HOURS = 48
HITL_ESCALATION_DEADLINE_HOURS = 72    # escalate after another 24 h past deadline


class HitlManager:
    """
    Manages the HITL review queue.
    Called by the nightly run scheduler and by the ESLL service.
    """

    def __init__(self, pg: PostgresStore, audit: AuditLog):
        self.pg = pg
        self.audit = audit

    # ── Queue operations ──────────────────────────────────────────────────

    def enqueue(self, item: HitlQueueItem) -> bool:
        """Add an item to the queue with a default deadline."""
        if not item.deadline:
            item.deadline = (
                datetime.now(timezone.utc) + timedelta(hours=HITL_DEFAULT_DEADLINE_HOURS)
            ).isoformat()
        result = self.pg.enqueue_hitl(item)
        logger.info("[HitlManager] Enqueued item %s (type=%s, tier=%s)",
                    item.item_id, item.item_type, item.target_tier)
        self._check_depth_alert()
        return result

    def get_open_items(self, assigned_to: Optional[str] = None) -> list[dict]:
        """Return all open queue items, optionally filtered by reviewer."""
        items = self.pg.get_open_hitl_items()
        if assigned_to:
            items = [i for i in items if i.get("assigned_to") == assigned_to]
        return items

    def assign(self, item_id: str, reviewer_id: str) -> bool:
        """Assign a queue item to a specific reviewer."""
        if self.pg._dry_run:
            return True
        try:
            with self.pg._conn.cursor() as cur:
                cur.execute(
                    "UPDATE hitl_queue SET assigned_to=%s, status='in_review' WHERE item_id=%s",
                    (reviewer_id, item_id),
                )
            logger.info("[HitlManager] Item %s assigned to %s", item_id, reviewer_id)
            return True
        except Exception as e:
            logger.error("[HitlManager] assign failed: %s", e)
            return False

    def resolve(self, item_id: str, resolution: str, resolver_id: str) -> bool:
        """Mark an item as resolved."""
        result = self.pg.resolve_hitl_item(item_id, resolution, resolver_id)
        self.audit.append(
            action=AuditAction.INSIGHT_APPROVED.value,
            actor_id=resolver_id,
            actor_role="reviewer",
            target_id=item_id,
            payload={"resolution": resolution},
        )
        return result

    # ── Deadline enforcement (§13.4) ──────────────────────────────────────

    def process_overdue_items(self) -> dict:
        """
        Called by the nightly scheduler.
        Items past their deadline are escalated to a senior reviewer.
        Items past the escalation deadline are auto-rejected with structured reason.
        Returns counts of escalated and auto-rejected items.
        """
        overdue = self.pg.get_overdue_hitl_items()
        escalated, auto_rejected = 0, 0
        now = datetime.now(timezone.utc)

        for item in overdue:
            deadline_str = item.get("deadline")
            if not deadline_str:
                continue

            deadline_dt = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
            hours_overdue = (now - deadline_dt).total_seconds() / 3600

            if hours_overdue >= (HITL_ESCALATION_DEADLINE_HOURS - HITL_DEFAULT_DEADLINE_HOURS):
                # Past escalation window → auto-reject
                self._auto_reject(item)
                auto_rejected += 1
            else:
                # Past initial deadline → escalate
                self._escalate(item)
                escalated += 1

        logger.info("[HitlManager] Overdue sweep: %d escalated, %d auto-rejected",
                    escalated, auto_rejected)
        return {"escalated": escalated, "auto_rejected": auto_rejected}

    def _escalate(self, item: dict) -> None:
        item_id = item.get("item_id", "")
        if not self.pg._dry_run and self.pg._conn:
            try:
                with self.pg._conn.cursor() as cur:
                    cur.execute(
                        "UPDATE hitl_queue SET status='escalated' WHERE item_id=%s",
                        (item_id,),
                    )
            except Exception as e:
                logger.error("[HitlManager] escalate DB update failed: %s", e)
        self.audit.append(
            action=AuditAction.HITL_ESCALATED.value,
            actor_id="system",
            actor_role="system",
            target_id=item_id,
            target_tier=item.get("target_tier", ""),
            payload={"reason": "deadline_exceeded", "item_type": item.get("item_type")},
        )
        logger.warning("[HitlManager] Item %s escalated (deadline exceeded)", item_id)

    def _auto_reject(self, item: dict) -> None:
        item_id = item.get("item_id", "")
        reason = "Auto-rejected: escalation deadline exceeded with no reviewer action"
        self.pg.resolve_hitl_item(item_id, reason, resolver_id="system")
        self.audit.append(
            action=AuditAction.HITL_AUTO_REJECTED.value,
            actor_id="system",
            actor_role="system",
            target_id=item_id,
            target_tier=item.get("target_tier", ""),
            payload={"reason": reason},
        )
        logger.warning("[HitlManager] Item %s auto-rejected", item_id)

    # ── Queue depth metric ────────────────────────────────────────────────

    def queue_depth(self) -> int:
        """Return the current number of open HITL items."""
        return len(self.pg.get_open_hitl_items())

    def _check_depth_alert(self) -> None:
        depth = self.queue_depth()
        if depth >= HITL_DEPTH_ALERT_THRESHOLD:
            logger.warning(
                "[HitlManager] ALERT: HITL queue depth=%d exceeds threshold=%d. "
                "Sustained backlog triggers process review.",
                depth, HITL_DEPTH_ALERT_THRESHOLD,
            )

    def get_depth_metric(self) -> dict:
        """Return queue depth metric for the metrics dashboard (§14.2)."""
        open_items = self.pg.get_open_hitl_items()
        now = datetime.now(timezone.utc)
        overdue_count = 0
        resolution_times = []

        for item in open_items:
            deadline_str = item.get("deadline")
            if deadline_str:
                try:
                    dl = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
                    if dl < now:
                        overdue_count += 1
                except Exception:
                    pass

        return {
            "open": len(open_items),
            "overdue": overdue_count,
            "alert": len(open_items) >= HITL_DEPTH_ALERT_THRESHOLD,
            "threshold": HITL_DEPTH_ALERT_THRESHOLD,
        }
