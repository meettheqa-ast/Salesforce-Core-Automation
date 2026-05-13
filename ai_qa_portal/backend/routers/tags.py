"""Project tag listing endpoint."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.models.tag import TagScope
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)

router = APIRouter(
    prefix="/tags",
    tags=["tags"],
    dependencies=[Depends(get_current_user)],
)


@router.get("")
def list_tags(project_id: UUID = Query(...)):
    _store.seed_static_tags(project_id)
    rows = _store.list_tags(project_id)
    rows.sort(key=lambda r: (r.get("scope") != "static", str(r.get("name", "")).lower()))
    return rows


@router.post("")
def create_tag(body: dict):
    raw_project_id = str(body.get("project_id") or "").strip()
    name = str(body.get("name") or "").strip()
    color = str(body.get("color") or "#64748b").strip() or "#64748b"
    if not raw_project_id:
        raise HTTPException(422, "project_id is required")
    if not name:
        raise HTTPException(422, "name is required")
    try:
        project_id = UUID(raw_project_id)
    except ValueError as exc:
        raise HTTPException(422, "project_id must be a UUID") from exc

    _store.seed_static_tags(project_id)
    for row in _store.list_tags(project_id):
        if str(row.get("name", "")).lower() == name.lower():
            return row

    tag_id = uuid5(NAMESPACE_URL, f"custom-tag:{project_id}:{name.lower()}")
    row = {
        "id": str(tag_id),
        "project_id": str(project_id),
        "name": name,
        "color": color,
        "scope": TagScope.custom.value,
    }
    _store.save_tag(row)
    return row


@router.delete("/{tag_id}")
def delete_tag(tag_id: UUID, project_id: UUID = Query(...)):
    _store.seed_static_tags(project_id)
    rows = _store.list_tags(project_id)
    target = next((r for r in rows if str(r.get("id")) == str(tag_id)), None)
    if not target:
        raise HTTPException(404, "Tag not found")
    if target.get("scope") == TagScope.static.value:
        raise HTTPException(400, "Static tags cannot be deleted")
    key = f"tags:{project_id}:{target.get('name')}"
    file_path = _store._path_for(key)  # noqa: SLF001 -- controlled internal helper
    if file_path.is_file():
        file_path.unlink()
    return {"ok": True, "deleted": str(tag_id)}
