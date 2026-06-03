"""
Per-run HTML report generator.

Produces one self-contained HTML file per test run that includes:
  - Persona profile card (identity, goals, pain points, technical depth)
  - Workflow summary (title, initial prompt, expected agents, evaluation criteria)
  - Full journey timeline (every action step with timestamps, types, status badges)
  - SAI conversation transcript (Q&A with role styling)
  - Canvas agent evidence (per-agent sub-tab content + embedded screenshots)
  - LLM Judge verdict (scores, improvement flags, rationale)
  - Screenshot gallery (all captured screenshots embedded as base64)
  - Run metadata (run_id, duration, overall status)

Output path: reports/<run_id>_report.html
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"


# ── Score → colour mapping ────────────────────────────────────────────────────

def _score_colour(score: Optional[int]) -> str:
    if score is None:
        return "#6b7280"
    if score >= 8:
        return "#16a34a"
    if score >= 6:
        return "#d97706"
    return "#dc2626"


def _status_badge(status: str) -> tuple[str, str]:
    """Returns (background_colour, text) for a status string."""
    mapping = {
        "pass":    ("#16a34a", "PASS"),
        "partial": ("#d97706", "PARTIAL"),
        "fail":    ("#dc2626", "FAIL"),
        "error":   ("#7c3aed", "ERROR"),
        "in_progress": ("#2563eb", "IN PROGRESS"),
    }
    return mapping.get(status, ("#6b7280", status.upper()))


def _action_type_colour(action_type: str) -> str:
    mapping = {
        "login":          "#2563eb",
        "send_prompt":    "#7c3aed",
        "sai_loop":       "#0891b2",
        "canvas_wait":    "#6b7280",
        "canvas_capture": "#059669",
        "canvas_agent":   "#16a34a",
        "canvas_subtab":  "#0d9488",
        "judge":          "#d97706",
        "screenshot":     "#9333ea",
        "error":          "#dc2626",
    }
    return mapping.get(action_type, "#6b7280")


def _embed_image(path: str) -> Optional[str]:
    """Return base64 data URI for a screenshot, or None if file missing."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
        return f"data:{mime};base64,{data}"
    except Exception:
        return None


def _duration(start: Optional[str], end: Optional[str]) -> str:
    """Human-readable duration between two ISO timestamps."""
    if not start or not end:
        return "N/A"
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
        secs = int((t1 - t0).total_seconds())
        m, s = divmod(secs, 60)
        return f"{m}m {s}s"
    except Exception:
        return "N/A"


# ── HTML template helpers ─────────────────────────────────────────────────────

def _tag_list(items: list, colour: str = "#2563eb") -> str:
    if not items:
        return "<span style='color:#9ca3af'>None</span>"
    return "".join(
        f"<span style='display:inline-block;margin:2px 4px 2px 0;padding:2px 10px;"
        f"background:{colour}1a;color:{colour};border-radius:12px;font-size:0.8rem;"
        f"border:1px solid {colour}44'>{item}</span>"
        for item in items
    )


def _score_ring(label: str, score: Optional[int]) -> str:
    colour = _score_colour(score)
    display = str(score) if score is not None else "?"
    return (
        f"<div style='text-align:center;padding:12px 20px'>"
        f"<div style='width:72px;height:72px;border-radius:50%;border:5px solid {colour};"
        f"display:flex;align-items:center;justify-content:center;margin:0 auto;"
        f"font-size:1.5rem;font-weight:700;color:{colour}'>{display}</div>"
        f"<div style='margin-top:6px;font-size:0.75rem;color:#6b7280;font-weight:600'>{label}</div>"
        f"</div>"
    )


# ── Main HTML builder ─────────────────────────────────────────────────────────

