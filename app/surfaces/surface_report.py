"""
Surface Report Generator — produces a self-contained HTML report for one surface run.

Report sections:
  - Header: surface name, area, run_id, persona, timestamp, overall status
  - Summary bar: pass/fail/partial/escalated counts + pass-rate gauge
  - Step table: step_id | description | status | duration | escalation | screenshot thumbnail
  - SAI I/O log: per step collapsible — exact SAI INPUT, SAI OUTPUT (full text),
                 sub-agents invoked, tool calls, DOM snapshot link
  - Viewport gallery: all scrolled screenshots in a filmstrip per step
  - Metadata footer
"""

import base64
import html
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

REPORTS_DIR = "reports/surface_runs"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64_image(path: str) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{data}"
    except Exception:
        return None


def _status_badge(status: str) -> str:
    colours = {
        "pass":    ("#16a34a", "#dcfce7"),
        "fail":    ("#dc2626", "#fee2e2"),
        "partial": ("#d97706", "#fef3c7"),
        "skip":    ("#6b7280", "#f3f4f6"),
        "in_progress": ("#2563eb", "#dbeafe"),
    }
    fg, bg = colours.get(status, ("#374151", "#f9fafb"))
    label = html.escape(status.upper())
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;'
        f'font-size:11px;font-weight:700;background:{bg};color:{fg};">{label}</span>'
    )


def _thumb(path: str, size: int = 120) -> str:
    uri = _b64_image(path)
    if not uri:
        return '<span style="color:#9ca3af;font-size:11px;">no screenshot</span>'
    return (
        f'<img src="{uri}" style="max-width:{size}px;max-height:{size}px;'
        f'border:1px solid #e5e7eb;border-radius:4px;cursor:zoom-in;" '
        f'onclick="window.open(this.src)" title="{html.escape(path)}">'
    )


