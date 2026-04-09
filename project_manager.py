"""
Project Workspace Manager.

Manages named project directories under Saved_Projects/ at the repo root.
Each project has:
  project.json  — metadata (name, created_at, description)
  config.json   — multi-environment, multi-persona credentials
  Tests/        — generated .robot files
  Data/         — uploaded CSV test-data files

Pure Python — no Streamlit dependency.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAVED_PROJECTS_ROOT = ROOT / "Saved_Projects"

CONFIG_FILENAME = "config.json"
DEFAULT_ENVIRONMENTS: list[str] = ["Dev", "QA", "UAT", "Prod"]
DEFAULT_PERSONA = "System Admin"
_CREDENTIAL_KEYS: list[str] = [
    "sandbox_url", "username", "password", "security_token", "slack_webhook_url",
]


def _empty_cred_block() -> dict[str, str]:
    """Return a credential dict with all keys set to empty strings."""
    return {k: "" for k in _CREDENTIAL_KEYS}


def _default_persona_block() -> dict[str, dict[str, str]]:
    return {"personas": {DEFAULT_PERSONA: _empty_cred_block()}}


_JIRA_KEYS: list[str] = ["jira_base_url", "jira_api_token", "jira_project_key"]


def _empty_jira_block() -> dict[str, str]:
    return {k: "" for k in _JIRA_KEYS}


def _default_full_config() -> dict:
    """Return ``{"environments": {}, "jira_base_url": "", ...}``."""
    cfg: dict = {"environments": {}}
    cfg.update(_empty_jira_block())
    return cfg


# Keep legacy alias so existing imports don't break.
ENVIRONMENTS = DEFAULT_ENVIRONMENTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a user-facing project/test name to a safe filesystem slug."""
    slug = re.sub(r"[^\w\s-]", "", name.strip())
    slug = re.sub(r"[\s\-]+", "_", slug).strip("_")
    return slug[:80]


def _project_dir(name: str) -> Path:
    return SAVED_PROJECTS_ROOT / name


# ---------------------------------------------------------------------------
# Project operations
# ---------------------------------------------------------------------------

def list_projects() -> list[str]:
    """Return sorted project folder names that contain a valid project.json."""
    SAVED_PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    return sorted(
        d.name
        for d in SAVED_PROJECTS_ROOT.iterdir()
        if d.is_dir() and (d / "project.json").is_file()
    )


def project_exists(name: str) -> bool:
    return (_project_dir(name) / "project.json").is_file()


def delete_project(name: str) -> bool:
    """Permanently remove a project directory. Returns True if deleted."""
    proj_dir = _project_dir(name)
    if not proj_dir.is_dir():
        return False
    shutil.rmtree(proj_dir, ignore_errors=True)
    return not proj_dir.exists()


def create_project(name: str, description: str = "") -> Path:
    """
    Create a new project at Saved_Projects/<slug>/ with Tests/ and Data/ subdirs.

    Returns the project root Path.
    Raises ValueError if name is blank or project already exists.
    """
    slug = _slugify(name)
    if not slug:
        raise ValueError("Project name must contain at least one alphanumeric character.")
    if project_exists(slug):
        raise ValueError(f"Project '{slug}' already exists.")

    proj_dir = _project_dir(slug)
    (proj_dir / "Tests").mkdir(parents=True)
    (proj_dir / "Data").mkdir(parents=True)

    meta = {
        "name": slug,
        "display_name": name.strip(),
        "description": description.strip(),
        "created_at": datetime.now().isoformat(),
    }
    (proj_dir / "project.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_full_config(slug, _default_full_config())
    return proj_dir


def get_project_path(name: str) -> Path:
    """Return project root Path; raises FileNotFoundError if project doesn't exist."""
    proj_dir = _project_dir(name)
    if not (proj_dir / "project.json").is_file():
        raise FileNotFoundError(
            f"Project '{name}' not found under {SAVED_PROJECTS_ROOT}."
        )
    return proj_dir


def read_project_meta(name: str) -> dict:
    proj_dir = get_project_path(name)
    return json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))


def update_project_meta(
    project_name: str,
    description: str | None = None,
    **extra_meta: str,
) -> dict:
    """Update ``project.json`` metadata. Returns the updated dict.

    Only *description* and explicit **extra_meta** keys are merged;
    core keys (``name``, ``created_at``) are never overwritten by extras.
    Raises ``FileNotFoundError`` if the project does not exist.
    """
    proj_dir = get_project_path(project_name)
    meta_path = proj_dir / "project.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    if description is not None:
        meta["description"] = description.strip()

    protected = {"name", "display_name", "created_at"}
    for k, v in extra_meta.items():
        if k not in protected:
            meta[k] = v.strip() if isinstance(v, str) else v

    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return meta


