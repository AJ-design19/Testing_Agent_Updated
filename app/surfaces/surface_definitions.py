"""
SAI Web UI Surface Definitions — 8 capability areas.

Each surface definition contains:
  id          : unique surface ID
  name        : human-readable name
  area        : capability area label
  url_path    : URL fragment to navigate to (appended to ADYA_URL)
  nav_sequence: ordered list of top-nav / sidebar clicks to reach the surface
  steps       : ordered list of test steps; each step has
                  step_id, description, action, selectors, expected_output,
                  fallback_playwright (True = deterministic Playwright fallback available)
  tabs        : tab names to visit (re-detected live from DOM on every run)
  sub_tabs    : sub-tab names to visit inside each tab
  scroll      : True = scroll full page top-to-bottom capturing screenshot per viewport
  sai_prompts : if the step requires sending a message to the SAI copilot, the exact
                text of the prompt for each sub-action
"""

from typing import Optional

# ── Surface step schema ──────────────────────────────────────────────────────

def _step(
    step_id: str,
    description: str,
    action: str,
    selectors: Optional[list] = None,
    sai_prompt: Optional[str] = None,
    expected_output: str = "",
    fallback_playwright: bool = True,
    scroll_after: bool = False,
) -> dict:
    return {
        "step_id": step_id,
        "description": description,
        "action": action,
        "selectors": selectors or [],
        "sai_prompt": sai_prompt,
        "expected_output": expected_output,
        "fallback_playwright": fallback_playwright,
        "scroll_after": scroll_after,
    }


# ── SURFACE 1: Onboarding ────────────────────────────────────────────────────

SURFACE_ONBOARDING = {
    "id": "SURF-01",
    "name": "Onboarding",
    "area": "onboarding",
    "url_path": "/orchestrator",
    "nav_sequence": [],
    "tabs": [],
    "sub_tabs": [],
    "scroll": True,
    "steps": [
        _step(
            "OB-01", "Navigate to SAI landing page",
            action="navigate",
            selectors=[], expected_output="Login/landing page rendered",
        ),
        _step(
            "OB-02", "Detect and click Sign In button",
            action="click",
            selectors=[
                'button:has-text("Sign in")', 'button:has-text("Sign In")',
                'a:has-text("Sign in")', '[role="button"]:has-text("Sign in")',
            ],
            expected_output="Auth modal appears",
            fallback_playwright=True,
        ),
        _step(
            "OB-03", "Enter email credential",
            action="fill",
            selectors=['input[type="email"]', '#email', 'input[name="email"]'],
            expected_output="Email field populated",
        ),
        _step(
            "OB-04", "Click Continue and enter password",
            action="click",
            selectors=['button:has-text("Continue")', 'button:has-text("Next")'],
            expected_output="Password field visible",
        ),
        _step(
            "OB-05", "Submit password and verify dashboard loads",
            action="click",
            selectors=['button:has-text("Sign in")', 'button[type="submit"]'],
            expected_output="Dashboard with prompt textarea visible",
        ),
        _step(
            "OB-06", "Detect workspace selector / New Workspace button",
            action="detect",
            selectors=[
                'button:has-text("New Workspace")', 'button:has-text("+ New Workspace")',
                'button:has-text("New Chat")', '[data-testid="new-workspace"]',
            ],
            expected_output="New Workspace control visible",
            scroll_after=True,
        ),
        _step(
            "OB-07", "Open a fresh new workspace",
            action="click",
            selectors=[
                'button:has-text("+ New Workspace")', 'button:has-text("New Workspace")',
                'button:has-text("New Chat")',
            ],
            expected_output="Blank orchestrator textarea ready",
        ),
        _step(
            "OB-08", "Send first SAI chat message (first-use onboarding flow)",
            action="sai_prompt",
            selectors=['textarea[aria-label="Write your prompt here"]', 'textarea'],
            sai_prompt="Hello, I'm new here. Can you briefly explain what you can help me build?",
            expected_output="SAI responds with onboarding / capability explanation",
            fallback_playwright=False,
            scroll_after=True,
        ),
        _step(
            "OB-09", "Scroll chat top-to-bottom, capture every viewport increment",
            action="scroll_capture",
            selectors=[],
            expected_output="Full chat area captured",
            scroll_after=True,
        ),
        _step(
            "OB-10", "Capture top nav bar (Workspaces / Apps / Workflows / Models / Agents)",
            action="screenshot",
            selectors=['nav', 'header', '[role="navigation"]'],
            expected_output="All nav buttons visible",
            scroll_after=False,
        ),
    ],
}