def _esc(text) -> str:
    return html.escape(str(text or ""))


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root{{--pass:#16a34a;--fail:#dc2626;--partial:#d97706;--skip:#6b7280;--bg:#f8fafc;--card:#fff;--border:#e2e8f0;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:#1e293b;font-size:14px;}}
  .container{{max-width:1400px;margin:0 auto;padding:24px;}}
  h1{{font-size:22px;font-weight:700;margin-bottom:4px;}}
  h2{{font-size:16px;font-weight:600;margin:24px 0 12px;border-bottom:2px solid var(--border);padding-bottom:6px;}}
  .meta{{color:#64748b;font-size:12px;margin-bottom:20px;}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;}}
  .summary-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px;}}
  .stat-box{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center;}}
  .stat-num{{font-size:28px;font-weight:700;}}
  .stat-label{{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;}}
  th{{background:#f1f5f9;padding:8px 10px;text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid var(--border);}}
  td{{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top;}}
  tr:hover{{background:#fafafa;}}
  details{{margin-bottom:8px;}}
  summary{{cursor:pointer;font-weight:600;padding:6px 10px;background:#f8fafc;border-radius:4px;font-size:13px;}}
  summary:hover{{background:#f1f5f9;}}
  .io-block{{background:#0f172a;color:#e2e8f0;border-radius:6px;padding:12px;font-family:monospace;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all;max-height:320px;overflow:auto;margin:8px 0;}}
  .filmstrip{{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0;}}
  .filmstrip img{{max-height:100px;max-width:160px;border:1px solid var(--border);border-radius:4px;cursor:zoom-in;}}
  .tag{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;}}
  .tag-agentic{{background:#ede9fe;color:#6d28d9;}}
  .tag-playwright{{background:#e0f2fe;color:#0369a1;}}
  .progress-bar{{height:8px;border-radius:4px;background:#e2e8f0;overflow:hidden;margin:4px 0;}}
  .progress-fill{{height:100%;border-radius:4px;background:var(--pass);}}
</style>
</head>
<body><div class="container">
"""

_HTML_FOOT = """</div></body></html>"""


class SurfaceReportGenerator:

    def generate(
        self,
        surface: dict,
        persona: dict,
        run_id: str,
        step_records: list[dict],
        summary: dict,
    ) -> str:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        surface_id = surface["id"]
        filename   = f"{run_id}_{surface_id}.html"
        out_path   = os.path.join(REPORTS_DIR, filename)

        body  = _HTML_HEAD.format(title=f"{surface['name']} — SAI Surface Test")
        body += self._header(surface, persona, run_id, summary)
        body += self._summary_grid(summary)
        body += self._step_table(step_records)
        body += self._io_log_section(step_records)
        body += self._viewport_gallery(step_records)
        body += _HTML_FOOT

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body)
        logger.info("[SurfaceReport] Report written: %s", out_path)
        return out_path

    # ── Header ────────────────────────────────────────────────────────────────

    def _header(self, surface: dict, persona: dict, run_id: str, summary: dict) -> str:
        status  = summary.get("status", "")
        overall = (
            "pass"    if summary.get("failed", 0) == 0
            else "partial" if summary.get("passed", 0) > 0
            else "fail"
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"""
<h1>{_esc(surface['name'])} &nbsp;{_status_badge(overall)}</h1>
<p class="meta">
  Surface ID: <strong>{_esc(surface['id'])}</strong> &nbsp;|&nbsp;
  Area: <strong>{_esc(surface['area'])}</strong> &nbsp;|&nbsp;
  Run: <code>{_esc(run_id)}</code> &nbsp;|&nbsp;
  Persona: <strong>{_esc(persona.get('name', persona.get('id','')))}</strong> &nbsp;|&nbsp;
  {ts}
</p>
"""

    # ── Summary grid ──────────────────────────────────────────────────────────

    def _summary_grid(self, summary: dict) -> str:
        total      = summary.get("total", 0)
        passed     = summary.get("passed", 0)
        failed     = summary.get("failed", 0)
        partial    = summary.get("partial", 0)
        skipped    = summary.get("skipped", 0)
        escalated  = summary.get("escalated", 0)
        pass_rate  = summary.get("pass_rate", 0.0)
        fill_w     = int(min(pass_rate, 100))

        return f"""
<div class="summary-grid">
  <div class="stat-box">
    <div class="stat-num">{total}</div>
    <div class="stat-label">Total Steps</div>
  </div>
  <div class="stat-box">
    <div class="stat-num" style="color:var(--pass);">{passed}</div>
    <div class="stat-label">Passed</div>
  </div>
  <div class="stat-box">
    <div class="stat-num" style="color:var(--fail);">{failed}</div>
    <div class="stat-label">Failed</div>
  </div>
  <div class="stat-box">
    <div class="stat-num" style="color:var(--partial);">{partial}</div>
    <div class="stat-label">Partial</div>
  </div>
  <div class="stat-box">
    <div class="stat-num" style="color:var(--skip);">{skipped}</div>
    <div class="stat-label">Skipped</div>
  </div>
  <div class="stat-box">
    <div class="stat-num" style="color:#7c3aed;">{escalated}</div>
    <div class="stat-label">Escalated</div>
  </div>
</div>
<div class="card" style="padding:12px;">
  <strong>Pass Rate: {pass_rate}%</strong>
  <div class="progress-bar">
    <div class="progress-fill" style="width:{fill_w}%;"></div>
  </div>
</div>
"""

    # ── Step table ────────────────────────────────────────────────────────────

    def _step_table(self, step_records: list[dict]) -> str:
        rows = ""
        for r in step_records:
            step_id   = _esc(r.get("step_id", ""))
            action    = _esc(r.get("action", ""))
            status    = r.get("status", "skip")
            dur       = r.get("duration_ms", 0)
            notes     = _esc(r.get("notes", ""))
            escalation = _esc(r.get("escalation") or "")
            ss_path    = r.get("screenshot_uri", "")

            mode_tag = (
                '<span class="tag tag-playwright">Playwright</span>'
                if escalation else
                '<span class="tag tag-agentic">Agentic</span>'
            )

            rows += f"""<tr>
  <td><code>{step_id}</code></td>
  <td><code>{action}</code> {mode_tag}</td>
  <td>{_status_badge(status)}</td>
  <td style="text-align:right;">{dur}ms</td>
  <td>{escalation or "—"}</td>
  <td>{notes or "—"}</td>
  <td>{_thumb(ss_path, 80)}</td>
</tr>"""

        return f"""
<h2>Step Results</h2>
<div class="card" style="padding:0;overflow:auto;">
<table>
  <thead><tr>
    <th>Step ID</th><th>Action / Mode</th><th>Status</th>
    <th>Duration</th><th>Escalation</th><th>Notes</th><th>Screenshot</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
"""

    # ── IO log section ────────────────────────────────────────────────────────

    def _io_log_section(self, step_records: list[dict]) -> str:
        items = ""
        for r in step_records:
            step_id = _esc(r.get("step_id", "?"))
            status  = r.get("status", "skip")

            sai_in  = r.get("sai_input", {})
            sai_out = r.get("sai_output", {})
            dom_uri = _esc(r.get("dom_snapshot_uri") or "")

            input_text = json.dumps(sai_in, indent=2, default=str)
            output_text = json.dumps(sai_out, indent=2, default=str)

            sub_agents = sai_out.get("sub_agents", [])
            sub_tag = ""
            if sub_agents:
                sub_tag = " ".join(
                    f'<code style="background:#f0fdf4;color:#166534;padding:1px 4px;border-radius:3px;">{_esc(a)}</code>'
                    for a in sub_agents
                )

            dom_link = (
                f'<a href="{dom_uri}" target="_blank" style="font-size:11px;">'
                f'DOM snapshot ↗</a>'
            ) if dom_uri else ""

            items += f"""
<details>
  <summary>{step_id} &nbsp;{_status_badge(status)}
    {f'&nbsp;Agents: {sub_tag}' if sub_tag else ''}
  </summary>
  <div style="padding:10px 0;">
    <strong style="font-size:12px;color:#64748b;">SAI INPUT</strong>
    <div class="io-block">{_esc(input_text)}</div>
    <strong style="font-size:12px;color:#64748b;">SAI OUTPUT</strong>
    <div class="io-block">{_esc(output_text)}</div>
    {f'<p style="margin-top:6px;">{dom_link}</p>' if dom_link else ''}
  </div>
</details>"""

        return f"""
<h2>SAI Input / Output Log (per step)</h2>
<div class="card">{items}</div>
"""

    # ── Viewport gallery ──────────────────────────────────────────────────────

    def _viewport_gallery(self, step_records: list[dict]) -> str:
        sections = ""
        for r in step_records:
            vp_shots = r.get("viewport_screenshots", [])
            if not vp_shots:
                continue
            step_id = _esc(r.get("step_id", "?"))
            thumbs = ""
            for path in vp_shots:
                uri = _b64_image(path)
                if uri:
                    thumbs += (
                        f'<img src="{uri}" '
                        f'title="{_esc(os.path.basename(path))}" '
                        f'onclick="window.open(this.src)">'
                    )
            if thumbs:
                sections += f"""
<details>
  <summary>Viewport filmstrip — {step_id} ({len(vp_shots)} frames)</summary>
  <div class="filmstrip" style="padding:10px 0;">{thumbs}</div>
</details>"""

        if not sections:
            return ""

        return f"""
<h2>Viewport Scroll Gallery</h2>
<div class="card">{sections}</div>
"""
