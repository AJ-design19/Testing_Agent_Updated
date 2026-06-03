"""
Per-Agent Detailed Report Generator.

For EVERY canvas agent that ran (AIA, AGP, ETL, App Studio, etc.), generates:
  - Metadata (session, workflow, persona, timing, agent name)
  - Interaction summary (what SAI said before this agent started)
  - Canvas observations (all sub-tabs visited, content captured)
  - Timeline events (from CanvasMonitor)
  - Output evaluation (correctness, quality, hallucination flags)
  - Failure & recovery details
  - Embedded screenshots (base64)
  - Visual judge scores

Output: reports/{run_id}_{agent_name}_agent_report.html (self-contained)
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

REPORTS_DIR = "reports"

AGENT_DESCRIPTIONS = {
    "AIA":        "AI Application agent — builds the application workflow graph and architecture",
    "AGP":        "AI Governance Protocols agent — defines compliance, governance rules, and policies",
    "ETL":        "Data Mapping / ETL agent — configures data sources, schema, and transformations",
    "App Studio": "Code generation agent with internal adversarial reviewer — produces working code",
    "PRD":        "Product Requirements Document agent — generates detailed PRDs",
    "Architecture": "Architecture agent — designs system architecture and component diagrams",
}


def _embed_image(path: str) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{data}"
    except Exception:
        return None


def _score_colour(score) -> str:
    if score is None:
        return "#6b7280"
    s = int(score)
    if s >= 8: return "#16a34a"
    if s >= 6: return "#d97706"
    return "#dc2626"


def _verdict_badge(verdict: str) -> tuple[str, str]:
    m = {
        "pass":    ("#16a34a", "PASS"),
        "partial": ("#d97706", "PARTIAL"),
        "fail":    ("#dc2626", "FAIL"),
        "unclear": ("#6b7280", "UNCLEAR"),
        "error":   ("#7c3aed", "ERROR"),
    }
    return m.get(verdict, ("#6b7280", verdict.upper()))


class AgentReportGenerator:
    """Generates a rich per-agent HTML report."""

    def generate(
        self,
        run_id: str,
        persona: dict,
        workflow: dict,
        agent_name: str,
        agent_evidence: dict,
        psi_conversation: list[dict],
        canvas_timeline: list[dict],
        agent_verdict: dict,
        run_metadata: dict,
    ) -> str:
        """
        Build and save the per-agent HTML report.
        Returns the file path.
        """
        os.makedirs(REPORTS_DIR, exist_ok=True)
        html = self._build_html(
            run_id, persona, workflow, agent_name,
            agent_evidence, psi_conversation, canvas_timeline,
            agent_verdict, run_metadata,
        )
        safe_name = agent_name.replace(" ", "_").replace("/", "_")
        path = os.path.join(REPORTS_DIR, f"{run_id}_{safe_name}_agent_report.html")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("[AgentReport] Saved: %s", path)
        except Exception as e:
            logger.error("[AgentReport] Save failed: %s", e)
        return path

    def _build_html(
        self,
        run_id: str,
        persona: dict,
        workflow: dict,
        agent_name: str,
        evidence: dict,
        sai_log: list[dict],
        timeline: list[dict],
        verdict: dict,
        meta: dict,
    ) -> str:
        appeared = evidence.get("appeared", False)
        state    = evidence.get("state", "unknown")
        sub_tabs = evidence.get("sub_tabs", {})
        screenshots = evidence.get("screenshots", [])

        agent_score = verdict.get("per_agent_scores", {}).get(agent_name)
        sc = _score_colour(agent_score)
        score_display = str(agent_score) if agent_score is not None else "?"

        appeared_colour = "#16a34a" if appeared else "#dc2626"
        appeared_label  = "APPEARED ✓" if appeared else "DID NOT APPEAR ✗"

        # ── Sub-tab content blocks ─────────────────────────────────────────────
        subtab_html = ""
        for tab_name, content in sub_tabs.items():
            content_display = (content or "(empty)").replace("<", "&lt;").replace(">", "&gt;")
            subtab_html += f"""
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
            padding:14px 16px;margin-bottom:12px">
  <div style="font-size:0.72rem;font-weight:700;color:#7c3aed;text-transform:uppercase;
              letter-spacing:.05em;margin-bottom:6px">{tab_name}</div>
  <pre style="font-size:0.8rem;color:#334155;white-space:pre-wrap;word-break:break-word;
              max-height:280px;overflow-y:auto;margin:0">{content_display[:1500]}</pre>
