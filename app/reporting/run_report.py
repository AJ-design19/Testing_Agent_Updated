"""
Per-run HTML report generator.

Produces one self-contained HTML file per test run that includes:
  - Persona profile card (identity, goals, pain points, technical depth)
  - Workflow summary (title, initial prompt, expected agents, evaluation criteria)
  - Full journey timeline (every action step with timestamps, types, status badges)
  - Psi conversation transcript (Q&A with role styling)
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
        "psi_loop":       "#0891b2",
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

        verdict  = r.get("judge_verdict") or {}
        steps    = r.get("steps") or []
        psi_log  = r.get("psi_conversation") or []
        canvas   = r.get("canvas_evidence") or {}

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
  .psi-msg{{padding:10px 14px;border-radius:10px;margin-bottom:8px;max-width:85%}}
  .psi-assistant{{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;margin-right:auto}}
  .psi-user{{background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;margin-left:auto}}
  .psi-system{{background:#fefce8;border:1px solid #fde68a;color:#92400e;margin:0 auto;
               font-style:italic;font-size:0.82rem}}
  .psi-role{{font-size:0.7rem;font-weight:700;margin-bottom:3px;opacity:.7;text-transform:uppercase}}
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
        Psi Quality
      </div>
      <div style="font-size:1.4rem;font-weight:700;color:#93c5fd;margin-top:4px">
        {verdict.get("psi_questions_rating", "N/A").upper()}
      </div>
      <div style="font-size:0.75rem;opacity:.6;margin-top:4px">
        {len(steps)} steps · {len(psi_log)} conversation turns
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
      <a href="#journey"   class="toc-link">3 · Journey Timeline</a>
      <a href="#psi"       class="toc-link">4 · Psi Conversation</a>
    </div>
    <div>
      <a href="#canvas"    class="toc-link">5 · Canvas Agent Evidence</a>
      <a href="#verdict"   class="toc-link">6 · LLM Judge Verdict</a>
      <a href="#failures"  class="toc-link">7 · Failure Analysis</a>
      <a href="#gallery"   class="toc-link">8 · Screenshot Gallery</a>
    </div>
  </div>
</div>

{self._section_persona(r, persona_data)}
{self._section_workflow(r)}
{self._section_journey(steps)}
{self._section_psi(psi_log)}
{self._section_canvas(canvas)}
{self._section_verdict(verdict)}
{self._section_failure_analysis(r, steps, canvas, verdict)}
{self._section_gallery(steps, canvas)}

<!-- ═══════════════════ FOOTER ═══════════════════ -->
<div style="text-align:center;padding:20px 0;color:#94a3b8;font-size:0.78rem">
  Generated by Adya AI · SAI Browser Testing Agent · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
</div>

</div><!-- /page -->
</body>
</html>"""

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

    # ── §3 Journey Timeline ────────────────────────────────────────────────────

    def _section_journey(self, steps: list) -> str:
        if not steps:
            return """<div id="journey" class="card"><h2>3 · Journey Timeline</h2>
                      <p style="color:#9ca3af">No steps recorded.</p></div>"""

        rows = []
        for s in steps:
            atype   = s.get("action_type", "")
            colour  = _action_type_colour(atype)
            success = s.get("success", True)
            icon    = "✓" if success else "✗"
            icon_c  = "#16a34a" if success else "#dc2626"
            ts      = (s.get("timestamp") or "")[:19].replace("T", " ")
            agent   = s.get("agent_tab")
            subtab  = s.get("sub_tab")
            content = s.get("content_snapshot")
            notes   = s.get("notes")
            ss_path = s.get("screenshot_path")
            ss_uri  = _embed_image(ss_path) if ss_path else None

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
    {content_block}{notes_block}{thumb}
  </div>
</div>""")

        return f"""
<div id="journey" class="card">
  <h2>3 · Journey Timeline <span style="font-size:0.85rem;font-weight:400;color:#64748b">
    ({len(steps)} steps)</span></h2>
  {"".join(rows)}
</div>"""

    # ── §4 Psi Conversation ────────────────────────────────────────────────────

    def _section_psi(self, psi_log: list) -> str:
        if not psi_log:
            return """<div id="psi" class="card"><h2>4 · Psi Conversation</h2>
                      <p style="color:#9ca3af">No conversation recorded.</p></div>"""

        bubbles = []
        for entry in psi_log:
            role    = entry.get("role", "").lower()
            text    = entry.get("text") or entry.get("content") or ""
            ts      = (entry.get("timestamp") or "")[:19].replace("T", " ")

            if role == "assistant":
                css  = "psi-assistant"
                rlab = "Psi (SAI)"
            elif role == "user":
                css  = "psi-user"
                rlab = "User (Persona)"
            else:
                css  = "psi-system"
                rlab = "System"

            ts_html = (f"<div style='font-size:0.68rem;color:#94a3b8;margin-top:4px'>{ts}</div>"
                       if ts else "")
            bubbles.append(f"""
<div class="psi-msg {css}">
  <div class="psi-role">{rlab}</div>
  <div>{text}</div>
  {ts_html}
</div>""")

        q_count = sum(1 for e in psi_log if e.get("role", "").lower() == "assistant")
        a_count = sum(1 for e in psi_log if e.get("role", "").lower() == "user")

        return f"""