# ── SURFACE 2: AI Marketplace ────────────────────────────────────────────────

SURFACE_AI_MARKETPLACE = {
    "id": "SURF-02",
    "name": "AI Marketplace",
    "area": "ai_marketplace",
    "url_path": "/marketplace",
    "nav_sequence": [
        {"action": "click", "label": "Apps", "selectors": [
            'button:has-text("Apps")', '[aria-label="Apps"]',
            'a:has-text("Apps")', 'nav button:has-text("Apps")',
        ]},
    ],
    "tabs": [],
    "sub_tabs": [],
    "scroll": True,
    "steps": [
        _step(
            "MKT-01", "Navigate to AI Marketplace via Apps nav",
            action="nav_click",
            selectors=['button:has-text("Apps")', 'a:has-text("Apps")'],
            expected_output="Marketplace / App listing page visible",
            scroll_after=True,
        ),
        _step(
            "MKT-02", "Browse app listing — scroll full page",
            action="scroll_capture",
            selectors=[],
            expected_output="App cards / grid visible",
            scroll_after=True,
        ),
        _step(
            "MKT-03", "Use search / filter controls if visible",
            action="detect_and_interact",
            selectors=[
                'input[placeholder*="search" i]', 'input[type="search"]',
                '[aria-label*="search" i]', 'input[placeholder*="filter" i]',
            ],
            sai_prompt=None,
            expected_output="Search input found and focused",
            fallback_playwright=True,
        ),
        _step(
            "MKT-04", "Type search query",
            action="fill",
            selectors=['input[placeholder*="search" i]', 'input[type="search"]'],
            sai_prompt=None,
            expected_output="Search results filtered",
        ),
        _step(
            "MKT-05", "Click first app / template card to open detail view",
            action="click_first",
            selectors=[
                '[class*="card"]:first-child', '[class*="app-card"]:first-child',
                '[class*="template"]:first-child', '[role="listitem"]:first-child',
                'article:first-child',
            ],
            expected_output="App detail / overview modal or page opens",
            scroll_after=True,
        ),
        _step(
            "MKT-06", "Detect Clone / Fork / Use buttons",
            action="detect",
            selectors=[
                'button:has-text("Clone")', 'button:has-text("Fork")',
                'button:has-text("Use")', 'button:has-text("Deploy")',
                'button:has-text("Install")',
            ],
            expected_output="Action buttons present on detail page",
        ),
        _step(
            "MKT-07", "Detect Submit for Review / Publish button",
            action="detect",
            selectors=[
                'button:has-text("Submit for Review")', 'button:has-text("Publish")',
                'button:has-text("Submit")', 'button:has-text("List on Marketplace")',
            ],
            expected_output="Publisher action button found (or note absent)",
            fallback_playwright=False,
        ),
        _step(
            "MKT-08", "Scroll detail view top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full app detail captured",
            scroll_after=True,
        ),
        _step(
            "MKT-09", "Return to listing and capture full page state",
            action="navigate_back",
            selectors=[],
            expected_output="Marketplace listing restored",
            scroll_after=True,
        ),
    ],
}


# ── SURFACE 3: Agent Studio (MAN-ESM) ───────────────────────────────────────

