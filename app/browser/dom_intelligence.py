"""
DOM Intelligence Layer — vision and browser intelligence for SAI testing.

Instead of relying on brittle CSS selectors, this module uses JavaScript
DOM inspection to understand the page state at runtime. It reads the actual
live DOM structure, finds elements by their visible text and semantic role,
and builds robust selectors dynamically.

This is the "browser intelligence" layer that makes the agent work even
when CSS class names change or data-testid attributes are missing.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# JS that snapshots the entire visible interactive DOM as a structured list.
# Used to understand what the page looks like without hardcoded selectors.
# ─────────────────────────────────────────────────────────────────────────────

SNAPSHOT_JS = """() => {
    function isVisible(el) {
        if (!el || !el.getBoundingClientRect) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && el.offsetParent !== null
               && getComputedStyle(el).visibility !== 'hidden'
               && getComputedStyle(el).display !== 'none';
    }
    function getText(el) {
        return (el.innerText || el.textContent || el.value || el.placeholder || '').trim();
    }
    function buildSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        if (el.getAttribute('aria-label'))
            return `[aria-label=${JSON.stringify(el.getAttribute('aria-label'))}]`;
        if (el.getAttribute('data-testid'))
            return `[data-testid=${JSON.stringify(el.getAttribute('data-testid'))}]`;
        if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
        // nth-child path as last resort
        let path = [];
        let node = el;
        while (node && node.tagName && node.tagName !== 'BODY') {
            let idx = 1;
            let sib = node.previousElementSibling;
            while (sib) { if (sib.tagName === node.tagName) idx++; sib = sib.previousElementSibling; }
            path.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${idx})`);
            node = node.parentElement;
        }
        return path.slice(-4).join(' > ');
    }

    const tags = ['button','input','textarea','select','a','[role="tab"]',
                  '[role="option"]','[role="checkbox"]','[contenteditable="true"]'];
    const seen = new Set();
    const result = [];
    for (const tag of tags) {
        for (const el of document.querySelectorAll(tag)) {
            if (!isVisible(el)) continue;
            const text = getText(el).slice(0, 120);
            const key = el.tagName + ':' + text;
            if (seen.has(key)) continue;
            seen.add(key);
            result.push({
                tag:       el.tagName.toLowerCase(),
                type:      el.type || '',
                role:      el.getAttribute('role') || '',
                text:      text,
                ariaLabel: el.getAttribute('aria-label') || '',
                testId:    el.getAttribute('data-testid') || '',
                placeholder: el.placeholder || '',
                disabled:  el.disabled || false,
                selected:  el.getAttribute('aria-selected') === 'true' || el.selected || false,
                selector:  buildSelector(el),
            });
        }
    }
    return result;
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that reads all visible text blocks on the page, grouped by region.
# Used to understand what SAI has rendered without relying on class names.
# ─────────────────────────────────────────────────────────────────────────────

PAGE_TEXT_JS = """() => {
    function isVisible(el) {
        if (!el || !el.getBoundingClientRect) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && el.offsetParent !== null;
    }
    const blocks = [];
    const seen = new Set();
    // Get meaningful text containers
    const selectors = ['p','h1','h2','h3','h4','li','td','th',
                        'pre','code','[class*="content"]','[class*="output"]',
                        '[class*="message"]','[class*="response"]',
                        '[class*="answer"]','[class*="result"]'];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (!isVisible(el)) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length < 10 || seen.has(t.slice(0,60))) continue;
            seen.add(t.slice(0,60));
            const r = el.getBoundingClientRect();
            blocks.push({
                text:  t.slice(0, 500),
                x:     Math.round(r.x),
                y:     Math.round(r.y),
                tag:   el.tagName.toLowerCase(),
                cls:   (el.className || '').slice(0, 80),
            });
        }
    }
    return blocks;
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that finds the active/generating state of SAI
# ─────────────────────────────────────────────────────────────────────────────

SAI_STATE_JS = """() => {
    const body = document.body;
    const text = (body.innerText || '').toLowerCase();

    // Detect Submit answers button (AGENT QUESTION card present)
    // Check this FIRST — a card with a spinner should NOT count as "generating"
    const submitBtn = Array.from(document.querySelectorAll('button')).find(b =>
        /submit answers/i.test((b.innerText || b.textContent || '').trim()) &&
        b.offsetParent !== null
    );
    const hasSubmitCard = !!submitBtn;

    // Detect SAI generating: spinners that are NOT inside the submit card
    const spinners = Array.from(document.querySelectorAll('*')).filter(el => {
        if (!el.offsetParent) return false;
        // Skip elements inside the agent question card
        if (submitBtn && submitBtn.parentElement && submitBtn.parentElement.contains(el)) return false;
        const cls = (el.className || '').toString().toLowerCase();
        return cls.includes('animate-spin') || cls.includes('animate-pulse')
            || cls.includes('animate-bounce');
    });
    const hasSpinner = spinners.some(el => {
        const r = el.getBoundingClientRect();
        return r.width > 8 && r.height > 8;
    });

    // Detect streaming text cursor (NOT inside submit card)
    const streaming = Array.from(document.querySelectorAll(
        '[data-streaming="true"], [class*="streaming-cursor"]'
    )).some(el => el.offsetParent !== null);

    // Detect explicit AI generation text (in chat area, not card)
    const loadingWords = ['thinking...', 'generating...', 'processing...'];
    const hasLoadingText = loadingWords.some(w => text.includes(w));

    // Textarea enabled = SAI is idle and ready for input
    const textarea = Array.from(document.querySelectorAll('textarea'))
        .filter(el => el.offsetParent !== null && !el.disabled && !el.readOnly)
        .pop();
    const textareaReady = !!textarea;

    // Chat messages — exclude tiny badge numbers like "(1)", "(12)"
    const msgs = Array.from(document.querySelectorAll(
        '[class*="message"], [class*="chat-bubble"], [class*="assistant"], [class*="response"]'
    )).filter(el => {
        if (!el.offsetParent) return false;
        const t = (el.innerText || '').trim();
        return t.length > 15;
    }).map(el => ({
        text: (el.innerText || '').trim().slice(0, 400),
        cls: (el.className || '').toString().slice(0, 80),
    }));

    // Canvas/agent tabs — filter out badge numbers like "(1)"
    const knownAgents = ['AIA', 'AGP', 'ETL', 'App Studio'];
    const knownSubTabs = ['Overview', 'Output', 'Questions', 'Thinking',
                          'Workflow', 'Architecture', 'Execution trace'];
    const tabs = Array.from(document.querySelectorAll('[role="tab"]'))
        .filter(el => el.offsetParent !== null)
        .map(el => ({
            text: (el.innerText || '').trim().replace(/\\s*\\(\\d+\\)\\s*/g, '').trim(),
            selected: el.getAttribute('aria-selected') === 'true',
        }))
        .filter(t => t.text.length > 1 &&
                     (knownAgents.includes(t.text) || knownSubTabs.includes(t.text)));

    // isGenerating: only true if SAI is actively generating a NEW response
    // NOT just because a submit card with a loading icon is present
    const isGenerating = (hasSpinner || streaming || hasLoadingText) && !hasSubmitCard;

    return {
        isGenerating,
        hasSpinner,
        streaming,
        hasLoadingText,
        hasSubmitCard,
        messages:       msgs.slice(-5),
        textareaReady,
        textareaValue:  textarea ? (textarea.value || textarea.placeholder || '').slice(0, 60) : '',
        tabs,
        rawTextSnippet: text.slice(0, 200),
    };
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that finds the most appropriate chat input
# ─────────────────────────────────────────────────────────────────────────────

FIND_CHAT_INPUT_JS = """() => {
    // Priority 1: currently focused input
    const ae = document.activeElement;
    if (ae && ae.offsetParent !== null && !ae.disabled && !ae.readOnly) {
        if (ae.tagName === 'TEXTAREA' || ae.contentEditable === 'true') {
            return { found: true, selector: null, useFocused: true,
                     tag: ae.tagName.toLowerCase() };
        }
    }
    // Priority 2: last visible enabled textarea
    const textareas = Array.from(document.querySelectorAll('textarea'))
        .filter(el => el.offsetParent !== null && !el.disabled && !el.readOnly);
    if (textareas.length > 0) {
        const el = textareas[textareas.length - 1];
        return {
            found: true,
            useFocused: false,
            tag: 'textarea',
            id: el.id || '',
            name: el.name || '',
            placeholder: el.placeholder || '',
            ariaLabel: el.getAttribute('aria-label') || '',
        };
    }
    // Priority 3: contenteditable
    const ces = Array.from(document.querySelectorAll('[contenteditable="true"]'))
        .filter(el => el.offsetParent !== null);
    if (ces.length > 0) {
        return { found: true, useFocused: false, tag: 'contenteditable' };
    }
    return { found: false };
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that reads the AGENT QUESTION card fully
# ─────────────────────────────────────────────────────────────────────────────

READ_AGENT_CARD_JS = """() => {
    // Find Submit answers button — anchors us to the card
    const submitBtn = Array.from(document.querySelectorAll('button')).find(b =>
        /submit answers/i.test((b.innerText || b.textContent || '').trim()) &&
        b.offsetParent !== null
    );
    if (!submitBtn) return null;

    // Walk up to the card container — stop at the element that contains
    // "agent question" text OR has checkboxes OR gets wider than 80% viewport
    let card = submitBtn;
    let bestCard = submitBtn.parentElement;
    for (let i = 0; i < 12; i++) {
        card = card.parentElement;
        if (!card || card === document.body) break;
        const t = (card.innerText || card.textContent || '').toLowerCase();
        if (t.includes('agent question')) { bestCard = card; break; }
        if (card.querySelector('input[type="checkbox"], [role="checkbox"]')) bestCard = card;
        const r = card.getBoundingClientRect();
        if (r.width > window.innerWidth * 0.8) break;
    }
    card = bestCard;
    if (!card) return null;

    const cardText = (card.innerText || '').trim();

    // ── Extract question blocks ───────────────────────────────────────────────
    // SAI renders each question as a visual block: question heading + options OR input.
    // We walk the card's children and group them into question blocks.
    const questions = [];

    function buildSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        if (el.name) return `[name="${el.name}"]`;
        let path = [], node = el;
        while (node && node.tagName && node !== card) {
            let idx = 1, sib = node.previousElementSibling;
            while (sib) { if (sib.tagName === node.tagName) idx++; sib = sib.previousElementSibling; }
            path.unshift(node.tagName.toLowerCase() + ':nth-of-type(' + idx + ')');
            node = node.parentElement;
        }
        return path.slice(-4).join(' > ');
    }

    // Strategy A: find all visible text+input inputs (textarea / text inputs)
    // Each one is its own question — walk backwards to find the question text
    const processedInputs = new Set();
    for (const input of card.querySelectorAll('textarea, input[type="text"], input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]):not([type])')) {
        if (!input.offsetParent || input.disabled) continue;
        processedInputs.add(input);
        // Walk upward to find a nearby question label (p, div with text, label)
        let questionText = input.placeholder || '';
        let node = input.parentElement;
        for (let i = 0; i < 5 && node && node !== card; i++) {
            const t = (node.innerText || '').replace((input.value || input.placeholder || ''), '').trim();
            if (t.length > 10 && t.length < 400 && !/submit answers/i.test(t)) {
                // prefer the closest text that looks like a question
                const lines = t.split('\\n').map(l => l.trim()).filter(l => l.length > 5);
                if (lines.length > 0) { questionText = lines[0]; break; }
            }
            node = node.parentElement;
        }
        questions.push({
            type: 'text',
            questionText: questionText.slice(0, 300),
            selector: buildSelector(input),
            placeholder: input.placeholder || '',
            options: [],
        });
    }

    // Strategy B: find all checkbox/radio groups — each group = one question block
    const processedCbs = new Set();
    for (const cb of card.querySelectorAll('input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]')) {
        if (!cb.offsetParent || processedCbs.has(cb)) continue;

        // Collect all siblings in the same group (same name or same parent container)
        const groupContainer = cb.closest('fieldset, [role="group"], [role="radiogroup"]') ||
                               cb.parentElement?.parentElement || cb.parentElement;
        const groupCbs = groupContainer
            ? Array.from(groupContainer.querySelectorAll(
                'input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"]'
              )).filter(e => e.offsetParent)
            : [cb];
        groupCbs.forEach(e => processedCbs.add(e));

        // Find the question text for this group
        let questionText = '';
        let node = groupContainer?.parentElement || cb.parentElement;
        for (let i = 0; i < 5 && node && node !== card; i++) {
            const children = Array.from(node.children);
            for (const child of children) {
                if (child.contains(groupContainer || cb)) continue;
                const t = (child.innerText || '').trim();
                if (t.length > 10 && t.length < 400 && !/submit answers/i.test(t)) {
                    questionText = t.split('\\n')[0].trim();
                    break;
                }
            }
            if (questionText) break;
            node = node.parentElement;
        }

        // Collect option texts
        const opts = [];
        const seenOpts = new Set();
        for (const c of groupCbs) {
            const row = c.closest('label, li, div') || c.parentElement;
            const text = (row?.innerText || '').trim();
            if (text && !seenOpts.has(text) && !/submit answers/i.test(text)) {
                seenOpts.add(text);
                opts.push({
                    text,
                    checked: c.checked || c.getAttribute('aria-checked') === 'true',
                    selector: buildSelector(c),
                });
            }
        }

        if (opts.length > 0) {
            questions.push({
                type: 'mcq',
                questionText: (questionText || 'Choose an option').slice(0, 300),
                selector: '',
                placeholder: '',
                options: opts,
            });
        }
    }

    // Fallback: if no structured questions found, treat whole card as one flat MCQ
    if (questions.length === 0) {
        const opts = [];
        const seen = new Set();
        const walker = document.createTreeWalker(card, NodeFilter.SHOW_ELEMENT);
        while (walker.nextNode()) {
            const el = walker.currentNode;
            if (!el.offsetParent || el.children.length > 4) continue;
            const text = (el.innerText || '').trim();
            if (!text || text.length > 200 || text.length < 3) continue;
            if (/submit answers|agent question/i.test(text)) continue;
            if (/^\\d+$/.test(text)) continue;
            if (!seen.has(text)) { seen.add(text); opts.push({ text, checked: false, selector: '' }); }
        }
        if (opts.length > 0) {
            questions.push({ type: 'mcq', questionText: cardText.slice(0, 200), selector: '', placeholder: '', options: opts });
        }
    }

    // Legacy flat fields for backward compat
    const allOptions = questions.filter(q => q.type === 'mcq').flatMap(q => q.options);
    const textInputs  = questions.filter(q => q.type === 'text').map(q => ({
        selector: q.selector, label: q.questionText, placeholder: q.placeholder, currentValue: '',
    }));

    return {
        cardText: cardText.slice(0, 600),
        questions,
        options: allOptions,
        optionCount: allOptions.length,
        textInputs,
    };
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that clicks a Submit answers button
# ─────────────────────────────────────────────────────────────────────────────

CLICK_SUBMIT_JS = """() => {
    const btn = Array.from(document.querySelectorAll('button')).find(b =>
        /submit answers/i.test((b.innerText || b.textContent || '').trim()) &&
        b.offsetParent !== null && !b.disabled
    );
    if (btn) { btn.click(); return true; }
    return false;
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that clicks an option by its exact text
# ─────────────────────────────────────────────────────────────────────────────

def click_option_js(option_text: str) -> str:
    safe = option_text.replace("'", "\\'").replace("\\", "\\\\")
    return f"""() => {{
        // Try clicking a checkbox/radio first
        const allEls = Array.from(document.querySelectorAll(
            'input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="option"], label, div, li'
        ));
        for (const el of allEls) {{
            if (!el.offsetParent) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t === '{safe}' || t.includes('{safe}')) {{
                el.click();
                return true;
            }}
        }}
        // Fallback: find element containing the text anywhere
        const xpath = document.evaluate(
            '//*[normalize-space(text())="{safe}"]',
            document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
        );
        const node = xpath.singleNodeValue;
        if (node && node.offsetParent) {{ node.click(); return true; }}
        return false;
    }}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that reads content of the currently visible agent panel
# ─────────────────────────────────────────────────────────────────────────────

READ_CANVAS_CONTENT_JS = """() => {
    // Find the main content area — prioritise right panel / canvas area
    const candidates = [
        '[data-testid="canvas"]',
        '[data-testid="agent-content"]',
        '[class*="canvas"]',
        '[class*="Canvas"]',
        '[class*="agent-output"]',
        '[class*="AgentOutput"]',
        '[class*="right-panel"]',
        '[class*="RightPanel"]',
        'aside',
        'main',
    ];
    for (const sel of candidates) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent) {
            const text = (el.innerText || '').trim();
            if (text.length > 20) return text.slice(0, 3000);
        }
    }
    // Fallback: read all visible text blocks in bottom-right quadrant
    const vw = window.innerWidth, vh = window.innerHeight;
    const blocks = Array.from(document.querySelectorAll('p,pre,code,li,h1,h2,h3,h4,span'))
        .filter(el => {
            if (!el.offsetParent) return false;
            const r = el.getBoundingClientRect();
            return r.x > vw * 0.4 || r.y > vh * 0.3;
        })
        .map(el => (el.innerText || '').trim())
        .filter(t => t.length > 10);
    return blocks.join('\\n').slice(0, 3000);
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that detects all visible canvas agent tabs by name
# ─────────────────────────────────────────────────────────────────────────────

FIND_AGENT_TABS_JS = """() => {
    const tabs = [];
    const seen = new Set();
    const knownAgents = ['AIA', 'AGP', 'ETL', 'App Studio'];

    // First look for elements containing known agent names
    for (const name of knownAgents) {
        const els = Array.from(document.querySelectorAll(
            `button, [role="tab"], [class*="tab"], a`
        )).filter(el => {
            if (!el.offsetParent) return false;
            return (el.innerText || '').trim() === name ||
                   (el.innerText || '').trim().includes(name);
        });
        if (els.length > 0) {
            const el = els[0];
            if (!seen.has(name)) {
                seen.add(name);
                tabs.push({
                    name,
                    selected: el.getAttribute('aria-selected') === 'true',
                    selector: el.id ? `#${CSS.escape(el.id)}` :
                              el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
                              null,
                    text: (el.innerText || '').trim(),
                });
            }
        }
    }

    // Also scan all visible tabs for unknown agent names
    const allTabs = Array.from(document.querySelectorAll('[role="tab"]'))
        .filter(el => el.offsetParent !== null);
    for (const el of allTabs) {
        const name = (el.innerText || '').trim();
        if (!name || seen.has(name) || name.length > 40) continue;
        // Skip sub-tab names
        const subTabNames = ['Overview','Output','Questions','Thinking','Workflow',
                             'Architecture','Execution trace','Steps'];
        if (subTabNames.includes(name)) continue;
        seen.add(name);
        tabs.push({
            name,
            selected: el.getAttribute('aria-selected') === 'true',
            selector: null,
            text: name,
        });
    }
    return tabs;
}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that finds and clicks an agent tab by name
# ─────────────────────────────────────────────────────────────────────────────