<div id="psi" class="card">
  <h2>4 · Psi Conversation
    <span style="font-size:0.85rem;font-weight:400;color:#64748b">
      ({q_count} Psi messages · {a_count} user replies)
    </span>
  </h2>
  <div style="max-height:600px;overflow-y:auto;padding:4px 0">
    {"".join(bubbles)}
  </div>
</div>"""

    # ── §5 Canvas Agent Evidence ───────────────────────────────────────────────

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
                  <div class="subtab-name">{st}</div>
                  <div class="subtab-content">{(content or "(empty)")[:800]}</div>
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
  <h2>5 · Canvas Agent Evidence</h2>
  {content_inner}
</div>"""

    # ── §6 Judge Verdict ───────────────────────────────────────────────────────

    def _section_verdict(self, verdict: dict) -> str:
        if not verdict:
            return """<div id="verdict" class="card"><h2>6 · LLM Judge Verdict</h2>
                      <p style="color:#9ca3af">No verdict recorded.</p></div>"""

        flags   = verdict.get("improvement_flags") or []
        missing = verdict.get("missing_elements") or []
        proc_ev = verdict.get("process_evaluation") or ""
        out_ev  = verdict.get("output_evaluation") or ""
        ratio   = verdict.get("rationale") or ""

        flags_html = ("".join(f"<div class='flag-item'>⚑ {f}</div>" for f in flags)
                      or "<div style='color:#9ca3af;font-size:0.85rem'>None — great job!</div>")
        missing_html = ("".join(f"<div class='missing-item'>○ {m}</div>" for m in missing)
                        or "<div style='color:#9ca3af;font-size:0.85rem'>None missing.</div>")

        return f"""
<div id="verdict" class="card">
  <h2>6 · LLM Judge Verdict</h2>

  <!-- Score summary row -->
  <div class="grid-4" style="margin-bottom:20px">
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('correctness_score'))}">{verdict.get('correctness_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Correctness</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('process_score'))}">{verdict.get('process_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Process (Psi)</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:{_score_colour(verdict.get('output_score'))}">{verdict.get('output_score','?')}<span style="font-size:1rem">/10</span></div>
      <div class="stat-label">Output (Canvas)</div>
    </div>
    <div style="text-align:center;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">
      <div class="stat-num" style="color:#6366f1;font-size:1.3rem">{verdict.get('psi_questions_rating','?').upper()}</div>
      <div class="stat-label">Psi Q Rating</div>
    </div>
  </div>

  <div class="grid-2" style="margin-bottom:20px">
    <div>
      <h3 style="margin-bottom:8px">Process Evaluation (Psi)</h3>
      <div class="rationale">{proc_ev or "N/A"}</div>
    </div>
    <div>
      <h3 style="margin-bottom:8px">Output Evaluation (Canvas)</h3>
      <div class="rationale">{out_ev or "N/A"}</div>
    </div>
  </div>

  <h3 style="margin-bottom:8px">Overall Rationale</h3>
  <div class="rationale" style="margin-bottom:20px">{ratio or "N/A"}</div>

  <div class="grid-2">
    <div>
      <h3 style="margin-bottom:8px">Improvement Flags ({len(flags)})</h3>
      {flags_html}
    </div>
    <div>
      <h3 style="margin-bottom:8px">Missing Elements ({len(missing)})</h3>
      {missing_html}
    </div>
  </div>
</div>"""

    # ── §7 Failure Analysis ────────────────────────────────────────────────────

    def _section_failure_analysis(self, r: dict, steps: list, canvas: dict, verdict: dict) -> str:
        """
        Dedicated failure analysis section covering:
          - Failed and error steps with inline screenshots
          - Canvas agents that did not appear
          - Missing elements and improvement flags from the judge
          - Psi conversation failures (no response, timeout)
        """
        failed_steps = [s for s in steps if not s.get("success", True) or s.get("action_type") == "error"]
        agents_data  = canvas.get("agents", {})
        missing_agents = [name for name, a in agents_data.items() if not a.get("appeared", False)]
        flags        = verdict.get("improvement_flags") or []
        missing_els  = verdict.get("missing_elements") or []
        psi_log      = r.get("psi_conversation") or []
        psi_failures = [e for e in psi_log if e.get("role") == "system" or "error" in (e.get("text") or "").lower()]

        has_failures = bool(failed_steps or missing_agents or flags or missing_els or psi_failures)

        if not has_failures:
            return """
<div id="failures" class="card">
  <h2>7 · Failure Analysis</h2>
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

        # ── Psi conversation anomalies ────────────────────────────────────────
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
    ⚡ Psi Conversation Anomalies ({len(psi_failures)})
  </h3>
  {psi_items}
</div>""")

        return f"""
<div id="failures" class="card">
  <h2>7 · Failure Analysis</h2>
  {"".join(sections_html)}
</div>"""

    # ── §8 Screenshot Gallery ──────────────────────────────────────────────────

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
            return """<div id="gallery" class="card"><h2>8 · Screenshot Gallery</h2>
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
  <h2>8 · Screenshot Gallery <span style="font-size:0.85rem;font-weight:400;color:#64748b">
    ({len(unique_images)} screenshots · click to enlarge)</span></h2>
  <div style="columns:2;column-gap:16px">
    {"".join(cards)}
  </div>
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