SURFACE_AGENT_STUDIO = {
    "id": "SURF-03",
    "name": "Agent Studio (MAN-ESM)",
    "area": "agent_studio",
    "url_path": "/orchestrator",
    "nav_sequence": [],
    "tabs": ["AIA", "AGP", "ETL", "App Studio"],
    "sub_tabs": ["Overview", "Output", "Questions", "Thinking", "Workflow", "Architecture", "Execution trace"],
    "scroll": True,
    "steps": [
        _step(
            "AS-01", "Navigate to Orchestrator and open fresh workspace",
            action="ensure_workspace",
            selectors=['button:has-text("+ New Workspace")', 'button:has-text("New Workspace")'],
            expected_output="Blank orchestrator textarea ready",
        ),
        _step(
            "AS-02", "Send network kick-off prompt to SAI",
            action="sai_prompt",
            selectors=['textarea[aria-label="Write your prompt here"]', 'textarea'],
            sai_prompt=(
                "Build a real-time network monitoring dashboard that tracks "
                "latency, packet loss, and uptime across 5 nodes. "
                "Include alerting when any metric exceeds threshold."
            ),
            expected_output="SAI begins conversation / clarifying questions",
            fallback_playwright=False,
            scroll_after=True,
        ),
        _step(
            "AS-03", "Run full SAI conversation loop until canvas appears",
            action="sai_conversation_loop",
            selectors=[],
            expected_output="Canvas agent tabs appear (AIA / AGP / ETL / App Studio)",
            fallback_playwright=False,
        ),
        _step(
            "AS-04", "Wait for canvas and detect all visible agent tabs from live DOM",
            action="detect_all_tabs",
            selectors=['[role="tab"]', 'button:has-text("AIA")', 'button:has-text("AGP")'],
            expected_output="At least one agent tab detected",
            scroll_after=True,
        ),
        _step(
            "AS-05", "Navigate to AIA tab — visit every sub-tab, scroll, screenshot each",
            action="visit_agent_tabs",
            selectors=['button:has-text("AIA")', '[role="tab"]:has-text("AIA")'],
            expected_output="AIA tab opened and all sub-tabs captured",
            scroll_after=True,
        ),
        _step(
            "AS-06", "Navigate to AGP tab — visit every sub-tab, scroll, screenshot each",
            action="visit_agent_tabs",
            selectors=['button:has-text("AGP")', '[role="tab"]:has-text("AGP")'],
            expected_output="AGP tab opened and all sub-tabs captured",
            scroll_after=True,
        ),
        _step(
            "AS-07", "Navigate to ETL tab — visit every sub-tab, scroll, screenshot each",
            action="visit_agent_tabs",
            selectors=['button:has-text("ETL")', '[role="tab"]:has-text("ETL")'],
            expected_output="ETL tab opened and all sub-tabs captured",
            scroll_after=True,
        ),
        _step(
            "AS-08", "Navigate to App Studio tab — visit every sub-tab, scroll, screenshot each",
            action="visit_agent_tabs",
            selectors=['button:has-text("App Studio")', '[role="tab"]:has-text("App Studio")'],
            expected_output="App Studio tab opened and all sub-tabs captured",
            scroll_after=True,
        ),
        _step(
            "AS-09", "Capture Flow View (right panel showing agent network)",
            action="screenshot_region",
            selectors=[
                'text=Flow View', '[data-testid="flow-view"]',
                '[class*="flow-view"]', '[class*="FlowView"]',
            ],
            expected_output="Flow View diagram captured",
        ),
        _step(
            "AS-10", "Capture run results summary / verdict panel",
            action="screenshot",
            selectors=[
                '[class*="results"]', '[class*="verdict"]',
                '[class*="summary"]', '[class*="completion"]',
            ],
            expected_output="Run results panel captured (or noted absent)",
            scroll_after=True,
        ),
    ],
}


# ── SURFACE 4: AI-ELT + Analytics ───────────────────────────────────────────

SURFACE_AI_ELT = {
    "id": "SURF-04",
    "name": "AI-ELT + Analytics",
    "area": "ai_elt_analytics",
    "url_path": "/orchestrator",
    "nav_sequence": [],
    "tabs": ["ETL"],
    "sub_tabs": ["Overview", "Output", "Questions", "Thinking"],
    "scroll": True,
    "steps": [
        _step(
            "ELT-01", "Open fresh workspace",
            action="ensure_workspace",
            selectors=['button:has-text("+ New Workspace")', 'button:has-text("New Workspace")'],
            expected_output="Blank orchestrator ready",
        ),
        _step(
            "ELT-02", "Send CSV ingest + analytics prompt",
            action="sai_prompt",
            selectors=['textarea[aria-label="Write your prompt here"]', 'textarea'],
            sai_prompt=(
                "I have a CSV file with columns: date, region, product_sku, revenue, units_sold. "
                "Please ingest it, run a revenue-by-region analysis, generate a bar chart, "
                "and produce a PDF export report with executive summary."
            ),
            expected_output="SAI asks clarifying questions about data source / format",
            fallback_playwright=False,
            scroll_after=True,
        ),
        _step(
            "ELT-03", "Run SAI conversation loop (answer all clarifying questions)",
            action="sai_conversation_loop",
            selectors=[],
            expected_output="Canvas with ETL agent tab appears",
            fallback_playwright=False,
        ),
        _step(
            "ELT-04", "Navigate to ETL tab and visit all sub-tabs",
            action="visit_agent_tabs",
            selectors=['button:has-text("ETL")', '[role="tab"]:has-text("ETL")'],
            expected_output="ETL pipeline steps visible",
            scroll_after=True,
        ),
        _step(
            "ELT-05", "Capture ETL Output sub-tab (data schema / pipeline code)",
            action="visit_subtab",
            selectors=['button:has-text("Output")', '[role="tab"]:has-text("Output")'],
            expected_output="Data transformation output visible",
            scroll_after=True,
        ),
        _step(
            "ELT-06", "Detect chart / visualisation output",
            action="detect",
            selectors=[
                'canvas', '[class*="chart"]', '[class*="Chart"]',
                '[class*="visualization"]', 'svg[class*="chart"]', 'img[alt*="chart" i]',
            ],
            expected_output="Chart element present in output",
        ),
        _step(
            "ELT-07", "Detect export / report download controls",
            action="detect",
            selectors=[
                'button:has-text("Export")', 'button:has-text("Download")',
                'button:has-text("Report")', 'a[download]',
                'button:has-text("Generate Report")',
            ],
            expected_output="Export/download control present",
        ),
        _step(
            "ELT-08", "Scroll ETL panel top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full ETL panel captured",
            scroll_after=True,
        ),
        _step(
            "ELT-09", "Navigate to App Studio tab if present (chart dashboard UI)",
            action="visit_agent_tabs",
            selectors=['button:has-text("App Studio")', '[role="tab"]:has-text("App Studio")'],
            expected_output="App Studio tab with dashboard code captured",
            scroll_after=True,
        ),
    ],
}


