"""Map filesystem project slugs to stable UUIDs for JSON-backed features (orgs, stories)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)


def ensure_project_uuid(slug: str) -> UUID:
    """Return persistent UUID for a Saved_Projects slug; seed static tags on first use."""
    key = f"project_registry_{slug}"
    row = _store.read(key)
    if row.get("project_id"):
        return UUID(str(row["project_id"]))
    pid = uuid4()
    _store.write(key, {"project_id": str(pid), "slug": slug})
    _store.seed_static_tags(pid)
    return pid


def slug_for_project_id(project_id: UUID | str) -> str | None:
    """Reverse lookup: return the project slug for a given project UUID, or None.

    Scans the registry files (one per project) since we don't keep a reverse index.
    O(N) on number of projects -- fine for the dozens-of-projects scale this app
    runs at; revisit if it ever grows to thousands.
    """
    target = str(project_id)
    data_dir = Path(settings.data_dir)
    if not data_dir.is_dir():
        return None
    for p in data_dir.glob("project_registry_*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(row.get("project_id")) == target:
            return row.get("slug")
    return None
