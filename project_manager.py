"""
Project Workspace Manager.

Manages named project directories under Saved_Projects/ at the repo root.
Each project has:
  project.json  — metadata (name, created_at, description)
  config.json   — sandbox_url, username, password (local desktop use; empty strings allowed)
  Tests/        — generated .robot files
  Data/         — uploaded CSV test-data files

Pure Python — no Streamlit dependency.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAVED_PROJECTS_ROOT = ROOT / "Saved_Projects"

CONFIG_FILENAME = "config.json"
DEFAULT_PROJECT_CONFIG: dict[str, str] = {
    "sandbox_url": "",
    "username": "",
    "password": "",
    "security_token": "",
}


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
    write_project_credentials(slug, "", "", "")
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


def read_project_config(name: str) -> dict[str, str]:
    """
    Load ``config.json`` for a project. Missing file or bad JSON yields empty strings
    and writes a fresh ``config.json`` when the file was missing.
    """
    proj_dir = get_project_path(name)
    path = proj_dir / CONFIG_FILENAME
    if not path.is_file():
        write_project_credentials(name, "", "", "")
        return DEFAULT_PROJECT_CONFIG.copy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return DEFAULT_PROJECT_CONFIG.copy()
    if not isinstance(raw, dict):
        return DEFAULT_PROJECT_CONFIG.copy()
    out = DEFAULT_PROJECT_CONFIG.copy()
    for key in out:
        val = raw.get(key)
        out[key] = "" if val is None else str(val).strip()
    return out


def write_project_credentials(
    project_name: str,
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str = "",
) -> Path:
    """Overwrite ``config.json`` with credential fields (empty strings allowed)."""
    proj_dir = get_project_path(project_name)
    data = {
        "sandbox_url": (sandbox_url or "").strip(),
        "username": (username or "").strip(),
        "password": password or "",
        "security_token": security_token or "",
    }
    path = proj_dir / CONFIG_FILENAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
