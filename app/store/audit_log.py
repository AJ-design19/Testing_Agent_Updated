"""
Append-only audit log (§13, §13.4).

Every approval, rejection, anonymisation, and global-tier promotion is logged here.
The log is immutable — no record is ever updated or deleted.
Writes go to:
  1. A local JSONL file  (reports/audit_log.jsonl)  — always available
  2. Postgres audit_log table                        — when DATABASE_URL is set
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.store.models import AuditLogEntry, AuditAction

logger = logging.getLogger(__name__)

AUDIT_LOG_FILE = "reports/audit_log.jsonl"

try:
    import psycopg2
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    TEXT PRIMARY KEY,
    action      TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    actor_role  TEXT,
    target_id   TEXT NOT NULL,
    target_tier TEXT,
    payload     JSONB DEFAULT '{}',
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    immutable   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action  ON audit_log(action, timestamp DESC);
"""


class AuditLog:
    """
    Append-only audit log.
    Dual-writes to JSONL file (always) + Postgres (when available).
    Provides no update or delete operations by design.
    """

    def __init__(self, pg_conn=None):
        self._pg_conn = pg_conn
        os.makedirs("reports", exist_ok=True)

        if pg_conn and _PG_AVAILABLE:
            try:
                with pg_conn.cursor() as cur:
                    cur.execute(_AUDIT_DDL)
            except Exception as e:
                logger.warning("[AuditLog] Could not create audit_log table: %s", e)

    def append(
        self,
        action: str,          # AuditAction value
        actor_id: str,
        actor_role: str,
        target_id: str,
        target_tier: str = "",
        payload: Optional[dict] = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            target_id=target_id,
            target_tier=target_tier,
            payload=payload or {},
        )
        self._write_jsonl(entry)
        self._write_pg(entry)
        logger.info("[AuditLog] %s by %s on %s (tier=%s)",
                    action, actor_id, target_id, target_tier)
        return entry

    def _write_jsonl(self, entry: AuditLogEntry) -> None:
        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), default=str) + "\n")
        except Exception as e:
            logger.error("[AuditLog] JSONL write failed: %s", e)

    def _write_pg(self, entry: AuditLogEntry) -> None:
        if not self._pg_conn or not _PG_AVAILABLE:
            return
        try:
            d = entry.to_dict()
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_log
                       (audit_id, action, actor_id, actor_role, target_id,
                        target_tier, payload, timestamp, immutable)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                       ON CONFLICT (audit_id) DO NOTHING""",
                    (d["audit_id"], d["action"], d["actor_id"], d["actor_role"],
                     d["target_id"], d["target_tier"],
                     json.dumps(d["payload"]), d["timestamp"], True),
                )
        except Exception as e:
            logger.error("[AuditLog] Postgres write failed: %s", e)

    def tail(self, n: int = 50) -> list[dict]:
        """Read the last N lines from the JSONL audit log."""
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(l) for l in lines[-n:] if l.strip()]
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error("[AuditLog] tail failed: %s", e)
            return []

    def entries_for_target(self, target_id: str) -> list[dict]:
        """Return all audit entries for a given target_id from the JSONL log."""
        try:
            result = []
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("target_id") == target_id:
                        result.append(entry)
            return result
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error("[AuditLog] entries_for_target failed: %s", e)
            return []