def click_agent_tab_js(agent_name: str) -> str:
    safe = agent_name.replace("'", "\\'")
    return f"""() => {{
        const candidates = Array.from(document.querySelectorAll(
            'button, [role="tab"], [class*="tab"], a'
        ));
        for (const el of candidates) {{
            if (!el.offsetParent || el.disabled) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t === '{safe}' || t === '{safe} ' || t.startsWith('{safe}')) {{
                el.click();
                return true;
            }}
        }}
        return false;
    }}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that finds and clicks a sub-tab by name
# ─────────────────────────────────────────────────────────────────────────────

def click_sub_tab_js(sub_tab_name: str) -> str:
    safe = sub_tab_name.replace("'", "\\'")
    return f"""() => {{
        // Sub-tabs are typically smaller tab buttons within the canvas panel
        const candidates = Array.from(document.querySelectorAll(
            '[role="tab"], button, [class*="subtab"], [class*="sub-tab"]'
        ));
        for (const el of candidates) {{
            if (!el.offsetParent || el.disabled) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t === '{safe}') {{
                el.click();
                return true;
            }}
        }}
        // Partial match fallback
        for (const el of candidates) {{
            if (!el.offsetParent || el.disabled) continue;
            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
            if (t.includes('{safe.lower()}')) {{
                el.click();
                return true;
            }}
        }}
        return false;
    }}"""


# ─────────────────────────────────────────────────────────────────────────────
# JS that checks if the page / canvas is still loading
# ─────────────────────────────────────────────────────────────────────────────

IS_LOADING_JS = """() => {
    // Spinners
    const hasSpinner = Array.from(document.querySelectorAll('*')).some(el => {
        if (!el.offsetParent) return false;
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) return false;
        const cls = (el.className || '').toString().toLowerCase();
        return cls.includes('spin') || cls.includes('animate-spin')
            || cls.includes('loading') || cls.includes('skeleton')
            || cls.includes('bounce') || cls.includes('pulse');
    });
    if (hasSpinner) return { loading: true, reason: 'spinner' };

    // Loading text
    const bodyText = (document.body.innerText || '').toLowerCase();
    const loadingPhrases = ['thinking...','generating...','processing...','loading...','building...','please wait'];
    for (const phrase of loadingPhrases) {
        if (bodyText.includes(phrase)) return { loading: true, reason: phrase };
    }

    // aria-busy
    const busy = document.querySelector('[aria-busy="true"]');
    if (busy && busy.offsetParent) return { loading: true, reason: 'aria-busy' };

    return { loading: false, reason: 'idle' };
}"""


class DOMIntelligence:
    """
    Browser intelligence layer.
    Wraps all JS probes and provides high-level methods to understand
    and interact with the SAI page without brittle CSS selectors.
    """

    def __init__(self, page):
        self.page = page

    async def snapshot(self) -> list[dict]:
        """Return all visible interactive elements on the page."""
        try:
            return await self.page.evaluate(SNAPSHOT_JS) or []
        except Exception as e:
            logger.debug("[DOM] snapshot error: %s", e)
            return []

    async def page_text_blocks(self) -> list[dict]:
        """Return all visible text blocks with position info."""
        try:
            return await self.page.evaluate(PAGE_TEXT_JS) or []
        except Exception as e:
            logger.debug("[DOM] page_text_blocks error: %s", e)
            return []

    async def get_sai_state(self) -> dict:
        """Return SAI platform state: generating, messages, tabs, textarea."""
        try:
            return await self.page.evaluate(SAI_STATE_JS) or {}
        except Exception as e:
            logger.debug("[DOM] get_sai_state error: %s", e)
            return {}

    async def is_loading(self) -> bool:
        """True if any loading indicator is visible."""
        try:
            result = await self.page.evaluate(IS_LOADING_JS)
            if result and result.get("loading"):
                logger.debug("[DOM] loading: %s", result.get("reason"))
                return True
        except Exception:
            pass
        return False

    async def get_chat_input_selector(self) -> Optional[str]:
        """Return a CSS selector for the current active chat input."""
        try:
            info = await self.page.evaluate(FIND_CHAT_INPUT_JS)
            if not info or not info.get("found"):
                return None
            if info.get("useFocused"):
                tag = info.get("tag", "textarea")
                return f"{tag}:focus" if tag != "contenteditable" else "[contenteditable='true']:focus"
            if info.get("ariaLabel"):
                return f'textarea[aria-label="{info["ariaLabel"]}"]'
            if info.get("placeholder"):
                ph = info["placeholder"][:30].replace('"', "'")
                return f'textarea[placeholder*="{ph}" i]'
            if info.get("id"):
                return f'textarea#{info["id"]}'
            if info.get("tag") == "contenteditable":
                return '[contenteditable="true"]'
            return "textarea"
        except Exception as e:
            logger.debug("[DOM] get_chat_input_selector error: %s", e)
            return None

    async def read_agent_card(self) -> Optional[dict]:
        """Return AGENT QUESTION card data if one is visible."""
        try:
            return await self.page.evaluate(READ_AGENT_CARD_JS)
        except Exception as e:
            logger.debug("[DOM] read_agent_card error: %s", e)
            return None

    async def click_option(self, option_text: str) -> bool:
        """Click an option row in an AGENT QUESTION card by its text."""
        try:
            return await self.page.evaluate(click_option_js(option_text))
        except Exception as e:
            logger.debug("[DOM] click_option error (%s): %s", option_text, e)
            return False

    async def click_submit_answers(self) -> bool:
        """Click the Submit answers button."""
        try:
            return await self.page.evaluate(CLICK_SUBMIT_JS)
        except Exception as e:
            logger.debug("[DOM] click_submit error: %s", e)
            return False

    async def get_visible_agent_tabs(self) -> list[dict]:
        """Return all visible canvas agent tabs."""
        try:
            return await self.page.evaluate(FIND_AGENT_TABS_JS) or []
        except Exception as e:
            logger.debug("[DOM] get_visible_agent_tabs error: %s", e)
            return []

    async def click_agent_tab(self, agent_name: str) -> bool:
        """Click an agent tab by name using DOM intelligence."""
        try:
            result = await self.page.evaluate(click_agent_tab_js(agent_name))
            if result:
                logger.info("[DOM] Clicked agent tab: %s", agent_name)
            return bool(result)
        except Exception as e:
            logger.debug("[DOM] click_agent_tab error (%s): %s", agent_name, e)
            return False

    async def click_sub_tab(self, sub_tab_name: str) -> bool:
        """Click a sub-tab by name using DOM intelligence."""
        try:
            result = await self.page.evaluate(click_sub_tab_js(sub_tab_name))
            if result:
                logger.info("[DOM] Clicked sub-tab: %s", sub_tab_name)
            return bool(result)
        except Exception as e:
            logger.debug("[DOM] click_sub_tab error (%s): %s", sub_tab_name, e)
            return False

    async def read_canvas_content(self) -> str:
        """Read the text content of the currently visible canvas panel."""
        try:
            return await self.page.evaluate(READ_CANVAS_CONTENT_JS) or ""
        except Exception as e:
            logger.debug("[DOM] read_canvas_content error: %s", e)
            return ""

    async def find_element_by_text(self, text: str, tag: str = "*") -> Optional[str]:
        """Return a CSS selector for an element containing the given text."""
        safe = text.replace("'", "\\'")
        js = f"""() => {{
            for (const el of document.querySelectorAll('{tag}')) {{
                if (!el.offsetParent) continue;
                const t = (el.innerText || el.textContent || '').trim();
                if (t === '{safe}' || t.includes('{safe}')) {{
                    if (el.id) return '#' + CSS.escape(el.id);
                    const tid = el.getAttribute('data-testid');
                    if (tid) return `[data-testid="${{tid}}"]`;
                    const al = el.getAttribute('aria-label');
                    if (al) return `[aria-label="${{al}}"]`;
                    return null;
                }}
            }}
            return null;
        }}"""
        try:
            return await self.page.evaluate(js)
        except Exception:
            return None

    async def log_page_state(self, label: str = "") -> None:
        """Log a summary of the current page state for debugging."""
        try:
            state = await self.get_sai_state()
            tabs = [t["text"] for t in state.get("tabs", [])]
            msgs = state.get("messages", [])
            last_msg = msgs[-1]["text"][:100] if msgs else "(none)"
            logger.info(
                "[DOM] State[%s] generating=%s submitCard=%s textarea=%s tabs=%s lastMsg=%s",
                label,
                state.get("isGenerating"),
                state.get("hasSubmitCard"),
                state.get("textareaReady"),
                tabs,
                last_msg,
            )
        except Exception as e:
            logger.debug("[DOM] log_page_state error: %s", e)
