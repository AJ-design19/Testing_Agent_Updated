"""
IO Logger — records every SAI INPUT, SAI OUTPUT, DOM snapshot URI, and
screenshot URI for each test step.

The record structure produced per step:
  {
    "step_id":         str,
    "surface_id":      str,
    "persona_id":      str,
    "timestamp":       ISO-8601,
    "action":          str,
    "sai_input":       {
        "prompt":       str,            # exact text sent or action label
        "action":       str,
        "selectors":    list[str],
    },
    "sai_output":      {
        "full_text":    str,            # full SAI response text
        "sub_agents":   list[str],      # agent tabs detected after action
        "tool_calls":   list[dict],     # tool/sub-agent invocations (if parseable)
        "result":       str,            # short result summary
    },
    "dom_snapshot_uri":  str | None,    # path to saved DOM snapshot JSON
    "screenshot_uri":    str | None,    # path(s) to screenshot PNG(s)
    "viewport_screenshots": list[str],  # paths for scroll-increment captures
    "status":          "pass"|"fail"|"skip"|"partial",
    "escalation":      str | None,      # reason if Playwright fallback used
    "duration_ms":     int,
    "notes":           str,
  }
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class IOLogger:
    """
    Thread-safe per-run log of every step's INPUT → OUTPUT pair plus
    DOM/screenshot evidence.
    """

    def __init__(self, run_id: str, surface_id: str, persona_id: str = ""):
        self.run_id = run_id
        self.surface_id = surface_id
        self.persona_id = persona_id
        self._steps: list[dict] = []
        self._current: Optional[dict] = None
        self._start_ts: Optional[float] = None
        self._log_dir = os.path.join("reports", "surface_logs", run_id)
        os.makedirs(self._log_dir, exist_ok=True)

    # ── Step lifecycle ────────────────────────────────────────────────────────

    def begin_step(
        self,
        step_id: str,
        action: str,
        sai_prompt: Optional[str] = None,
        selectors: Optional[list] = None,
    ) -> None:
        import time
        self._start_ts = time.monotonic()
        self._current = {
            "step_id":     step_id,
            "surface_id":  self.surface_id,
            "persona_id":  self.persona_id,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action":      action,
            "sai_input": {
                "prompt":    sai_prompt or "",
                "action":    action,
                "selectors": selectors or [],
            },
            "sai_output": {
                "full_text":  "",
                "sub_agents": [],
                "tool_calls": [],
                "result":     "",
            },
            "dom_snapshot_uri":     None,
            "screenshot_uri":       None,
            "viewport_screenshots": [],
            "status":               "in_progress",
            "escalation":           None,
            "duration_ms":          0,
            "notes":                "",
        }
        logger.debug("[IOLogger] Step begin: %s / %s", step_id, action)

    def set_sai_output(
        self,
        full_text: str = "",
        sub_agents: Optional[list] = None,
        tool_calls: Optional[list] = None,
        result: str = "",
    ) -> None:
        if self._current:
            self._current["sai_output"]["full_text"]  = full_text
            self._current["sai_output"]["sub_agents"] = sub_agents or []
            self._current["sai_output"]["tool_calls"] = tool_calls or []
            self._current["sai_output"]["result"]     = result

    def set_dom_snapshot(self, snapshot_data: dict) -> Optional[str]:
        """Save DOM snapshot JSON and return path."""
        if not self._current:
            return None
        step_id = self._current["step_id"]
        path = os.path.join(self._log_dir, f"dom_{step_id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot_data, f, indent=2, default=str)
            self._current["dom_snapshot_uri"] = path
            return path
        except Exception as e:
            logger.warning("[IOLogger] Could not save DOM snapshot: %s", e)
            return None

    def set_screenshot(self, path: str) -> None:
        if self._current and path:
            self._current["screenshot_uri"] = path

    def add_viewport_screenshot(self, path: str) -> None:
        if self._current and path:
            self._current["viewport_screenshots"].append(path)

    def set_escalation(self, reason: str) -> None:
        if self._current:
            self._current["escalation"] = reason
            logger.info("[IOLogger] Escalated to Playwright: %s", reason)

    def set_notes(self, notes: str) -> None:
        if self._current:
            self._current["notes"] = notes

    def finish_step(self, status: str = "pass") -> dict:
        import time
        if not self._current:
            return {}
        elapsed_ms = int((time.monotonic() - (self._start_ts or 0)) * 1000)
        self._current["status"]      = status
        self._current["duration_ms"] = elapsed_ms
        record = dict(self._current)
        self._steps.append(record)
        self._current  = None
        self._start_ts = None
        logger.info("[IOLogger] Step done: %s → %s (%dms)",
                    record["step_id"], status, elapsed_ms)
        return record

    # ── Accessors ────────────────────────────────────────────────────────────

    def get_steps(self) -> list[dict]:
        return list(self._steps)

    def get_summary(self) -> dict:
        total    = len(self._steps)
        passed   = sum(1 for s in self._steps if s["status"] == "pass")
        failed   = sum(1 for s in self._steps if s["status"] == "fail")
        skipped  = sum(1 for s in self._steps if s["status"] == "skip")
        partial  = sum(1 for s in self._steps if s["status"] == "partial")
        escalated = sum(1 for s in self._steps if s.get("escalation"))
        return {
            "surface_id": self.surface_id,
            "run_id":     self.run_id,
            "total":      total,
            "passed":     passed,
            "failed":     failed,
            "skipped":    skipped,
            "partial":    partial,
            "escalated":  escalated,
            "pass_rate":  round(passed / total * 100, 1) if total else 0.0,
        }

    def save_to_file(self) -> str:
        path = os.path.join(self._log_dir, "io_log.json")
        payload = {
            "run_id":     self.run_id,
            "surface_id": self.surface_id,
            "persona_id": self.persona_id,
            "generated":  datetime.now(timezone.utc).isoformat(),
            "summary":    self.get_summary(),
            "steps":      self._steps,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        logger.info("[IOLogger] Log saved: %s", path)
        return path