</div>"""

        # ── Screenshots gallery ────────────────────────────────────────────────
        ss_html = ""
        for ss in screenshots:
            uri = _embed_image(ss.get("path", ""))
            if uri:
                lbl = ss.get("label") or ss.get("sub_tab") or ""
                ss_html += f"""
<div style="break-inside:avoid;margin-bottom:14px">
  <a href="{uri}" target="_blank">
    <img src="{uri}" style="width:100%;border-radius:8px;border:1px solid #e2e8f0;
                            cursor:zoom-in;max-height:280px;object-fit:contain;background:#000"/>
  </a>
  <div style="font-size:0.7rem;color:#94a3b8;text-align:center;margin-top:3px">{lbl}</div>
</div>"""

        if not ss_html:
            ss_html = "<p style='color:#9ca3af'>No screenshots captured for this agent.</p>"

        # ── Canvas timeline events ─────────────────────────────────────────────
        agent_events = [e for e in timeline if e.get("agent") == agent_name]
        timeline_html = ""
        for evt in agent_events:
            ts = evt.get("timestamp", "")[:19].replace("T", " ")
            etype = evt.get("event_type", "")
            preview = evt.get("content_preview", "")
            colour = {
                "agent_tab_appeared":   "#2563eb",
                "agent_output_ready":   "#16a34a",
                "content_updated":      "#0891b2",
                "error":                "#dc2626",
            }.get(etype, "#6b7280")
            timeline_html += f"""
<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #f1f5f9">
  <div style="font-size:0.7rem;color:#94a3b8;min-width:130px">{ts}</div>
  <div style="font-size:0.75rem;font-weight:700;padding:1px 8px;border-radius:10px;
              background:{colour}1a;color:{colour};border:1px solid {colour}44;
              white-space:nowrap">{etype}</div>
  <div style="font-size:0.78rem;color:#475569;flex:1">{preview[:120]}</div>
</div>"""

        if not timeline_html:
            timeline_html = "<p style='color:#9ca3af;font-size:0.85rem'>No timeline events for this agent.</p>"

        # ── Improvement flags ─────────────────────────────────────────────────
        flags = verdict.get("improvement_flags", [])
        hallucinations = verdict.get("hallucination_flags", [])
        flags_html = "".join(
            f"<div style='padding:5px 12px;background:#fef2f2;border-left:3px solid #f87171;"
            f"border-radius:0 4px 4px 0;margin-bottom:5px;font-size:0.82rem;color:#991b1b'>"
            f"⚑ {f}</div>"
            for f in flags
        ) or "<p style='color:#9ca3af;font-size:0.85rem'>No flags.</p>"

        hall_html = "".join(
            f"<div style='padding:5px 12px;background:#fefce8;border-left:3px solid #fde047;"
            f"border-radius:0 4px 4px 0;margin-bottom:5px;font-size:0.82rem;color:#713f12'>"
            f"⚠ {h}</div>"
            for h in hallucinations
        ) or "<p style='color:#9ca3af;font-size:0.85rem'>None detected.</p>"

        # ── SAI messages before this agent ────────────────────────────────────
        psi_html = ""
        for entry in sai_log:
            role = entry.get("role", "").lower()
            text = (entry.get("text") or "").replace("<", "&lt;").replace(">", "&gt;")
            if role == "assistant":
                css, label = "background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af", "SAI (SAI)"
            elif role == "user":
                css, label = "background:#f0fdf4;border:1px solid #bbf7d0;color:#166534", "User (Persona)"
            else:
                css, label = "background:#fefce8;border:1px solid #fde68a;color:#92400e", "System"
            psi_html += f"""
<div style="padding:8px 12px;border-radius:8px;margin-bottom:7px;{css};max-width:90%">
  <div style="font-size:0.68rem;font-weight:700;opacity:.7;text-transform:uppercase;margin-bottom:2px">{label}</div>
  <div style="font-size:0.82rem">{text[:500]}</div>