# ── SURFACE 5: AGP / Governance ──────────────────────────────────────────────

SURFACE_AGP_GOVERNANCE = {
    "id": "SURF-05",
    "name": "AGP / Governance",
    "area": "agp_governance",
    "url_path": "/orchestrator",
    "nav_sequence": [],
    "tabs": ["AGP"],
    "sub_tabs": ["Overview", "Output", "Questions", "Thinking"],
    "scroll": True,
    "steps": [
        _step(
            "GOV-01", "Open fresh workspace",
            action="ensure_workspace",
            selectors=['button:has-text("+ New Workspace")', 'button:has-text("New Workspace")'],
            expected_output="Blank orchestrator ready",
        ),
        _step(
            "GOV-02", "Send governance / policy check prompt",
            action="sai_prompt",
            selectors=['textarea[aria-label="Write your prompt here"]', 'textarea'],
            sai_prompt=(
                "Build a GDPR-compliant data handling policy checker. "
                "It should inspect our customer data pipeline, flag any GDPR violations, "
                "generate an audit trail, and export a compliance report."
            ),
            expected_output="SAI initiates governance workflow with AGP agent",
            fallback_playwright=False,
            scroll_after=True,
        ),
        _step(
            "GOV-03", "Run SAI conversation loop",
            action="sai_conversation_loop",
            selectors=[],
            expected_output="Canvas with AGP tab appears",
            fallback_playwright=False,
        ),
        _step(
            "GOV-04", "Navigate to AGP tab and visit all sub-tabs",
            action="visit_agent_tabs",
            selectors=['button:has-text("AGP")', '[role="tab"]:has-text("AGP")'],
            expected_output="AGP governance rules / policy output visible",
            scroll_after=True,
        ),
        _step(
            "GOV-05", "Capture AGP Output sub-tab (policy rules output)",
            action="visit_subtab",
            selectors=['button:has-text("Output")', '[role="tab"]:has-text("Output")'],
            expected_output="Policy output / GDPR rules listed",
            scroll_after=True,
        ),
        _step(
            "GOV-06", "Detect audit trail section",
            action="detect",
            selectors=[
                '[class*="audit"]', 'text=Audit', 'text=audit trail',
                '[data-testid*="audit"]', '[class*="trail"]',
            ],
            expected_output="Audit trail section found",
        ),
        _step(
            "GOV-07", "Detect export report control",
            action="detect",
            selectors=[
                'button:has-text("Export")', 'button:has-text("Download Report")',
                'button:has-text("Generate Report")', 'a[download]',
            ],
            expected_output="Export control present",
        ),
        _step(
            "GOV-08", "Scroll AGP panel top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full governance panel captured",
            scroll_after=True,
        ),
    ],
}


# ── SURFACE 6: Admin ─────────────────────────────────────────────────────────

