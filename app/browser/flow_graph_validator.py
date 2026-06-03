"""
Flow Graph Validator.

Extracts structured phase/node data from the SAI Flow View canvas panel
and validates whether the computational graph satisfies the user's original
query intent.

Extraction strategy (in priority order):
  1. Click the "Table" view tab — gives a proper HTML table of phases with
     name, status, and description.  Most reliable.
  2. Parse individual phase cards in the Flow grid thumbnail view — each
     card is a repeating DOM node with name, status badge, and description.
  3. Fall back to inner_text of the whole panel (legacy behaviour).

Validation checks:
  - All expected_agents appear as phases in the graph
  - Total phase count is reasonable (> 0, < 50)
  - Every phase has a non-empty name and a known status
  - No phases in "Error" or "Cancelled" state (flags as issues)
  - Phase count vs. expected coverage
  - Semantic alignment: LLM judge prompt includes structured phase JSON
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = "screenshots"

# ── JS: extract phases from the Table view ────────────────────────────────────
_TABLE_VIEW_JS = """() => {
    // Look for a table with rows representing phases/steps
    const tables = Array.from(document.querySelectorAll('table'));
    for (const tbl of tables) {
        if (!tbl.offsetParent) continue;
        const rows = Array.from(tbl.querySelectorAll('tr')).filter(r => r.offsetParent);
        if (rows.length < 2) continue;  // need at least header + 1 data row

        // Extract headers
        const headers = Array.from(rows[0].querySelectorAll('th, td'))
            .map(c => (c.innerText || '').trim().toLowerCase());

        const phases = [];
        for (let i = 1; i < rows.length; i++) {
            const cells = Array.from(rows[i].querySelectorAll('td'))
                .map(c => (c.innerText || '').trim());
            if (cells.length === 0) continue;

            const phase = {};
            headers.forEach((h, idx) => {
                if (h && cells[idx] !== undefined) phase[h] = cells[idx];
            });
            // Normalise common column name variants
            phase.name        = phase.name || phase.phase || phase.step || phase['phase name'] || cells[0] || '';
            phase.status      = phase.status || phase.state || phase['phase status'] || '';
            phase.description = phase.description || phase.details || phase.summary || cells[2] || '';
            phase.agent       = phase.agent || phase['agent name'] || phase.owner || '';
            phases.push(phase);
        }
        if (phases.length > 0) return { source: 'table', phases };
    }
    return null;
}"""


# ── JS: extract phases from Flow grid thumbnail cards ────────────────────────
_FLOW_CARDS_JS = """() => {
    // Phase cards in the Flow grid — each card is a repeating block
    // Try multiple class-name heuristics used by Vanij / React Flow renderers
    const cardSelectors = [
        '[class*="phase-card"]',
        '[class*="PhaseCard"]',
        '[class*="flow-node"]',
        '[class*="FlowNode"]',
        '[class*="agent-phase"]',
        '[class*="workflow-step"]',
        '[class*="WorkflowStep"]',
        '[class*="step-card"]',
        '[class*="StepCard"]',
        '[class*="node-card"]',
        '[class*="NodeCard"]',
    ];

    let cards = [];
    for (const sel of cardSelectors) {
        const found = Array.from(document.querySelectorAll(sel)).filter(el => el.offsetParent);
        if (found.length > 0) { cards = found; break; }
    }

    // Fallback: find a grid/flex container inside the flow panel and treat its
    // direct children as cards (at least 3 children, each has visible text)
    if (cards.length === 0) {
        const flowPanel = document.querySelector(
            '[data-testid="flow-view"], [class*="flow-view"], [class*="FlowView"], ' +
            '[class*="flow-canvas"], [class*="FlowCanvas"]'
        );
        if (flowPanel) {
            const containers = Array.from(flowPanel.querySelectorAll('div, section'))
                .filter(el => {
                    const style = window.getComputedStyle(el);
                    return (style.display === 'grid' || style.display === 'flex') &&
                           el.children.length >= 3 && el.offsetParent;
                });
            if (containers.length > 0) {
                cards = Array.from(containers[0].children).filter(el => el.offsetParent);
            }
        }
    }

    if (cards.length === 0) return null;

    // Known status badge class patterns
    const STATUS_CLASSES = {
        'completed':   ['completed', 'success', 'done', 'green'],
        'in_progress': ['in-progress', 'inprogress', 'active', 'running', 'blue', 'yellow'],
        'pending':     ['pending', 'waiting', 'queued', 'gray', 'grey'],
        'cancelled':   ['cancelled', 'canceled', 'skipped', 'orange'],
        'error':       ['error', 'failed', 'fail', 'red'],
    };

    function inferStatus(el) {
        // Check text badges first
        const badges = Array.from(el.querySelectorAll('[class*="badge"], [class*="status"], [class*="chip"], span'));
        for (const badge of badges) {
            const t = (badge.innerText || '').trim().toLowerCase();
            for (const [status, keywords] of Object.entries(STATUS_CLASSES)) {
                if (keywords.some(k => t.includes(k))) return status;
            }
        }
        // Check class names on the card itself
        const cls = (el.className || '').toString().toLowerCase();
        for (const [status, keywords] of Object.entries(STATUS_CLASSES)) {
            if (keywords.some(k => cls.includes(k))) return status;
        }
        return 'unknown';
    }

    const phases = cards.map((card, idx) => {
        // Phase name: try heading elements first, then first text block
        const heading = card.querySelector('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="name"], [class*="label"]');
        const name = (heading ? heading.innerText : '').trim() ||
                     (card.innerText || '').trim().split('\\n')[0].trim().slice(0, 80);

        // Description: remaining text after the name
        const lines = (card.innerText || '').trim().split('\\n')
            .map(l => l.trim()).filter(l => l.length > 0);
        const description = lines.slice(1).join(' ').slice(0, 200);

        // Agent name inside card (if present)
        const agentEl = card.querySelector('[class*="agent"], [class*="owner"]');
        const agent = agentEl ? agentEl.innerText.trim() : '';

        // Bounding box for element screenshot
        const r = card.getBoundingClientRect();

        return {
            index:       idx + 1,
            name:        name || `Phase ${idx + 1}`,
            status:      inferStatus(card),
            description: description,
            agent:       agent,
            bbox:        { x: Math.round(r.x), y: Math.round(r.y),
                           width: Math.round(r.width), height: Math.round(r.height) },
        };
    });

    return { source: 'flow_cards', phases };
}"""


# ── JS: extract summary counts from flow legend ───────────────────────────────
_FLOW_LEGEND_JS = """() => {
    // The legend row at the bottom shows counts per status
    const legend = {};
    const legendEl = document.querySelector(
        '[class*="legend"], [class*="Legend"], [class*="flow-legend"]'
    );
    if (!legendEl) return legend;

    const items = Array.from(legendEl.querySelectorAll('span, div, li'))
        .filter(el => el.offsetParent && (el.innerText || '').trim().length > 0);
    for (const item of items) {
        const text = (item.innerText || '').trim().toLowerCase();
        // "Completed · 3" or "3 Completed" patterns
        const numMatch = text.match(/(\\d+)/);
        if (!numMatch) continue;
        const count = parseInt(numMatch[1], 10);
        if (text.includes('complet')) legend.completed = count;
        else if (text.includes('progress')) legend.in_progress = count;
        else if (text.includes('pend')) legend.pending = count;
        else if (text.includes('cancel')) legend.cancelled = count;
        else if (text.includes('error') || text.includes('fail')) legend.error = count;
    }
    return legend;
}"""


# ── JS: click the Table view tab ─────────────────────────────────────────────
_CLICK_TABLE_TAB_JS = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="tab"], a'))
        .filter(el => el.offsetParent);
    for (const btn of btns) {
        const t = (btn.innerText || btn.getAttribute('aria-label') || '').trim().toLowerCase();
        if (t === 'table' || t === 'table view') {
            btn.click();
            return true;
        }
    }
    return false;
}"""

