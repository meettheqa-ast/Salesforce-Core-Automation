from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from ai_qa_portal.backend.config import settings
from ..models.org import SalesforceOrg
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(prefix="/orgs", tags=["orgs"])
_store = JsonFileBackend(settings.data_dir)
_KEY = "orgs"


def _load() -> list[dict]:
    return _store.read(_KEY).get("items", [])


def _save(items: list[dict]) -> None:
    _store.write(_KEY, {"items": items})


@router.get("", response_model=list[SalesforceOrg])
def list_orgs(project_id: UUID | None = None):
    items = [SalesforceOrg(**o) for o in _load()]
    if project_id:
        items = [o for o in items if o.project_id == project_id]
    return items


@router.post("", response_model=SalesforceOrg, status_code=201)
def create_org(body: SalesforceOrg):
    items = _load()
    items.append(body.model_dump(mode="json"))
    _save(items)
    return body


@router.get("/{org_id}", response_model=SalesforceOrg)
def get_org(org_id: UUID):
    for o in _load():
        if str(o["id"]) == str(org_id):
            return SalesforceOrg(**o)
    raise HTTPException(404, "Org not found")


@router.delete("/{org_id}", status_code=204)
def delete_org(org_id: UUID):
    items = _load()
    filtered = [o for o in items if str(o["id"]) != str(org_id)]
    if len(filtered) == len(items):
        raise HTTPException(404, "Org not found")
    _save(filtered)