SURFACE_ADMIN = {
    "id": "SURF-06",
    "name": "Admin",
    "area": "admin",
    "url_path": "/settings",
    "nav_sequence": [
        {"action": "click", "label": "Settings/Admin", "selectors": [
            'a:has-text("Settings")', 'button:has-text("Settings")',
            '[aria-label="Settings"]', '[href*="settings"]',
            'a:has-text("Admin")', 'button:has-text("Admin")',
            '[data-testid="settings"]', '[data-testid="admin"]',
        ]},
    ],
    "tabs": [],
    "sub_tabs": [],
    "scroll": True,
    "steps": [
        _step(
            "ADM-01", "Navigate to Settings / Admin panel",
            action="nav_click",
            selectors=[
                'a:has-text("Settings")', 'button:has-text("Settings")',
                '[aria-label="Settings"]', '[href*="settings"]',
                '[class*="settings"]', '[class*="admin"]',
                'a:has-text("Admin")',
            ],
            expected_output="Settings or admin page rendered",
            scroll_after=True,
        ),
        _step(
            "ADM-02", "Detect workspace provisioning controls",
            action="detect",
            selectors=[
                'button:has-text("New Workspace")', 'button:has-text("Create Workspace")',
                '[class*="workspace"]', 'text=Workspace Settings',
                'text=Provision', '[data-testid*="workspace"]',
            ],
            expected_output="Workspace provisioning UI found",
            scroll_after=True,
        ),
        _step(
            "ADM-03", "Detect AGP / governance rule configuration",
            action="detect",
            selectors=[
                'text=AGP', 'text=Governance', 'text=Policy Rules',
                '[class*="governance"]', '[class*="agp"]',
                'button:has-text("Configure")', 'text=Rules',
            ],
            expected_output="Governance config section found",
        ),
        _step(
            "ADM-04", "Detect credential / API key rotation controls",
            action="detect",
            selectors=[
                'text=API Key', 'text=Credentials', 'text=Rotate',
                'button:has-text("Rotate")', 'button:has-text("Regenerate")',
                'input[type="password"]', '[class*="credential"]', '[class*="api-key"]',
            ],
            expected_output="Credential rotation UI found",
        ),
        _step(
            "ADM-05", "Detect budget / spend cap controls",
            action="detect",
            selectors=[
                'text=Budget', 'text=Spend Cap', 'text=Limit',
                '[class*="budget"]', '[class*="spend"]',
                'input[type="number"]', '[data-testid*="budget"]',
            ],
            expected_output="Budget cap controls found",
        ),
        _step(
            "ADM-06", "Scroll full settings page top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full admin panel captured",
            scroll_after=True,
        ),
        _step(
            "ADM-07", "Detect and navigate all settings sub-sections / tabs",
            action="detect_all_tabs",
            selectors=['[role="tab"]', '[class*="settings-tab"]', '[class*="nav-item"]'],
            expected_output="All settings sections enumerated",
        ),
    ],
}


# ── SURFACE 7: Content Generation ───────────────────────────────────────────

SURFACE_CONTENT_GENERATION = {
    "id": "SURF-07",
    "name": "Content Generation",
    "area": "content_generation",
    "url_path": "/orchestrator",
    "nav_sequence": [],
    "tabs": ["AIA", "App Studio"],
    "sub_tabs": ["Overview", "Output", "Questions", "Thinking"],
    "scroll": True,
    "steps": [
        _step(
            "CG-01", "Open fresh workspace",
            action="ensure_workspace",
            selectors=['button:has-text("+ New Workspace")', 'button:has-text("New Workspace")'],
            expected_output="Blank orchestrator ready",
        ),
        _step(
            "CG-02", "Send draft message / KB search prompt",
            action="sai_prompt",
            selectors=['textarea[aria-label="Write your prompt here"]', 'textarea'],
            sai_prompt=(
                "Draft a professional email to our enterprise clients announcing "
                "a new AI feature launch. Then search the knowledge base for any "
                "existing brand guidelines. Finally, generate an executive summary "
                "of our Q4 product roadmap."
            ),
            expected_output="SAI engages with content request",
            fallback_playwright=False,
            scroll_after=True,
        ),
        _step(
            "CG-03", "Run SAI conversation loop",
            action="sai_conversation_loop",
            selectors=[],
            expected_output="Canvas appears with generated content",
            fallback_playwright=False,
        ),
        _step(
            "CG-04", "Detect KB search results or knowledge base panel",
            action="detect",
            selectors=[
                '[class*="knowledge"]', 'text=Knowledge Base',
                '[class*="kb"]', 'text=KB', 'text=knowledge base',
                '[data-testid*="kb"]',
            ],
            expected_output="KB results visible",
        ),
        _step(
            "CG-05", "Navigate to AIA tab and capture content generation output",
            action="visit_agent_tabs",
            selectors=['button:has-text("AIA")', '[role="tab"]:has-text("AIA")'],
            expected_output="Content generation pipeline visible",
            scroll_after=True,
        ),
        _step(
            "CG-06", "Navigate to App Studio tab and capture generated document",
            action="visit_agent_tabs",
            selectors=['button:has-text("App Studio")', '[role="tab"]:has-text("App Studio")'],
            expected_output="Executive summary / email draft visible",
            scroll_after=True,
        ),
        _step(
            "CG-07", "Scroll full canvas top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full content generation result captured",
            scroll_after=True,
        ),
    ],
}


