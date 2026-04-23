from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import (
    assert_user_owns_project,
    get_current_user,
)
from ai_qa_portal.backend.services.db import User
from ..models.org import SalesforceOrg
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(
    prefix="/orgs",
    tags=["orgs"],
    dependencies=[Depends(get_current_user)],
)
_store = JsonFileBackend(settings.data_dir)
_KEY = "orgs"


def _load() -> list[dict]:
    return _store.read(_KEY).get("items", [])


def _save(items: list[dict]) -> None:
    _store.write(_KEY, {"items": items})


def _user_can_see(o: SalesforceOrg, user: User) -> bool:
    if user.is_admin:
        return True
    return bool(o.owner_user_id) and o.owner_user_id == user.id


@router.get("", response_model=list[SalesforceOrg])
def list_orgs(
    project_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
):
    items = [SalesforceOrg(**o) for o in _load()]
    items = [o for o in items if _user_can_see(o, current_user)]
    if project_id:
        items = [o for o in items if o.project_id == project_id]
    return items


@router.post("", response_model=SalesforceOrg, status_code=201)
def create_org(body: SalesforceOrg, current_user: User = Depends(get_current_user)):
    # Block creating an org under someone else's project.
    assert_user_owns_project(current_user, body.project_id)
    body.owner_user_id = current_user.id
    items = _load()
    items.append(body.model_dump(mode="json"))
    _save(items)
    return body


@router.get("/{org_id}", response_model=SalesforceOrg)
def get_org(org_id: UUID, current_user: User = Depends(get_current_user)):
    for o in _load():
        if str(o["id"]) == str(org_id):
            org = SalesforceOrg(**o)
            if not _user_can_see(org, current_user):
                raise HTTPException(404, "Org not found")
            return org
    raise HTTPException(404, "Org not found")


@router.delete("/{org_id}", status_code=204)
def delete_org(org_id: UUID, current_user: User = Depends(get_current_user)):
    items = _load()
    target = next((o for o in items if str(o["id"]) == str(org_id)), None)
    if target is None:
        raise HTTPException(404, "Org not found")
    if not _user_can_see(SalesforceOrg(**target), current_user):
        raise HTTPException(404, "Org not found")
    filtered = [o for o in items if str(o["id"]) != str(org_id)]
    _save(filtered)
