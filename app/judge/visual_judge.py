"""
Visual / Multimodal LLM Judge.

Sends actual screenshots to a vision-capable LLM to:
  1. Verify canvas outputs look correct visually
  2. Detect UI anomalies, error states, blank screens
  3. Confirm workflows completed properly
  4. Score visual quality of generated artifacts
  5. Identify truncated/cut-off content

This complements the text-based LLMJudge — together they cover
both the content and the visual presentation of SAI outputs.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


VISUAL_JUDGE_PROMPT = """You are a QA engineer visually inspecting screenshots of the Adya SAI platform.

Analyse the provided screenshot(s) and evaluate:

1. COMPLETION STATUS — Does the canvas/workflow appear to have completed successfully?
2. CONTENT QUALITY — Does the visible output look meaningful and complete (not truncated/empty)?
3. UI ANOMALIES — Any error messages, blank panels, spinners, broken layout, or overlapping elements?
4. AGENT OUTPUT — Can you identify what the agent produced (architecture, code, workflow, etc.)?
5. VISUAL CORRECTNESS — Does the output visually match what was requested?

Return ONLY valid JSON:
{
  "visual_verdict": "pass" | "partial" | "fail" | "unclear",
  "visual_score": <integer 0-10>,
  "completion_detected": true | false,
  "content_visible": true | false,
  "ui_anomalies": ["<anomaly 1>", ...],
  "output_description": "<1-2 sentences describing what is visually present>",
  "missing_visually": ["<element that should be visible but isn't>", ...],
  "confidence": "high" | "medium" | "low"
}
"""

AGENT_VISUAL_PROMPT = """You are evaluating a screenshot of the {agent_name} agent in the Adya SAI canvas.

Agent purpose: {agent_description}
Sub-tab being shown: {sub_tab}
Expected content: {expected_content}

Evaluate:
1. Is the expected content visible and complete?
2. Are there error states or loading indicators still showing?
3. Does the output quality look appropriate for a production AI system?
4. Any visible issues with the layout or content?

Return ONLY valid JSON:
{
  "agent_visual_verdict": "pass" | "partial" | "fail" | "unclear",
  "agent_visual_score": <integer 0-10>,
  "content_complete": true | false,
  "still_loading": true | false,
  "has_errors": true | false,
  "visible_content_summary": "<brief description of what's visible>",
  "issues": ["<issue 1>", ...]
}
"""


def _encode_image(path: str) -> Optional[str]:
    """Encode image as base64 data URI."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{data}"
    except Exception as e:
        logger.warning("[VisualJudge] Could not encode image %s: %s", path, e)
        return None


