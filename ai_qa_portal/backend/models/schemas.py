"""Pydantic models for all API request/response types."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# ── Projects ──────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    environment: str = "Dev"
    sandbox_url: str = ""
    username: str = ""
    password: str = ""

class ProjectMeta(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    owner: str = ""
    created_at: str = ""

class ProjectSummary(BaseModel):
    name: str
    environments: list[str] = []
    test_count: int = 0

class CredentialsUpdate(BaseModel):
    sandbox_url: str = ""
    username: str = ""
    password: str = ""
    security_token: str = ""
    slack_webhook_url: str = ""
    persona: str = "System Admin"
    # Per-project Playwright-MCP opt-in (Phase 0+). Independent of the
    # global ``settings.playwright_enabled`` master switch -- a project
    # can opt in here AND the global switch must be on. Default False
    # so existing projects are unchanged after this column lands.
    playwright_mcp_enabled: bool = False

class EnvironmentConfig(BaseModel):
    name: str
    personas: dict[str, dict[str, str]] = {}

class TestInfo(BaseModel):
    name: str
    path: str
    modified: datetime


# ── Generation ────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    sandbox_url: str
    username: str
    password: str
    project_name: str | None = None
    test_name: str | None = None
    overwrite: bool = True
    auto_generate_data: bool = True
    headless: bool = False
    generation_mode: str = "mcp_stepwise"
    # Persona's default Salesforce app (e.g. "Pentair Sales"). When set, the
    # planner / quick-generator gets a "Persona context" block telling the
    # LLM that ${salesAutomationAppName} will be injected at runtime, so PO
    # keywords like SalesPO.Open New Lead From Sales App land in the right
    # app instead of falling back to "Sales".
    default_app: str = ""

class ValidationErrorPayload(BaseModel):
    """One actionable issue surfaced by the script validator. Mirrors
    ``ai_qa_portal.backend.services.script_validator.ValidationError``
    but is duplicated here so the schema module stays free of internal
    service imports (avoids circular imports + keeps the public API
    decoupled from the validator's internal representation)."""
    line: int = 0
    column: int = 0
    kind: str = ""        # Now includes ``locator_not_found`` (Phase 1)
    symbol: str = ""
    message: str = ""
    closest_matches: list[str] = []
    snippet: str = ""
    # Phase 1 Playwright locator-validation fields. Optional; only
    # populated when ``kind == "locator_not_found"``. Backwards
    # compatible: existing clients ignore these fields.
    page_url: str = ""
    suggested_locator: str = ""


class GenerationAttempt(BaseModel):
    """One trip through the validate-fix-validate loop. Surfaced in the
    response so the UI can render the correction trail."""
    attempt: int
    ok: bool
    error_count: int
    fix_prompt_excerpt: str = ""
    script_excerpt: str = ""


class ProviderSwitchPayload(BaseModel):
    """One LLM-provider failover event surfaced to the UI. Populated when
    the primary LLM hit a quota / rate-limit / auth / availability issue
    and ``call_llm`` automatically failed over to a configured backup.
    The frontend renders this as a banner: 'Switched from Gemini to Groq
    because Gemini hit its quota'."""
    from_provider: str = ""
    from_label: str = ""
    to_provider: str = ""
    to_label: str = ""
    reason: str = ""
    error_excerpt: str = ""


class GenerateResponse(BaseModel):
    robot_code: str
    test_path: str | None = None
    lint_errors: list[str] = []
    # Validation metadata. Populated by the new retry-loop pipeline.
    # Empty when validation was skipped (legacy path) or not available.
    validation_ok: bool = True
    validation_errors: list[ValidationErrorPayload] = []
    validation_attempts: list[GenerationAttempt] = []
    # LLM-provider failover events that occurred while servicing this
    # request. Empty in the common case (primary provider succeeded).
    provider_switches: list[ProviderSwitchPayload] = []
    # When the deterministic recipe-first tier matched, this is the
    # recipe name (e.g. "lead_routing_by_state") and NO LLM call was
    # made. The frontend renders a "Generated from recipe: <name>"
    # badge, proving the deterministic path fired. Empty/None means
    # the LLM tier (local or cloud) produced the script.
    used_recipe: str | None = None
    used_recipe_confidence: str | None = None
    # Phase 1 Playwright locator-validation surface. ``locator_validation_ok``
    # is None when the gate didn't run for this generation (most current
    # production traffic, since the feature is flagged off by default).
    # When the gate ran: True/False with counts. ``shadow=True`` means
    # results were observed but not allowed to block the Run button --
    # used during the 1-2 week burn-in period to gather false-positive
    # data before flipping the gate to load-bearing.
    locator_validation_ok: bool | None = None
    locator_validation_count: int = 0
    locator_validation_failed: int = 0
    locator_validation_shadow: bool = False

class StepwiseProgress(BaseModel):
    step: int
    total: int
    keyword: str
    status: str
    message: str = ""


# ── Runs ──────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    test_path: str
    sandbox_url: str
    username: str
    password: str
    headless: bool = True
    use_pabot: bool = False
    include_tags: str = ""
    exclude_tags: str = ""

class RunResult(BaseModel):
    status: str
    duration_s: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    output_dir: str = ""
    log_html: str | None = None
    report_html: str | None = None
    error_message: str | None = None

class RunHistoryEntry(BaseModel):
    run_name: str
    timestamp: datetime
    passed: int = 0
    failed: int = 0
    total: int = 0
    duration_s: float = 0.0


# ── LLM ───────────────────────────────────────────────────────────────

class LLMChatRequest(BaseModel):
    system_prompt: str = ""
    user_message: str
    provider: str | None = None
    image_bytes: str | None = None

class LLMChatResponse(BaseModel):
    content: str
    provider: str
    model: str = ""

class FailureAnalysis(BaseModel):
    prompt: str
    robot_code: str
    error_message: str

class FailureAnalysisResponse(BaseModel):
    analysis: str
    suggested_fix: str = ""


# ── MCP ───────────────────────────────────────────────────────────────

class MCPStatus(BaseModel):
    running: bool
    url: str = ""
    # Optional fields populated when the server is reachable. Older callers
    # that read only ``running``/``url`` keep working unchanged.
    version: str = ""
    tool_count: int = 0
    memory_enabled: bool = False
    memory_db_path: str = ""

class MCPSessionInit(BaseModel):
    sandbox_url: str
    username: str
    password: str
    resources: list[str] = []

class MCPSessionResponse(BaseModel):
    session_id: str

class MCPStepRequest(BaseModel):
    session_id: str
    keyword: str
    args: list[str] = []

class MCPStepResponse(BaseModel):
    status: str
    output: str = ""
    screenshot: str | None = None


# ── Salesforce ────────────────────────────────────────────────────────

class SchemaRequest(BaseModel):
    prompt: str
    sandbox_url: str
    username: str
    password: str

class SchemaResponse(BaseModel):
    objects: list[str]
    context: str

class SOQLRequest(BaseModel):
    query: str

class SOQLResponse(BaseModel):
    records: list[dict[str, Any]]
    total_size: int = 0


# ── Catalog ───────────────────────────────────────────────────────────

class KeywordEntry(BaseModel):
    keyword_name: str
    arguments: list[str] = []
    documentation: str = ""
    tags: list[str] = []
    source_file: str = ""

class CatalogResponse(BaseModel):
    keywords: list[KeywordEntry]
    count: int
    generated_at: str = ""


# ── Analytics ─────────────────────────────────────────────────────────

class AnalyticsSummary(BaseModel):
    total_runs: int = 0
    total_passed: int = 0
    total_failed: int = 0
    pass_rate: float = 0.0
    avg_duration_s: float = 0.0
    history: list[RunHistoryEntry] = []


# ── Locators ──────────────────────────────────────────────────────────

class LocatorScanRequest(BaseModel):
    sandbox_url: str
    username: str
    password: str

class LocatorResult(BaseModel):
    name: str
    locator: str
    status: str
    error: str | None = None

class LocatorScanResponse(BaseModel):
    healthy: int = 0
    stale: int = 0
    skipped: int = 0
    results: list[LocatorResult] = []
