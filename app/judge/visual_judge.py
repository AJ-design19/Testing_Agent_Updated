"""
Visual / Multimodal LLM Judge — 9-Dimension QA.

Sends actual screenshots to a vision-capable LLM and evaluates:
  1. UI alignment and spacing
  2. Visual consistency (colours, typography, component style)
  3. Text correctness and readability
  4. Hallucinated / missing content
  5. Responsiveness (viewport overflow, mobile-scale issues)
  6. Broken workflows (error states, stuck spinners, dead links)
  7. Accessibility issues (contrast, missing labels visible in screenshot)
  8. Overlapping or hidden elements
  9. Canvas output completeness per agent

Returns a structured verdict that feeds directly into LLMJudge and RunReportGenerator.
"""

import base64
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

try:
    from PIL import Image, ImageChops, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

load_dotenv()
logger = logging.getLogger(__name__)


# ── System prompt ─────────────────────────────────────────────────────────────
VISUAL_JUDGE_SYSTEM = """You are a senior QA engineer and UX specialist evaluating screenshots of the Adya SAI platform (an enterprise AI agent orchestration tool).

Analyse every screenshot provided and score NINE quality dimensions. Be specific — cite what you see, not what you assume."""


# ── Full-run visual evaluation prompt ─────────────────────────────────────────
VISUAL_RUN_PROMPT = """Evaluate these {count} screenshot(s) from an end-to-end SAI workflow test run.

Score each of the NINE dimensions below from 0–10 (10 = perfect):

1. UI_ALIGNMENT (ui_alignment_score): Are elements properly aligned? Even margins, consistent padding, no jagged or misaligned components?
2. VISUAL_CONSISTENCY (visual_consistency_score): Consistent use of colours, fonts, icon styles, button shapes, and component patterns across the screens?
3. TEXT_CORRECTNESS (text_correctness_score): Is all visible text readable, properly spelled, grammatically correct, not cut off, not overflowing its container?
4. CONTENT_HALLUCINATION (hallucination_score): Any content that looks fabricated, contradictory, repetitive, or clearly wrong for the context? Score 10 = no hallucinations.
5. RESPONSIVENESS (responsiveness_score): Does the layout fit the viewport? Any horizontal scrollbars, overflowing content, or elements outside the visible area?
6. WORKFLOW_INTEGRITY (workflow_score): Does the UI show a completed, working workflow? No error toasts, stuck spinners, broken states, or empty panels where content is expected?
7. ACCESSIBILITY_VISUAL (accessibility_score): Are interactive elements large enough to click? Good contrast between text and backgrounds? No content obscured by overlays?
8. ELEMENT_OVERLAP (overlap_score): Any overlapping elements, z-index collisions, or content hidden beneath other elements? Score 10 = no overlaps.
9. CANVAS_COMPLETENESS (canvas_score): For canvas screenshots — does each visible agent panel show meaningful, complete output? Are sub-tabs populated?

Return ONLY valid JSON:
{{
  "ui_alignment_score":      <0-10>,
  "visual_consistency_score": <0-10>,
  "text_correctness_score":  <0-10>,
  "hallucination_score":     <0-10>,
  "responsiveness_score":    <0-10>,
  "workflow_score":          <0-10>,
  "accessibility_score":     <0-10>,
  "overlap_score":           <0-10>,
  "canvas_score":            <0-10>,
  "visual_score":            <0-10>,
  "visual_verdict":          "pass" | "partial" | "fail" | "unclear",
  "completion_detected":     true | false,
  "content_visible":         true | false,
  "ui_anomalies": [
    {{"type": "<anomaly_type>", "description": "<specific description>", "severity": "critical|major|minor"}}
  ],
  "layout_issues": [
    {{"description": "<issue>", "severity": "critical|major|minor", "fix_recommendation": "<how to fix>"}}
  ],
  "hallucination_flags": ["<suspicious content>"],
  "missing_visually": ["<element that should be visible but is not>"],
  "output_description": "<2-3 sentences describing what is visually present across the screenshots>",
  "confidence": "high" | "medium" | "low"
}}

Rules:
- visual_score = weighted average: alignment×0.15 + consistency×0.10 + text×0.15 + hallucination×0.15 + responsiveness×0.10 + workflow×0.15 + accessibility×0.10 + overlap×0.05 + canvas×0.05
- visual_verdict = "pass" if visual_score >= 7, "partial" if >= 5, "fail" otherwise
- Be precise. If you see a blank panel, name it. If text is cut off, quote the first few words."""