def _load_raw_config(name: str) -> dict:
    """Read and migrate ``config.json`` to the current schema, returning the full dict."""
    proj_dir = get_project_path(name)
    path = proj_dir / CONFIG_FILENAME

    if not path.is_file():
        full = _default_full_config()
        _write_full_config(name, full)
        return full

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_full_config()
    if not isinstance(raw, dict):
        return _default_full_config()

    migrated = False

    # Migration 1: flat config (v1) → Dev / System Admin
    if "environments" not in raw:
        legacy = _empty_cred_block()
        for k in _CREDENTIAL_KEYS:
            val = raw.get(k)
            legacy[k] = "" if val is None else str(val).strip()
        raw = _default_full_config()
        raw["environments"]["Dev"]["personas"][DEFAULT_PERSONA] = legacy
        migrated = True

    # Migration 2: env-only config (v2, no "personas" sub-key) → wrap into personas
    envs = raw.get("environments", {})
    for env_name, env_val in list(envs.items()):
        if isinstance(env_val, dict) and "personas" not in env_val:
            creds = _empty_cred_block()
            for k in _CREDENTIAL_KEYS:
                v = env_val.get(k)
                creds[k] = "" if v is None else str(v).strip()
            envs[env_name] = {"personas": {DEFAULT_PERSONA: creds}}
            migrated = True
    raw["environments"] = envs

    # Ensure Jira keys exist (added after initial schema)
    for jk in _JIRA_KEYS:
        if jk not in raw:
            raw[jk] = ""
            migrated = True

    if migrated:
        _write_full_config(name, raw)
    return raw


def read_project_config(
    name: str,
    environment: str = "Dev",
    persona: str = DEFAULT_PERSONA,
) -> dict[str, str]:
    """Load credentials for *environment* / *persona* from ``config.json``.

    Backward-compatible: flat (v1) and env-only (v2) configs are migrated
    automatically on first read.
    """
    raw = _load_raw_config(name)
    env_block = raw.get("environments", {}).get(environment, {})
    personas = env_block.get("personas", {})
    cred = personas.get(persona) or _empty_cred_block()
    out = _empty_cred_block()
    for k in _CREDENTIAL_KEYS:
        val = cred.get(k)
        out[k] = "" if val is None else str(val).strip()
    return out


def _env_has_credentials(env_block: dict) -> bool:
    """Return True if at least one persona in *env_block* has a non-empty credential."""
    personas = env_block.get("personas", {})
    for cred in personas.values():
        if isinstance(cred, dict) and any(
            cred.get(k, "").strip() for k in ("sandbox_url", "username", "password")
        ):
            return True
    return False


def list_environments(name: str) -> list[str]:
    """Return sorted environment names that have at least one configured credential."""
    raw = _load_raw_config(name)
    envs = raw.get("environments", {})
    configured = [k for k, v in envs.items() if _env_has_credentials(v)]
    return sorted(configured) if configured else sorted(envs.keys())


def list_personas(name: str, environment: str = "Dev") -> list[str]:
    """Return sorted persona names for *environment*."""
    raw = _load_raw_config(name)
    env_block = raw.get("environments", {}).get(environment, {})
    return sorted(env_block.get("personas", {}).keys())


def _write_full_config(project_name: str, data: dict) -> Path:
    """Low-level helper — write the entire config dict to ``config.json``."""
    proj_dir = get_project_path(project_name)
    path = proj_dir / CONFIG_FILENAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def delete_environment(project_name: str, environment: str) -> bool:
    """Remove *environment* from the project config. Returns True if deleted."""
    raw = _load_raw_config(project_name)
    envs = raw.get("environments", {})
    if environment not in envs:
        return False
    del envs[environment]
    raw["environments"] = envs
    _write_full_config(project_name, raw)
    return True


def read_jira_config(name: str) -> dict[str, str]:
    """Return project-level Jira/Zephyr settings (not environment-scoped)."""
    raw = _load_raw_config(name)
    out = _empty_jira_block()
    for k in _JIRA_KEYS:
        val = raw.get(k)
        out[k] = "" if val is None else str(val).strip()
    return out


def write_jira_config(
    project_name: str,
    jira_base_url: str = "",
    jira_api_token: str = "",
    jira_project_key: str = "",
) -> Path:
    """Persist Jira/Zephyr settings at the project root level."""
    raw = _load_raw_config(project_name)
    raw["jira_base_url"] = (jira_base_url or "").strip()
    raw["jira_api_token"] = jira_api_token or ""
    raw["jira_project_key"] = (jira_project_key or "").strip()
    return _write_full_config(project_name, raw)


