"""
Style Extractor — layout bounds, computed CSS properties, z-index layers,
and visual overlap detection for the SAI canvas and UI components.

Used by:
  - CanvasMonitor: annotates each agent snapshot with layout data
  - VisualJudge: feeds structured layout info into the visual prompt
  - RunReportGenerator: §11 layout issues table
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Full layout snapshot ──────────────────────────────────────────────────────
_LAYOUT_SNAPSHOT_JS = """() => {
    const results = [];
    const seen = new Set();

    const selectors = [
        // Canvas structural regions
        'main', 'aside', 'header', 'footer', 'nav',
        '[role="main"]', '[role="navigation"]', '[role="complementary"]',
        // SAI-specific
        '[data-testid]', '[class*="canvas"]', '[class*="panel"]',
        '[class*="tab"]', '[class*="card"]', '[class*="modal"]',
        '[class*="dialog"]', '[class*="overlay"]', '[class*="spinner"]',
        '[class*="toolbar"]', '[class*="sidebar"]',
        // Content containers
        '[class*="output"]', '[class*="content"]', '[class*="agent"]',
    ];

    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (!el.offsetParent && el.tagName !== 'BODY') continue;
            const r  = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;

            const key = Math.round(r.x) + ':' + Math.round(r.y) + ':' + el.tagName;
            if (seen.has(key)) continue;
            seen.add(key);

            const st = getComputedStyle(el);
            results.push({
                tag:       el.tagName.toLowerCase(),
                id:        el.id || '',
                classes:   (el.className || '').toString().slice(0, 80),
                testId:    el.getAttribute('data-testid') || '',
                text:      (el.innerText || '').trim().slice(0, 60),
                bounds: {
                    x:      Math.round(r.x),
                    y:      Math.round(r.y),
                    width:  Math.round(r.width),
                    height: Math.round(r.height),
                    right:  Math.round(r.right),
                    bottom: Math.round(r.bottom),
                },
                style: {
                    display:         st.display,
                    position:        st.position,
                    zIndex:          st.zIndex,
                    overflow:        st.overflow,
                    overflowX:       st.overflowX,
                    overflowY:       st.overflowY,
                    visibility:      st.visibility,
                    opacity:         st.opacity,
                    color:           st.color,
                    backgroundColor: st.backgroundColor,
                    fontSize:        st.fontSize,
                    fontWeight:      st.fontWeight,
                    padding:         st.padding,
                    margin:          st.margin,
                    borderRadius:    st.borderRadius,
                    boxShadow:       st.boxShadow !== 'none' ? st.boxShadow.slice(0, 80) : '',
                },
                isVisible:     el.offsetParent !== null || el.tagName === 'BODY',
                isInteractive: ['button','a','input','select','textarea'].includes(el.tagName.toLowerCase()),
            });
        }
    }
    return results;
}"""


# ── Overlap / z-index collision detection ────────────────────────────────────
_OVERLAP_JS = """() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const candidates = Array.from(document.querySelectorAll(
        'button, [role="button"], [role="dialog"], [class*="modal"], [class*="overlay"], ' +
        '[class*="dropdown"], [class*="tooltip"], [class*="popover"], [class*="card"]'
    )).filter(el => {
        if (!el.offsetParent) return false;
        const r = el.getBoundingClientRect();
        return r.width > 10 && r.height > 10 && r.x < vw && r.y < vh;
    });

    const overlaps = [];
    for (let i = 0; i < candidates.length; i++) {
        for (let j = i + 1; j < candidates.length; j++) {
            const a = candidates[i].getBoundingClientRect();
            const b = candidates[j].getBoundingClientRect();
            const overlapX = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
            const overlapY = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
            if (overlapX > 5 && overlapY > 5) {
                const area = overlapX * overlapY;
                const smaller = Math.min(a.width * a.height, b.width * b.height);
                const pct = smaller > 0 ? Math.round(area / smaller * 100) : 0;
                if (pct > 15) {
                    const zA = parseInt(getComputedStyle(candidates[i]).zIndex) || 0;
                    const zB = parseInt(getComputedStyle(candidates[j]).zIndex) || 0;
                    overlaps.push({
                        element_a: {
                            tag:     candidates[i].tagName.toLowerCase(),
                            text:    (candidates[i].innerText||'').trim().slice(0,40),
                            z_index: zA,
                            bounds:  { x: Math.round(a.x), y: Math.round(a.y),
                                       w: Math.round(a.width), h: Math.round(a.height) },
                        },
                        element_b: {
                            tag:     candidates[j].tagName.toLowerCase(),
                            text:    (candidates[j].innerText||'').trim().slice(0,40),
                            z_index: zB,
                            bounds:  { x: Math.round(b.x), y: Math.round(b.y),
                                       w: Math.round(b.width), h: Math.round(b.height) },
                        },
                        overlap_px:  Math.round(area),
                        overlap_pct: pct,
                        severity:    pct > 50 ? 'critical' : pct > 25 ? 'major' : 'minor',
                    });
                }
            }
        }
        if (overlaps.length >= 10) break;
    }
    return overlaps;
}"""


# ── Spacing / alignment consistency ──────────────────────────────────────────
_SPACING_JS = """() => {
    const issues = [];
    const vw = window.innerWidth;

    // 1. Find all direct siblings that are visually stacked vertically
    //    and check if their vertical gaps are consistent
    const containers = document.querySelectorAll(
        '[class*="list"], [class*="grid"], [class*="flex"], [class*="stack"], ' +
        '[class*="cards"], [class*="items"], [class*="panel"]'
    );
    for (const container of containers) {
        const children = Array.from(container.children).filter(el => {
            if (!el.offsetParent) return false;
            const r = el.getBoundingClientRect();
            return r.width > 20 && r.height > 10;
        });
        if (children.length < 3) continue;

        const gaps = [];
        for (let i = 1; i < children.length; i++) {
            const prev = children[i-1].getBoundingClientRect();
            const curr = children[i].getBoundingClientRect();
            const gap  = Math.round(curr.top - prev.bottom);
            if (gap >= 0 && gap < 200) gaps.push(gap);
        }
        if (gaps.length < 2) continue;

        const mean = gaps.reduce((a, b) => a + b, 0) / gaps.length;
        const outliers = gaps.filter(g => Math.abs(g - mean) > Math.max(mean * 0.5, 8));
        if (outliers.length > 0 && mean > 2) {
            issues.push({
                rule:        'inconsistent-spacing',
                severity:    'minor',
                element:     container.tagName.toLowerCase(),
                selector:    container.id ? '#' + CSS.escape(container.id) :
                             container.className ? '.' + container.className.trim().split(' ')[0] : container.tagName.toLowerCase(),
                description: `Inconsistent vertical spacing in container: gaps range from ${Math.min(...gaps)}px to ${Math.max(...gaps)}px (mean ${Math.round(mean)}px)`,
                element_text: (container.innerText||'').trim().slice(0, 40),
            });
        }
        if (issues.length >= 5) break;
    }

    // 2. Detect elements that appear truncated (text overflow visible)
    for (const el of document.querySelectorAll('p, h1, h2, h3, span, div')) {
        if (!el.offsetParent) continue;
        const st = getComputedStyle(el);
        if (st.overflow === 'hidden' && el.scrollWidth > el.clientWidth + 4) {
            const text = (el.innerText||'').trim().slice(0, 60);
            if (text.length > 10) {
                issues.push({
                    rule:        'text-truncated',
                    severity:    'major',
                    element:     el.tagName.toLowerCase(),
                    selector:    el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase(),
                    description: `Text content appears truncated — scrollWidth (${el.scrollWidth}px) exceeds clientWidth (${el.clientWidth}px)`,
                    element_text: text,
                });
                if (issues.length >= 8) break;
            }
        }
    }

    return issues;
}"""


# ── Scroll depth and hidden content ──────────────────────────────────────────
_HIDDEN_CONTENT_JS = """() => {
    const issues = [];
    const vh = window.innerHeight;
    const vw = window.innerWidth;

    // Elements that are off-screen but supposedly important
    const important = Array.from(document.querySelectorAll(
        'button, [role="button"], [class*="error"], [class*="warning"], ' +
        '[class*="alert"], [class*="notification"]'
    )).filter(el => {
        if (!el.offsetParent) return false;
        const r = el.getBoundingClientRect();
        return r.bottom < 0 || r.top > vh * 2 || r.right < 0 || r.left > vw * 1.5;
    });

    if (important.length > 0) {
        issues.push({
            rule:        'content-offscreen',
            severity:    'minor',
            element:     'multiple',
            selector:    'button, [role="button"]',
            description: `${important.length} interactive elements are positioned outside the visible viewport`,
            element_text: (important[0].innerText||'').trim().slice(0,40),
        });
    }

    // Check for overflow containers that hide content
    for (const el of document.querySelectorAll('[class*="content"], [class*="panel"], [class*="canvas"]')) {
        if (!el.offsetParent) continue;
        const st = getComputedStyle(el);
        if ((st.overflow === 'hidden' || st.overflowY === 'hidden') &&
            el.scrollHeight > el.clientHeight + 40) {
            issues.push({
                rule:        'content-clipped',
                severity:    'minor',
                element:     el.tagName.toLowerCase(),
                selector:    el.id ? '#' + CSS.escape(el.id) :
                             '.' + (el.className||'').toString().trim().split(' ')[0],
                description: `Container clips ${el.scrollHeight - el.clientHeight}px of content with overflow:hidden`,
                element_text: (el.innerText||'').trim().slice(0,40),
            });
            if (issues.length >= 4) break;
        }
    }
    return issues;
}"""


class StyleExtractor:
    """
    Extracts layout, CSS, overlap, and spacing data from the live page DOM.
    Results are attached to agent evidence and fed to LLMJudge/VisualJudge.
    """

    def __init__(self, page):
        self.page = page

    async def extract_layout(self) -> list[dict]:
        """Return layout bounds and computed styles for all significant elements."""
        try:
            return await self.page.evaluate(_LAYOUT_SNAPSHOT_JS) or []
        except Exception as e:
            logger.debug("[StyleExtractor] layout snapshot error: %s", e)
            return []

    async def detect_overlaps(self) -> list[dict]:
        """Return list of overlapping element pairs with severity."""
        try:
            return await self.page.evaluate(_OVERLAP_JS) or []
        except Exception as e:
            logger.debug("[StyleExtractor] overlap detection error: %s", e)
            return []

    async def check_spacing(self) -> list[dict]:
        """Return spacing inconsistencies and truncated text issues."""
        try:
            return await self.page.evaluate(_SPACING_JS) or []
        except Exception as e:
            logger.debug("[StyleExtractor] spacing check error: %s", e)
            return []

    async def check_hidden_content(self) -> list[dict]:
        """Detect important content that is hidden or clipped."""
        try:
            return await self.page.evaluate(_HIDDEN_CONTENT_JS) or []
        except Exception as e:
            logger.debug("[StyleExtractor] hidden content check error: %s", e)
            return []

    async def run_all(self, context_label: str = "") -> dict:
        """
        Run the full style audit suite and return a merged report dict.

        Returns:
            {
                "context": str,
                "checked_at": ISO str,
                "layout_elements": [...],
                "overlaps": [...],
                "spacing_issues": [...],
                "hidden_content_issues": [...],
                "all_layout_issues": [...],   # flat merged list for the judge
                "summary": { "overlaps": N, "spacing": N, "hidden": N, "total": N },
                "score": 0-10,
            }
        """
        layout   = await self.extract_layout()
        overlaps = await self.detect_overlaps()
        spacing  = await self.check_spacing()
        hidden   = await self.check_hidden_content()

        # Annotate overlaps and spacing with category for the judge
        for o in overlaps:
            o["category"] = "overlap"
        for s in spacing:
            s["category"] = "spacing"
        for h in hidden:
            h["category"] = "hidden_content"

        all_issues = overlaps + spacing + hidden
        critical = sum(1 for i in all_issues if i.get("severity") == "critical")
        major    = sum(1 for i in all_issues if i.get("severity") == "major")
        minor    = sum(1 for i in all_issues if i.get("severity") == "minor")

        score = max(0, 10 - (critical * 2.5) - (major * 1.0) - (minor * 0.3))
        score = round(score, 1)

        report = {
            "context":               context_label,
            "checked_at":            datetime.now(timezone.utc).isoformat(),
            "layout_elements":       layout[:50],  # top 50 elements for context
            "overlaps":              overlaps,
            "spacing_issues":        spacing,
            "hidden_content_issues": hidden,
            "all_layout_issues":     all_issues,
            "summary": {
                "overlaps": len(overlaps),
                "spacing":  len(spacing),
                "hidden":   len(hidden),
                "total":    len(all_issues),
            },
            "score": score,
        }
        logger.info(
            "[StyleExtractor] %s — layout_els=%d overlaps=%d spacing=%d hidden=%d score=%.1f",
            context_label or "check",
            len(layout), len(overlaps), len(spacing), len(hidden), score,
        )
        return report

    @staticmethod
    def fix_recommendation(issue: dict) -> str:
        rule = issue.get("rule", "")
        recs = {
            "inconsistent-spacing": "Standardise spacing with CSS variables or a design token system (e.g., --space-4: 16px). Apply consistent gap/margin to flex/grid containers.",
            "text-truncated":       "Allow text to wrap with white-space:normal and overflow:visible, or increase container width. Add title attribute to show full text on hover.",
            "content-offscreen":    "Review absolute/fixed positioning. Ensure important UI elements are within the viewport, particularly on smaller screens.",
            "content-clipped":      "Replace overflow:hidden with overflow:auto or overflow:scroll on content containers, or increase the container's height/max-height.",
        }
        if issue.get("category") == "overlap":
            return "Resolve z-index stacking context conflicts. Use a consistent z-index scale (e.g., modals=1000, tooltips=900, dropdowns=800). Ensure positioned elements don't visually collide."
        return recs.get(rule, f"Review layout issue: {rule}.")
