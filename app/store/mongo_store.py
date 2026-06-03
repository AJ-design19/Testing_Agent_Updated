"""
MongoDB store (§12.1).

Collections:
  app_studio_esm_events       — ESMEvent documents (sai.* namespaced types added)
  app_studio_esm_projections  — ESMProjection documents (5 new projection types)
  app_studio_esm_learning     — ESMInsight documents (sai.* insight types added)

Contract with ORCH / MAN-ESM:
  - We are a PRODUCER on the same collections ORCH's ESLL writes to.
  - We extend the schema by adding fields and new namespaced types only.
  - We never change existing fields or remove existing types.
  - All our writes use the  source="sai_test_harness"  field for isolation.
"""

import logging
import os
from typing import Optional

from app.store.models import ESMEvent, ESMProjection, ESMInsight

logger = logging.getLogger(__name__)

# Lazy import so the app boots without pymongo installed (falls back to noop)
try:
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.collection import Collection
    _PYMONGO_AVAILABLE = True
except ImportError:
    _PYMONGO_AVAILABLE = False
    logger.warning("[MongoStore] pymongo not installed — store will run in dry-run mode")


class MongoStore:
    """
    Thin wrapper around three MongoDB collections.
    All methods are safe-to-call even when MongoDB is unavailable
    (they log a warning and return None/empty list).
    """

    EVENTS_COLLECTION      = "app_studio_esm_events"
    PROJECTIONS_COLLECTION = "app_studio_esm_projections"
    LEARNING_COLLECTION    = "app_studio_esm_learning"

    def __init__(self):
        self._client = None
        self._db = None
        self._dry_run = not _PYMONGO_AVAILABLE

        if _PYMONGO_AVAILABLE:
            uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
            db_name = os.getenv("MONGODB_DB", "adya_esm")
            try:
                self._client = MongoClient(uri, serverSelectionTimeoutMS=3000)
                self._client.admin.command("ping")
                self._db = self._client[db_name]
                self._ensure_indexes()
                logger.info("[MongoStore] Connected to %s / %s", uri, db_name)
            except Exception as e:
                logger.debug("[MongoStore] MongoDB not available (%s) — dry-run mode", e)
                self._dry_run = True

    def _col(self, name: str):
        if self._dry_run or self._db is None:
            return None
        return self._db[name]

    def _ensure_indexes(self):
        try:
            events = self._db[self.EVENTS_COLLECTION]
            events.create_index([("run_id", ASCENDING), ("timestamp", DESCENDING)])
            events.create_index([("persona_id", ASCENDING), ("journey_id", ASCENDING)])
            events.create_index([("event_type", ASCENDING)])

            projections = self._db[self.PROJECTIONS_COLLECTION]
            projections.create_index([("projection_type", ASCENDING),
                                      ("scope_persona_id", ASCENDING),
                                      ("scope_journey_id", ASCENDING)], unique=True,
                                     partialFilterExpression={"source": "sai_test_harness"})

            learning = self._db[self.LEARNING_COLLECTION]
            learning.create_index([("insight_type", ASCENDING), ("target_tier", ASCENDING)])
            learning.create_index([("journey_id", ASCENDING), ("persona_id", ASCENDING)])
        except Exception as e:
            logger.warning("[MongoStore] Index creation warning: %s", e)

    # ── Events ────────────────────────────────────────────────────────────

    def write_event(self, event: ESMEvent) -> Optional[str]:
        col = self._col(self.EVENTS_COLLECTION)
        if col is None:
            logger.debug("[MongoStore][dry-run] write_event: %s", event.event_type)
            return event.event_id
        try:
            col.insert_one(event.to_dict())
            return event.event_id
        except Exception as e:
            logger.error("[MongoStore] write_event failed: %s", e)
            return None

    def get_events_for_run(self, run_id: str) -> list[dict]:
        col = self._col(self.EVENTS_COLLECTION)
        if col is None:
            return []
        try:
            return list(col.find({"run_id": run_id}, {"_id": 0}).sort("timestamp", 1))
        except Exception as e:
            logger.error("[MongoStore] get_events_for_run failed: %s", e)
            return []

    def get_events_for_journey(self, journey_id: str, limit: int = 500) -> list[dict]:
        col = self._col(self.EVENTS_COLLECTION)
        if col is None:
            return []
        try:
            return list(col.find({"journey_id": journey_id, "source": "sai_test_harness"},
                                 {"_id": 0}).sort("timestamp", -1).limit(limit))
        except Exception as e:
            logger.error("[MongoStore] get_events_for_journey failed: %s", e)
            return []

    # ── Projections ───────────────────────────────────────────────────────

    def upsert_projection(self, projection: ESMProjection) -> Optional[str]:
        col = self._col(self.PROJECTIONS_COLLECTION)
        if col is None:
            logger.debug("[MongoStore][dry-run] upsert_projection: %s", projection.projection_type)
            return projection.projection_id
        try:
            from pymongo import ReturnDocument
            col.find_one_and_update(
                {
                    "projection_type": projection.projection_type,
                    "scope_persona_id": projection.scope_persona_id,
                    "scope_journey_id": projection.scope_journey_id,
                    "source": "sai_test_harness",
                },
                {"$set": projection.to_dict()},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return projection.projection_id
        except Exception as e:
            logger.error("[MongoStore] upsert_projection failed: %s", e)
            return None

    def get_projection(self, projection_type: str,
                       persona_id: Optional[str] = None,
                       journey_id: Optional[str] = None) -> Optional[dict]:
        col = self._col(self.PROJECTIONS_COLLECTION)
        if col is None:
            return None
        try:
            query = {"projection_type": projection_type, "source": "sai_test_harness"}
            if persona_id:
                query["scope_persona_id"] = persona_id
            if journey_id:
                query["scope_journey_id"] = journey_id
            doc = col.find_one(query, {"_id": 0})
            return doc
        except Exception as e:
            logger.error("[MongoStore] get_projection failed: %s", e)
            return None

    # ── Insights (learning store) ─────────────────────────────────────────

    def write_insight(self, insight: ESMInsight) -> Optional[str]:
        col = self._col(self.LEARNING_COLLECTION)
        if col is None:
            logger.debug("[MongoStore][dry-run] write_insight: %s", insight.insight_type)
            return insight.insight_id
        try:
            col.insert_one(insight.to_dict())
            return insight.insight_id
        except Exception as e:
            logger.error("[MongoStore] write_insight failed: %s", e)
            return None

    def get_insights_for_persona(self, persona_id: str) -> list[dict]:
        col = self._col(self.LEARNING_COLLECTION)
        if col is None:
            return []
        try:
            return list(col.find({"persona_id": persona_id, "source": "sai_test_harness"},
                                 {"_id": 0}).sort("proposed_at", -1))
        except Exception as e:
            logger.error("[MongoStore] get_insights_for_persona failed: %s", e)
            return []

    def update_insight_status(self, insight_id: str,
                              status: str,
                              reviewed_by: Optional[str] = None) -> bool:
        col = self._col(self.LEARNING_COLLECTION)
        if col is None:
            return True
        try:
            from datetime import datetime, timezone
            update = {"$set": {"review_status": status,
                               "reviewed_at": datetime.now(timezone.utc).isoformat()}}
            if reviewed_by:
                update["$set"]["reviewed_by"] = reviewed_by
            col.update_one({"insight_id": insight_id}, update)
            return True
        except Exception as e:
            logger.error("[MongoStore] update_insight_status failed: %s", e)
            return False

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