# ── JS: click the Flow view tab ──────────────────────────────────────────────
_CLICK_FLOW_TAB_JS = """() => {
    const btns = Array.from(document.querySelectorAll('button, [role="tab"], a'))
        .filter(el => el.offsetParent);
    for (const btn of btns) {
        const t = (btn.innerText || btn.getAttribute('aria-label') || '').trim().toLowerCase();
        if (t === 'flow' || t === 'flow view' || t === 'graph') {
            btn.click();
            return true;
        }
    }
    return false;
}"""


class FlowGraphValidator:
    """
    Extracts and validates the computational graph shown in the SAI Flow View.

    Usage:
        fgv = FlowGraphValidator(page, screenshot_agent, run_id)
        evidence = await fgv.capture_and_validate(
            expected_agents=["AIA", "AGP", "ETL", "App Studio"],
            user_query="Build a sales dashboard…",
        )
    """

    def __init__(self, page, screenshot_agent, run_id: str):
        self.page    = page
        self.ss      = screenshot_agent
        self.run_id  = run_id
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    async def capture_and_validate(
        self,
        expected_agents: list[str],
        user_query: str = "",
    ) -> dict:
        """
        Capture full Flow View evidence and run validation checks.
        Zooms into the canvas panel before every screenshot and text read so
        the Vision LLM receives readable, high-resolution content.
        Returns a structured dict consumed by LLMJudge and RunReportGenerator.
        """
        from app.browser.canvas_zoomer import CanvasZoomer

        evidence: dict = {
            "appeared":          False,
            "phases":            [],
            "phase_count":       0,
            "legend_counts":     {},
            "extraction_source": "none",
            "screenshots":       [],
            "screenshot_path":   None,
            "validation":        {},
            "planned_steps":     "",
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

        # 1. Zoomed initial screenshot of the flow panel before any clicks
        async with CanvasZoomer(self.page) as cz:
            ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
            path = os.path.join(SCREENSHOTS_DIR, f"{self.run_id}_flow_view_initial_{ts}.png")
            ss   = await cz.screenshot_panel(path)
        if ss:
            evidence["screenshots"].append({"label": "flow_view_initial", "path": ss})
            evidence["screenshot_path"] = ss

        # 2. Try to extract phases from Table view (most structured)
        #    All work inside the zoomer so inner_text returns un-clipped content
        phases, source = await self._extract_phases_table_view()

        # 3. If Table view failed, try parsing Flow card grid
        if not phases:
            phases, source = await self._extract_phases_flow_cards()

        # 4. Fallback: plain inner_text with CSS un-clipping
        if not phases:
            phases, source = await self._extract_phases_fallback()

        if phases:
            evidence["appeared"]          = True
            evidence["phases"]            = phases
            evidence["phase_count"]       = len(phases)
            evidence["extraction_source"] = source
            evidence["planned_steps"]     = self._phases_to_text(phases)

        # 5. Legend counts (no zoom needed — just JS evaluation)
        try:
            legend = await self.page.evaluate(_FLOW_LEGEND_JS) or {}
            evidence["legend_counts"] = legend
        except Exception:
            pass

        # 6. Zoomed flow panel element screenshot
        flow_ss = await self._screenshot_flow_panel()
        if flow_ss:
            evidence["screenshots"].append({"label": "flow_panel_element", "path": flow_ss})
            evidence["screenshot_path"] = flow_ss

        # 7. Zoomed table view screenshot
        table_ss = await self._screenshot_table_view()
        if table_ss:
            evidence["screenshots"].append({"label": "flow_table_view", "path": table_ss})

        # 8. Per-phase card screenshots (zoomed crops, up to 9)
        if phases and evidence["extraction_source"] == "flow_cards":
            card_shots = await self._screenshot_phase_cards(phases)
            evidence["screenshots"].extend(card_shots)

        # 9. Restore Flow tab (so later screenshot captures the graph, not Table)
        await self._restore_flow_tab()

        # 10. Validate
        evidence["validation"] = self._validate(
            phases=phases,
            expected_agents=expected_agents,
            user_query=user_query,
            legend_counts=evidence["legend_counts"],
        )

        logger.info(
            "[FlowGraphValidator] %d phases extracted (source=%s), %d issues",
            len(phases), source,
            len(evidence["validation"].get("issues", [])),
        )
        return evidence

    # ── Phase extraction ───────────────────────────────────────────────────────

    async def _extract_phases_table_view(self) -> tuple[list[dict], str]:
        """Click Table tab, wait for table to render, extract rows (with CSS un-clip)."""
        from app.browser.canvas_zoomer import CanvasZoomer
        try:
            clicked = await self.page.evaluate(_CLICK_TABLE_TAB_JS)
            if not clicked:
                return [], "none"
            await asyncio.sleep(1.2)
            # Inject un-clip CSS so table cell text is not truncated
            async with CanvasZoomer(self.page):
                result = await self.page.evaluate(_TABLE_VIEW_JS)
            if result and result.get("phases"):
                return result["phases"], "table_view"
        except Exception as e:
            logger.debug("[FlowGraphValidator] Table view extraction failed: %s", e)
        return [], "none"

    async def _extract_phases_flow_cards(self) -> tuple[list[dict], str]:
        """Parse phase thumbnail cards from the Flow grid (with CSS un-clip)."""
        from app.browser.canvas_zoomer import CanvasZoomer
        try:
            async with CanvasZoomer(self.page):
                result = await self.page.evaluate(_FLOW_CARDS_JS)
            if result and result.get("phases"):
                return result["phases"], "flow_cards"
        except Exception as e:
            logger.debug("[FlowGraphValidator] Flow cards extraction failed: %s", e)
        return [], "none"

    async def _extract_phases_fallback(self) -> tuple[list[dict], str]:
        """Last resort: read full inner_text with CSS un-clipping."""
        from app.browser.sai_navigator import FLOW_VIEW_SELECTORS, _try_selectors
        from app.browser.canvas_zoomer import CanvasZoomer
        try:
            fv_sel = await _try_selectors(self.page, FLOW_VIEW_SELECTORS, timeout=3000)
            if fv_sel:
                text = await CanvasZoomer.read_full_text(self.page, fv_sel)
                if not text:
                    text = (await self.page.inner_text(fv_sel) or "").strip()
                text = text[:800]
                if text:
                    return [{"name": "Flow View (raw)", "status": "unknown",
                             "description": text, "agent": "", "index": 1}], "raw_text"
        except Exception:
            pass
        return [], "none"

    # ── Screenshots ────────────────────────────────────────────────────────────

    async def _screenshot_flow_panel(self) -> Optional[str]:
        """Zoomed element screenshot of the flow panel."""
        from app.browser.canvas_zoomer import CanvasZoomer
        panel_selectors = [
            '[data-testid="flow-view"]',
            '[class*="flow-view"]',
            '[class*="FlowView"]',
            '[class*="flow-canvas"]',
            'aside',
            '[class*="right-panel"]',
        ]
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
        path = os.path.join(SCREENSHOTS_DIR, f"{self.run_id}_flow_panel_{ts}.png")
        for sel in panel_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    result = await CanvasZoomer.screenshot_zoomed(self.page, sel, path)
                    if result:
                        return result
            except Exception:
                continue
        # Fallback: zoomed full canvas panel
        try:
            async with CanvasZoomer(self.page) as cz:
                result = await cz.screenshot_panel(path)
            return result
        except Exception:
            return None

    async def _screenshot_table_view(self) -> Optional[str]:
        """Zoomed screenshot of the Table view after clicking the Table tab."""
        from app.browser.canvas_zoomer import CanvasZoomer
        try:
            clicked = await self.page.evaluate(_CLICK_TABLE_TAB_JS)
            if not clicked:
                return None
            await asyncio.sleep(1.0)
            ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
            path = os.path.join(SCREENSHOTS_DIR, f"{self.run_id}_flow_table_{ts}.png")
            async with CanvasZoomer(self.page) as cz:
                result = await cz.screenshot_panel(path)
            return result
        except Exception as e:
            logger.debug("[FlowGraphValidator] Table view screenshot failed: %s", e)
            return None

    async def _screenshot_phase_cards(self, phases: list[dict]) -> list[dict]:
        """Crop each phase card at 2× DPR using its bounding box."""
        from app.browser.canvas_zoomer import CanvasZoomer
        results = []
        # Take all card crops within a single zoom session to avoid repeated DPR toggles
        async with CanvasZoomer(self.page) as cz:
            for phase in phases[:9]:
                bbox = phase.get("bbox")
                if not bbox or bbox.get("width", 0) < 20:
                    continue
                try:
                    ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:19]
                    safe  = (phase.get("name", "") or f"phase_{phase.get('index',0)}").replace(" ", "_")[:30]
                    path  = os.path.join(SCREENSHOTS_DIR,
                                         f"{self.run_id}_flow_card_{phase.get('index',0):02d}_{safe}_{ts}.png")
                    result = await cz.screenshot_clip(bbox, path)
                    if result:
                        results.append({
                            "label":      f"phase_card_{phase.get('index', 0):02d}",
                            "path":       path,
                            "phase_name": phase.get("name", ""),
                            "status":     phase.get("status", ""),
                        })
                        logger.debug("[FlowGraphValidator] Phase card (zoomed): %s", path)
                except Exception as e:
                    logger.debug("[FlowGraphValidator] Phase card screenshot failed: %s", e)
        return results

    async def _restore_flow_tab(self) -> None:
        """Switch back to the Flow tab after reading the Table."""
        try:
            await self.page.evaluate(_CLICK_FLOW_TAB_JS)
            await asyncio.sleep(0.8)
        except Exception:
            pass

    # ── Validation ─────────────────────────────────────────────────────────────

    def _validate(
        self,
        phases: list[dict],
        expected_agents: list[str],
        user_query: str,
        legend_counts: dict,
    ) -> dict:
        """
        Run structural validation on the extracted phases.
        Returns a dict with:
          - issues: list of {severity, rule, description, fix_recommendation}
          - warnings: list of str
          - metrics: {total, completed, in_progress, pending, cancelled, error, coverage_pct}
          - verdict: "pass" | "partial" | "fail"
        """
        issues  = []
        metrics = {
            "total":       len(phases),
            "completed":   sum(1 for p in phases if p.get("status") == "completed"),
            "in_progress": sum(1 for p in phases if p.get("status") == "in_progress"),
            "pending":     sum(1 for p in phases if p.get("status") == "pending"),
            "cancelled":   sum(1 for p in phases if p.get("status") == "cancelled"),
            "error":       sum(1 for p in phases if p.get("status") == "error"),
            "unknown":     sum(1 for p in phases if p.get("status") == "unknown"),
        }

        # Merge legend counts (more authoritative if available)
        if legend_counts:
            for k in ("completed", "in_progress", "pending", "cancelled", "error"):
                if k in legend_counts:
                    metrics[k] = legend_counts[k]

        # ── Rule 1: Flow View must appear ─────────────────────────────────────
        if not phases:
            issues.append({
                "severity": "critical",
                "rule":     "flow_view_empty",
                "description": "No phases found in the Flow View — graph was not rendered or could not be extracted.",
                "fix_recommendation": "Check if the workflow completed. Verify FLOW_VIEW_SELECTORS match the DOM. Try refreshing the canvas.",
            })

        # ── Rule 2: Phase count sanity ────────────────────────────────────────
        if 0 < len(phases) < 3:
            issues.append({
                "severity": "major",
                "rule":     "too_few_phases",
                "description": f"Only {len(phases)} phase(s) found — expected at least 3 for any meaningful workflow.",
                "fix_recommendation": "Verify the complete flow was generated. Check if the canvas finished loading before capture.",
            })
        if len(phases) > 30:
            issues.append({
                "severity": "minor",
                "rule":     "too_many_phases",
                "description": f"{len(phases)} phases found — unusually large graph, may indicate duplicate card extraction.",
                "fix_recommendation": "Review JS selector specificity to avoid double-counting nested elements.",
            })

        # ── Rule 3: Error / Cancelled phases ─────────────────────────────────
        error_phases    = [p["name"] for p in phases if p.get("status") == "error"]
        cancelled_phases = [p["name"] for p in phases if p.get("status") == "cancelled"]
        if error_phases:
            issues.append({
                "severity": "critical",
                "rule":     "phase_error",
                "description": f"Phase(s) in ERROR state: {', '.join(error_phases[:5])}",
                "fix_recommendation": "Investigate the failing agent. Check SAI logs for the specific phase. Consider retrying the workflow.",
            })
        if cancelled_phases:
            issues.append({
                "severity": "major",
                "rule":     "phase_cancelled",
                "description": f"Phase(s) CANCELLED: {', '.join(cancelled_phases[:5])}",
                "fix_recommendation": "Determine why these phases were skipped. Check if the user's query was fully satisfied despite cancellations.",
            })

        # ── Rule 4: Expected agents coverage ─────────────────────────────────
        phase_names_lower = [p.get("name", "").lower() for p in phases]
        all_text_lower    = " ".join(p.get("description", "") + " " + p.get("agent", "")
                                      for p in phases).lower()
        missing_agents = []
        for agent in expected_agents:
            agent_l = agent.lower()
            if not any(agent_l in n or agent_l in all_text_lower for n in phase_names_lower):
                missing_agents.append(agent)
        if missing_agents:
            issues.append({
                "severity": "major",
                "rule":     "missing_expected_agent",
                "description": f"Expected agent(s) not found as phases: {', '.join(missing_agents)}",
                "fix_recommendation": "Verify the workflow ran all expected agents. Check if agent names in the graph differ from expected_agents config.",
            })

        coverage_pct = round(
            (len(expected_agents) - len(missing_agents)) / max(len(expected_agents), 1) * 100
        )
        metrics["coverage_pct"] = coverage_pct

        # ── Rule 5: In-progress / pending phases at end-of-run ────────────────
        still_running = [p["name"] for p in phases if p.get("status") in ("in_progress", "pending")]
        if still_running:
            issues.append({
                "severity": "major",
                "rule":     "incomplete_phases",
                "description": f"Phase(s) still In Progress or Pending at end of run: {', '.join(still_running[:5])}",
                "fix_recommendation": "Increase the canvas wait timeout. Check if the SAI platform is still generating when the test agent captures evidence.",
            })

        # ── Rule 6: Phases with empty names ──────────────────────────────────
        unnamed = sum(1 for p in phases if not (p.get("name") or "").strip())
        if unnamed:
            issues.append({
                "severity": "minor",
                "rule":     "unnamed_phases",
                "description": f"{unnamed} phase(s) have empty names — card extraction may have partial DOM coverage.",
                "fix_recommendation": "Refine phase card selector to target the correct heading element within each card.",
            })

        # ── Determine verdict ─────────────────────────────────────────────────
        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        major_count    = sum(1 for i in issues if i["severity"] == "major")
        if critical_count > 0:
            verdict = "fail"
        elif major_count > 0:
            verdict = "partial"
        else:
            verdict = "pass"

        return {
            "verdict":  verdict,
            "issues":   issues,
            "metrics":  metrics,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _phases_to_text(phases: list[dict]) -> str:
        """Convert phase list to a compact human-readable summary for the judge prompt."""
        lines = []
        for p in phases:
            status = p.get("status", "unknown").upper()
            name   = p.get("name", f"Phase {p.get('index', '?')}")
            desc   = p.get("description", "")
            agent  = p.get("agent", "")
            line   = f"  [{status}] {name}"
            if agent:
                line += f" (agent: {agent})"
            if desc:
                line += f" — {desc[:120]}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def build_judge_context(flow_evidence: dict) -> str:
        """
        Build the Flow Graph section text for the LLM judge prompt.
        Provides structured phase data and validation summary.
        """
        if not flow_evidence or not flow_evidence.get("appeared"):
            return "Flow View: NOT FOUND — graph was not rendered or agent did not produce a workflow.\n"

        phases    = flow_evidence.get("phases", [])
        val       = flow_evidence.get("validation", {})
        metrics   = val.get("metrics", {})
        issues    = val.get("issues", [])
        verdict   = val.get("verdict", "unknown")

        lines = [
            f"Flow View: PRESENT  (source={flow_evidence.get('extraction_source','?')})",
            f"Phase count: {metrics.get('total', len(phases))}  |  "
            f"Completed: {metrics.get('completed', 0)}  |  "
            f"In Progress: {metrics.get('in_progress', 0)}  |  "
            f"Pending: {metrics.get('pending', 0)}  |  "
            f"Error: {metrics.get('error', 0)}  |  "
            f"Cancelled: {metrics.get('cancelled', 0)}",
            f"Agent coverage: {metrics.get('coverage_pct', '?')}%",
            f"Structural verdict: {verdict.upper()}",
            "",
            "Phases:",
        ]
        lines.append(flow_evidence.get("planned_steps", "(no phase detail)"))

        if issues:
            lines.append("")
            lines.append(f"Flow Graph Issues ({len(issues)}):")
            for iss in issues:
                lines.append(
                    f"  [{iss['severity'].upper()}] {iss['rule']}: {iss['description']}"
                )

        return "\n".join(lines)