class RunReportGenerator:
    """
    Generates a rich, self-contained HTML report for a single test run.
    Call generate(run_record) → writes HTML to reports/<run_id>_report.html
    and returns the file path.
    """

    def generate(self, run_record: dict, persona_data: Optional[dict] = None) -> str:
        """
        Build and save the HTML report.
        run_record: the dict returned by ActionRecorder.finish()
        persona_data: optional full persona JSON (enriches the persona card)
        Returns the path to the saved HTML file.
        """
        os.makedirs(REPORTS_DIR, exist_ok=True)
        run_id = run_record.get("run_id", "unknown")
        html = self._build_html(run_record, persona_data or {})
        out_path = os.path.join(REPORTS_DIR, f"{run_id}_report.html")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("[RunReport] Report saved: %s", out_path)
        except Exception as e:
            logger.error("[RunReport] Failed to save report: %s", e)
        return out_path

    # ── Top-level HTML document ───────────────────────────────────────────────

    def _build_html(self, r: dict, persona_data: dict) -> str:
        run_id       = r.get("run_id", "unknown")
        status       = r.get("overall_status", "unknown")
        bg, label    = _status_badge(status)
        start_time   = r.get("start_time", "")
        end_time     = r.get("end_time", "")
        duration     = _duration(start_time, end_time)
        persona_id   = r.get("persona_id", "")
        persona_name = r.get("persona_name", "")
        workflow_id  = r.get("workflow_id", "")
        workflow_title = r.get("workflow_title", "")

        verdict       = r.get("judge_verdict") or {}
        steps         = r.get("steps") or []
        sai_log       = r.get("psi_conversation") or []
        canvas        = r.get("canvas_evidence") or {}
        a11y          = r.get("a11y_report") or {}
        style         = r.get("style_report") or {}
        changes       = r.get("change_timeline") or []
        flow_view     = canvas.get("flow_view") or {}
        health_checks = r.get("health_checks") or {}
        brd_result    = r.get("brd_result") or {}

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Test Report · {run_id}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f8fafc;color:#1e293b;font-size:14px;line-height:1.6}}
  .page{{max-width:1200px;margin:0 auto;padding:24px}}
  h1{{font-size:1.6rem;font-weight:700}}
  h2{{font-size:1.15rem;font-weight:700;margin-bottom:14px;color:#0f172a;
      border-bottom:2px solid #e2e8f0;padding-bottom:6px}}
  h3{{font-size:0.95rem;font-weight:700;color:#334155;margin-bottom:8px}}
  .card{{background:#fff;border-radius:12px;border:1px solid #e2e8f0;
         padding:24px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
  .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
  .grid-3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}}
  .grid-4{{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}}
  .meta-row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}}
  .badge{{display:inline-block;padding:3px 12px;border-radius:20px;font-size:0.75rem;
          font-weight:700;color:#fff}}
  .pill{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:0.78rem;
         background:#f1f5f9;color:#475569;border:1px solid #cbd5e1;margin:2px}}
  .step-row{{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #f1f5f9}}
  .step-row:last-child{{border-bottom:none}}
  .step-num{{min-width:32px;height:32px;border-radius:50%;background:#f1f5f9;
             display:flex;align-items:center;justify-content:center;
             font-size:0.75rem;font-weight:700;color:#64748b;flex-shrink:0}}
  .step-body{{flex:1}}
  .step-desc{{font-weight:500;color:#1e293b}}
  .step-meta{{font-size:0.75rem;color:#94a3b8;margin-top:2px}}
  .step-content{{background:#f8fafc;border-left:3px solid #cbd5e1;padding:6px 10px;
                 margin-top:6px;font-size:0.78rem;color:#475569;border-radius:0 4px 4px 0;
                 white-space:pre-wrap;word-break:break-word}}
  .sai-msg{{padding:10px 14px;border-radius:10px;margin-bottom:8px;max-width:85%}}
  .sai-assistant{{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;margin-right:auto}}
  .sai-user{{background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;margin-left:auto}}
  .sai-system{{background:#fefce8;border:1px solid #fde68a;color:#92400e;margin:0 auto;
               font-style:italic;font-size:0.82rem}}
  .sai-role{{font-size:0.7rem;font-weight:700;margin-bottom:3px;opacity:.7;text-transform:uppercase}}
  .agent-card{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:12px}}
  .subtab{{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:10px 14px;
           margin-top:8px}}
  .subtab-name{{font-size:0.72rem;font-weight:700;color:#7c3aed;margin-bottom:4px;
                text-transform:uppercase;letter-spacing:.05em}}
  .subtab-content{{font-size:0.82rem;color:#334155;white-space:pre-wrap;word-break:break-word;
                   max-height:220px;overflow-y:auto}}
  .screenshot{{border-radius:8px;border:1px solid #e2e8f0;width:100%;
               object-fit:contain;background:#000;cursor:zoom-in}}
  .screenshot-caption{{font-size:0.72rem;color:#94a3b8;text-align:center;margin-top:4px}}
  .flag-item{{padding:6px 12px;background:#fef2f2;border-left:3px solid #f87171;
              border-radius:0 4px 4px 0;margin-bottom:6px;font-size:0.85rem;color:#991b1b}}
  .missing-item{{padding:6px 12px;background:#fff7ed;border-left:3px solid #fb923c;
                 border-radius:0 4px 4px 0;margin-bottom:6px;font-size:0.85rem;color:#9a3412}}
  .rationale{{background:#f8fafc;border-radius:8px;padding:16px;font-size:0.9rem;
              color:#334155;line-height:1.7;border:1px solid #e2e8f0}}
  .stat-num{{font-size:2rem;font-weight:700;line-height:1}}
  .stat-label{{font-size:0.75rem;color:#64748b;margin-top:4px}}
  .toc-link{{display:block;color:#2563eb;text-decoration:none;padding:3px 0;font-size:0.9rem}}
  .toc-link:hover{{text-decoration:underline}}
  details>summary{{cursor:pointer;font-weight:600;padding:8px 0;color:#2563eb;list-style:none}}
  details>summary::-webkit-details-marker{{display:none}}
  details>summary::before{{content:'▶ ';font-size:0.7em;transition:.15s}}
  details[open]>summary::before{{content:'▼ '}}
  @media(max-width:700px){{.grid-2,.grid-3,.grid-4{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="page">

<!-- ═══════════════════ HEADER ═══════════════════ -->
<div class="card" style="background:linear-gradient(135deg,#1e293b 0%,#334155 100%);color:#fff;border:none">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
    <div>
      <div style="font-size:0.75rem;opacity:.6;margin-bottom:4px;text-transform:uppercase;letter-spacing:.08em">
        Adya AI · SAI Browser Testing Agent
      </div>
      <h1 style="color:#fff;margin-bottom:6px">{workflow_title}</h1>
      <div class="meta-row">
        <span class="badge" style="background:{bg}">{label}</span>
        <span class="pill" style="background:rgba(255,255,255,.12);color:#e2e8f0;border-color:rgba(255,255,255,.2)">
          {persona_id} · {persona_name}
        </span>
        <span class="pill" style="background:rgba(255,255,255,.12);color:#e2e8f0;border-color:rgba(255,255,255,.2)">
          {workflow_id}
        </span>
      </div>
    </div>
    <div style="text-align:right;opacity:.8;font-size:0.8rem">
      <div>Run ID: <code style="background:rgba(255,255,255,.1);padding:1px 6px;border-radius:4px">{run_id}</code></div>
      <div style="margin-top:4px">Duration: <strong>{duration}</strong></div>
      <div style="margin-top:2px">{start_time[:19].replace("T"," ")} UTC</div>
    </div>
  </div>

  <!-- Score rings -->
  <div style="display:flex;gap:0;margin-top:20px;border-top:1px solid rgba(255,255,255,.15);
              padding-top:16px;flex-wrap:wrap">
    {_score_ring("Correctness", verdict.get("correctness_score"))}
    {_score_ring("Process", verdict.get("process_score"))}
    {_score_ring("Output", verdict.get("output_score"))}
    <div style="flex:1;min-width:200px;padding:12px 20px;display:flex;flex-direction:column;
                justify-content:center;border-left:1px solid rgba(255,255,255,.15)">
      <div style="font-size:0.75rem;opacity:.6;text-transform:uppercase;letter-spacing:.06em">
        SAI Quality
      </div>
      <div style="font-size:1.4rem;font-weight:700;color:#93c5fd;margin-top:4px">
        {verdict.get("psi_questions_rating", "N/A").upper()}
      </div>
      <div style="font-size:0.75rem;opacity:.6;margin-top:4px">
        {len(steps)} steps · {len(sai_log)} conversation turns
      </div>
    </div>
  </div>
</div>

<!-- ═══════════════════ TABLE OF CONTENTS ═══════════════════ -->
<div class="card" style="padding:16px 24px">
  <h2 style="border:none;margin-bottom:8px">Contents</h2>
  <div class="grid-2">
    <div>
      <a href="#persona"   class="toc-link">1 · Persona Profile</a>
      <a href="#workflow"  class="toc-link">2 · Workflow Summary</a>
      <a href="#qa-table"  class="toc-link">3 · QA Step Table</a>
      <a href="#journey"   class="toc-link">4 · Journey Timeline</a>
      <a href="#sai"       class="toc-link">5 · SAI Conversation</a>
      <a href="#canvas"    class="toc-link">6 · Canvas Agent Evidence</a>
    </div>
    <div>
      <a href="#verdict"     class="toc-link">7 · LLM Judge Verdict</a>
      <a href="#doc-quality" class="toc-link">8 · Document Quality Analysis</a>
      <a href="#failures"    class="toc-link">9 · Failure Analysis</a>
      <a href="#metrics"     class="toc-link">10 · Final Metrics</a>
      <a href="#gallery"     class="toc-link">11 · Screenshot Gallery</a>
      <a href="#a11y"        class="toc-link">12 · Accessibility Report</a>
      <a href="#diff"        class="toc-link">13 · Visual Diff Timeline</a>
    </div>
  </div>
</div>

{self._section_health_checks(health_checks, verdict)}
{self._section_brd_validation(brd_result)}
{self._section_persona(r, persona_data)}
{self._section_workflow(r)}
{self._section_qa_table(steps, verdict)}
{self._section_journey(steps)}
{self._section_psi(sai_log)}
{self._section_canvas(canvas)}
{self._section_verdict(verdict)}
{self._section_document_quality(verdict)}
{self._section_failure_analysis(r, steps, canvas, verdict)}
{self._section_final_metrics(r, verdict, steps, canvas)}
{self._section_gallery(steps, canvas)}
{self._section_flow_graph(flow_view, verdict)}
{self._section_accessibility(verdict, a11y)}
{self._section_visual_diff(changes, verdict)}

<!-- ═══════════════════ FOOTER ═══════════════════ -->
<div style="text-align:center;padding:20px 0;color:#94a3b8;font-size:0.78rem">
  Generated by Adya AI · SAI Browser Testing Agent · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
</div>

</div><!-- /page -->
</body>
</html>"""

    # ── §0 Health Checks ──────────────────────────────────────────────────────

    def _section_health_checks(self, health_checks: dict, verdict: dict) -> str:
        if not health_checks:
            return ""

        copilot = health_checks.get("copilot") or {}
        canvas  = health_checks.get("canvas")  or {}
        overall = health_checks.get("overall_status", "unknown")
        summary = health_checks.get("summary", "")

        STATUS_STYLE = {
            "pass":    ("16a34a", "dcfce7", "PASS"),
            "partial": ("d97706", "fef3c7", "PARTIAL"),
            "fail":    ("dc2626", "fee2e2", "FAIL"),
        }

        def check_card(chk: dict) -> str:
            st = chk.get("status", "fail")
            col, bg, lbl = STATUS_STYLE.get(st, ("6b7280", "f8fafc", st.upper()))
            sc  = chk.get("score", 0)
            ev  = chk.get("evidence", [])
            iss = chk.get("issues", [])

            ev_html = "".join(
                f'<li style="margin-bottom:3px;color:#475569">{e}</li>'
                for e in ev
            )
            def iss_row(i: dict) -> str:
                sev = i.get("severity", "minor")
                sev_col = {"critical": "dc2626", "major": "d97706", "minor": "64748b"}.get(sev, "64748b")
                return (
                    f'<div style="background:#f8fafc;border-left:3px solid #{sev_col};'
                    f'padding:8px 12px;margin-bottom:6px;border-radius:0 6px 6px 0">'
                    f'<span style="background:#{sev_col};color:#fff;border-radius:8px;'
                    f'padding:1px 7px;font-size:0.72rem;font-weight:700;margin-right:6px">'
                    f'{sev.upper()}</span>'
                    f'<span style="font-size:0.85rem">{i.get("description","")}</span>'
                    f'<div style="font-size:0.78rem;color:#2563eb;margin-top:4px">'
                    f'Fix: {i.get("fix","")}</div>'
                    f'</div>'
                )

            issues_html = "".join(iss_row(i) for i in iss) if iss else (
                '<p style="color:#16a34a;font-size:0.85rem">No issues found.</p>'
            )

            score_col = "16a34a" if sc >= 8 else ("d97706" if sc >= 5 else "dc2626")
            return f"""
<div style="background:#{bg};border:1px solid #{col}33;border-radius:10px;padding:18px 20px">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <span style="background:#{col};color:#fff;border-radius:14px;padding:3px 14px;
                 font-size:0.78rem;font-weight:700">{lbl}</span>
    <span style="font-size:1.3rem;font-weight:700;color:#{score_col}">{sc}/10</span>
    <span style="font-weight:600;font-size:0.9rem;color:#1e293b">{chk.get("label","")}</span>
  </div>
  <p style="font-size:0.88rem;color:#334155;margin-bottom:10px">{chk.get("summary","")}</p>
  {"<ul style='margin:0 0 12px 16px;font-size:0.83rem'>" + ev_html + "</ul>" if ev else ""}
  {issues_html}
</div>"""

        # Overall banner colour
        ov_col, ov_bg, ov_lbl = STATUS_STYLE.get(overall, ("6b7280", "f8fafc", overall.upper()))

        return f"""
<div class="card" id="health-checks">
  <h2>Core Health Checks</h2>
  <div style="background:#{ov_bg};border:2px solid #{ov_col};border-radius:10px;
              padding:14px 20px;margin-bottom:18px;display:flex;align-items:center;gap:12px">
    <span style="background:#{ov_col};color:#fff;border-radius:14px;padding:4px 16px;
                 font-size:0.85rem;font-weight:700">OVERALL: {ov_lbl}</span>
    <span style="font-size:0.95rem;color:#1e293b">{summary}</span>
  </div>
  <div class="grid-2" style="align-items:start">
    {check_card(copilot)}
    {check_card(canvas)}
  </div>
</div>"""

    # ── §0b BRD Validation ────────────────────────────────────────────────────

    def _section_brd_validation(self, brd_result: dict) -> str:
        if not brd_result or brd_result.get("skipped"):
            return ""
        if not brd_result.get("download_success"):
            err = brd_result.get("error", "Download failed")
            return f"""
<div class="card" id="brd-validation">
  <h2>BRD Download &amp; Validation</h2>
  <div style="background:#fee2e2;border:1px solid #dc2626;border-radius:10px;
              padding:14px 20px;color:#7f1d1d;font-size:0.9rem">
    <strong>Download failed:</strong> {err}
  </div>
</div>"""

        val      = brd_result.get("validation") or {}
        verdict  = val.get("verdict", "fail")
        overall  = val.get("overall_score", 0)
        comp     = val.get("completeness_score", 0)
        acc      = val.get("accuracy_score", 0)
        struct   = val.get("structure_score", 0)
        req      = val.get("requirement_score", 0)
        missing  = val.get("missing_sections") or []
        issues   = val.get("issues") or []
        hilights = val.get("highlights") or []
        rationale = val.get("rationale", "")
        sections  = brd_result.get("sections") or {}
        pdf_pages = brd_result.get("pdf_pages", 0)
        pdf_chars = brd_result.get("pdf_chars", 0)
        pdf_path  = brd_result.get("pdf_path", "")

        VERDICT_STYLE = {
            "pass":    ("16a34a", "dcfce7"),
            "partial": ("d97706", "fef3c7"),
            "fail":    ("dc2626", "fee2e2"),
        }
        v_col, v_bg = VERDICT_STYLE.get(verdict, ("6b7280", "f8fafc"))

        def score_bar(label: str, score: int) -> str:
            col = "16a34a" if score >= 8 else ("d97706" if score >= 5 else "dc2626")
            pct = score * 10
            return f"""
<div style="margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px">
    <span style="font-size:0.85rem;color:#475569">{label}</span>
    <span style="font-size:0.85rem;font-weight:700;color:#{col}">{score}/10</span>
  </div>
  <div style="background:#e2e8f0;border-radius:99px;height:8px">
    <div style="background:#{col};width:{pct}%;height:8px;border-radius:99px"></div>
  </div>
</div>"""

        scores_html = (
            score_bar("Completeness", comp) +
            score_bar("Accuracy",     acc)  +
            score_bar("Structure",    struct) +
            score_bar("Requirement Quality", req)
        )

        missing_html = "".join(
            f'<span style="background:#fee2e2;color:#7f1d1d;border-radius:6px;'
            f'padding:2px 10px;font-size:0.78rem;margin:2px;display:inline-block">{s}</span>'
            for s in missing
        ) if missing else '<span style="color:#16a34a;font-size:0.85rem">All required sections present</span>'

        issues_html = "".join(
            f'<li style="color:#7f1d1d;font-size:0.84rem;margin-bottom:4px">{i}</li>'
            for i in issues
        )
        hilights_html = "".join(
            f'<li style="color:#166534;font-size:0.84rem;margin-bottom:4px">{h}</li>'
            for h in hilights
        )

        sections_html = ""
        if sections:
            rows = "".join(
                f'<tr><td style="padding:6px 12px;border-bottom:1px solid #e2e8f0;'
                f'font-weight:600;font-size:0.82rem;white-space:nowrap">{k}</td>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #e2e8f0;'
                f'font-size:0.8rem;color:#475569">{v[:200]}{"…" if len(v)>200 else ""}</td></tr>'
                for k, v in list(sections.items())[:15]
            )
            sections_html = f"""
<div style="margin-top:18px">
  <h3>Extracted BRD Sections ({len(sections)})</h3>
  <div style="overflow-x:auto;margin-top:8px">
    <table style="width:100%;border-collapse:collapse;font-size:0.83rem">
      <thead><tr style="background:#f1f5f9">
        <th style="padding:8px 12px;text-align:left;font-size:0.8rem;color:#64748b">Section</th>
        <th style="padding:8px 12px;text-align:left;font-size:0.8rem;color:#64748b">Preview</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>"""

        return f"""
<div class="card" id="brd-validation">
  <h2>BRD Download &amp; Validation</h2>

  <!-- Verdict banner -->
  <div style="background:#{v_bg};border:2px solid #{v_col};border-radius:10px;
              padding:14px 20px;margin-bottom:18px;display:flex;
              align-items:center;gap:16px;flex-wrap:wrap">
    <span style="background:#{v_col};color:#fff;border-radius:14px;
                 padding:4px 18px;font-size:0.85rem;font-weight:700">
      BRD {verdict.upper()}
    </span>
    <span style="font-size:1.4rem;font-weight:800;color:#{v_col}">{overall}/10</span>
    <span style="font-size:0.9rem;color:#334155">Overall BRD Quality Score</span>
    <span style="margin-left:auto;font-size:0.82rem;color:#64748b">
      {pdf_pages} pages · {pdf_chars:,} chars · {pdf_path.split("/")[-1] if pdf_path else ""}
    </span>
  </div>

  <div class="grid-2" style="align-items:start;gap:20px">

    <!-- Score bars -->
    <div>
      <h3 style="margin-bottom:12px">Dimension Scores</h3>
      {scores_html}
    </div>

    <!-- Rationale + missing sections -->
    <div>
      <h3 style="margin-bottom:8px">Validator Rationale</h3>
      <p style="font-size:0.87rem;color:#334155;margin-bottom:16px">{rationale or "—"}</p>

      <h3 style="margin-bottom:8px">Missing Sections</h3>
      <div style="margin-bottom:16px">{missing_html}</div>
    </div>
  </div>

  <!-- Issues & Highlights -->
  <div class="grid-2" style="align-items:start;gap:20px;margin-top:4px">
    <div>
      <h3 style="margin-bottom:8px;color:#dc2626">Issues Found ({len(issues)})</h3>
      {"<ul style='padding-left:18px'>" + issues_html + "</ul>" if issues
       else '<p style="color:#16a34a;font-size:0.85rem">No issues found.</p>'}
    </div>
    <div>
      <h3 style="margin-bottom:8px;color:#16a34a">Highlights ({len(hilights)})</h3>
      {"<ul style='padding-left:18px'>" + hilights_html + "</ul>" if hilights
       else '<p style="color:#94a3b8;font-size:0.85rem">None recorded.</p>'}
    </div>
  </div>

  {sections_html}
</div>"""

    # ── §1 Persona ─────────────────────────────────────────────────────────────

    def _section_persona(self, r: dict, p: dict) -> str:
        pid   = r.get("persona_id", "")
        pname = r.get("persona_name", "")

        # Merge run-level info with any loaded persona JSON
        role      = p.get("role", "")
        segment   = p.get("segment", "")
        industry  = p.get("industry", "")
        org_size  = p.get("org_size", "")
        td        = p.get("technical_depth", "")
        sai_pct   = p.get("sai_usage_pct", "")
        dev_pct   = p.get("dev_usage_pct", "")
        buyer     = p.get("buyer_profile", "")
        comms     = p.get("communication_style", "")
        goals     = p.get("goals", [])
        pains     = p.get("pain_points", [])
        prompts   = p.get("sample_prompts", [])

        td_colour = {"low": "#16a34a", "medium": "#d97706", "high": "#dc2626"}.get(str(td).lower(), "#6b7280")

        goals_html   = "".join(f"<li style='margin-bottom:4px'>✦ {g}</li>" for g in goals) or "<li>N/A</li>"
        pains_html   = "".join(f"<li style='margin-bottom:4px'>⚡ {p_}</li>" for p_ in pains) or "<li>N/A</li>"
        prompts_html = "".join(
            f"<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;"
            f"padding:8px 12px;margin-bottom:6px;font-size:0.82rem;color:#334155;"
            f"font-style:italic'>\"{s}\"</div>"
            for s in prompts
        ) or "<div style='color:#9ca3af'>N/A</div>"

        return f"""
<div id="persona" class="card">
  <h2>1 · Persona Profile</h2>
  <div class="grid-2">
    <div>
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
        <div style="width:60px;height:60px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#8b5cf6);
                    display:flex;align-items:center;justify-content:center;font-size:1.4rem;
                    color:#fff;font-weight:700;flex-shrink:0">{pid[:2]}</div>
        <div>
          <div style="font-size:1.2rem;font-weight:700">{pname}</div>
          <div style="color:#64748b;font-size:0.85rem">{role}</div>
          <div class="meta-row" style="margin-top:4px">
            <span class="badge" style="background:#6366f1">{pid}</span>
            <span class="pill">{segment or "N/A"}</span>
          </div>
        </div>
      </div>

      <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
        {''.join(f'<tr><td style="padding:5px 0;color:#64748b;width:130px">{k}</td><td style="font-weight:500">{v}</td></tr>'
                 for k, v in [
                     ("Industry", industry or "N/A"),
                     ("Org Size", org_size or "N/A"),
                     ("Buyer Profile", buyer or "N/A"),
                     ("SAI Usage", f"{sai_pct}%" if sai_pct != "" else "N/A"),
                     ("Dev Usage", f"{dev_pct}%" if dev_pct != "" else "N/A"),
                 ])}
        <tr>
          <td style="padding:5px 0;color:#64748b;vertical-align:top">Technical Depth</td>
          <td><span style="font-weight:700;color:{td_colour}">{str(td).upper() or "N/A"}</span></td>
        </tr>
      </table>
    </div>

    <div>
      <h3>Goals</h3>
      <ul style="list-style:none;padding:0;margin-bottom:14px;color:#334155;font-size:0.85rem">
        {goals_html}
      </ul>
      <h3>Pain Points</h3>
      <ul style="list-style:none;padding:0;color:#334155;font-size:0.85rem">
        {pains_html}
      </ul>
    </div>
  </div>

  <div style="margin-top:16px;border-top:1px solid #f1f5f9;padding-top:14px">
    <h3>Communication Style</h3>
    <p style="color:#475569;font-size:0.85rem;margin-bottom:14px">{comms or "N/A"}</p>
    <h3>Sample Prompts This Persona Would Send</h3>
    {prompts_html}
  </div>
</div>"""

    # ── §2 Workflow ────────────────────────────────────────────────────────────

    def _section_workflow(self, r: dict) -> str:
        wid    = r.get("workflow_id", "")
        title  = r.get("workflow_title", "")
        # These may not be in run_record directly; show what we have
        return f"""
<div id="workflow" class="card">
  <h2>2 · Workflow Summary</h2>
  <div class="grid-2">
    <div>
      <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
        <tr><td style="padding:5px 0;color:#64748b;width:120px">Workflow ID</td>
            <td><code style="background:#f1f5f9;padding:1px 6px;border-radius:4px">{wid}</code></td></tr>
        <tr><td style="padding:5px 0;color:#64748b">Title</td>
            <td style="font-weight:600">{title}</td></tr>
        <tr><td style="padding:5px 0;color:#64748b">Persona</td>
            <td>{r.get("persona_id")} · {r.get("persona_name")}</td></tr>
        <tr><td style="padding:5px 0;color:#64748b">Status</td>
            <td><span class="badge" style="background:{_status_badge(r.get('overall_status',''))[0]}">
              {_status_badge(r.get('overall_status',''))[1]}</span></td></tr>
        <tr><td style="padding:5px 0;color:#64748b">Start</td>
            <td>{(r.get("start_time") or "")[:19].replace("T"," ")} UTC</td></tr>
        <tr><td style="padding:5px 0;color:#64748b">End</td>
            <td>{(r.get("end_time") or "")[:19].replace("T"," ")} UTC</td></tr>
        <tr><td style="padding:5px 0;color:#64748b">Duration</td>
            <td>{_duration(r.get("start_time"), r.get("end_time"))}</td></tr>
      </table>
    </div>
    <div>
      <h3>Agents Expected</h3>
      <div style="margin-bottom:12px">
        {_tag_list(r.get("judge_verdict", {}).get("agents_that_should_have_run", []), "#2563eb")}
      </div>
      <h3>Agents That Ran</h3>
      <div>
        {_tag_list(r.get("judge_verdict", {}).get("agents_that_ran", []), "#16a34a")}
      </div>
    </div>
  </div>
</div>"""

    # ── §3 QA Step Table ──────────────────────────────────────────────────────

    def _section_qa_table(self, steps: list, verdict: dict) -> str:
        if not steps:
            return """<div id="qa-table" class="card"><h2>3 · QA Step Table</h2>
                      <p style="color:#9ca3af">No steps recorded.</p></div>"""

        issues = {i.get("description", ""): i for i in (verdict.get("issues_found") or [])}

        def _cell(text: str, max_chars: int = 200) -> str:
            if not text or text == "—":
                return "<span style='color:#cbd5e1'>—</span>"
            t = text[:max_chars] + ("…" if len(text) > max_chars else "")
            return f'<span style="white-space:pre-wrap;word-break:break-word">{t}</span>'

        rows = []
        for s in steps:
            step_num      = s.get("step_number", "")
            action        = s.get("action_type", "")
            desc          = s.get("description", "")
            success       = s.get("success", True)
            colour        = _action_type_colour(action)
            ss_path       = s.get("screenshot_path")
            reasoning     = s.get("reasoning") or ""
            input_sent    = s.get("input_sent") or ""
            sai_output    = s.get("sai_output") or ""
            canvas_result = s.get("canvas_result") or ""

            result_html = (
                "<span class='badge' style='background:#16a34a'>PASS</span>"
                if success else
                "<span class='badge' style='background:#dc2626'>FAIL</span>"
            )
            ss_uri = _embed_image(ss_path) if ss_path else None
            thumb = (
                f"<a href='{ss_uri}' target='_blank'>"
                f"<img src='{ss_uri}' style='max-height:60px;border-radius:4px;"
                f"border:1px solid #e2e8f0;display:block'/></a>"
                if ss_uri else "<span style='color:#cbd5e1;font-size:0.75rem'>—</span>"
            )

            row_bg = "#fef2f2" if not success else "#fff"
            rows.append(f"""
<tr style="border-bottom:1px solid #f1f5f9;background:{row_bg};vertical-align:top">
  <td style="padding:8px 12px;text-align:center;font-weight:700;color:{colour};white-space:nowrap">{step_num}</td>
  <td style="padding:8px 12px;min-width:140px">
    <span style="font-size:0.75rem;font-weight:700;padding:2px 8px;border-radius:10px;
                 background:{colour}1a;color:{colour};border:1px solid {colour}44">{action}</span>
    <div style="font-size:0.82rem;color:#475569;margin-top:3px">{desc}</div>
  </td>
  <td style="padding:8px 12px;font-size:0.81rem;color:#334155;max-width:200px">
    {_cell(reasoning, 180)}
  </td>
  <td style="padding:8px 12px;font-size:0.81rem;color:#0f172a;max-width:200px">
    {_cell(input_sent, 200)}
  </td>
  <td style="padding:8px 12px;font-size:0.81rem;color:#166534;max-width:200px">
    {_cell(sai_output, 200)}
  </td>
  <td style="padding:8px 12px;font-size:0.81rem;color:#1e40af;max-width:200px">
    {_cell(canvas_result, 200)}
  </td>
  <td style="padding:8px 12px;text-align:center">{thumb}</td>
  <td style="padding:8px 12px;text-align:center">{result_html}</td>
</tr>""")

        pass_count = sum(1 for s in steps if s.get("success", True))
        fail_count = len(steps) - pass_count
        accuracy   = int(pass_count / len(steps) * 100) if steps else 0

        th = ("padding:10px 12px;font-size:0.72rem;font-weight:700;"
              "color:#64748b;text-transform:uppercase;letter-spacing:.05em;text-align:left")
        return f"""
<div id="qa-table" class="card">
  <h2>3 · QA Step Table
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      ({len(steps)} steps · {pass_count} passed · {fail_count} failed · {accuracy}% accuracy)
    </span>
  </h2>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;min-width:900px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="{th};text-align:center;white-space:nowrap">Step</th>
          <th style="{th}">Action / Description</th>
          <th style="{th}">Reasoning</th>
          <th style="{th}">Input Sent</th>
          <th style="{th}">SAI Output</th>
          <th style="{th}">Canvas Result</th>
          <th style="{th};text-align:center">Screenshot</th>
          <th style="{th};text-align:center">Result</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</div>"""

    # ── §4 Journey Timeline ────────────────────────────────────────────────────

    def _section_journey(self, steps: list) -> str:
        if not steps:
            return """<div id="journey" class="card"><h2>4 · Journey Timeline</h2>
                      <p style="color:#9ca3af">No steps recorded.</p></div>"""

        def _detail_row(label: str, value: str, colour: str) -> str:
            if not value:
                return ""
            v = value[:400] + ("…" if len(value) > 400 else "")
            return (
                f'<div style="margin-top:6px;padding:6px 10px;border-left:3px solid {colour};'
                f'background:{colour}0d;border-radius:0 6px 6px 0">'
                f'<span style="font-size:0.7rem;font-weight:700;color:{colour};'
                f'text-transform:uppercase;letter-spacing:.04em">{label}</span>'
                f'<div style="font-size:0.8rem;color:#334155;margin-top:2px;'
                f'white-space:pre-wrap;word-break:break-word">{v}</div></div>'
            )

        rows = []
        for s in steps:
            atype         = s.get("action_type", "")
            colour        = _action_type_colour(atype)
            success       = s.get("success", True)
            icon          = "✓" if success else "✗"
            icon_c        = "#16a34a" if success else "#dc2626"
            ts            = (s.get("timestamp") or "")[:19].replace("T", " ")
            agent         = s.get("agent_tab")
            subtab        = s.get("sub_tab")
            content       = s.get("content_snapshot")
            notes         = s.get("notes")
            ss_path       = s.get("screenshot_path")
            ss_uri        = _embed_image(ss_path) if ss_path else None
            reasoning     = s.get("reasoning") or ""
            input_sent    = s.get("input_sent") or ""
            sai_output    = s.get("sai_output") or ""
            canvas_result = s.get("canvas_result") or ""

            agent_pill = (
                f"<span class='pill' style='background:#ede9fe;color:#6d28d9;border-color:#c4b5fd'>"
                f"{agent}</span>" if agent else ""
            )
            subtab_pill = (
                f"<span class='pill' style='background:#ecfdf5;color:#065f46;border-color:#6ee7b7'>"
                f"{subtab}</span>" if subtab else ""
            )
            content_block = (
                f"<div class='step-content'>{content}</div>" if content else ""
            )
            notes_block = (
                f"<div style='font-size:0.75rem;color:#6b7280;margin-top:4px;font-style:italic'>"
                f"ℹ {notes}</div>" if notes else ""
            )
            thumb = (
                f"<img src='{ss_uri}' style='max-height:80px;border-radius:4px;"
                f"border:1px solid #e2e8f0;margin-top:6px'/>"
                if ss_uri else ""
            )

            rows.append(f"""
<div class="step-row">
  <div class="step-num" style="background:{colour}1a;color:{colour}">{s.get("step_number","")}</div>
  <div class="step-body">
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      <span style="font-size:0.7rem;font-weight:700;padding:1px 8px;border-radius:10px;
                   background:{colour}1a;color:{colour};border:1px solid {colour}44">{atype}</span>
      {agent_pill}{subtab_pill}
      <span style="font-size:0.75rem;color:{icon_c};font-weight:700">{icon}</span>
      <span style="font-size:0.72rem;color:#94a3b8;margin-left:auto">{ts}</span>
    </div>
    <div class="step-desc" style="margin-top:4px">{s.get("description","")}</div>
    {content_block}{notes_block}
    {_detail_row("Reasoning", reasoning, icon_c)}
    {_detail_row("Input sent to SAI", input_sent, "#2563eb")}
    {_detail_row("SAI output", sai_output, "#059669")}
    {_detail_row("Canvas result", canvas_result, "#7c3aed")}
    {thumb}
  </div>
</div>""")

        return f"""
<div id="journey" class="card">
  <h2>4 · Journey Timeline <span style="font-size:0.85rem;font-weight:400;color:#64748b">
    ({len(steps)} steps)</span></h2>
  {"".join(rows)}
</div>"""

    # ── §5 SAI Conversation ────────────────────────────────────────────────────

    def _section_psi(self, sai_log: list) -> str:
        if not sai_log:
            return """<div id="sai" class="card"><h2>5 · SAI Conversation</h2>
                      <p style="color:#9ca3af">No conversation recorded.</p></div>"""

        bubbles = []
        turn = 0
        for i, entry in enumerate(sai_log):
            role    = entry.get("role", "").lower()
            text    = entry.get("text") or entry.get("content") or ""
            ts      = (entry.get("timestamp") or "")[:19].replace("T", " ")
            ss_path = entry.get("screenshot_path")
            tokens  = entry.get("tokens")

            if role == "assistant":
                turn += 1
                bg_col    = "#eff6ff"
                border_c  = "#bfdbfe"
                text_c    = "#1e40af"
                align     = "margin-right:auto"
                role_icon = "🤖"
                rlab      = f"SAI · Turn {turn}"
            elif role == "user":
                bg_col    = "#f0fdf4"
                border_c  = "#bbf7d0"
                text_c    = "#166534"
                align     = "margin-left:auto"
                role_icon = "👤"
                rlab      = "User (Persona)"
            else:
                bg_col    = "#fefce8"
                border_c  = "#fde68a"
                text_c    = "#92400e"
                align     = "margin:0 auto"
                role_icon = "⚙"
                rlab      = "System"

            # Inline screenshot if captured at this turn
            ss_html = ""
            if ss_path:
                uri = _embed_image(ss_path)
                if uri:
                    ss_html = (
                        f"<div style='margin-top:8px'>"
                        f"<div style='font-size:0.68rem;color:#94a3b8;margin-bottom:3px'>"
                        f"📸 Screenshot captured at this turn</div>"
                        f"<a href='{uri}' target='_blank'>"
                        f"<img src='{uri}' style='max-height:160px;border-radius:6px;"
                        f"border:1px solid {border_c};cursor:zoom-in'/>"
                        f"</a></div>"
                    )

            meta_parts = []
            if ts:
                meta_parts.append(ts)
            if tokens:
                meta_parts.append(f"{tokens} tokens")
            meta_html = (
                f"<div style='font-size:0.68rem;color:#94a3b8;margin-top:6px'>"
                f"{'  ·  '.join(meta_parts)}</div>"
                if meta_parts else ""
            )

            # Turn number badge on the left
            turn_badge = (
                f"<div style='min-width:28px;height:28px;border-radius:50%;"
                f"background:{border_c};display:flex;align-items:center;"
                f"justify-content:center;font-size:0.72rem;font-weight:700;"
                f"color:{text_c};flex-shrink:0;margin-top:2px'>{i+1}</div>"
            )

            bubbles.append(f"""
<div style="display:flex;gap:10px;margin-bottom:12px;align-items:flex-start">
  {turn_badge}
  <div style="background:{bg_col};border:1px solid {border_c};color:{text_c};
              border-radius:10px;padding:12px 16px;flex:1;max-width:90%">
    <div style="font-size:0.7rem;font-weight:700;margin-bottom:5px;opacity:.8;
                text-transform:uppercase;letter-spacing:.04em">
      {role_icon} {rlab}
    </div>
    <div style="font-size:0.88rem;line-height:1.65;white-space:pre-wrap;word-break:break-word">{text}</div>
    {ss_html}{meta_html}
  </div>
</div>""")

        q_count = sum(1 for e in sai_log if e.get("role", "").lower() == "assistant")
        a_count = sum(1 for e in sai_log if e.get("role", "").lower() == "user")

        # Build a plain-text summary table of what was asked / answered
        qa_rows = []
        pairs = []
        current_q = None
        for e in sai_log:
            r = e.get("role", "").lower()
            t = (e.get("text") or e.get("content") or "").strip()
            if r == "assistant":
                current_q = t
            elif r == "user" and current_q is not None:
                pairs.append((current_q, t))
                current_q = None
        for idx, (q, a) in enumerate(pairs, 1):
            qa_rows.append(
                f"<tr style='border-bottom:1px solid #f1f5f9'>"
                f"<td style='padding:8px 12px;font-size:0.75rem;font-weight:700;color:#64748b;"
                f"white-space:nowrap;vertical-align:top'>#{idx}</td>"
                f"<td style='padding:8px 12px;font-size:0.82rem;color:#1e40af;"
                f"background:#eff6ff;max-width:360px;word-break:break-word;vertical-align:top'>{q[:400]}</td>"
                f"<td style='padding:8px 12px;font-size:0.82rem;color:#166534;"
                f"background:#f0fdf4;max-width:360px;word-break:break-word;vertical-align:top'>{a[:400]}</td>"
                f"</tr>"
            )

        qa_table = ""
        if qa_rows:
            qa_table = f"""
<details style="margin-bottom:18px">
  <summary style="font-weight:600;color:#2563eb;padding:8px 0;cursor:pointer">
    Q&A Summary Table ({len(qa_rows)} exchange{'s' if len(qa_rows)!=1 else ''})
  </summary>
  <div style="overflow-x:auto;margin-top:8px">
    <table style="width:100%;border-collapse:collapse;min-width:600px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;
                     text-transform:uppercase;letter-spacing:.05em">#</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#1e40af;
                     text-transform:uppercase;letter-spacing:.05em">SAI Said</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#166534;
                     text-transform:uppercase;letter-spacing:.05em">User Replied</th>
        </tr>
      </thead>
      <tbody>{"".join(qa_rows)}</tbody>
    </table>
  </div>
</details>"""

        return f"""
<div id="sai" class="card">
  <h2>5 · SAI Conversation
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      ({q_count} SAI messages · {a_count} user replies · {len(sai_log)} total turns)
    </span>
  </h2>
  {qa_table}
  <div style="max-height:700px;overflow-y:auto;padding:4px 0;
              border-top:1px solid #f1f5f9;padding-top:12px">
    {"".join(bubbles)}
  </div>
</div>"""

    # ── §6 Canvas Agent Evidence ───────────────────────────────────────────────

    def _section_canvas(self, canvas: dict) -> str:
        agents = canvas.get("agents", {})
        fv     = canvas.get("flow_view", {})

        fv_html = ""
        if fv:
            fv_ss = fv.get("screenshot_path")
            fv_uri = _embed_image(fv_ss) if fv_ss else None
            fv_img = (f"<img src='{fv_uri}' class='screenshot' style='max-height:200px'/>"
                      if fv_uri else "")
            fv_html = f"""
<div class="agent-card" style="border-color:#6366f1">
  <h3 style="color:#6366f1">⬡ Flow View</h3>
  <div style="font-size:0.82rem;color:#475569;margin-bottom:8px">
    Appeared: <strong>{'Yes' if fv.get('appeared') else 'No'}</strong>
  </div>
  {f'<div class="subtab"><div class="subtab-name">Planned Steps</div><div class="subtab-content">{fv.get("planned_steps","")[:600]}</div></div>' if fv.get("planned_steps") else ""}
  {fv_img}
</div>"""

        agent_blocks = []
        for agent_name, adata in agents.items():
            appeared  = adata.get("appeared", False)
            sub_tabs  = adata.get("sub_tabs", {})
            raw_ss    = adata.get("screenshots", [])
            border_c  = "#16a34a" if appeared else "#dc2626"
            ap_label  = "Appeared ✓" if appeared else "Did Not Appear ✗"
            ap_colour = "#16a34a" if appeared else "#dc2626"

            subtab_blocks = "".join(
                f"""<div class="subtab">
                  <div class="subtab-name">{st}
                    <span style="font-size:0.68rem;color:#94a3b8;font-weight:400;margin-left:6px">
                      {len((content or "").split())} words
                    </span>
                  </div>
                  <div class="subtab-content" style="max-height:400px;overflow-y:auto">{(content or "(empty)")}</div>
                </div>"""
                for st, content in sub_tabs.items()
            )

            ss_thumbs = ""
            for ss in raw_ss[:4]:
                uri = _embed_image(ss.get("path"))
                if uri:
                    lbl = ss.get("sub_tab") or ss.get("label") or ""
                    ss_thumbs += (
                        f"<div style='flex:1;min-width:160px'>"
                        f"<img src='{uri}' class='screenshot' style='max-height:140px'/>"
                        f"<div class='screenshot-caption'>{lbl}</div></div>"
                    )
            ss_row = (f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:10px'>{ss_thumbs}</div>"
                      if ss_thumbs else "")

            agent_blocks.append(f"""
<div class="agent-card" style="border-color:{border_c}">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <div style="font-size:1.1rem;font-weight:700;color:{border_c}">{agent_name}</div>
    <span style="font-size:0.75rem;font-weight:700;color:{ap_colour}">{ap_label}</span>
  </div>
  {subtab_blocks}
  {ss_row}
</div>""")

        if not agents and not fv:
            content_inner = "<p style='color:#9ca3af'>No canvas evidence captured.</p>"
        else:
            content_inner = fv_html + "".join(agent_blocks)

        return f"""
<div id="canvas" class="card">
  <h2>6 · Canvas Agent Evidence</h2>
  {content_inner}
</div>"""

    # ── §7 Judge Verdict ───────────────────────────────────────────────────────

    def _section_verdict(self, verdict: dict) -> str:
        if not verdict:
            return """<div id="verdict" class="card"><h2>7 · LLM Judge Verdict</h2>
                      <p style="color:#9ca3af">No verdict recorded.</p></div>"""

        flags        = verdict.get("improvement_flags") or []
        missing      = verdict.get("missing_elements") or []
        proc_ev      = verdict.get("process_evaluation") or ""
        out_ev       = verdict.get("output_evaluation") or ""
        reason_ev    = verdict.get("reasoning_evaluation") or ""
        ratio        = verdict.get("rationale") or ""
        issues_found = verdict.get("issues_found") or []
        rec_impr     = verdict.get("recommended_workflow_improvements") or []
        subtab_scores = verdict.get("per_agent_subtab_scores") or {}
        per_agent    = verdict.get("per_agent_scores") or {}

        # ── Helper: render one issue card with proof + reasoning ──────────────
        def _issue_card(item: dict, accent: str, bg: str, icon: str) -> str:
            desc      = item.get("description") or item.get("flag") or str(item)
            component = item.get("affected_component") or item.get("component") or ""
            fix       = item.get("fix_recommendation") or item.get("fix") or ""
            sev       = item.get("severity", "")
            evidence  = item.get("evidence") or item.get("proof") or ""
            reasoning = item.get("reasoning") or item.get("rationale") or ""
            category  = item.get("category") or ""

            sev_badge = ""
            if sev:
                sev_col = {"critical": "#dc2626", "major": "#d97706", "minor": "#64748b"}.get(sev.lower(), "#6b7280")
                sev_badge = (
                    f"<span style='font-size:0.7rem;font-weight:700;padding:1px 7px;"
                    f"border-radius:8px;background:{sev_col};color:#fff;margin-left:6px'>"
                    f"{sev.upper()}</span>"
                )
            cat_badge = ""
            if category:
                cat_badge = (
                    f"<span style='font-size:0.7rem;padding:1px 7px;border-radius:8px;"
                    f"background:#f1f5f9;color:#475569;border:1px solid #e2e8f0;margin-left:4px'>"
                    f"{category}</span>"
                )

            comp_html = (
                f"<div style='font-size:0.75rem;color:#64748b;margin-top:3px'>"
                f"📍 <em>{component}</em></div>"
                if component else ""
            )
            evidence_html = (
                f"<div style='margin-top:8px;background:#fffbeb;border-left:3px solid #fbbf24;"
                f"padding:8px 12px;border-radius:0 6px 6px 0;font-size:0.82rem;"
                f"color:#78350f;white-space:pre-wrap;word-break:break-word'>"
                f"<strong>📎 Evidence from run:</strong><br/>{evidence}</div>"
                if evidence else ""
            )
            reasoning_html = (
                f"<div style='margin-top:6px;background:#f0f9ff;border-left:3px solid #38bdf8;"
                f"padding:8px 12px;border-radius:0 6px 6px 0;font-size:0.82rem;"
                f"color:#0c4a6e;white-space:pre-wrap;word-break:break-word'>"
                f"<strong>💡 Judge reasoning:</strong><br/>{reasoning}</div>"
                if reasoning else ""
            )
            fix_html = (
                f"<div style='margin-top:6px;background:#f0fdf4;border-left:3px solid #86efac;"
                f"padding:8px 12px;border-radius:0 6px 6px 0;font-size:0.82rem;"
                f"color:#14532d;white-space:pre-wrap;word-break:break-word'>"
                f"<strong>🔧 Fix:</strong> {fix}</div>"
                if fix else ""
            )

            return f"""
<div style="background:{bg};border:1px solid {accent}33;border-radius:8px;
            padding:12px 16px;margin-bottom:10px">
  <div style="display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap">
    <span style="font-size:1rem">{icon}</span>
    <span style="font-size:0.88rem;font-weight:600;color:#1e293b;flex:1">{desc}</span>
    {sev_badge}{cat_badge}
  </div>
  {comp_html}{evidence_html}{reasoning_html}{fix_html}
</div>"""

        # ── Per-agent sub-tab score heatmap ───────────────────────────────────
        subtab_heatmap = ""
        if subtab_scores:
            all_subtabs = ["Overview", "Output", "Questions", "Thinking",
                           "Workflow", "Architecture", "Execution trace"]
            agents_with_scores = list(subtab_scores.keys())

            # Header row
            header_cols = "".join(
                f"<th style='padding:7px 10px;font-size:0.7rem;font-weight:700;"
                f"color:#64748b;text-transform:uppercase;letter-spacing:.04em;"
                f"background:#f8fafc;white-space:nowrap'>{st}</th>"
                for st in all_subtabs
            )

            rows_html = ""
            for agent in agents_with_scores:
                agent_sub = subtab_scores.get(agent, {})
                agent_overall = per_agent.get(agent)
                agent_col = _score_colour(agent_overall)
                cells = ""
                for st in all_subtabs:
                    sc = agent_sub.get(st)
                    if sc is None:
                        cells += "<td style='padding:7px 10px;text-align:center;color:#d1d5db;font-size:0.8rem'>—</td>"
                    else:
                        c = _score_colour(sc)
                        bg_cell = f"{c}18"
                        cells += (
                            f"<td style='padding:7px 10px;text-align:center;background:{bg_cell};"
                            f"font-weight:700;color:{c};font-size:0.85rem'>{sc}</td>"
                        )
                rows_html += (
                    f"<tr style='border-bottom:1px solid #f1f5f9'>"
                    f"<td style='padding:7px 12px;font-weight:700;color:{agent_col};"
                    f"white-space:nowrap;font-size:0.85rem'>"
                    f"{agent}"
                    f"{'<span style=\"font-size:0.75rem;color:#64748b;margin-left:4px\">(' + str(agent_overall) + '/10)</span>' if agent_overall is not None else ''}"
                    f"</td>"
                    f"{cells}</tr>"
                )

            subtab_heatmap = f"""
<div style="margin-bottom:24px">
  <h3 style="margin-bottom:10px">Per-Agent Sub-Tab Scores</h3>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;min-width:600px">
      <thead>
        <tr>
          <th style="padding:7px 12px;font-size:0.72rem;font-weight:700;color:#64748b;
                     text-transform:uppercase;background:#f8fafc">Agent</th>
          {header_cols}
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
  <div style="font-size:0.72rem;color:#94a3b8;margin-top:6px">
    Colour key:
    <span style="color:#16a34a;font-weight:700">8-10 Good</span> ·
    <span style="color:#d97706;font-weight:700">5-7 Partial</span> ·
    <span style="color:#dc2626;font-weight:700">0-4 Poor</span> ·
    <span style="color:#d1d5db">— Not captured</span>
  </div>
</div>"""

        # ── Issues with evidence ──────────────────────────────────────────────
        issues_html = ""
        if issues_found:
            critical_issues = [i for i in issues_found if i.get("severity") == "critical"]
            major_issues    = [i for i in issues_found if i.get("severity") == "major"]
            other_issues    = [i for i in issues_found if i.get("severity") not in ("critical", "major")]
            cards = ""
            for iss in critical_issues:
                cards += _issue_card(iss, "#dc2626", "#fef2f2", "🔴")
            for iss in major_issues:
                cards += _issue_card(iss, "#d97706", "#fff7ed", "🟡")
            for iss in other_issues:
                cards += _issue_card(iss, "#64748b", "#f8fafc", "⚪")
            issues_html = f"""
<div style="margin-bottom:20px">
  <h3 style="margin-bottom:10px">Issues Found ({len(issues_found)})
    <span style="font-size:0.8rem;font-weight:400;color:#64748b">
      — each includes proof from the run and judge reasoning
    </span>
  </h3>
  {cards}
</div>"""

        # ── Improvement flags with reasoning ──────────────────────────────────
        flags_html = ""
        if flags:
            flag_cards = "".join(
                _issue_card(
                    {"description": f, "severity": "minor"},
                    "#f59e0b", "#fffbeb", "⚑"
                )
                if isinstance(f, dict) else
                f"<div style='padding:8px 14px;background:#fffbeb;border-left:3px solid #fbbf24;"
                f"border-radius:0 6px 6px 0;margin-bottom:8px;font-size:0.85rem;color:#78350f'>⚑ {f}</div>"
                for f in flags
            )
            flags_html = f"""
<div style="margin-bottom:20px">
  <h3 style="margin-bottom:10px">Improvement Flags ({len(flags)})</h3>
  {flag_cards}
</div>"""
        else:
            flags_html = "<div style='color:#16a34a;font-size:0.85rem;padding:8px'>✓ No improvement flags — run passed all checks.</div>"

        # ── Missing elements ──────────────────────────────────────────────────
        missing_html = ""
        if missing:
            missing_html = f"""
<div style="margin-bottom:20px">
  <h3 style="margin-bottom:10px">Missing Elements ({len(missing)})</h3>
  {"".join(
      f"<div style='padding:8px 14px;background:#fff7ed;border-left:3px solid #fb923c;"
      f"border-radius:0 6px 6px 0;margin-bottom:6px;font-size:0.85rem;color:#9a3412'>○ {m}</div>"
      for m in missing
  )}
</div>"""

        # ── Recommended workflow improvements ─────────────────────────────────
        rec_html = ""
        if rec_impr:
            rec_html = f"""
<div style="margin-bottom:20px">
  <h3 style="margin-bottom:10px">Recommended Workflow Improvements ({len(rec_impr)})</h3>
  {"".join(
      f"<div style='padding:8px 14px;background:#f0f9ff;border-left:3px solid #38bdf8;"
      f"border-radius:0 6px 6px 0;margin-bottom:6px;font-size:0.85rem;color:#0c4a6e'>→ {r}</div>"
      for r in rec_impr
  )}
</div>"""

        return f"""
<div id="verdict" class="card">
  <h2>7 · LLM Judge Verdict</h2>

  <!-- Score summary row -->
  <div class="grid-4" style="margin-bottom:20px">
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('correctness_score'))}">{verdict.get('correctness_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Correctness</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('process_score'))}">{verdict.get('process_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Process (SAI)</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('output_score'))}">{verdict.get('output_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Output (Canvas)</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:#6366f1;font-size:1.3rem">{(verdict.get('psi_questions_rating') or '?').upper()}</div>
      <div class="stat-label">SAI Q Rating</div>
    </div>
  </div>

  <!-- Evaluations grid -->
  <div class="grid-2" style="margin-bottom:20px">
    <div>
      <h3 style="margin-bottom:8px">Process Evaluation (SAI Questions)</h3>
      <div class="rationale">{proc_ev or "N/A"}</div>
    </div>
    <div>
      <h3 style="margin-bottom:8px">Output Evaluation (Canvas)</h3>
      <div class="rationale">{out_ev or "N/A"}</div>
    </div>
  </div>
  {"<div style='margin-bottom:20px'><h3 style='margin-bottom:8px'>Reasoning Quality</h3><div class='rationale'>" + reason_ev + "</div></div>" if reason_ev else ""}

  <!-- Overall rationale — the judge's narrative citing specific evidence -->
  <div style="margin-bottom:24px">
    <h3 style="margin-bottom:8px">Overall Rationale
      <span style="font-size:0.8rem;font-weight:400;color:#64748b">
        — judge's narrative citing specific evidence from this run
      </span>
    </h3>
    <div class="rationale" style="border-left:4px solid #6366f1;padding-left:16px;
                                   font-size:0.92rem;line-height:1.75">{ratio or "N/A"}</div>
  </div>

  <!-- Sub-tab heatmap -->
  {subtab_heatmap}

  <!-- Issues with proof + fix -->
  {issues_html}

  <!-- Flags, missing, recommendations -->
  {flags_html}
  {missing_html}
  {rec_html}
</div>"""

    # ── §8 Document Quality Analysis ─────────────────────────────────────────

    def _section_document_quality(self, verdict: dict) -> str:
        dq = verdict.get("document_quality") or {}
        if not dq:
            return ""

        doc_type  = dq.get("doc_type", "Output Document")
        agent     = dq.get("agent", "")
        comp_sc   = dq.get("completeness_score")
        spec_sc   = dq.get("specificity_score")
        struct_sc = dq.get("structure_score")
        ac_qual   = dq.get("acceptance_criteria_quality", "")
        pri_ok    = dq.get("priority_labelling_correct")
        covered   = dq.get("features_covered") or []
        missing   = dq.get("features_missing") or []
        doc_eval  = dq.get("doc_evaluation", "")

        def _mini_score(label: str, score) -> str:
            if score is None:
                return ""
            col = _score_colour(score)
            return (
                f"<div style='text-align:center;padding:10px 16px;background:#f8fafc;"
                f"border-radius:8px;border:1px solid #e2e8f0;flex:1;min-width:90px'>"
                f"<div style='font-size:1.4rem;font-weight:700;color:{col}'>{score}<span style='font-size:0.85rem'>/10</span></div>"
                f"<div style='font-size:0.72rem;color:#64748b;margin-top:2px'>{label}</div>"
                f"</div>"
            )

        scores_html = (
            f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px'>"
            f"{_mini_score('Completeness', comp_sc)}"
            f"{_mini_score('Specificity', spec_sc)}"
            f"{_mini_score('Structure', struct_sc)}"
            f"</div>"
        )

        ac_col = {"excellent": "#16a34a", "good": "#16a34a", "adequate": "#d97706",
                  "poor": "#dc2626", "absent": "#dc2626"}.get(ac_qual, "#6b7280")
        pri_html = ""
        if pri_ok is not None:
            pri_col = "#16a34a" if pri_ok else "#dc2626"
            pri_html = (
                f"<span style='margin-left:8px;font-size:0.78rem;font-weight:600;color:{pri_col}'>"
                f"{'✓' if pri_ok else '✗'} Priority labelling {'correct' if pri_ok else 'incorrect/missing'}</span>"
            )

        covered_html = ""
        if covered:
            covered_html = (
                "<div style='margin-top:12px'>"
                "<h3 style='margin-bottom:6px;color:#16a34a'>Features Covered</h3>"
                + "".join(
                    f"<div style='padding:4px 10px;background:#f0fdf4;border-left:3px solid #86efac;"
                    f"border-radius:0 4px 4px 0;margin-bottom:4px;font-size:0.83rem;color:#166534'>✓ {f}</div>"
                    for f in covered
                )
                + "</div>"
            )

        missing_html = ""
        if missing:
            missing_html = (
                "<div style='margin-top:12px'>"
                "<h3 style='margin-bottom:6px;color:#dc2626'>Features Missing from Document</h3>"
                + "".join(
                    f"<div style='padding:4px 10px;background:#fef2f2;border-left:3px solid #f87171;"
                    f"border-radius:0 4px 4px 0;margin-bottom:4px;font-size:0.83rem;color:#991b1b'>✗ {f}</div>"
                    for f in missing
                )
                + "</div>"
            )

        return f"""
<div id="doc-quality" class="card">
  <h2>8 · Document Quality Analysis
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      {doc_type}{" · " + agent if agent else ""}
    </span>
  </h2>
  {scores_html}
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
    <span style="font-size:0.82rem;color:#475569">Acceptance Criteria:</span>
    <span style="font-size:0.82rem;font-weight:700;color:{ac_col}">{ac_qual.upper() if ac_qual else 'N/A'}</span>
    {pri_html}
  </div>
  {"<div class='rationale' style='margin-bottom:16px'>" + doc_eval + "</div>" if doc_eval else ""}
  <div class="grid-2" style="align-items:start">
    <div>{covered_html}</div>
    <div>{missing_html}</div>
  </div>
</div>"""

    # ── §9 Failure Analysis ────────────────────────────────────────────────────

    def _section_failure_analysis(self, r: dict, steps: list, canvas: dict, verdict: dict) -> str:
        """
        Dedicated failure analysis section covering:
          - Failed and error steps with inline screenshots
          - Canvas agents that did not appear
          - Missing elements and improvement flags from the judge
          - SAI conversation failures (no response, timeout)
        """
        failed_steps = [s for s in steps if not s.get("success", True) or s.get("action_type") == "error"]
        agents_data  = canvas.get("agents", {})
        missing_agents = [name for name, a in agents_data.items() if not a.get("appeared", False)]
        flags        = verdict.get("improvement_flags") or []
        missing_els  = verdict.get("missing_elements") or []
        sai_log      = r.get("psi_conversation") or []
        psi_failures = [e for e in sai_log if e.get("role") == "system" or "error" in (e.get("text") or "").lower()]

        has_failures = bool(failed_steps or missing_agents or flags or missing_els or psi_failures)

        if not has_failures:
            return """
<div id="failures" class="card">
  <h2>8 · Failure Analysis</h2>
  <div style="display:flex;align-items:center;gap:10px;padding:16px;background:#f0fdf4;
              border-radius:8px;border:1px solid #bbf7d0">
    <span style="font-size:1.4rem">✓</span>
    <span style="color:#166534;font-weight:600">No failures detected in this run.</span>
  </div>
</div>"""

        sections_html = []

        # ── Failed Steps ──────────────────────────────────────────────────────
        if failed_steps:
            rows = []
            for s in failed_steps:
                atype   = s.get("action_type", "unknown")
                colour  = _action_type_colour(atype)
                ts      = (s.get("timestamp") or "")[:19].replace("T", " ")
                desc    = s.get("description") or ""
                notes   = s.get("notes") or ""
                content = s.get("content_snapshot") or ""
                ss_path = s.get("screenshot_path")
                ss_uri  = _embed_image(ss_path) if ss_path else None

                notes_html = (
                    f"<div style='font-size:0.78rem;color:#991b1b;margin-top:4px;font-style:italic'>{notes}</div>"
                    if notes else ""
                )
                content_html = (
                    f"<div style='background:#fff5f5;border-left:3px solid #f87171;padding:6px 10px;"
                    f"margin-top:6px;font-size:0.78rem;color:#7f1d1d;border-radius:0 4px 4px 0;"
                    f"white-space:pre-wrap;word-break:break-word'>{content[:600]}</div>"
                    if content else ""
                )
                screenshot_html = (
                    f"<div style='margin-top:8px'>"
                    f"<a href='{ss_uri}' target='_blank'>"
                    f"<img src='{ss_uri}' style='max-height:180px;border-radius:6px;"
                    f"border:2px solid #f87171;cursor:zoom-in'/>"
                    f"</a>"
                    f"<div style='font-size:0.7rem;color:#94a3b8;margin-top:2px'>"
                    f"{os.path.basename(ss_path)}</div></div>"
                    if ss_uri else
                    "<div style='font-size:0.75rem;color:#9ca3af;margin-top:6px;font-style:italic'>"
                    "No screenshot captured for this failure.</div>"
                )

                rows.append(f"""
<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;
            padding:14px 16px;margin-bottom:10px">
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
    <span style="font-size:1rem;color:#dc2626;font-weight:700">✗</span>
    <span style="font-size:0.75rem;font-weight:700;padding:1px 8px;border-radius:10px;
                 background:{colour}1a;color:{colour};border:1px solid {colour}44">{atype}</span>
    <span style="font-weight:600;color:#1e293b">{desc}</span>
    <span style="font-size:0.72rem;color:#94a3b8;margin-left:auto">{ts}</span>
  </div>
  {notes_html}{content_html}{screenshot_html}
</div>""")

            sections_html.append(f"""
<div style="margin-bottom:20px">
  <h3 style="color:#dc2626;margin-bottom:10px">
    ✗ Failed Steps ({len(failed_steps)})
  </h3>
  {"".join(rows)}
</div>""")

        # ── Agents that did not appear ─────────────────────────────────────────
        if missing_agents:
            agent_cards = []
            for name in missing_agents:
                adata = agents_data.get(name, {})
                # Find any error screenshot associated with this agent from steps
                agent_ss = None
                for s in steps:
                    if s.get("agent_tab") == name and s.get("screenshot_path"):
                        agent_ss = s.get("screenshot_path")
                agent_uri = _embed_image(agent_ss) if agent_ss else None
                img_html = (
                    f"<a href='{agent_uri}' target='_blank'>"
                    f"<img src='{agent_uri}' style='max-height:150px;border-radius:6px;"
                    f"border:2px solid #fb923c;margin-top:8px;cursor:zoom-in'/>"
                    f"</a>"
                    if agent_uri else
                    "<div style='font-size:0.75rem;color:#9ca3af;margin-top:4px;font-style:italic'>"
                    "No screenshot available.</div>"
                )
                agent_cards.append(f"""
<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;
            padding:14px 16px;flex:1;min-width:200px">
  <div style="font-weight:700;color:#9a3412;font-size:1rem;margin-bottom:4px">{name}</div>
  <div style="font-size:0.82rem;color:#c2410c">Agent tab never appeared — workflow may have stalled before this agent ran.</div>
  {img_html}
</div>""")

            sections_html.append(f"""
<div style="margin-bottom:20px">
  <h3 style="color:#ea580c;margin-bottom:10px">
    ⚠ Agents That Did Not Appear ({len(missing_agents)})
  </h3>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
    {"".join(agent_cards)}
  </div>
</div>""")

        # ── Judge: Missing Elements ────────────────────────────────────────────
        if missing_els:
            items_html = "".join(
                f"<div style='padding:6px 12px;background:#fff7ed;border-left:3px solid #fb923c;"
                f"border-radius:0 4px 4px 0;margin-bottom:6px;font-size:0.85rem;color:#9a3412'>"
                f"○ {m}</div>"
                for m in missing_els
            )
            sections_html.append(f"""
<div style="margin-bottom:20px">
  <h3 style="color:#d97706;margin-bottom:10px">
    ○ Missing Elements Identified by Judge ({len(missing_els)})
  </h3>
  {items_html}
</div>""")

        # ── Judge: Improvement Flags ──────────────────────────────────────────
        if flags:
            flags_html = "".join(
                f"<div style='padding:6px 12px;background:#fef2f2;border-left:3px solid #f87171;"
                f"border-radius:0 4px 4px 0;margin-bottom:6px;font-size:0.85rem;color:#991b1b'>"
                f"⚑ {f}</div>"
                for f in flags
            )
            sections_html.append(f"""
<div style="margin-bottom:20px">
  <h3 style="color:#dc2626;margin-bottom:10px">
    ⚑ Improvement Flags from Judge ({len(flags)})
  </h3>
  {flags_html}
</div>""")

        # ── SAI conversation anomalies ────────────────────────────────────────
        if psi_failures:
            psi_items = "".join(
                f"<div style='padding:8px 12px;background:#fefce8;border-left:3px solid #fde047;"
                f"border-radius:0 4px 4px 0;margin-bottom:6px;font-size:0.82rem;color:#713f12'>"
                f"{e.get('text','')[:300]}</div>"
                for e in psi_failures
            )
            sections_html.append(f"""
<div style="margin-bottom:20px">
  <h3 style="color:#ca8a04;margin-bottom:10px">
    ⚡ SAI Conversation Anomalies ({len(psi_failures)})
  </h3>
  {psi_items}
</div>""")

        return f"""
<div id="failures" class="card">
  <h2>8 · Failure Analysis</h2>
  {"".join(sections_html)}
</div>"""

    # ── §9 Final Metrics ──────────────────────────────────────────────────────

    def _section_final_metrics(self, r: dict, verdict: dict, steps: list, canvas: dict) -> str:
        total_steps  = len(steps)
        passed_steps = sum(1 for s in steps if s.get("success", True))
        failed_steps = total_steps - passed_steps
        accuracy_pct = int(passed_steps / total_steps * 100) if total_steps else 0

        # Aggregate scores from verdict
        score_fields = [
            ("correctness_score", "Correctness"),
            ("process_score",     "Process"),
            ("output_score",      "Output"),
            ("reliability_score", "Reliability"),
            ("ux_score",          "UX"),
            ("reasoning_score",   "Reasoning"),
            ("visual_score",      "Visual"),
        ]
        score_bars = []
        for field, label in score_fields:
            val = verdict.get(field)
            if val is None:
                continue
            colour = _score_colour(val)
            bar_w  = int(val * 10)
            score_bars.append(
                f"<div style='margin-bottom:10px'>"
                f"<div style='display:flex;justify-content:space-between;margin-bottom:3px'>"
                f"<span style='font-size:0.82rem;color:#475569'>{label}</span>"
                f"<span style='font-weight:700;color:{colour}'>{val}/10</span>"
                f"</div>"
                f"<div style='height:8px;background:#f1f5f9;border-radius:4px;overflow:hidden'>"
                f"<div style='height:100%;width:{bar_w}%;background:{colour};border-radius:4px'></div>"
                f"</div></div>"
            )

        # Per-agent scores
        per_agent = verdict.get("per_agent_scores") or {}
        agent_cells = []
        for agent, score in per_agent.items():
            colour = _score_colour(score)
            agent_cells.append(
                f"<div style='text-align:center;padding:12px 16px;background:#f8fafc;"
                f"border-radius:8px;border:1px solid #e2e8f0;flex:1;min-width:100px'>"
                f"<div style='font-size:1.4rem;font-weight:700;color:{colour}'>{score}</div>"
                f"<div style='font-size:0.72rem;color:#64748b;margin-top:2px'>{agent}</div>"
                f"</div>"
            )

        # Issue severity breakdown
        issues = verdict.get("issues_found") or []
        critical = sum(1 for i in issues if i.get("severity") == "critical")
        major    = sum(1 for i in issues if i.get("severity") == "major")
        minor    = sum(1 for i in issues if i.get("severity") == "minor")

        issues_html = ""
        if issues:
            def _issue_row(i: dict) -> str:
                sev = (i.get("severity") or "").lower()
                bg  = "#fef2f2" if sev == "critical" else "#fff7ed" if sev == "major" else "#f8fafc"
                col = "#dc2626" if sev == "critical" else "#d97706" if sev == "major" else "#64748b"
                return (
                    f"<tr style='border-bottom:1px solid #f1f5f9'>"
                    f"<td style='padding:7px 12px'>"
                    f"<span style='font-size:0.72rem;font-weight:700;padding:1px 8px;border-radius:10px;"
                    f"background:{bg};color:{col}'>"
                    f"{sev.upper()}</span></td>"
                    f"<td style='padding:7px 12px;font-size:0.8rem;color:#475569'>{i.get('category','')}</td>"
                    f"<td style='padding:7px 12px;font-size:0.82rem;color:#1e293b'>{i.get('description','')}</td>"
                    f"</tr>"
                )
            issue_rows = "".join(_issue_row(i) for i in issues)
            issues_html = f"""
<div style="margin-top:20px">
  <h3 style="margin-bottom:10px">Issues Found ({len(issues)})
    <span style="font-size:0.82rem;font-weight:400;color:#64748b;margin-left:8px">
      <span style="color:#dc2626">{critical} critical</span> ·
      <span style="color:#d97706">{major} major</span> ·
      <span style="color:#64748b">{minor} minor</span>
    </span>
  </h3>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;
                     text-transform:uppercase;letter-spacing:.05em;white-space:nowrap">Severity</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;
                     text-transform:uppercase;letter-spacing:.05em">Category</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;
                     text-transform:uppercase;letter-spacing:.05em">Description</th>
        </tr>
      </thead>
      <tbody>{issue_rows}</tbody>
    </table>
  </div>
</div>"""

        # Common failure patterns from improvement flags
        flags = verdict.get("improvement_flags") or []
        patterns_html = ""
        if flags:
            patterns_html = (
                f"<div style='margin-top:20px'>"
                f"<h3 style='margin-bottom:10px'>Common Failure Patterns / Improvements</h3>"
                + "".join(
                    f"<div style='padding:6px 12px;background:#fef9ee;border-left:3px solid #fbbf24;"
                    f"border-radius:0 4px 4px 0;margin-bottom:5px;font-size:0.82rem;color:#78350f'>→ {f}</div>"
                    for f in flags
                )
                + "</div>"
            )

        # Status badge
        status    = r.get("overall_status", "unknown")
        bg, label = _status_badge(status)
        conf      = verdict.get("confidence_score", "N/A")

        return f"""
<div id="metrics" class="card">
  <h2>9 · Final Metrics</h2>

  <!-- Top-level run stats -->
  <div class="grid-4" style="margin-bottom:24px">
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num">{total_steps}</div>
      <div class="stat-label">Total Steps</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0">
      <div class="stat-num" style="color:#16a34a">{passed_steps}</div>
      <div class="stat-label">Steps Passed</div>
    </div>
    <div style="text-align:center;padding:16px;background:#fef2f2;border-radius:8px;border:1px solid #fecaca">
      <div class="stat-num" style="color:#dc2626">{failed_steps}</div>
      <div class="stat-label">Steps Failed</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0">
      <div class="stat-num" style="color:{'#16a34a' if accuracy_pct>=80 else '#d97706' if accuracy_pct>=60 else '#dc2626'}">{accuracy_pct}%</div>
      <div class="stat-label">Step Accuracy</div>
    </div>
  </div>

  <div class="grid-2">
    <!-- Score bars -->
    <div>
      <h3 style="margin-bottom:14px">Score Breakdown</h3>
      {"".join(score_bars) or "<p style='color:#9ca3af'>No scores available.</p>"}
    </div>
    <!-- Run summary -->
    <div>
      <h3 style="margin-bottom:14px">Run Summary</h3>
      <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
        <tr><td style="padding:6px 0;color:#64748b;width:140px">Overall Status</td>
            <td><span class="badge" style="background:{bg}">{label}</span></td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Confidence Score</td>
            <td><strong>{conf}</strong>/100</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">SAI Rating</td>
            <td><strong>{verdict.get("psi_questions_rating","N/A").upper()}</strong></td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Recovery Needed</td>
            <td>{"Yes" if verdict.get("recovery_needed") else "No"}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Issues (critical)</td>
            <td><span style="color:#dc2626;font-weight:700">{critical}</span></td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Issues (major)</td>
            <td><span style="color:#d97706;font-weight:700">{major}</span></td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Issues (minor)</td>
            <td><span style="color:#64748b;font-weight:700">{minor}</span></td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Empty Sections</td>
            <td>{len(verdict.get("empty_sections") or [])}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Hallucination Flags</td>
            <td>{len(verdict.get("hallucination_flags") or [])}</td></tr>
      </table>
    </div>
  </div>

  <!-- Per-agent scores -->
  {f'''<div style="margin-top:20px">
    <h3 style="margin-bottom:12px">Per-Agent Scores</h3>
    <div style="display:flex;gap:10px;flex-wrap:wrap">{"".join(agent_cells)}</div>
  </div>''' if agent_cells else ""}

  {issues_html}
  {patterns_html}
</div>"""

    # ── §10 Screenshot Gallery ─────────────────────────────────────────────────

    def _section_gallery(self, steps: list, canvas: dict) -> str:
        images = []

        # Collect from step log
        for s in steps:
            p = s.get("screenshot_path")
            if p:
                label = f"Step {s.get('step_number')} · {s.get('action_type')} — {s.get('description','')[:50]}"
                images.append((p, label))

        # Collect from canvas agent screenshots
        for agent_name, adata in canvas.get("agents", {}).items():
            for ss in adata.get("screenshots", []):
                p = ss.get("path")
                if p:
                    label = f"{agent_name} · {ss.get('sub_tab','')}"
                    images.append((p, label))

        if not images:
            return """<div id="gallery" class="card"><h2>10 · Screenshot Gallery</h2>
                      <p style="color:#9ca3af">No screenshots captured.</p></div>"""

        # Deduplicate by path
        seen = set()
        unique_images = []
        for path, label in images:
            if path not in seen:
                seen.add(path)
                unique_images.append((path, label))

        cards = []
        for path, label in unique_images:
            uri = _embed_image(path)
            if not uri:
                continue
            fname = os.path.basename(path)
            cards.append(f"""
<div style="break-inside:avoid;margin-bottom:16px">
  <a href="{uri}" target="_blank">
    <img src="{uri}" class="screenshot"/>
  </a>
  <div class="screenshot-caption" style="padding:4px 2px">
    <span title="{path}">{fname}</span><br/>
    <span style="color:#cbd5e1">{label}</span>
  </div>
</div>""")

        return f"""
<div id="gallery" class="card">
  <h2>10 · Screenshot Gallery <span style="font-size:0.85rem;font-weight:400;color:#64748b">
    ({len(unique_images)} screenshots · click to enlarge)</span></h2>
  <div style="columns:2;column-gap:16px">
    {"".join(cards)}
  </div>
</div>"""


    # ── §11 Flow Graph Report ─────────────────────────────────────────────────

    def _section_flow_graph(self, flow_view: dict, verdict: dict) -> str:
        if not flow_view:
            return ""

        phases   = flow_view.get("phases") or []
        val      = flow_view.get("validation") or {}
        metrics  = val.get("metrics") or {}
        issues   = val.get("issues") or []
        fv_verdict = val.get("verdict", "unknown")
        fv_score = verdict.get("flow_graph_score")
        source   = flow_view.get("extraction_source", "unknown")
        fv_eval  = verdict.get("flow_graph_evaluation", "")

        bg_col, label = _status_badge(fv_verdict)
        score_col     = _score_colour(fv_score)

        STATUS_COLOURS = {
            "completed":   ("#16a34a", "#dcfce7"),
            "in_progress": ("#2563eb", "#dbeafe"),
            "pending":     ("#64748b", "#f1f5f9"),
            "cancelled":   ("#d97706", "#fef3c7"),
            "error":       ("#dc2626", "#fee2e2"),
            "unknown":     ("#6b7280", "#f8fafc"),
        }

        # Phase cards
        phase_cards = ""
        for p in phases:
            st  = p.get("status", "unknown")
            col, bg = STATUS_COLOURS.get(st, ("#6b7280", "#f8fafc"))
            name = p.get("name") or f"Phase {p.get('index','?')}"
            desc = p.get("description", "")[:120]
            agent = p.get("agent", "")
            phase_cards += f"""
<div style="background:{bg};border:1px solid {col}33;border-radius:8px;padding:10px 14px;margin-bottom:8px">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="background:{col};color:#fff;border-radius:12px;padding:1px 9px;font-size:0.72rem;font-weight:700">{st.upper().replace("_"," ")}</span>
    <span style="font-weight:600;font-size:0.9rem">{name}</span>
    {"<span style='font-size:0.78rem;color:#64748b;margin-left:auto'>agent: " + agent + "</span>" if agent else ""}
  </div>
  {"<div style='font-size:0.82rem;color:#475569'>" + desc + "</div>" if desc else ""}
</div>"""

        if not phase_cards:
            phase_cards = "<p style='color:#6b7280;font-style:italic'>No phase data extracted.</p>"

        # Issues table
        issues_rows = ""
        for iss in issues:
            sev = iss.get("severity", "minor")
            sev_col = {"critical": "#dc2626", "major": "#d97706", "minor": "#64748b"}.get(sev, "#64748b")
            issues_rows += f"""
<tr>
  <td><span style="background:{sev_col};color:#fff;border-radius:10px;padding:1px 8px;font-size:0.72rem;font-weight:700">{sev.upper()}</span></td>
  <td style="font-family:monospace;font-size:0.78rem">{iss.get("rule","")}</td>
  <td>{iss.get("description","")}</td>
  <td style="color:#2563eb;font-size:0.82rem">{iss.get("fix_recommendation","")}</td>
</tr>"""

        issues_section = ""
        if issues_rows:
            issues_section = f"""
<h3 style="margin-top:18px;margin-bottom:8px">Flow Graph Issues</h3>
<div style="overflow-x:auto">
<table style="width:100%;border-collapse:collapse;font-size:0.85rem">
  <thead style="background:#f1f5f9">
    <tr>
      <th style="padding:8px 10px;text-align:left;font-weight:600">Severity</th>
      <th style="padding:8px 10px;text-align:left;font-weight:600">Rule</th>
      <th style="padding:8px 10px;text-align:left;font-weight:600">Description</th>
      <th style="padding:8px 10px;text-align:left;font-weight:600;color:#2563eb">Fix</th>
    </tr>
  </thead>
  <tbody>
    {issues_rows}
  </tbody>
</table>
</div>"""

        # Phase screenshots
        ss_html = ""
        for ss in flow_view.get("screenshots", [])[:6]:
            uri = _embed_image(ss.get("path", ""))
            if uri:
                label_txt = ss.get("label", "")
                phase_lbl = ss.get("phase_name") or ss.get("status", "")
                ss_html += f"""
<div style="break-inside:avoid;margin-bottom:12px">
  <img src="{uri}" style="width:100%;border-radius:8px;border:1px solid #e2e8f0" alt="{label_txt}"/>
  <div style="font-size:0.75rem;color:#64748b;margin-top:4px">{label_txt}{" · " + phase_lbl if phase_lbl else ""}</div>
</div>"""

        screenshots_section = ""
        if ss_html:
            screenshots_section = f"""
<h3 style="margin-top:18px;margin-bottom:10px">Flow View Screenshots</h3>
<div style="columns:2;column-gap:14px">{ss_html}</div>"""

        score_display = f'<span style="font-size:1.5rem;font-weight:700;color:{score_col}">{fv_score}/10</span>' if fv_score is not None else ""

        return f"""
<div class="card" id="flow-graph">
  <h2>§11 Flow Graph Validation</h2>
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px;flex-wrap:wrap">
    <span class="badge" style="background:{bg_col}">{label}</span>
    {score_display}
    <span style="color:#64748b;font-size:0.82rem">source: {source} · phases: {len(phases)} · completed: {metrics.get("completed",0)} · errors: {metrics.get("error",0)}</span>
  </div>
  {"<p style='color:#475569;margin-bottom:14px'>" + fv_eval + "</p>" if fv_eval else ""}

  <div class="grid-2" style="align-items:start">
    <div>
      <h3>Phase Breakdown</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        {"".join(f'<span class="pill" style="background:{STATUS_COLOURS.get(k,(None,"#f1f5f9"))[1]};border-color:{STATUS_COLOURS.get(k,("#6b7280",None))[0]}33;color:{STATUS_COLOURS.get(k,("#6b7280",None))[0]};font-weight:600">{k.replace("_"," ").title()}: {v}</span>' for k,v in metrics.items() if k in ("completed","in_progress","pending","cancelled","error","unknown") and isinstance(v,int))}
      </div>
      {phase_cards}
    </div>
    <div>
      {screenshots_section}
    </div>
  </div>
  {issues_section}
</div>"""

    # ── §12 Accessibility Report ───────────────────────────────────────────────

    def _section_accessibility(self, verdict: dict, a11y_report: dict) -> str:
        a11y_issues = verdict.get("a11y_issues") or []
        if not a11y_issues and a11y_report:
            a11y_issues = a11y_report.get("issues") or []

        score   = verdict.get("a11y_score") or (a11y_report.get("score") if a11y_report else None)
        summary = a11y_report.get("summary", {}) if a11y_report else {}
        score_c = _score_colour(int(score) if score is not None else None)

        sev_colours = {"critical": "#dc2626", "major": "#d97706", "minor": "#64748b"}

        if not a11y_issues:
            return f"""
<div id="a11y" class="card">
  <h2>11 · Accessibility Report</h2>
  <div style="display:flex;align-items:center;gap:12px;padding:16px;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0">
    <span style="font-size:1.4rem">✓</span>
    <span style="color:#166534;font-weight:600">No accessibility issues detected.</span>
    {f'<span style="margin-left:auto;font-size:1.3rem;font-weight:700;color:{score_c}">{score}/10</span>' if score is not None else ""}
  </div>
</div>"""

        # Group by category
        by_rule: dict[str, list] = {}
        for iss in a11y_issues:
            rule = iss.get("rule", "other")
            by_rule.setdefault(rule, []).append(iss)

        rows = []
        for iss in a11y_issues:
            sev     = (iss.get("severity") or "minor").lower()
            col     = sev_colours.get(sev, "#64748b")
            bg      = "#fef2f2" if sev == "critical" else "#fff7ed" if sev == "major" else "#f8fafc"
            fix     = iss.get("fix_recommendation") or ""
            element = iss.get("element") or ""
            text    = iss.get("element_text") or ""
            rows.append(f"""
<tr style="border-bottom:1px solid #f1f5f9;background:{bg}">
  <td style="padding:8px 12px">
    <span style="font-size:0.72rem;font-weight:700;padding:2px 8px;border-radius:10px;
                 background:{col}22;color:{col};border:1px solid {col}44">{sev.upper()}</span>
  </td>
  <td style="padding:8px 12px">
    <code style="background:#f1f5f9;padding:1px 6px;border-radius:4px;font-size:0.78rem">{iss.get('rule','')}</code>
  </td>
  <td style="padding:8px 12px;font-size:0.82rem;color:#334155">{iss.get('description','')}</td>
  <td style="padding:8px 12px;font-size:0.78rem;color:#64748b">{element}{(' — ' + text[:30]) if text else ''}</td>
  <td style="padding:8px 12px;font-size:0.78rem;color:#0f172a;background:#eff6ff;border-left:3px solid #3b82f6">{fix}</td>
</tr>""")

        critical = summary.get("critical", sum(1 for i in a11y_issues if i.get("severity") == "critical"))
        major    = summary.get("major",    sum(1 for i in a11y_issues if i.get("severity") == "major"))
        minor    = summary.get("minor",    sum(1 for i in a11y_issues if i.get("severity") == "minor"))

        return f"""
<div id="a11y" class="card">
  <h2>11 · Accessibility Report
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      ({len(a11y_issues)} issues · <span style="color:#dc2626">{critical} critical</span> ·
      <span style="color:#d97706">{major} major</span> · <span style="color:#64748b">{minor} minor</span>)
    </span>
    {f'<span style="float:right;font-size:1rem;font-weight:700;color:{score_c}">Score: {score}/10</span>' if score is not None else ""}
  </h2>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;min-width:700px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap">Severity</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Rule</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Issue</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Element</th>
          <th style="padding:8px 12px;font-size:0.72rem;font-weight:700;color:#2563eb;text-transform:uppercase;letter-spacing:.05em">Fix Recommendation</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</div>"""

    # ── §13 Visual Diff Report ────────────────────────────────────────────────

    def _section_visual_diff(self, change_timeline: list, verdict: dict) -> str:
        """
        Full Visual Diff Report:
          - Per diff-pair card: expected screenshot | actual screenshot | diff image
          - Metrics table: Similarity %, Changed Regions, UI Drift
          - Summary banner + layout issues table
        Falls back to change-timeline view when no pixel diffs are available.
        """
        diff_pairs      = verdict.get("visual_diff_pairs") or []
        layout_issues_all = verdict.get("layout_issues") or []

        # ── Summary stats across all pairs ────────────────────────────────────
        valid_pairs  = [d for d in diff_pairs if d.get("similarity_pct") is not None]
        avg_sim      = round(sum(d["similarity_pct"] for d in valid_pairs) / len(valid_pairs), 1) if valid_pairs else None
        total_regions = sum(d.get("changed_regions") or 0 for d in valid_pairs)
        fail_count   = sum(1 for d in valid_pairs if d.get("verdict") == "FAIL")
        partial_count = sum(1 for d in valid_pairs if d.get("verdict") == "PARTIAL")
        pass_count   = sum(1 for d in valid_pairs if d.get("verdict") == "PASS")

        # Overall drift from average similarity
        if avg_sim is not None:
            from app.judge.visual_judge import _drift_label
            overall_drift, overall_drift_colour = _drift_label(avg_sim)
        else:
            overall_drift, overall_drift_colour = "Unknown", "#6b7280"

        # ── Summary banner ────────────────────────────────────────────────────
        if valid_pairs:
            sim_colour = "#16a34a" if avg_sim >= 85 else ("#d97706" if avg_sim >= 60 else "#dc2626")
            summary_html = f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px">
  <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
    <div style="font-size:1.9rem;font-weight:700;color:{sim_colour}">{avg_sim}%</div>
    <div style="font-size:0.75rem;color:#64748b;margin-top:4px">Avg Similarity</div>
  </div>
  <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
    <div style="font-size:1.9rem;font-weight:700;color:#334155">{total_regions}</div>
    <div style="font-size:0.75rem;color:#64748b;margin-top:4px">Changed Regions</div>
  </div>
  <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
    <div style="font-size:1.5rem;font-weight:700;color:{overall_drift_colour}">{overall_drift}</div>
    <div style="font-size:0.75rem;color:#64748b;margin-top:4px">UI Drift</div>
  </div>
  <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
    <div style="font-size:0.85rem;font-weight:600;margin-top:4px">
      <span style="color:#16a34a">{pass_count} PASS</span> ·
      <span style="color:#d97706">{partial_count} PARTIAL</span> ·
      <span style="color:#dc2626">{fail_count} FAIL</span>
    </div>
    <div style="font-size:0.75rem;color:#64748b;margin-top:6px">{len(valid_pairs)} comparisons</div>
  </div>
</div>"""
        else:
            summary_html = """
<div style="padding:14px;background:#fefce8;border:1px solid #fde68a;border-radius:8px;margin-bottom:20px;color:#92400e">
  No pixel-level diffs available — Pillow may not be installed or no screenshot pairs were found.
  Install with: <code>pip install Pillow</code>
</div>"""

        # ── Per-pair cards ─────────────────────────────────────────────────────
        pair_cards = []
        for dp in diff_pairs:
            label     = dp.get("label", "")
            sim       = dp.get("similarity_pct")
            regions   = dp.get("changed_regions")
            drift     = dp.get("ui_drift", "Unknown")
            drift_col = dp.get("ui_drift_colour", "#6b7280")
            dv        = dp.get("verdict", "unclear").upper()
            err       = dp.get("error")
            diff_b64  = dp.get("diff_image_b64")

            exp_uri  = _embed_image(dp.get("expected_path", ""))
            act_uri  = _embed_image(dp.get("actual_path", ""))
            diff_uri = f"data:image/png;base64,{diff_b64}" if diff_b64 else None

            verdict_colour = {"PASS": "#16a34a", "PARTIAL": "#d97706", "FAIL": "#dc2626"}.get(dv, "#6b7280")
            card_border    = {"PASS": "#bbf7d0", "PARTIAL": "#fde68a", "FAIL": "#fecaca"}.get(dv, "#e2e8f0")
            card_bg        = {"PASS": "#f0fdf4", "PARTIAL": "#fefce8", "FAIL": "#fef2f2"}.get(dv, "#f8fafc")

            if err:
                pair_cards.append(f"""
<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:16px;margin-bottom:16px">
  <div style="font-weight:600;color:#991b1b;margin-bottom:6px">{label or "Diff pair"}</div>
  <div style="font-size:0.82rem;color:#dc2626">Error: {err}</div>
</div>""")
                continue

            # Metrics row
            sim_colour = "#16a34a" if (sim or 0) >= 85 else ("#d97706" if (sim or 0) >= 60 else "#dc2626")

            def _metric(label_: str, value_: str, colour_: str) -> str:
                return (
                    f"<td style='padding:8px 16px;text-align:center'>"
                    f"<div style='font-size:1.2rem;font-weight:700;color:{colour_}'>{value_}</div>"
                    f"<div style='font-size:0.7rem;color:#64748b;margin-top:2px'>{label_}</div>"
                    f"</td>"
                )

            metrics_row = (
                f"<table style='width:100%;border-collapse:collapse;background:#f8fafc;"
                f"border-radius:8px;border:1px solid #e2e8f0;margin-bottom:14px'><tr>"
                f"{_metric('Similarity', f'{sim}%' if sim is not None else '—', sim_colour)}"
                f"<td style='width:1px;background:#e2e8f0'></td>"
                f"{_metric('Changed Regions', str(regions) if regions is not None else '—', '#334155')}"
                f"<td style='width:1px;background:#e2e8f0'></td>"
                f"{_metric('UI Drift', drift, drift_col)}"
                f"<td style='width:1px;background:#e2e8f0'></td>"
                f"{_metric('Verdict', dv, verdict_colour)}"
                f"</tr></table>"
            )

            # Screenshot trio: expected | actual | diff
            def _ss_col(uri_: Optional[str], cap_: str, border_: str = "#e2e8f0") -> str:
                if uri_:
                    return (
                        f"<div style='flex:1;min-width:0'>"
                        f"<div style='font-size:0.7rem;font-weight:700;color:#64748b;text-transform:uppercase;"
                        f"letter-spacing:.05em;margin-bottom:4px'>{cap_}</div>"
                        f"<a href='{uri_}' target='_blank'>"
                        f"<img src='{uri_}' style='width:100%;border-radius:6px;border:2px solid {border_};"
                        f"cursor:zoom-in;object-fit:contain;background:#000;max-height:220px'/>"
                        f"</a></div>"
                    )
                return (
                    f"<div style='flex:1;min-width:0;display:flex;align-items:center;justify-content:center;"
                    f"background:#f1f5f9;border-radius:6px;border:1px dashed #cbd5e1;min-height:120px'>"
                    f"<span style='color:#9ca3af;font-size:0.8rem'>{cap_}<br/>not available</span></div>"
                )

            screenshots_row = (
                f"<div style='display:flex;gap:10px;flex-wrap:wrap'>"
                f"{_ss_col(exp_uri,  'Expected',    '#93c5fd')}"
                f"{_ss_col(act_uri,  'Actual',       '#86efac')}"
                f"{_ss_col(diff_uri, 'Diff (red = changed)', '#fca5a5')}"
                f"</div>"
            )

            pair_cards.append(f"""
<details style="margin-bottom:14px" {'open' if dv != 'PASS' else ''}>
  <summary style="list-style:none;cursor:pointer;padding:12px 16px;
                  background:{card_bg};border:1px solid {card_border};border-radius:10px;
                  display:flex;align-items:center;gap:10px">
    <span style="background:{verdict_colour};color:#fff;border-radius:12px;padding:2px 10px;
                 font-size:0.72rem;font-weight:700">{dv}</span>
    <span style="font-weight:600;font-size:0.9rem;flex:1">{label}</span>
    <span style="font-size:0.82rem;color:{sim_colour};font-weight:700">
      {f'{sim}% similar' if sim is not None else ''}
    </span>
    <span style="font-size:0.72rem;color:{drift_col};margin-left:8px">
      Drift: {drift}
    </span>
  </summary>
  <div style="padding:16px;border:1px solid {card_border};border-top:none;
              border-radius:0 0 10px 10px;background:#fff">
    {metrics_row}
    {screenshots_row}
  </div>
</details>""")

        # ── Change-event timeline (collapsed, secondary info) ─────────────────
        type_colours = {
            "canvas_updated":   "#7c3aed", "tab_change":  "#2563eb",
            "new_message":      "#0891b2", "loading_complete": "#16a34a",
            "loading_started":  "#d97706", "ui_change":   "#9333ea",
            "dom_mutation":     "#64748b",
        }
        timeline_cards = []
        for i, ev in enumerate(change_timeline or []):
            ct     = ev.get("change_type", "unknown")
            desc   = ev.get("description", "")
            ts     = (ev.get("timestamp") or "")[:19].replace("T", " ")
            ss_uri = _embed_image(ev.get("screenshot_path", ""))
            colour = type_colours.get(ct, "#64748b")
            thumb  = (
                f"<a href='{ss_uri}' target='_blank'>"
                f"<img src='{ss_uri}' style='max-height:80px;border-radius:4px;"
                f"border:1px solid #e2e8f0;cursor:zoom-in'/></a>"
                if ss_uri else ""
            )
            timeline_cards.append(f"""
<div style="display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #f1f5f9;align-items:flex-start">
  <div style="width:8px;height:8px;border-radius:50%;background:{colour};margin-top:6px;flex-shrink:0"></div>
  <div style="flex:1">
    <span style="font-size:0.72rem;font-weight:700;padding:1px 8px;border-radius:10px;
                 background:{colour}1a;color:{colour};border:1px solid {colour}44">{ct}</span>
    <span style="font-size:0.72rem;color:#94a3b8;margin-left:6px">#{i+1} · {ts}</span>
    <div style="font-size:0.82rem;color:#334155;margin-top:3px">{desc}</div>
  </div>
  {thumb}
</div>""")

        timeline_section = ""
        if timeline_cards:
            timeline_section = f"""
<details style="margin-top:20px">
  <summary>Change Event Timeline ({len(timeline_cards)} events)</summary>
  <div style="max-height:400px;overflow-y:auto;padding:8px 0;margin-top:8px">
    {"".join(timeline_cards)}
  </div>
</details>"""

        # ── Layout issues table ────────────────────────────────────────────────
        layout_panel = ""
        if layout_issues_all:
            sev_c = {"critical": "#dc2626", "major": "#d97706", "minor": "#64748b"}
            li_rows = "".join(
                f"<tr style='border-bottom:1px solid #f1f5f9'>"
                f"<td style='padding:6px 10px'>"
                f"<span style='font-size:0.72rem;font-weight:700;padding:1px 6px;border-radius:8px;"
                f"background:{sev_c.get(iss.get('severity','minor'),'#64748b')}22;"
                f"color:{sev_c.get(iss.get('severity','minor'),'#64748b')}'>"
                f"{(iss.get('severity','minor')).upper()}</span></td>"
                f"<td style='padding:6px 10px;font-size:0.8rem;color:#334155'>{iss.get('description','')}</td>"
                f"<td style='padding:6px 10px;font-size:0.78rem;color:#0f172a;background:#eff6ff;"
                f"border-left:2px solid #3b82f6'>{iss.get('fix_recommendation','')}</td>"
                f"</tr>"
                for iss in layout_issues_all[:12]
            )
            layout_panel = f"""
<div style="margin-top:24px">
  <h3 style="margin-bottom:10px">Layout Issues Detected ({len(layout_issues_all)})</h3>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;min-width:600px">
      <thead>
        <tr style="background:#f8fafc">
          <th style="padding:8px 10px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase">Severity</th>
          <th style="padding:8px 10px;font-size:0.72rem;font-weight:700;color:#64748b;text-transform:uppercase">Issue</th>
          <th style="padding:8px 10px;font-size:0.72rem;font-weight:700;color:#2563eb;text-transform:uppercase">Fix</th>
        </tr>
      </thead>
      <tbody>{li_rows}</tbody>
    </table>
  </div>
</div>"""

        pair_count_label = (
            f"{len(valid_pairs)} diff pair(s)"
            if valid_pairs else
            "no pixel diffs"
        )

        return f"""
<div id="diff" class="card">
  <h2>13 · Visual Diff Report
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      ({pair_count_label}{f' · {len(change_timeline)} change events' if change_timeline else ''})
    </span>
  </h2>

  {summary_html}

  {"".join(pair_cards) if pair_cards else '<p style=\"color:#9ca3af\">No diff pairs to display.</p>'}

  {timeline_section}
  {layout_panel}
</div>"""


# ── Batch index page ──────────────────────────────────────────────────────────

def generate_batch_index(results: list[dict], persona_map: dict[str, dict]) -> str:
    """
    Generate a batch summary index HTML page linking to all individual run reports.
    results: list of run record dicts (from run_all_workflows)
    persona_map: {persona_id: persona_dict}
    Returns path to saved index file.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    total   = len(results)
    passed  = sum(1 for r in results if r.get("overall_status") == "pass")
    partial = sum(1 for r in results if r.get("overall_status") == "partial")
    failed  = sum(1 for r in results if r.get("overall_status") == "fail")
    errors  = sum(1 for r in results if r.get("overall_status") == "error")

    rows = []
    for r in results:
        run_id   = r.get("run_id", "")
        pid      = r.get("persona_id", "")
        pname    = r.get("persona_name", "")
        wid      = r.get("workflow_id", "")
        wtitle   = r.get("workflow_title", "")
        status   = r.get("overall_status", "")
        bg, lab  = _status_badge(status)
        verdict  = r.get("judge_verdict") or {}
        ps       = verdict.get("process_score")
        os_      = verdict.get("output_score")
        dur      = _duration(r.get("start_time"), r.get("end_time"))
        report_f = f"{run_id}_report.html"

        ps_cell  = f"<span style='color:{_score_colour(ps)};font-weight:600'>{ps}/10</span>" if ps is not None else "N/A"
        os_cell  = f"<span style='color:{_score_colour(os_)};font-weight:600'>{os_}/10</span>" if os_ is not None else "N/A"

        rows.append(f"""
<tr style="border-bottom:1px solid #f1f5f9">
  <td style="padding:10px 12px">
    <span class="badge" style="background:{bg}">{lab}</span>
  </td>
  <td style="padding:10px 12px">
    <span class="pill" style="background:#ede9fe;color:#6d28d9;border-color:#c4b5fd">{pid}</span>
    <span style="font-size:0.82rem;color:#64748b;margin-left:4px">{pname}</span>
  </td>
  <td style="padding:10px 12px">
    <div style="font-weight:600;font-size:0.88rem">{wtitle}</div>
    <div style="font-size:0.75rem;color:#94a3b8">{wid}</div>
  </td>
  <td style="padding:10px 12px;text-align:center">{ps_cell}</td>
  <td style="padding:10px 12px;text-align:center">{os_cell}</td>
  <td style="padding:10px 12px;font-size:0.82rem;color:#64748b">{dur}</td>
  <td style="padding:10px 12px">
    <a href="{report_f}" style="color:#2563eb;font-size:0.82rem;text-decoration:none;
       padding:3px 10px;border:1px solid #bfdbfe;border-radius:6px;background:#eff6ff">
      View Report →
    </a>
  </td>
</tr>""")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Batch Run Index · Adya AI SAI Testing</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f8fafc;color:#1e293b;font-size:14px}}
  .page{{max-width:1200px;margin:0 auto;padding:24px}}
  .card{{background:#fff;border-radius:12px;border:1px solid #e2e8f0;
         padding:24px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
  .badge{{display:inline-block;padding:3px 12px;border-radius:20px;font-size:0.75rem;
          font-weight:700;color:#fff}}
  .pill{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:0.78rem;
         background:#f1f5f9;color:#475569;border:1px solid #cbd5e1}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;padding:10px 12px;background:#f8fafc;font-size:0.78rem;
      font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em}}
  tr:hover{{background:#fafafa}}
</style>
</head>
<body>
<div class="page">
  <div class="card" style="background:linear-gradient(135deg,#1e293b,#334155);color:#fff;border:none">
    <h1 style="color:#fff;margin-bottom:8px">SAI Testing Agent · Batch Run Report</h1>
    <div style="opacity:.7;font-size:0.85rem">
      Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} · {total} total runs
    </div>
    <div style="display:flex;gap:24px;margin-top:16px;border-top:1px solid rgba(255,255,255,.15);padding-top:16px;flex-wrap:wrap">
      {_stat_box("Total", total, "#93c5fd")}
      {_stat_box("Pass", passed, "#86efac")}
      {_stat_box("Partial", partial, "#fcd34d")}
      {_stat_box("Fail", failed, "#fca5a5")}
      {_stat_box("Error", errors, "#c4b5fd")}
    </div>
  </div>

  <div class="card" style="padding:0;overflow:hidden">
    <table>
      <thead>
        <tr>
          <th>Status</th><th>Persona</th><th>Workflow</th>
          <th style="text-align:center">Process</th>
          <th style="text-align:center">Output</th>
          <th>Duration</th><th>Report</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""

    path = os.path.join(REPORTS_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("[RunReport] Batch index saved: %s", path)
    return path


def _stat_box(label: str, value: int, colour: str) -> str:
    return (
        f"<div style='text-align:center'>"
        f"<div style='font-size:2rem;font-weight:700;color:{colour}'>{value}</div>"
        f"<div style='font-size:0.75rem;opacity:.7'>{label}</div>"
        f"</div>"
    )
