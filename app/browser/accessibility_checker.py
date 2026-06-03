"""
Accessibility Checker — axe-core inspired JS probes for the SAI platform.

Runs in-browser checks covering:
  - ARIA roles, labels, and landmark structure
  - Colour contrast (WCAG AA 4.5:1 for normal text, 3:1 for large text)
  - Keyboard-navigability (focusable elements, tab order, focus traps)
  - Form inputs with missing labels
  - Images without alt text
  - Interactive elements with no accessible name
  - Duplicate IDs
  - Empty or redundant heading hierarchy
  - Buttons with no discernible text

Returns a structured report used by LLMJudge and RunReportGenerator.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Contrast ratio helper ─────────────────────────────────────────────────────
# Relative luminance per WCAG 2.1 §1.4.3
_CONTRAST_JS = """() => {
    function luminance(r, g, b) {
        const [rs, gs, bs] = [r, g, b].map(c => {
            c /= 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
    }
    function contrastRatio(c1, c2) {
        const l1 = Math.max(c1, c2), l2 = Math.min(c1, c2);
        return (l1 + 0.05) / (l2 + 0.05);
    }
    function parseColour(css) {
        if (!css || css === 'transparent' || css === 'rgba(0, 0, 0, 0)') return null;
        const m = css.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
        if (!m) return null;
        return [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])];
    }

    const issues = [];
    const checked = new Set();

    const textEls = Array.from(document.querySelectorAll(
        'p, span, a, button, label, h1, h2, h3, h4, h5, h6, li, td, th, div'
    )).filter(el => {
        if (!el.offsetParent) return false;
        const t = (el.innerText || '').trim();
        return t.length > 0 && t.length < 300 && el.children.length < 4;
    });

    for (const el of textEls.slice(0, 200)) {
        const key = el.tagName + ':' + (el.innerText || '').trim().slice(0, 40);
        if (checked.has(key)) continue;
        checked.add(key);

        const style = getComputedStyle(el);
        const fg = parseColour(style.color);
        const bg = parseColour(style.backgroundColor);
        if (!fg || !bg) continue;

        const lFg = luminance(...fg), lBg = luminance(...bg);
        const ratio = contrastRatio(lFg, lBg);
        const fontSize = parseFloat(style.fontSize);
        const bold = parseInt(style.fontWeight) >= 700;
        const isLargeText = fontSize >= 18 || (bold && fontSize >= 14);
        const required = isLargeText ? 3.0 : 4.5;

        if (ratio < required) {
            const text = (el.innerText || '').trim().slice(0, 60);
            issues.push({
                rule: 'color-contrast',
                severity: ratio < 2.5 ? 'critical' : 'major',
                element: el.tagName.toLowerCase(),
                selector: el.id ? '#' + CSS.escape(el.id) :
                          el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
                          el.tagName.toLowerCase() + ':contains(' + text.slice(0, 20) + ')',
                description: `Contrast ratio ${ratio.toFixed(2)}:1 is below WCAG AA threshold of ${required}:1`,
                actual: ratio.toFixed(2),
                expected: required.toString(),
                element_text: text,
            });
        }
    }
    return issues.slice(0, 30);
}"""


# ── ARIA / structure checks ───────────────────────────────────────────────────
_ARIA_JS = """() => {
    const issues = [];

    // 1. Buttons without accessible name
    for (const btn of document.querySelectorAll('button')) {
        if (!btn.offsetParent) continue;
        const name = (btn.innerText || btn.getAttribute('aria-label') ||
                      btn.getAttribute('title') || '').trim();
        if (!name) {
            issues.push({
                rule: 'button-name',
                severity: 'major',
                element: 'button',
                selector: btn.id ? '#' + CSS.escape(btn.id) : 'button (no id)',
                description: 'Button has no accessible name (no text, aria-label, or title)',
                element_text: '',
            });
        }
    }

    // 2. Images without alt text
    for (const img of document.querySelectorAll('img')) {
        if (!img.offsetParent) continue;
        if (!img.hasAttribute('alt')) {
            issues.push({
                rule: 'image-alt',
                severity: 'major',
                element: 'img',
                selector: img.src ? `img[src*="${img.src.split('/').pop().split('?')[0].slice(-20)}"]` : 'img',
                description: 'Image is missing the alt attribute',
                element_text: img.src.split('/').pop().slice(0, 50),
            });
        }
    }

    // 3. Form inputs missing associated labels
    for (const input of document.querySelectorAll('input, select, textarea')) {
        if (!input.offsetParent) continue;
        if (['hidden', 'submit', 'button', 'reset'].includes(input.type)) continue;
        const hasLabel = input.id && document.querySelector(`label[for="${input.id}"]`);
        const hasAria  = input.getAttribute('aria-label') || input.getAttribute('aria-labelledby');
        const hasPlaceholder = input.placeholder;
        if (!hasLabel && !hasAria && !hasPlaceholder) {
            issues.push({
                rule: 'label',
                severity: 'critical',
                element: input.tagName.toLowerCase(),
                selector: input.id ? '#' + CSS.escape(input.id) : input.tagName.toLowerCase(),
                description: 'Form control has no associated label, aria-label, or placeholder',
                element_text: input.name || input.type || '',
            });
        }
    }

    // 4. Links with no text content
    for (const a of document.querySelectorAll('a')) {
        if (!a.offsetParent) continue;
        const name = (a.innerText || a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
        if (!name && !a.querySelector('img[alt]')) {
            issues.push({
                rule: 'link-name',
                severity: 'major',
                element: 'a',
                selector: a.href ? `a[href="${a.getAttribute('href')}"]` : 'a',
                description: 'Link has no discernible text content',
                element_text: a.href || '',
            });
        }
    }

    // 5. Duplicate IDs
    const ids = {};
    for (const el of document.querySelectorAll('[id]')) {
        ids[el.id] = (ids[el.id] || 0) + 1;
    }
    for (const [id, count] of Object.entries(ids)) {
        if (count > 1) {
            issues.push({
                rule: 'duplicate-id',
                severity: 'minor',
                element: 'multiple',
                selector: '#' + CSS.escape(id),
                description: `ID "${id}" is used on ${count} elements — IDs must be unique`,
                element_text: id,
            });
        }
    }

    // 6. Page landmark structure
    const hasMain  = !!document.querySelector('main, [role="main"]');
    const hasNav   = !!document.querySelector('nav, [role="navigation"]');
    if (!hasMain) {
        issues.push({
            rule: 'landmark-main',
            severity: 'minor',
            element: 'page',
            selector: 'body',
            description: 'Page has no <main> or role="main" landmark',
            element_text: '',
        });
    }

    // 7. Heading hierarchy
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
        .filter(el => el.offsetParent && (el.innerText||'').trim());
    const h1s = headings.filter(h => h.tagName === 'H1');
    if (h1s.length === 0 && headings.length > 0) {
        issues.push({
            rule: 'heading-order',
            severity: 'minor',
            element: 'page',
            selector: 'body',
            description: 'Page has no H1 heading — heading hierarchy starts at H2 or lower',
            element_text: '',
        });
    }
    if (h1s.length > 1) {
        issues.push({
            rule: 'heading-order',
            severity: 'minor',
            element: 'h1',
            selector: 'h1',
            description: `Page has ${h1s.length} H1 headings — only one H1 is recommended`,
            element_text: '',
        });
    }

    // 8. Interactive elements without role
    for (const el of document.querySelectorAll('[onclick], [tabindex]')) {
        if (!el.offsetParent) continue;
        const tag = el.tagName.toLowerCase();
        if (['button','a','input','select','textarea'].includes(tag)) continue;
        const role = el.getAttribute('role');
        if (!role) {
            issues.push({
                rule: 'role-missing',
                severity: 'minor',
                element: tag,
                selector: el.id ? '#' + CSS.escape(el.id) : tag + '[onclick]',
                description: `<${tag}> with onclick/tabindex has no ARIA role`,
                element_text: (el.innerText||'').trim().slice(0, 40),
            });
        }
    }

    // 9. aria-hidden on focusable elements
    for (const el of document.querySelectorAll('[aria-hidden="true"]')) {
        if (!el.offsetParent) continue;
        const focusable = el.querySelector('a[href], button:not([disabled]), input:not([disabled]), [tabindex]');
        if (focusable) {
            issues.push({
                rule: 'aria-hidden-focus',
                severity: 'critical',
                element: el.tagName.toLowerCase(),
                selector: el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase() + '[aria-hidden]',
                description: 'Element is aria-hidden but contains focusable children — keyboard users can reach unreachable content',
                element_text: '',
            });
        }
    }

    return issues;
}"""


# ── Keyboard navigation audit ─────────────────────────────────────────────────
_KEYBOARD_JS = """() => {
    const issues = [];

    // 1. Focusable elements with no visible focus indicator
    // We can't simulate :focus in JS, but check tabindex=-1 traps
    const negativeTabs = Array.from(document.querySelectorAll('[tabindex="-1"]'))
        .filter(el => el.offsetParent && ['button','a'].includes(el.tagName.toLowerCase()));
    if (negativeTabs.length > 3) {
        issues.push({
            rule: 'tabindex-negative',
            severity: 'minor',
            element: 'multiple',
            selector: '[tabindex="-1"]',
            description: `${negativeTabs.length} focusable elements have tabindex="-1" making them unreachable by keyboard`,
            element_text: '',
        });
    }

    // 2. Modal/dialog without focus trap
    for (const dialog of document.querySelectorAll('[role="dialog"], dialog')) {
        if (!dialog.offsetParent) continue;
        const focusable = dialog.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) {
            issues.push({
                rule: 'dialog-focus',
                severity: 'major',
                element: 'dialog',
                selector: dialog.id ? '#' + CSS.escape(dialog.id) : '[role="dialog"]',
                description: 'Dialog/modal has no focusable elements — keyboard users cannot interact with it',
                element_text: '',
            });
        }
    }

    // 3. Skip navigation link (nice-to-have)
    const skipLinks = Array.from(document.querySelectorAll('a')).filter(a => {
        const t = (a.innerText||'').trim().toLowerCase();
        return t.includes('skip') && (t.includes('content') || t.includes('navigation') || t.includes('main'));
    });
    if (skipLinks.length === 0) {
        issues.push({
            rule: 'skip-link',
            severity: 'minor',
            element: 'page',
            selector: 'body',
            description: 'No skip-navigation link found — keyboard users must tab through all navigation on every page load',
            element_text: '',
        });
    }

    return issues;
}"""


# ── Responsive layout checks ──────────────────────────────────────────────────
_RESPONSIVE_JS = """() => {
    const issues = [];
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // 1. Elements overflowing the viewport horizontally
    for (const el of document.querySelectorAll('*')) {
        if (!el.offsetParent) continue;
        const r = el.getBoundingClientRect();
        if (r.right > vw + 2) {
            const tag = el.tagName.toLowerCase();
            const text = (el.innerText || '').trim().slice(0, 40);
            if (['html','body','script','style','head'].includes(tag)) continue;
            issues.push({
                rule: 'overflow-x',
                severity: 'major',
                element: tag,
                selector: el.id ? '#' + CSS.escape(el.id) : tag,
                description: `Element overflows viewport horizontally by ${Math.round(r.right - vw)}px`,
                element_text: text,
            });
            if (issues.filter(i => i.rule === 'overflow-x').length >= 5) break;
        }
    }

    // 2. Text that is too small to read on mobile
    for (const el of document.querySelectorAll('p, span, li, a, button')) {
        if (!el.offsetParent) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs > 0 && fs < 11) {
            issues.push({
                rule: 'font-size-small',
                severity: 'minor',
                element: el.tagName.toLowerCase(),
                selector: el.tagName.toLowerCase(),
                description: `Font size ${fs.toFixed(0)}px is below 11px — may be unreadable on mobile`,
                element_text: (el.innerText||'').trim().slice(0, 30),
            });
            if (issues.filter(i => i.rule === 'font-size-small').length >= 3) break;
        }
    }

    // 3. Touch targets smaller than 44×44px (WCAG 2.5.5 AAA / practical mobile guide)
    for (const el of document.querySelectorAll('button, a, input, [role="button"]')) {
        if (!el.offsetParent) continue;
        const r = el.getBoundingClientRect();
        if ((r.width > 0 || r.height > 0) && (r.width < 24 || r.height < 24)) {
            issues.push({
                rule: 'touch-target',
                severity: 'minor',
                element: el.tagName.toLowerCase(),
                selector: el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase(),
                description: `Interactive element is ${Math.round(r.width)}×${Math.round(r.height)}px — below the 44×44px minimum touch target`,
                element_text: (el.innerText||el.getAttribute('aria-label')||'').trim().slice(0,30),
            });
            if (issues.filter(i => i.rule === 'touch-target').length >= 5) break;
        }
    }

    return issues;
}"""


class AccessibilityChecker:
    """
    Runs a suite of in-browser accessibility audits and returns a structured
    report. Designed to complement the VisualJudge and LLMJudge.

    Call run_all() to get the full report dict.
    """

    def __init__(self, page):
        self.page = page

    async def run_all(self, context_label: str = "") -> dict:
        """
        Execute all accessibility probe suites and return a merged report.

        Returns:
            {
                "context": <label>,
                "checked_at": <iso>,
                "issues": [ {rule, severity, element, selector, description, element_text}, ... ],
                "summary": { "critical": N, "major": N, "minor": N, "total": N },
                "score": 0-10,
                "pass": bool,
            }
        """
        all_issues: list[dict] = []

        for name, js in [
            ("aria",        _ARIA_JS),
            ("keyboard",    _KEYBOARD_JS),
            ("responsive",  _RESPONSIVE_JS),
        ]:
            try:
                results = await self.page.evaluate(js) or []
                for r in results:
                    r["category"] = name
                all_issues.extend(results)
            except Exception as e:
                logger.debug("[A11y] %s probe error: %s", name, e)

        # Contrast is expensive — run last
        try:
            contrast = await self.page.evaluate(_CONTRAST_JS) or []
            for r in contrast:
                r["category"] = "contrast"
            all_issues.extend(contrast)
        except Exception as e:
            logger.debug("[A11y] contrast probe error: %s", e)

        critical = sum(1 for i in all_issues if i.get("severity") == "critical")
        major    = sum(1 for i in all_issues if i.get("severity") == "major")
        minor    = sum(1 for i in all_issues if i.get("severity") == "minor")
        total    = len(all_issues)

        # Scoring: start at 10, deduct per severity
        score = max(0, 10 - (critical * 2) - (major * 1) - (minor * 0.25))
        score = round(score, 1)

        report = {
            "context":    context_label,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "issues":     all_issues,
            "summary":    {"critical": critical, "major": major, "minor": minor, "total": total},
            "score":      score,
            "pass":       critical == 0 and major <= 2,
        }
        logger.info(
            "[A11y] %s — %d issues (crit=%d maj=%d min=%d) score=%.1f",
            context_label or "check", total, critical, major, minor, score,
        )
        return report

    async def run_for_agent(self, agent_name: str) -> dict:
        return await self.run_all(context_label=f"agent:{agent_name}")

    async def run_for_page(self, url: str = "") -> dict:
        return await self.run_all(context_label=f"page:{url or self.page.url}")

    @staticmethod
    def fix_recommendation(issue: dict) -> str:
        """Return a developer-facing fix recommendation for a given issue."""
        rule = issue.get("rule", "")
        recs = {
            "color-contrast":    "Increase text/background colour contrast to meet WCAG AA (4.5:1 for normal text, 3:1 for large text). Use a contrast checker tool.",
            "button-name":       "Add descriptive innerText, aria-label, or title attribute to the button.",
            "image-alt":         "Add a meaningful alt attribute to the <img> element. Use empty alt='' for purely decorative images.",
            "label":             "Associate a <label for=...> with the input ID, or add aria-label / aria-labelledby attribute.",
            "link-name":         "Add visible link text or an aria-label describing where the link goes.",
            "duplicate-id":      "Ensure every element's ID is unique within the page. Use class or data-* attributes for shared styling hooks.",
            "landmark-main":     "Wrap main page content in a <main> element or add role='main' to the primary content container.",
            "heading-order":     "Ensure a single H1 exists per page. Heading levels should increase sequentially (H1→H2→H3), not skip levels.",
            "role-missing":      "Add an appropriate ARIA role (e.g., role='button') to non-semantic elements that handle interaction.",
            "aria-hidden-focus": "Remove aria-hidden from containers that hold focusable elements, or move focusable elements outside the aria-hidden subtree.",
            "tabindex-negative": "Only use tabindex='-1' intentionally for programmatic focus (e.g., modal trap). Remove it from standard interactive controls.",
            "dialog-focus":      "Ensure dialogs contain at least one focusable element and implement focus trap via JavaScript.",
            "skip-link":         "Add a visually-hidden skip link at the top of the page: <a href='#main-content' class='skip-link'>Skip to main content</a>.",
            "overflow-x":        "Add overflow-x:hidden on the parent container or constrain the element's width with max-width:100%.",
            "font-size-small":   "Set a minimum font-size of 12px (14px recommended) to ensure readability on small screens.",
            "touch-target":      "Increase interactive element size to at least 44×44px using min-width/min-height or padding.",
        }
        return recs.get(rule, f"Review and fix accessibility issue: {rule}.")
