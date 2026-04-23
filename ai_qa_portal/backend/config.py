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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


# Backwards-compatible top-level path for existing imports.
RESULTS_DIR = Path(settings.results_dir)
