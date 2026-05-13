"""Central configuration — env vars, paths, constants."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATED_SUITE = REPO_ROOT / "Tests" / "Generated" / "temp_test.robot"
CATALOG_PATH = REPO_ROOT / "keyword_catalog.json"
SYSTEM_PROMPT_PATH = REPO_ROOT / "system_prompt.txt"
ENVDATA_PATH = REPO_ROOT / "Resources" / "TestData" / "EnvData.robot"


class Settings(BaseSettings):
    fernet_key: str = ""
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8501,http://127.0.0.1:8501"
    )
    # Extra CORS origins to allow on top of the built-in regex (comma separated).
    # Useful when you attach a custom domain on top of *.vercel.app.
    extra_cors_origins: str = ""
    # Writable directories. Defaults keep dev unchanged; Docker / Fly point them
    # at the mounted volume via env vars.
    data_dir: str = str(REPO_ROOT / "ai_qa_portal" / "data")
    output_dir: str = str(REPO_ROOT / "ai_qa_portal" / "outputs")
    results_dir: str = str(REPO_ROOT / "Results")
    saved_projects_dir: str = str(REPO_ROOT / "Saved_Projects")

    # --- Auth (Phase 1) ---
    # OAuth 2.0 Client ID from Google Cloud Console. Required when AUTH_DISABLED is false.
    # The backend uses this as the expected `aud` claim when verifying Google ID tokens
    # via Google's JWKS endpoint. MUST match the GOOGLE_CLIENT_ID configured on the
    # frontend's NextAuth Google provider, otherwise verification fails.
    google_client_id: str = ""
    # Only Google accounts in this Workspace domain may log in. Empty string disables the check
    # (open to any Google account) -- not recommended for production.
    allowed_email_domain: str = "astounddigital.com"
    # Comma-separated list of emails that are auto-promoted to is_admin=True on first login.
    # Always include at least one address for bootstrap, otherwise nobody can manage users later.
    initial_admins: str = ""
    # If true, allow unauthenticated access to all routes (dev / migration only). Defaults False.
    auth_disabled: bool = False

    # --- Playwright-MCP integration (Phase 0+) ---
    # Master kill-switch: when False, NO Playwright code path activates
    # regardless of per-feature flags or per-project opt-ins. Lets us
    # turn the entire integration off with a single env var if anything
    # goes wrong in production.
    playwright_enabled: bool = True
    # Phase 1 -- locator validation gate during script generation.
    # Default OFF so existing flows are byte-for-byte unchanged. Flip
    # via env (PW_LOCATOR_VALIDATION=true) once you've validated on
    # a real project that it doesn't false-positive.
    pw_locator_validation: bool = False
    # Phase 1 sub-flag -- "shadow mode": run the locator gate in the
    # background, log results, but DO NOT surface failures to the user
    # or block the Run button. Use this to collect false-positive metrics
    # for 1-2 weeks before flipping the user-visible flag above.
    pw_locator_validation_shadow: bool = False
    # Phase 2 -- recording mode endpoints.
    pw_recording: bool = False
    # Phase 3 -- visual regression tier.
    pw_visual_regression: bool = False
    # Phase 4 -- trace viewer link in run reports.
    pw_trace_viewer: bool = False
    # Phase 5 -- AI exploratory testing endpoint (admin-only beta).
    pw_exploratory: bool = False

    # Pre-warm Playwright runtime on FastAPI startup. Off by default to
    # keep cold start times unchanged for non-Playwright deployments.
    pw_prewarm: bool = False

    # --- Database (Phase: Postgres + pgvector) ---
    # When set, the backend uses this URL (e.g.
    # `postgresql+psycopg://user:pass@host:5432/portal`). When empty, the
    # legacy SQLite path under {data_dir}/users.db is used.
    database_url: str = ""
    # Whether to attempt `CREATE EXTENSION IF NOT EXISTS vector` on startup.
    # Set False if your DB user lacks SUPERUSER and the extension is already
    # installed out-of-band.
    pgvector_auto_install: bool = True

    # --- Embeddings (Phase: RAG) ---
    embedding_provider: str = "openai"  # openai | ollama | gemini
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    # Optional override; otherwise we read OPENAI_API_KEY / GEMINI_API_KEY
    # from the existing LLM env vars.
    openai_embeddings_api_key: str = ""

    # --- Jira (Phase: Jira ingestion) ---
    # Org-wide default. Per-project overrides live in `jira_connections`.
    # All three optional; the UI prompts when missing.
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    # --- GitHub (Phase: GitHub integration) ---
    # GitHub App credentials (preferred). PAT path lives per-project.
    github_app_id: str = ""
    github_app_private_key: str = ""  # PEM contents; empty when using PAT-only
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_webhook_secret: str = ""  # HMAC secret for /webhooks/github
    github_app_install_url: str = ""  # https://github.com/apps/<name>/installations/new

    # --- Scheduler (Phase: Hybrid scheduling) ---
    # When True, the in-process APScheduler starts at FastAPI boot and runs
    # local schedules. Disable in worker-less / read-only deployments.
    scheduler_enabled: bool = True
    scheduler_max_workers: int = 4

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


# Backwards-compatible top-level path for existing imports.
RESULTS_DIR = Path(settings.results_dir)
