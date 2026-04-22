"""Map filesystem project slugs to stable UUIDs for JSON-backed features (orgs, stories)."""

from __future__ import annotations

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