# ── SURFACE 8: ESLL Lab ──────────────────────────────────────────────────────

SURFACE_ESLL_LAB = {
    "id": "SURF-08",
    "name": "ESLL Lab",
    "area": "esll_lab",
    "url_path": "/esll",
    "nav_sequence": [
        {"action": "click", "label": "ESLL / Lab", "selectors": [
            'a:has-text("ESLL")', 'button:has-text("ESLL")',
            '[href*="esll"]', '[data-testid="esll"]',
            'a:has-text("Lab")', 'button:has-text("Lab")',
            'a:has-text("Learnings")', 'button:has-text("Learnings")',
        ]},
    ],
    "tabs": [],
    "sub_tabs": [],
    "scroll": True,
    "steps": [
        _step(
            "ESLL-01", "Navigate to ESLL Lab",
            action="nav_click",
            selectors=[
                'a:has-text("ESLL")', 'button:has-text("ESLL")',
                '[href*="esll"]', '[data-testid="esll"]',
                'a:has-text("Lab")', 'a:has-text("Learnings")',
            ],
            expected_output="ESLL Lab page rendered",
            scroll_after=True,
        ),
        _step(
            "ESLL-02", "Browse promoted learnings list",
            action="scroll_capture",
            selectors=[],
            expected_output="Learning items visible",
            scroll_after=True,
        ),
        _step(
            "ESLL-03", "Detect Approve / Reject controls on learning items",
            action="detect",
            selectors=[
                'button:has-text("Approve")', 'button:has-text("Reject")',
                'button:has-text("Accept")', 'button:has-text("Decline")',
                '[data-testid*="approve"]', '[data-testid*="reject"]',
            ],
            expected_output="Approve/Reject controls found",
        ),
        _step(
            "ESLL-04", "Detect Redact control",
            action="detect",
            selectors=[
                'button:has-text("Redact")', '[data-testid*="redact"]',
                '[class*="redact"]', 'text=Redact',
            ],
            expected_output="Redact control found (or noted absent)",
        ),
        _step(
            "ESLL-05", "Detect Publish control",
            action="detect",
            selectors=[
                'button:has-text("Publish")', '[data-testid*="publish"]',
                '[class*="publish"]', 'text=Publish',
            ],
            expected_output="Publish control found",
        ),
        _step(
            "ESLL-06", "Navigate all visible sub-sections / tabs of ESLL Lab",
            action="detect_all_tabs",
            selectors=['[role="tab"]', '[class*="tab"]', '[class*="nav-item"]'],
            expected_output="All ESLL tabs enumerated and visited",
            scroll_after=True,
        ),
        _step(
            "ESLL-07", "Scroll entire ESLL Lab page top-to-bottom",
            action="scroll_capture",
            selectors=[],
            expected_output="Full ESLL Lab captured",
            scroll_after=True,
        ),
    ],
}


# ── Complete surface registry ────────────────────────────────────────────────

ALL_SURFACES = [
    SURFACE_ONBOARDING,
    SURFACE_AI_MARKETPLACE,
    SURFACE_AGENT_STUDIO,
    SURFACE_AI_ELT,
    SURFACE_AGP_GOVERNANCE,
    SURFACE_ADMIN,
    SURFACE_CONTENT_GENERATION,
    SURFACE_ESLL_LAB,
]


def get_surface_by_id(surface_id: str) -> dict:
    for s in ALL_SURFACES:
        if s["id"] == surface_id:
            return s
    raise KeyError(f"Surface '{surface_id}' not found")


def get_surface_by_area(area: str) -> dict:
    for s in ALL_SURFACES:
        if s["area"] == area:
            return s
    raise KeyError(f"Area '{area}' not found")