</div>"""

        if not psi_html:
            psi_html = "<p style='color:#9ca3af;font-size:0.85rem'>No conversation recorded.</p>"

        # ── Output evaluation ─────────────────────────────────────────────────
        output_eval = verdict.get("output_evaluation", "N/A")
        reasoning_eval = verdict.get("reasoning_evaluation", "N/A")

        # Visual judge result for this agent
        visual_data = verdict.get("visual_evaluation", {}).get("per_agent", {}).get(agent_name, {})
        visual_score = visual_data.get("agent_visual_score", "N/A")
        visual_desc  = visual_data.get("visible_content_summary", "N/A")
        visual_issues = visual_data.get("issues", [])

        duration = meta.get("duration", "N/A")
        start_ts = meta.get("start_time", "")[:19].replace("T", " ")
        desc = AGENT_DESCRIPTIONS.get(agent_name, "Canvas agent")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Agent Report · {agent_name} · {run_id}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f8fafc;color:#1e293b;font-size:14px;line-height:1.6}}
  .page{{max-width:1100px;margin:0 auto;padding:24px}}
  h2{{font-size:1.05rem;font-weight:700;margin-bottom:12px;color:#0f172a;
      border-bottom:2px solid #e2e8f0;padding-bottom:6px}}
  h3{{font-size:0.9rem;font-weight:700;color:#334155;margin-bottom:8px}}
  .card{{background:#fff;border-radius:12px;border:1px solid #e2e8f0;
         padding:20px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
  .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .badge{{display:inline-block;padding:3px 12px;border-radius:20px;
          font-size:0.72rem;font-weight:700;color:#fff}}
  @media(max-width:700px){{.grid-2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="page">

<!-- Header -->
<div class="card" style="background:linear-gradient(135deg,#1e293b,#334155);color:#fff;border:none">
  <div style="font-size:0.72rem;opacity:.6;margin-bottom:4px;text-transform:uppercase">
    Adya AI · SAI Browser Testing · Per-Agent Report
  </div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
    <div>
      <div style="font-size:1.5rem;font-weight:700;color:#fff">{agent_name}</div>
      <div style="font-size:0.85rem;opacity:.7;margin-top:2px">{desc}</div>
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
        <span class="badge" style="background:{appeared_colour}">{appeared_label}</span>
        <span style="background:rgba(255,255,255,.15);color:#e2e8f0;padding:2px 10px;
               border-radius:10px;font-size:0.78rem">{workflow.get('title','')}</span>
        <span style="background:rgba(255,255,255,.15);color:#e2e8f0;padding:2px 10px;
               border-radius:10px;font-size:0.78rem">{persona.get('name','')} · {persona.get('role','')}</span>
      </div>
    </div>
    <div style="text-align:center;padding:12px 20px">
      <div style="width:80px;height:80px;border-radius:50%;border:5px solid {sc};
                  display:flex;align-items:center;justify-content:center;margin:0 auto;
                  font-size:1.8rem;font-weight:700;color:{sc}">{score_display}</div>
      <div style="font-size:0.72rem;opacity:.6;margin-top:4px">Agent Score /10</div>
    </div>
  </div>
  <div style="margin-top:12px;font-size:0.78rem;opacity:.6">
    Run ID: {run_id} · Start: {start_ts} UTC · Duration: {duration}
  </div>
</div>

<!-- Metadata -->
<div class="card">
  <h2>1 · Metadata</h2>
  <div class="grid-2">
    <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
      <tr><td style="padding:4px 0;color:#64748b;width:130px">Agent</td>
          <td style="font-weight:600">{agent_name}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Status</td>
          <td><span class="badge" style="background:{appeared_colour}">{appeared_label}</span></td></tr>
      <tr><td style="padding:4px 0;color:#64748b">State</td>
          <td><code style="background:#f1f5f9;padding:1px 6px;border-radius:4px">{state}</code></td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Workflow</td>
          <td>{workflow.get('id','')} — {workflow.get('title','')}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Persona</td>
          <td>{persona.get('id','')} · {persona.get('name','')} ({persona.get('role','')})</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Industry</td>
          <td>{persona.get('industry','N/A')}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Run ID</td>
          <td><code style="font-size:0.75rem">{run_id}</code></td></tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
      <tr><td style="padding:4px 0;color:#64748b;width:130px">Start time</td>
          <td>{start_ts} UTC</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Duration</td>
          <td>{duration}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Screenshots</td>
          <td>{len(screenshots)}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Sub-tabs read</td>
          <td>{len(sub_tabs)}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Timeline events</td>
          <td>{len(agent_events)}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Agent score</td>
          <td style="font-weight:700;color:{sc}">{score_display}/10</td></tr>
      <tr><td style="padding:4px 0;color:#64748b">Visual score</td>
          <td style="font-weight:700">{visual_score}/10</td></tr>
    </table>
  </div>
</div>

<!-- Canvas Observations -->
<div class="card">
  <h2>2 · Canvas Observations — Sub-tab Content</h2>
  {subtab_html or "<p style='color:#9ca3af'>No sub-tab content captured.</p>"}
</div>

<!-- Timeline -->
<div class="card">
  <h2>3 · Execution Timeline</h2>
  {timeline_html}
</div>

<!-- SAI Conversation context -->
<div class="card">
  <h2>4 · SAI Conversation (Context Before This Agent)</h2>
  <div style="max-height:400px;overflow-y:auto">
    {psi_html}
  </div>
</div>

<!-- Output Evaluation -->
<div class="card">
  <h2>5 · Output Evaluation</h2>
  <div class="grid-2">
    <div>
      <h3>Output Quality</h3>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                  padding:12px;font-size:0.87rem;color:#334155;line-height:1.6;margin-bottom:12px">
        {output_eval}
      </div>
      <h3>Reasoning Quality</h3>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                  padding:12px;font-size:0.87rem;color:#334155;line-height:1.6">
        {reasoning_eval}
      </div>
    </div>
    <div>
      <h3>Visual Assessment (from VisualJudge)</h3>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                  padding:12px;font-size:0.87rem;color:#334155;margin-bottom:8px">
        <div><strong>Score:</strong> {visual_score}/10</div>
        <div style="margin-top:6px">{visual_desc}</div>
        {"".join(f'<div style="color:#dc2626;margin-top:4px;font-size:0.82rem">• {i}</div>' for i in visual_issues) if visual_issues else ""}
      </div>
    </div>
  </div>

  <div class="grid-2" style="margin-top:14px">
    <div>
      <h3>Improvement Flags</h3>
      {flags_html}
    </div>
    <div>
      <h3>Hallucination / Suspicious Content</h3>
      {hall_html}
    </div>
  </div>
</div>

<!-- Screenshot Gallery -->
<div class="card">
  <h2>6 · Screenshot Gallery ({len(screenshots)} screenshots)</h2>
  <div style="columns:2;column-gap:12px">
    {ss_html}
  </div>
</div>

<!-- Footer -->
<div style="text-align:center;padding:16px 0;color:#94a3b8;font-size:0.72rem">
  Generated by Adya AI · SAI Browser Testing Agent · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
</div>
</div>
</body>
</html>"""


def generate_all_agent_reports(
    run_id: str,
    persona: dict,
    workflow: dict,
    canvas_evidence: dict,
    psi_conversation: list[dict],
    canvas_timeline: list[dict],
    verdict: dict,
    run_metadata: dict,
) -> list[str]:
    """
    Generate per-agent HTML reports for every agent in canvas_evidence.
    Returns list of file paths.
    """
    gen = AgentReportGenerator()
    paths = []
    for agent_name, agent_evidence in canvas_evidence.get("agents", {}).items():
        try:
            path = gen.generate(
                run_id=run_id,
                persona=persona,
                workflow=workflow,
                agent_name=agent_name,
                agent_evidence=agent_evidence,
                psi_conversation=psi_conversation,
                canvas_timeline=canvas_timeline,
                agent_verdict=verdict,
                run_metadata=run_metadata,
            )
            paths.append(path)
        except Exception as e:
            logger.error("[AgentReport] Failed for agent %s: %s", agent_name, e)
    return paths