def write_project_credentials(
    project_name: str,
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str = "",
    slack_webhook_url: str = "",
    environment: str = "Dev",
    persona: str = DEFAULT_PERSONA,
) -> Path:
    """Write credentials for a single *environment* / *persona*."""
    _PLACEHOLDER_STRINGS = {
        "https://yourorg--sbx.sandbox.my.salesforce.com/",
        "user@example.com",
        "Optional — leave blank if IP whitelisted",
        "https://hooks.slack.com/services/T.../B.../...",
    }

    def _clean(val: str | None) -> str:
        v = (val or "").strip()
        return "" if v in _PLACEHOLDER_STRINGS else v

    raw = _load_raw_config(project_name)
    envs = raw.setdefault("environments", {})
    env_block = envs.setdefault(environment, {"personas": {}})
    personas = env_block.setdefault("personas", {})
    personas[persona] = {
        "sandbox_url": _clean(sandbox_url),
        "username": _clean(username),
        "password": (password or "").strip(),
        "security_token": _clean(security_token),
        "slack_webhook_url": _clean(slack_webhook_url),
    }
    return _write_full_config(project_name, raw)


# ---------------------------------------------------------------------------
# Test operations
# ---------------------------------------------------------------------------

def test_exists_in_project(project_name: str, test_name: str) -> bool:
    slug = _slugify(test_name) or test_name
    robot_path = _project_dir(project_name) / "Tests" / f"{slug}.robot"
    return robot_path.is_file()


def save_test_to_project(
    project_name: str,
    test_name: str,
    robot_code: str,
    csv_bytes: bytes | None = None,
    overwrite: bool = False,
) -> tuple[Path, Path | None]:
    """
    Save a .robot file (and optional CSV) into the project.

    Raises FileExistsError if the test already exists and overwrite=False.
    Returns (robot_path, csv_path_or_None).
    """
    proj_dir = get_project_path(project_name)
    safe_name = _slugify(test_name) or "test"

    robot_path = proj_dir / "Tests" / f"{safe_name}.robot"
    csv_path: Path | None = None

    if robot_path.is_file() and not overwrite:
        raise FileExistsError(
            f"Test '{safe_name}.robot' already exists in project '{project_name}'. "
            "Set overwrite=True to replace it."
        )

    robot_code = re.sub(r"(\.\./)+Resources/", "../../../Resources/", robot_code)
    robot_path.write_text(robot_code, encoding="utf-8")

    if csv_bytes and csv_bytes.strip():
        csv_path = proj_dir / "Data" / f"{safe_name}.csv"
        csv_path.write_bytes(csv_bytes)

    return robot_path, csv_path


def list_project_tests(project_name: str) -> list[dict]:
    """
    Return test metadata sorted by modification time (newest first).
    Each entry: {name, path, modified, has_csv}
    """
    proj_dir = _project_dir(project_name)
    tests_dir = proj_dir / "Tests"
    data_dir = proj_dir / "Data"

    if not tests_dir.is_dir():
        return []

    results = []
    for robot_file in sorted(
        tests_dir.glob("*.robot"), key=lambda p: -p.stat().st_mtime
    ):
        stem = robot_file.stem
        results.append(
            {
                "name": stem,
                "path": robot_file,
                "modified": datetime.fromtimestamp(robot_file.stat().st_mtime),
                "has_csv": (data_dir / f"{stem}.csv").is_file(),
            }
        )
    return results


def load_test_source(project_name: str, test_name: str) -> str:
    """Read and return the .robot source for a saved test."""
    proj_dir = get_project_path(project_name)
    robot_path = proj_dir / "Tests" / f"{test_name}.robot"
    if not robot_path.is_file():
        raise FileNotFoundError(
            f"Test '{test_name}.robot' not found in project '{project_name}'."
        )
    return robot_path.read_text(encoding="utf-8")


def delete_test_from_project(project_name: str, test_name: str) -> None:
    """Delete a .robot file (and its CSV if present) from the project."""
    proj_dir = get_project_path(project_name)
    robot_path = proj_dir / "Tests" / f"{test_name}.robot"
    csv_path = proj_dir / "Data" / f"{test_name}.csv"
    if robot_path.is_file():
        robot_path.unlink()
    if csv_path.is_file():
        csv_path.unlink()


# ---------------------------------------------------------------------------
# TDM — Test Data Management templates
# ---------------------------------------------------------------------------

DATA_TEMPLATE_FILENAME = "data_template.json"


def read_data_template(project_name: str) -> str:
    """Return the raw JSON string of the project's data template, or ``[]``."""
    proj_dir = get_project_path(project_name)
    path = proj_dir / DATA_TEMPLATE_FILENAME
    if not path.is_file():
        return "[]"
    try:
        text = path.read_text(encoding="utf-8").strip()
        json.loads(text)  # validate
        return text
    except (json.JSONDecodeError, OSError):
        return "[]"


def write_data_template(project_name: str, json_str: str) -> Path:
    """Persist a TDM template JSON to the project directory."""
    proj_dir = get_project_path(project_name)
    json.loads(json_str)  # raises JSONDecodeError on invalid input
    path = proj_dir / DATA_TEMPLATE_FILENAME
    path.write_text(json_str, encoding="utf-8")
    return path