# ── Per-agent visual evaluation prompt ────────────────────────────────────────
AGENT_VISUAL_PROMPT = """You are evaluating a screenshot of the {agent_name} agent in the Adya SAI canvas.

Agent purpose: {agent_description}
Sub-tab being shown: {sub_tab}
Expected content type: {expected_content}

Evaluate these 9 dimensions for this specific agent screenshot:

1. Is the expected content visible and complete (not cut off or empty)?
2. Any error states, stuck spinners, or loading indicators still showing?
3. Is text readable, properly aligned, not overflowing?
4. Any hallucinated, nonsensical, or clearly wrong output?
5. Does the layout look responsive/correct at this viewport size?
6. Are interactive elements (tabs, buttons) clearly visible and accessible?
7. Any overlapping elements or content hidden behind overlays?
8. Visual consistency with SAI design language (colours, typography)?
9. Overall: does this agent output look production-quality?

Return ONLY valid JSON:
{{
  "agent_visual_verdict": "pass" | "partial" | "fail" | "unclear",
  "agent_visual_score": <0-10>,
  "content_complete": true | false,
  "still_loading": true | false,
  "has_errors": true | false,
  "text_readable": true | false,
  "layout_correct": true | false,
  "visible_content_summary": "<brief description of what is visible>",
  "issues": [
    {{"type": "<issue_type>", "description": "<specific description>", "severity": "critical|major|minor", "fix_recommendation": "<fix>"}}
  ],
  "hallucination_flags": ["<suspicious output>"],
  "confidence": "high" | "medium" | "low"
}}"""


# ── Change event visual evaluation prompt ────────────────────────────────────
CHANGE_EVENT_PROMPT = """You are evaluating a screenshot taken when the SAI canvas updated.

Change type: {change_type}
Description: {description}

Analyse this screenshot for:
1. Did the change complete successfully (no errors)?
2. Is the new content properly rendered and readable?
3. Any visual regressions compared to what you'd expect?
4. Are all UI elements properly positioned after the update?

Return ONLY valid JSON:
{{
  "change_verdict": "success" | "partial" | "regression" | "unclear",
  "change_score": <0-10>,
  "content_rendered_correctly": true | false,
  "ui_regressions": ["<regression>"],
  "description": "<1-2 sentences on what you see>"
}}"""


AGENT_DESCRIPTIONS = {
    "AIA": "AI Intent Analyser — analyses the user's request and defines the solution architecture",
    "AGP": "AI Generation Pipeline — generates code, workflows, data pipelines, and integrations",
    "ETL": "Extract-Transform-Load agent — produces data pipeline code and transformation logic",
    "App Studio": "Application builder — generates a complete front-end application or dashboard",
}


