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
    kind: str = ""
    symbol: str = ""
    message: str = ""
    closest_matches: list[str] = []
    snippet: str = ""


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
