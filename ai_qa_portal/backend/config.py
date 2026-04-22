"""Central configuration — env vars, paths, constants."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATED_SUITE = REPO_ROOT / "Tests" / "Generated" / "temp_test.robot"
CATALOG_PATH = REPO_ROOT / "keyword_catalog.json"
SYSTEM_PROMPT_PATH = REPO_ROOT / "system_prompt.txt"
RESULTS_DIR = REPO_ROOT / "Results"
ENVDATA_PATH = REPO_ROOT / "Resources" / "TestData" / "EnvData.robot"


class Settings(BaseSettings):
    fernet_key: str = ""
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8501,http://127.0.0.1:8501"
    )
    data_dir: str = str(REPO_ROOT / "ai_qa_portal" / "data")
    output_dir: str = str(REPO_ROOT / "ai_qa_portal" / "outputs")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
