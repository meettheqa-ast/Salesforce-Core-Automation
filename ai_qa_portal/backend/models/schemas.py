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

class GenerateResponse(BaseModel):
    robot_code: str
    test_path: str | None = None
    lint_errors: list[str] = []

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