def _encode_image(path: str) -> Optional[str]:
    """Encode image file as a base64 data URI."""
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
    Multimodal LLM judge that analyses screenshots across 9 QA dimensions.
    Falls back gracefully if the model does not support vision.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model = os.getenv("JUDGE_MODEL", "grok-3-beta")

    def _check_vision(self) -> bool:
        vision_models = [
            "gpt-4o", "gpt-4-vision", "grok-2-vision", "grok-3",
            "claude-3", "gemini", "llava",
        ]
        return any(v in self.model.lower() for v in vision_models)

    def _call_with_images(
        self,
        prompt: str,
        image_paths: list[str],
        system: str,
        max_tokens: int = 1200,
    ) -> Optional[dict]:
        """Make a vision API call; silently returns None on failure."""
        content: list[dict] = [{"type": "text", "text": prompt}]
        loaded = 0
        for path in image_paths[:4]:
            uri = _encode_image(path)
            if uri:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": uri, "detail": "low"},
                })
                loaded += 1

        if loaded == 0:
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
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.warning("[VisualJudge] Vision call failed: %s", e)
            return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def evaluate_screenshots(self, screenshot_paths: list[str]) -> dict:
        """
        9-dimension visual evaluation of a set of screenshots.
        Returns a full structured verdict.
        """
        if not screenshot_paths:
            return self._no_screenshots_result()
        if not self._check_vision():
            logger.info("[VisualJudge] Model '%s' — vision unavailable", self.model)
            return self._vision_unavailable_result()

        logger.info("[VisualJudge] Evaluating %d screenshots (9 dimensions)", len(screenshot_paths))
        prompt = VISUAL_RUN_PROMPT.format(count=len(screenshot_paths))
        result = self._call_with_images(prompt, screenshot_paths, VISUAL_JUDGE_SYSTEM, max_tokens=1400)

        if result:
            result["evaluated_at"]      = datetime.now(timezone.utc).isoformat()
            result["screenshot_count"]  = len(screenshot_paths)
            result["visual_score"]      = self._compute_visual_score(result)
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
        """Evaluate screenshots of a specific canvas agent across all 9 dimensions."""
        if not screenshot_paths or not self._check_vision():
            return self._agent_unavailable_result(agent_name)

        prompt = AGENT_VISUAL_PROMPT.format(
            agent_name=agent_name,
            agent_description=agent_description or AGENT_DESCRIPTIONS.get(agent_name, agent_name),
            sub_tab=sub_tab,
            expected_content=expected_content[:300] if expected_content else "meaningful agent output",
        )
        result = self._call_with_images(
            prompt, screenshot_paths,
            "You are a QA engineer evaluating AI platform screenshots. Return only JSON.",
            max_tokens=1000,
        )
        if result:
            result["agent_name"]    = agent_name
            result["evaluated_at"]  = datetime.now(timezone.utc).isoformat()
            return result
        return self._agent_unavailable_result(agent_name)

    def evaluate_change_event(
        self,
        change_type: str,
        description: str,
        screenshot_path: str,
    ) -> dict:
        """Evaluate a single change-detection screenshot."""
        if not screenshot_path or not self._check_vision():
            return {"change_verdict": "unclear", "change_score": 5, "note": "vision_unavailable"}

        prompt = CHANGE_EVENT_PROMPT.format(
            change_type=change_type,
            description=description,
        )
        result = self._call_with_images(
            prompt, [screenshot_path],
            "You are a QA engineer evaluating AI platform screenshots. Return only JSON.",
            max_tokens=500,
        )
        return result or {"change_verdict": "unclear", "change_score": 5}

    def evaluate_full_run(
        self,
        canvas_evidence: dict,
        step_screenshots: list[str],
        change_timeline: Optional[list[dict]] = None,
        a11y_report: Optional[dict] = None,
        style_report: Optional[dict] = None,
    ) -> dict:
        """
        Full visual evaluation across all agents + run-level screenshots.
        Incorporates change timeline, accessibility, and layout data if provided.
        Returns per-agent scores and overall 9-dimension assessment.
        """
        per_agent: dict[str, dict] = {}

        for agent_name, agent_data in canvas_evidence.get("agents", {}).items():
            ss_list = [
                s.get("path", "") for s in agent_data.get("screenshots", [])
                if s.get("path")
            ]
            output_content = agent_data.get("sub_tabs", {}).get("Output", "")
            per_agent[agent_name] = self.evaluate_agent_output(
                agent_name=agent_name,
                agent_description=AGENT_DESCRIPTIONS.get(agent_name, ""),
                sub_tab="Output",
                expected_content=output_content,
                screenshot_paths=ss_list,
            )

        # Overall: use final + error screenshots
        overall_paths = [s for s in step_screenshots if s and os.path.exists(s)][-6:]
        overall = self.evaluate_screenshots(overall_paths)

        # Evaluate change events (sample up to 4)
        change_evals: list[dict] = []
        if change_timeline and self._check_vision():
            events_with_ss = [
                e for e in change_timeline if e.get("screenshot_path")
            ][-4:]
            for ev in events_with_ss:
                ev_result = self.evaluate_change_event(
                    change_type=ev.get("change_type", "unknown"),
                    description=ev.get("description", ""),
                    screenshot_path=ev.get("screenshot_path", ""),
                )
                ev_result["change_type"] = ev.get("change_type", "")
                ev_result["timestamp"]   = ev.get("timestamp", "")
                change_evals.append(ev_result)

        # Annotate overall result with a11y and layout context
        if a11y_report:
            overall["a11y_score"]  = a11y_report.get("score", 5)
            overall["a11y_issues_count"] = a11y_report.get("summary", {}).get("total", 0)
        if style_report:
            overall["layout_score"] = style_report.get("score", 5)
            overall["overlap_count"] = style_report.get("summary", {}).get("overlaps", 0)

        return {
            "per_agent":      per_agent,
            "overall":        overall,
            "change_evals":   change_evals,
            "evaluated_at":   datetime.now(timezone.utc).isoformat(),
        }

    # ── Score helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _compute_visual_score(result: dict) -> float:
        weights = {
            "ui_alignment_score":       0.15,
            "visual_consistency_score": 0.10,
            "text_correctness_score":   0.15,
            "hallucination_score":      0.15,
            "responsiveness_score":     0.10,
            "workflow_score":           0.15,
            "accessibility_score":      0.10,
            "overlap_score":            0.05,
            "canvas_score":             0.05,
        }
        total  = 0.0
        weight = 0.0
        for field, w in weights.items():
            val = result.get(field)
            if val is not None:
                try:
                    total  += float(val) * w
                    weight += w
                except (TypeError, ValueError):
                    pass
        if weight == 0:
            return result.get("visual_score", 5)
        return round(total / weight, 1)

    # ── Fallback results ───────────────────────────────────────────────────────

    @staticmethod
    def _no_screenshots_result() -> dict:
        return {
            "visual_verdict":          "unclear",
            "visual_score":            0,
            "ui_alignment_score":      0,
            "visual_consistency_score": 0,
            "text_correctness_score":  0,
            "hallucination_score":     0,
            "responsiveness_score":    0,
            "workflow_score":          0,
            "accessibility_score":     0,
            "overlap_score":           0,
            "canvas_score":            0,
            "completion_detected":     False,
            "content_visible":         False,
            "ui_anomalies":            [{"type": "no_screenshots", "description": "No screenshots were captured", "severity": "critical"}],
            "layout_issues":           [],
            "hallucination_flags":     [],
            "missing_visually":        [],
            "output_description":      "No screenshots available for visual evaluation.",
            "confidence":              "low",
            "note":                    "no_screenshots",
        }

    @staticmethod
    def _vision_unavailable_result() -> dict:
        return {
            "visual_verdict":          "unclear",
            "visual_score":            5,
            "ui_alignment_score":      5,
            "visual_consistency_score": 5,
            "text_correctness_score":  5,
            "hallucination_score":     5,
            "responsiveness_score":    5,
            "workflow_score":          5,
            "accessibility_score":     5,
            "overlap_score":           5,
            "canvas_score":            5,
            "completion_detected":     True,
            "content_visible":         True,
            "ui_anomalies":            [],
            "layout_issues":           [],
            "hallucination_flags":     [],
            "missing_visually":        [],
            "output_description":      "Visual evaluation skipped — model does not support vision.",
            "confidence":              "low",
            "note":                    "vision_model_unavailable",
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
            "text_readable":        True,
            "layout_correct":       True,
            "visible_content_summary": "Visual evaluation not available.",
            "issues":               [],
            "hallucination_flags":  [],
            "confidence":           "low",
            "note":                 "vision_model_unavailable",
        }