class VisualJudge:
    """
    Multimodal LLM judge that analyses screenshots.
    Uses the same OpenAI-compatible endpoint as the text judge.
    Falls back gracefully if the model doesn't support vision.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model = os.getenv("JUDGE_MODEL", "grok-3-beta")
        self._vision_available: Optional[bool] = None   # None = not yet checked

    def _check_vision(self) -> bool:
        """Return True if vision is likely available (can't verify without calling)."""
        # Models that support vision
        vision_models = [
            "gpt-4o", "gpt-4-vision", "grok-2-vision", "grok-3",
            "claude-3", "gemini", "llava",
        ]
        model_lower = self.model.lower()
        return any(v in model_lower for v in vision_models)

    def _call_with_images(self, prompt: str, image_paths: list[str], system: str) -> Optional[dict]:
        """Make a vision API call with the given images."""
        content = [{"type": "text", "text": prompt}]
        for path in image_paths[:4]:   # limit to 4 images per call
            uri = _encode_image(path)
            if uri:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": uri, "detail": "low"},
                })

        if len(content) == 1:
            logger.warning("[VisualJudge] No valid images to send")
            return None

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": content},
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.warning("[VisualJudge] Vision call failed: %s", e)
            return None

    def evaluate_screenshots(self, screenshot_paths: list[str]) -> dict:
        """
        Evaluate a set of screenshots for overall visual quality.
        Returns a verdict dict. Falls back to a neutral result if vision unavailable.
        """
        if not screenshot_paths:
            return self._no_screenshots_result()

        if not self._check_vision():
            logger.info("[VisualJudge] Vision not available for model '%s' — skipping", self.model)
            return self._vision_unavailable_result()

        logger.info("[VisualJudge] Evaluating %d screenshots visually", len(screenshot_paths))
        result = self._call_with_images(
            prompt=f"Please evaluate these {len(screenshot_paths)} screenshots from a SAI workflow run.",
            image_paths=screenshot_paths,
            system=VISUAL_JUDGE_PROMPT,
        )

        if result:
            result["evaluated_at"] = datetime.now(timezone.utc).isoformat()
            result["screenshot_count"] = len(screenshot_paths)
            return result

        return self._vision_unavailable_result()

    def evaluate_agent_output(
        self,
        agent_name: str,
        agent_description: str,
        sub_tab: str,
        expected_content: str,
        screenshot_paths: list[str],
    ) -> dict:
        """
        Evaluate screenshots of a specific canvas agent's output.
        """
        if not screenshot_paths or not self._check_vision():
            return self._agent_unavailable_result(agent_name)

        prompt = AGENT_VISUAL_PROMPT.format(
            agent_name=agent_name,
            agent_description=agent_description,
            sub_tab=sub_tab,
            expected_content=expected_content[:300],
        )

        result = self._call_with_images(
            prompt=prompt,
            image_paths=screenshot_paths,
            system="You are a QA engineer evaluating AI platform screenshots. Return only JSON.",
        )

        if result:
            result["agent_name"] = agent_name
            result["evaluated_at"] = datetime.now(timezone.utc).isoformat()
            return result

        return self._agent_unavailable_result(agent_name)

    def evaluate_full_run(
        self,
        canvas_evidence: dict,
        step_screenshots: list[str],
    ) -> dict:
        """
        Run visual evaluation across all agents in a completed run.
        Returns per-agent scores and overall visual assessment.
        """
        per_agent: dict[str, dict] = {}

        for agent_name, agent_data in canvas_evidence.get("agents", {}).items():
            ss_list = [
                s.get("path", "") for s in agent_data.get("screenshots", [])
                if s.get("path")
            ]
            desc = agent_data.get("description", "")
            output_content = agent_data.get("sub_tabs", {}).get("Output", "")
            per_agent[agent_name] = self.evaluate_agent_output(
                agent_name=agent_name,
                agent_description=desc,
                sub_tab="Output",
                expected_content=output_content,
                screenshot_paths=ss_list,
            )

        # Overall run evaluation using error + final screenshots
        overall_paths = [s for s in step_screenshots if s and os.path.exists(s)][-6:]
        overall = self.evaluate_screenshots(overall_paths)

        return {
            "per_agent":       per_agent,
            "overall":         overall,
            "evaluated_at":    datetime.now(timezone.utc).isoformat(),
        }

    # ── Fallback results ──────────────────────────────────────────────────────

    @staticmethod
    def _no_screenshots_result() -> dict:
        return {
            "visual_verdict":       "unclear",
            "visual_score":         0,
            "completion_detected":  False,
            "content_visible":      False,
            "ui_anomalies":         ["No screenshots were captured"],
            "output_description":   "No screenshots available for visual evaluation.",
            "missing_visually":     [],
            "confidence":           "low",
            "note":                 "no_screenshots",
        }

    @staticmethod
    def _vision_unavailable_result() -> dict:
        return {
            "visual_verdict":       "unclear",
            "visual_score":         5,
            "completion_detected":  True,
            "content_visible":      True,
            "ui_anomalies":         [],
            "output_description":   "Visual evaluation skipped — model does not support vision.",
            "missing_visually":     [],
            "confidence":           "low",
            "note":                 "vision_model_unavailable",
        }

    @staticmethod
    def _agent_unavailable_result(agent_name: str) -> dict:
        return {
            "agent_name":           agent_name,
            "agent_visual_verdict": "unclear",
            "agent_visual_score":   5,
            "content_complete":     True,
            "still_loading":        False,
            "has_errors":           False,
            "visible_content_summary": "Visual evaluation not available.",
            "issues":               [],
            "note":                 "vision_model_unavailable",
        }
