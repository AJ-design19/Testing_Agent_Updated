"""
SAI Self-Learning Module.

After every SAI interaction the agent observes and stores:
  - How long SAI took to respond (per round, per workflow type)
  - What kind of questions SAI tends to ask for each workflow type
  - What input patterns produced faster / better responses
  - Recommended wait times and phrasing adjustments for future runs

All observations are persisted to:
  learning/sai_observations_<workflow_id>.json

On startup the agent loads this file and uses the learned data to:
  - Set adaptive wait times (if SAI typically takes 45s, don't poll for 5 min)
  - Prefer phrasing patterns that previously triggered quick, complete handoffs
  - Log which question types recur so the answer engine can pre-cache answers
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

LEARNING_DIR = "learning"


class SAILearning:
    """
    Observes every SAI interaction and persists structured learning data
    per workflow type.  Provides adaptive recommendations to the handler.
    """

    def __init__(self, workflow_id: str, workflow_title: str = ""):
        self.workflow_id    = workflow_id
        self.workflow_title = workflow_title
        self._path          = self._obs_path(workflow_id)
        self._data          = self._load()
        # In-memory state for the current run
        self._run_start: Optional[float]  = None
        self._round_start: Optional[float] = None
        self._current_round: int           = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def start_run(self) -> None:
        """Call once at the start of a workflow run."""
        import time
        self._run_start = time.monotonic()
        logger.info("[SAILearning] Run started for workflow %s", self.workflow_id)

    def start_round(self, round_num: int) -> None:
        """Call at the start of each conversation round."""
        import time
        self._round_start   = time.monotonic()
        self._current_round = round_num

    def record_sai_response(
        self,
        round_num: int,
        sai_message: str,
        input_sent: str,
        response_time_s: float,
        was_question: bool,
        was_completion: bool,
        was_card: bool,
    ) -> None:
        """
        Record one SAI response observation.  Called after every response
        is received in the conversation loop.
        """
        obs = {
            "ts":               datetime.now(timezone.utc).isoformat(),
            "round":            round_num,
            "response_time_s":  round(response_time_s, 2),
            "was_question":     was_question,
            "was_completion":   was_completion,
            "was_card":         was_card,
            "input_length":     len(input_sent),
            "output_length":    len(sai_message),
            "question_keywords": self._extract_question_keywords(sai_message) if was_question else [],
            "input_preview":    input_sent[:200],
            "output_preview":   sai_message[:300],
        }
        self._data["observations"].append(obs)
        self._update_aggregates(obs)
        self._save()
        logger.info(
            "[SAILearning] Round %d: %.1fs response | question=%s | completion=%s | card=%s",
            round_num, response_time_s, was_question, was_completion, was_card,
        )

    def get_recommended_wait_s(self) -> float:
        """
        Return the recommended timeout for waiting for SAI to respond,
        based on observed response times for this workflow type.
        Defaults to 120 s if no history exists.
        """
        times = [o["response_time_s"] for o in self._data["observations"]
                 if o.get("response_time_s", 0) > 0]
        if not times:
            return 120.0
        avg = sum(times) / len(times)
        p90 = sorted(times)[int(len(times) * 0.9)]
        # Recommended wait = p90 + 30 s buffer, capped between 60 and 300 s
        recommended = min(300.0, max(60.0, p90 + 30.0))
        logger.info(
            "[SAILearning] Recommended wait: %.0fs (avg=%.1fs p90=%.1fs n=%d)",
            recommended, avg, p90, len(times),
        )
        return recommended

    def get_common_question_keywords(self) -> list[str]:
        """
        Return the most frequently observed question keywords for this
        workflow type — useful for pre-loading answers.
        """
        freq: dict[str, int] = {}
        for o in self._data["observations"]:
            for kw in o.get("question_keywords", []):
                freq[kw] = freq.get(kw, 0) + 1
        return sorted(freq, key=lambda k: -freq[k])[:20]

    def get_best_input_patterns(self) -> list[str]:
        """
        Return input previews that were followed by fast completions
        (response time ≤ median and was_completion=True).
        """
        completion_obs = [
            o for o in self._data["observations"]
            if o.get("was_completion") and o.get("response_time_s", 999) > 0
        ]
        if not completion_obs:
            return []
        median_t = sorted(o["response_time_s"] for o in completion_obs)[len(completion_obs) // 2]
        fast = [o["input_preview"] for o in completion_obs
                if o["response_time_s"] <= median_t]
        return fast[:5]

    def get_summary(self) -> dict:
        """Return a summary dict suitable for storing in the run report."""
        obs = self._data["observations"]
        if not obs:
            return {"workflow_id": self.workflow_id, "observations": 0}
        times = [o["response_time_s"] for o in obs if o.get("response_time_s", 0) > 0]
        return {
            "workflow_id":              self.workflow_id,
            "workflow_title":           self.workflow_title,
            "total_observations":       len(obs),
            "avg_response_time_s":      round(sum(times) / len(times), 2) if times else None,
            "min_response_time_s":      round(min(times), 2) if times else None,
            "max_response_time_s":      round(max(times), 2) if times else None,
            "recommended_wait_s":       self.get_recommended_wait_s(),
            "common_question_keywords": self.get_common_question_keywords(),
            "best_input_patterns":      self.get_best_input_patterns(),
            "total_questions":          sum(1 for o in obs if o.get("was_question")),
            "total_completions":        sum(1 for o in obs if o.get("was_completion")),
            "total_cards":              sum(1 for o in obs if o.get("was_card")),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _obs_path(workflow_id: str) -> str:
        os.makedirs(LEARNING_DIR, exist_ok=True)
        return os.path.join(LEARNING_DIR, f"sai_observations_{workflow_id}.json")

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(
                    "[SAILearning] Loaded %d prior observations for %s",
                    len(data.get("observations", [])), self.workflow_id,
                )
                return data
            except Exception as e:
                logger.warning("[SAILearning] Could not load observations: %s", e)
        return {
            "workflow_id":    self.workflow_id,
            "workflow_title": self.workflow_title,
            "observations":   [],
            "aggregates": {
                "total_runs":         0,
                "avg_response_time_s": None,
                "question_freq":       {},
            },
        }

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("[SAILearning] Could not save observations: %s", e)

    def _update_aggregates(self, obs: dict) -> None:
        agg = self._data.setdefault("aggregates", {
            "total_runs": 0, "avg_response_time_s": None, "question_freq": {},
        })
        # Rolling average response time
        times = [o["response_time_s"] for o in self._data["observations"]
                 if o.get("response_time_s", 0) > 0]
        if times:
            agg["avg_response_time_s"] = round(sum(times) / len(times), 2)
        # Question keyword frequency
        for kw in obs.get("question_keywords", []):
            agg["question_freq"][kw] = agg["question_freq"].get(kw, 0) + 1

    @staticmethod
    def _extract_question_keywords(text: str) -> list[str]:
        """
        Extract meaningful question keywords from SAI's message.
        Returns normalised lowercase tokens that appear in question sentences.
        """
        # Split into sentences that end with '?'
        question_sentences = [
            s.strip() for s in re.split(r'[.!]', text)
            if '?' in s and len(s.strip()) > 10
        ]
        stopwords = {
            "the", "a", "an", "is", "are", "you", "your", "what", "which",
            "how", "do", "does", "would", "could", "will", "can", "please",
            "any", "some", "for", "and", "or", "of", "to", "in", "on", "at",
            "it", "this", "that", "have", "has", "be", "with", "from", "i",
            "we", "they", "their", "our", "about", "need", "want", "like",
        }
        keywords: list[str] = []
        for sentence in question_sentences[:5]:  # max 5 question sentences
            words = re.findall(r'\b[a-z]{3,}\b', sentence.lower())
            keywords.extend(w for w in words if w not in stopwords)
        # Deduplicate preserving order
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:15]