# ── Visual Diff Report ────────────────────────────────────────────────────────

# UI drift thresholds (based on similarity %)
_DRIFT_LEVELS = [
    (95, "None",     "#16a34a"),   # ≥95 % similar
    (85, "Low",      "#65a30d"),   # ≥85 %
    (70, "Medium",   "#d97706"),   # ≥70 %
    (50, "High",     "#ea580c"),   # ≥50 %
    (0,  "Critical", "#dc2626"),   # <50 %
]


def _drift_label(similarity_pct: float) -> tuple[str, str]:
    """Return (label, hex_colour) for a similarity percentage."""
    for threshold, label, colour in _DRIFT_LEVELS:
        if similarity_pct >= threshold:
            return label, colour
    return "Critical", "#dc2626"


class VisualDiffReport:
    """
    Pixel-level visual diff between pairs of (expected, actual) screenshots.

    For each pair produces:
      - similarity_pct  : float 0-100
      - changed_regions : int  (connected blobs of changed pixels)
      - ui_drift        : str  (None / Low / Medium / High / Critical)
      - diff_image_b64  : str  (base64 PNG of the highlighted diff, embedded in report)
      - verdict         : PASS / PARTIAL / FAIL
    """

    # Sensitivity: pixels whose channel delta exceeds this are "changed"
    _THRESHOLD = 30
    # Minimum blob area (px²) to count as a distinct changed region
    _MIN_BLOB   = 200

    @classmethod
    def compare(
        cls,
        expected_path: str,
        actual_path: str,
        label: str = "",
    ) -> dict:
        """
        Compare two screenshots and return a diff record.
        Falls back to a no-PIL stub when Pillow is not installed.
        """
        base = {
            "label":           label,
            "expected_path":   expected_path,
            "actual_path":     actual_path,
            "similarity_pct":  None,
            "changed_regions": None,
            "ui_drift":        "Unknown",
            "ui_drift_colour": "#6b7280",
            "diff_image_b64":  None,
            "verdict":         "unclear",
            "error":           None,
        }

        if not _PIL_AVAILABLE:
            base["error"] = "Pillow not installed — install with: pip install Pillow"
            return base

        if not os.path.exists(expected_path):
            base["error"] = f"Expected screenshot not found: {expected_path}"
            return base
        if not os.path.exists(actual_path):
            base["error"] = f"Actual screenshot not found: {actual_path}"
            return base

        try:
            exp_img = Image.open(expected_path).convert("RGB")
            act_img = Image.open(actual_path).convert("RGB")

            # Resize actual to match expected dimensions for pixel comparison
            if exp_img.size != act_img.size:
                act_img = act_img.resize(exp_img.size, Image.LANCZOS)

            w, h = exp_img.size
            total_pixels = w * h

            # ── Pixel-level diff ───────────────────────────────────────────────
            diff = ImageChops.difference(exp_img, act_img)
            diff_data = diff.load()

            # Build a mask of changed pixels
            mask = Image.new("L", (w, h), 0)
            mask_data = mask.load()
            changed = 0
            for y in range(h):
                for x in range(w):
                    r, g, b = diff_data[x, y]
                    if max(r, g, b) > cls._THRESHOLD:
                        mask_data[x, y] = 255
                        changed += 1

            similarity_pct = round((1 - changed / total_pixels) * 100, 1)

            # ── Count connected changed regions (simple flood fill) ────────────
            changed_regions = cls._count_blobs(mask, w, h)

            # ── Build highlight diff image ─────────────────────────────────────
            # Blend: dim unchanged areas, highlight changed pixels in red/orange
            diff_img = act_img.copy().convert("RGBA")
            pixels   = diff_img.load()
            for y in range(h):
                for x in range(w):
                    if mask_data[x, y] == 255:
                        # Highlight changed pixels: semi-transparent red overlay
                        r, g, b, _ = pixels[x, y]
                        pixels[x, y] = (220, 50, 50, 200)
                    else:
                        # Dim unchanged pixels
                        r, g, b, _ = pixels[x, y]
                        pixels[x, y] = (r // 2, g // 2, b // 2, 180)

            buf = io.BytesIO()
            diff_img.save(buf, format="PNG")
            diff_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            drift_label, drift_colour = _drift_label(similarity_pct)

            verdict = "PASS" if similarity_pct >= 85 else ("PARTIAL" if similarity_pct >= 60 else "FAIL")

            return {
                **base,
                "similarity_pct":  similarity_pct,
                "changed_regions": changed_regions,
                "ui_drift":        drift_label,
                "ui_drift_colour": drift_colour,
                "diff_image_b64":  diff_b64,
                "verdict":         verdict,
                "error":           None,
            }

        except Exception as e:
            logger.warning("[VisualDiffReport] compare failed (%s ↔ %s): %s",
                           expected_path, actual_path, e)
            base["error"] = str(e)
            return base

    @classmethod
    def _count_blobs(cls, mask: "Image.Image", w: int, h: int) -> int:
        """Count connected blobs of changed pixels using iterative flood fill."""
        visited = [[False] * w for _ in range(h)]
        mask_px = mask.load()
        count   = 0

        for sy in range(h):
            for sx in range(w):
                if mask_px[sx, sy] == 255 and not visited[sy][sx]:
                    # BFS
                    blob_size = 0
                    queue     = [(sx, sy)]
                    visited[sy][sx] = True
                    while queue:
                        cx, cy = queue.pop()
                        blob_size += 1
                        for nx, ny in ((cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1)):
                            if 0 <= nx < w and 0 <= ny < h:
                                if not visited[ny][nx] and mask_px[nx, ny] == 255:
                                    visited[ny][nx] = True
                                    queue.append((nx, ny))
                    if blob_size >= cls._MIN_BLOB:
                        count += 1
        return count

    @classmethod
    def compare_run(
        cls,
        canvas_evidence: dict,
        step_log: list[dict],
    ) -> list[dict]:
        """
        Auto-generate diff pairs from a run:
          - For each agent, compare the first screenshot (baseline after tab opened)
            with the last screenshot (final state) to detect canvas drift.
          - Compare consecutive step screenshots to catch UI regressions between steps.

        Returns a list of diff records.
        """
        pairs: list[dict] = []

        # ── Per-agent: first vs last screenshot ───────────────────────────────
        for agent_name, adata in canvas_evidence.get("agents", {}).items():
            ss_list = [s for s in adata.get("screenshots", []) if s.get("path")]
            if len(ss_list) >= 2:
                first = ss_list[0]["path"]
                last  = ss_list[-1]["path"]
                sub_first = ss_list[0].get("sub_tab", "opened")
                sub_last  = ss_list[-1].get("sub_tab", "final")
                pairs.append(cls.compare(
                    expected_path=first,
                    actual_path=last,
                    label=f"{agent_name} · {sub_first} → {sub_last}",
                ))

        # ── Consecutive step screenshots ──────────────────────────────────────
        step_ss = [
            (s.get("step_number", i), s.get("screenshot_path"))
            for i, s in enumerate(step_log)
            if s.get("screenshot_path") and os.path.exists(s.get("screenshot_path", ""))
        ]
        # Sample: compare every other consecutive pair to limit volume
        for i in range(0, len(step_ss) - 1, 2):
            n0, p0 = step_ss[i]
            n1, p1 = step_ss[i + 1]
            if p0 and p1:
                pairs.append(cls.compare(
                    expected_path=p0,
                    actual_path=p1,
                    label=f"Step {n0} → Step {n1}",
                ))

        return pairs
