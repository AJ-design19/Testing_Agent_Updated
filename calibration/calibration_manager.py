"""
Calibration set manager (§12.1, §14.2, §15).

The calibration set is:
  - 200 held-out steps owned by QA
  - Locked once frozen for v1
  - Used to measure hybrid-judge agreement with human-only adjudication
  - Future expansions require a QA-led decision documented in the changelog

Metrics from the calibration set (§14.2):
  - Hybrid-judge agreement with human-only adjudication; target ≥ 90%
  - False-positive rate (LLM-pass, human-fail); target ≤ 3%

Threshold changes require re-running the calibration suite and recording
the new agreement rate (§13.1).
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CALIBRATION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "calibration",
)
CALIBRATION_RESULTS_FILE = "reports/calibration_results.json"
CALIBRATION_LOCK_FILE = os.path.join(CALIBRATION_DIR, ".frozen")

AGREEMENT_TARGET = 0.90      # §14.2 target
FALSE_POSITIVE_TARGET = 0.03  # §14.2 target


@dataclass
class CalibrationStep:
    """A single held-out calibration step with human ground-truth label."""
    step_id: str
    persona_id: str
    journey_id: str
    step_label: str
    psi_conversation_snippet: str
    canvas_snapshot: str
    human_verdict: str          # "pass" | "partial" | "fail"
    human_process_score: int    # 0-10
    human_output_score: int     # 0-10
    notes: str = ""


@dataclass
class CalibrationRunResult:
    run_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_steps: int = 0
    agreement_count: int = 0
    false_positive_count: int = 0    # LLM=pass, human=fail
    false_negative_count: int = 0    # LLM=fail, human=pass
    agreement_rate: float = 0.0
    false_positive_rate: float = 0.0
    meets_agreement_target: bool = False
    meets_fp_target: bool = False
    per_step_results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class CalibrationManager:
    """
    Loads and runs the held-out calibration set.
    Computes agreement rate and false-positive rate.
    Locks the set once frozen; rejects writes when locked.
    """

    def __init__(self):
        os.makedirs(CALIBRATION_DIR, exist_ok=True)

    def is_frozen(self) -> bool:
        return os.path.exists(CALIBRATION_LOCK_FILE)

    def freeze(self, qa_approver: str) -> None:
        """Lock the calibration set. Only QA can call this."""
        if self.is_frozen():
            logger.warning("[Calibration] Already frozen")
            return
        with open(CALIBRATION_LOCK_FILE, "w") as f:
            f.write(f"Frozen by {qa_approver} at {datetime.now(timezone.utc).isoformat()}\n")
        logger.info("[Calibration] Calibration set frozen by %s", qa_approver)

    def load_steps(self) -> list[CalibrationStep]:
        """Load all calibration JSONL files from calibration/."""
        steps = []
        jsonl_path = os.path.join(CALIBRATION_DIR, "calibration_set.jsonl")
        if not os.path.exists(jsonl_path):
            logger.info("[Calibration] No calibration_set.jsonl found — returning empty set")
            return steps
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        d = json.loads(line)
                        steps.append(CalibrationStep(**d))
        except Exception as e:
            logger.error("[Calibration] Failed to load calibration set: %s", e)
        logger.info("[Calibration] Loaded %d calibration steps", len(steps))
        return steps

    def run_calibration(self, judge_fn) -> CalibrationRunResult:
        """
        Run the hybrid judge against all calibration steps and compare to human verdicts.
        judge_fn: callable(step: CalibrationStep) -> dict with keys correctness_verdict, process_score, output_score

        Returns CalibrationRunResult with agreement and false-positive rates.
        """
        steps = self.load_steps()
        if not steps:
            logger.warning("[Calibration] No steps to calibrate against")
            return CalibrationRunResult()

        result = CalibrationRunResult(total_steps=len(steps))
        per_step = []

        for step in steps:
            try:
                llm_verdict = judge_fn(step)
            except Exception as e:
                logger.error("[Calibration] judge_fn failed on step %s: %s", step.step_id, e)
                continue

            llm_v = llm_verdict.get("correctness_verdict", "fail")
            human_v = step.human_verdict
            agreed = (llm_v == human_v)
            is_fp = (llm_v == "pass" and human_v == "fail")
            is_fn = (llm_v == "fail" and human_v == "pass")

            if agreed:
                result.agreement_count += 1
            if is_fp:
                result.false_positive_count += 1
            if is_fn:
                result.false_negative_count += 1

            per_step.append({
                "step_id": step.step_id,
                "human_verdict": human_v,
                "llm_verdict": llm_v,
                "agreed": agreed,
                "false_positive": is_fp,
                "false_negative": is_fn,
                "llm_process_score": llm_verdict.get("process_score"),
                "human_process_score": step.human_process_score,
            })

        n = result.total_steps
        result.per_step_results = per_step
        result.agreement_rate = round(result.agreement_count / n, 4) if n else 0.0
        result.false_positive_rate = round(result.false_positive_count / n, 4) if n else 0.0
        result.meets_agreement_target = result.agreement_rate >= AGREEMENT_TARGET
        result.meets_fp_target = result.false_positive_rate <= FALSE_POSITIVE_TARGET

        logger.info(
            "[Calibration] Agreement: %.1f%% (target ≥%.0f%%) | FP rate: %.1f%% (target ≤%.0f%%)",
            result.agreement_rate * 100, AGREEMENT_TARGET * 100,
            result.false_positive_rate * 100, FALSE_POSITIVE_TARGET * 100,
        )

        self._save_results(result)
        return result

    def _save_results(self, result: CalibrationRunResult) -> None:
        os.makedirs("reports", exist_ok=True)
        try:
            existing = []
            if os.path.exists(CALIBRATION_RESULTS_FILE):
                with open(CALIBRATION_RESULTS_FILE, "r") as f:
                    existing = json.load(f)
            existing.append(result.to_dict())
            with open(CALIBRATION_RESULTS_FILE, "w") as f:
                json.dump(existing, f, indent=2, default=str)
        except Exception as e:
            logger.error("[Calibration] Failed to save results: %s", e)

    def latest_result(self) -> Optional[dict]:
        if not os.path.exists(CALIBRATION_RESULTS_FILE):
            return None
        try:
            with open(CALIBRATION_RESULTS_FILE, "r") as f:
                results = json.load(f)
            return results[-1] if results else None
        except Exception:
            return None
