"""
Action recording system.

Records every action the testing agent takes as a structured, timestamped step log.
Produces a full audit trail that is sent to the LLM Judge alongside screenshots.

ActionRecorder is intentionally lightweight — it wraps an in-memory log and
flushes to JSON at the end of each test run.
"""

import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"


@dataclass
class ActionStep:
    step_number: int
    timestamp: str
    action_type: str        # e.g. "send_prompt", "psi_question", "psi_answer", "navigate_tab", "screenshot"
    description: str        # human-readable description of what happened
    agent_tab: Optional[str] = None       # e.g. "AIA", "AGP", "ETL", "App Studio"
    sub_tab: Optional[str] = None         # e.g. "Overview", "Output", "Questions", "Thinking"
    content_snapshot: Optional[str] = None  # first 300 chars of relevant content
    screenshot_path: Optional[str] = None
    success: bool = True
    notes: Optional[str] = None


@dataclass
class TestRunRecord:
    run_id: str
    persona_id: str
    persona_name: str
    workflow_id: str
    workflow_title: str
    start_time: str
    end_time: Optional[str] = None
    steps: list = field(default_factory=list)  # list of ActionStep dicts
    psi_conversation: list = field(default_factory=list)  # [{role, text}]
    canvas_evidence: dict = field(default_factory=dict)
    judge_verdict: Optional[dict] = None
    overall_status: str = "in_progress"   # "pass" | "partial" | "fail" | "error"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ActionRecorder:
    """
    Records all test actions for a single workflow run.
    Call record() after each significant action; call finish() at end of run.
    """

    def __init__(self, run_id: str, persona: dict, workflow: dict):
        self.run_id = run_id
        self.persona = persona
        self.workflow = workflow
        self._step_counter = 0
        self.record_obj = TestRunRecord(
            run_id=run_id,
            persona_id=persona.get("id", "unknown"),
            persona_name=persona.get("name", "unknown"),
            workflow_id=workflow.get("id", "unknown"),
            workflow_title=workflow.get("title", "unknown"),
            start_time=self._now(),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(
        self,
        action_type: str,
        description: str,
        agent_tab: Optional[str] = None,
        sub_tab: Optional[str] = None,
        content_snapshot: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        success: bool = True,
        notes: Optional[str] = None,
    ) -> ActionStep:
        step = ActionStep(
            step_number=self._step_counter,
            timestamp=self._now(),
            action_type=action_type,
            description=description,
            agent_tab=agent_tab,
            sub_tab=sub_tab,
            content_snapshot=content_snapshot[:300] if content_snapshot else None,
            screenshot_path=screenshot_path,
            success=success,
            notes=notes,
        )
        self.record_obj.steps.append(asdict(step))
        self._step_counter += 1
        logger.info("[Recorder] Step %d [%s]: %s", step.step_number, action_type, description[:80])
        return step

    def set_psi_conversation(self, conversation_log: list[dict]) -> None:
        self.record_obj.psi_conversation = conversation_log

    def set_canvas_evidence(self, canvas_evidence: dict) -> None:
        self.record_obj.canvas_evidence = canvas_evidence

    def set_judge_verdict(self, verdict: dict) -> None:
        self.record_obj.judge_verdict = verdict

    def finish(self, overall_status: str = "pass") -> dict:
        self.record_obj.end_time = self._now()
        self.record_obj.overall_status = overall_status
        result = self.record_obj.to_dict()
        self._save(result)
        return result

    def _save(self, data: dict) -> None:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        filename = os.path.join(REPORTS_DIR, f"{self.run_id}.json")
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("[Recorder] Run record saved: %s", filename)
        except Exception as e:
            logger.error("[Recorder] Failed to save run record: %s", e)

    def get_step_count(self) -> int:
        return self._step_counter

    def get_record(self) -> dict:
        return self.record_obj.to_dict()
