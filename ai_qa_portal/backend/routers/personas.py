from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import (
    assert_user_owns_project,
    get_current_user,
)
from ai_qa_portal.backend.services.db import User
from ..models.persona import Persona, PersonaPublic
from ..services.credential_service import CredentialService
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(
    prefix="/personas",
    tags=["personas"],
    dependencies=[Depends(get_current_user)],
)
_store = JsonFileBackend(settings.data_dir)
_KEY = "personas"


class PersonaCreateRequest(BaseModel):
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    password: str
    role_profile: str | None = None
    is_default: bool = False


def _load() -> list[dict]:
    return _store.read(_KEY).get("items", [])


def _save(items: list[dict]) -> None:
    _store.write(_KEY, {"items": items})


def _cred_svc() -> CredentialService:
    return CredentialService(settings.fernet_key or None)


def _to_public(p: Persona) -> PersonaPublic:
    return PersonaPublic(
        id=p.id,
        project_id=p.project_id,
        org_id=p.org_id,
        name=p.name,
        username=p.username,
        role_profile=p.role_profile,
        is_default=p.is_default,
        owner_user_id=p.owner_user_id,
    )


def _user_can_see(p: Persona, user: User) -> bool:
    """Phase 1: each user sees only personas they created. Admin sees all.
    Legacy unowned personas (empty owner_user_id) are admin-only."""
    if user.is_admin:
        return True
    return bool(p.owner_user_id) and p.owner_user_id == user.id


@router.get("", response_model=list[PersonaPublic])
def list_personas(
    project_id: UUID | None = None,
    org_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
):
    items = [Persona(**p) for p in _load()]
    items = [p for p in items if _user_can_see(p, current_user)]
    if project_id:
        items = [p for p in items if p.project_id == project_id]
    if org_id:
        items = [p for p in items if p.org_id == org_id]
    return [_to_public(p) for p in items]


@router.post("", response_model=PersonaPublic, status_code=201)
def create_persona(
    body: PersonaCreateRequest,
    current_user: User = Depends(get_current_user),
):
    # Block cross-user attachment: a non-admin must own the project_id (which
    # implicitly governs the org_id, since orgs live under projects).
    assert_user_owns_project(current_user, body.project_id)
    svc = _cred_svc()
    persona = Persona(
        project_id=body.project_id,
        org_id=body.org_id,
        name=body.name,
        username=body.username,
        encrypted_password=svc.encrypt(body.password),
        role_profile=body.role_profile,
        is_default=body.is_default,
        owner_user_id=current_user.id,
    )
    items = _load()
    items.append(persona.model_dump(mode="json"))
    _save(items)
    return _to_public(persona)


@router.get("/{persona_id}", response_model=PersonaPublic)
def get_persona(persona_id: UUID, current_user: User = Depends(get_current_user)):
    for p in _load():
        if str(p["id"]) == str(persona_id):
            persona = Persona(**p)
            if not _user_can_see(persona, current_user):
                raise HTTPException(404, "Persona not found")
            return _to_public(persona)
    raise HTTPException(404, "Persona not found")


@router.delete("/{persona_id}", status_code=204)
def delete_persona(persona_id: UUID, current_user: User = Depends(get_current_user)):
    items = _load()
    target = next((p for p in items if str(p["id"]) == str(persona_id)), None)
    if target is None:
        raise HTTPException(404, "Persona not found")
    if not _user_can_see(Persona(**target), current_user):
        raise HTTPException(404, "Persona not found")
    filtered = [p for p in items if str(p["id"]) != str(persona_id)]
    _save(filtered)


def load_all_personas() -> list[Persona]:
    """Internal helper for persona resolution -- returns full models with encrypted passwords.

    NOTE: callers that resolve personas server-side (e.g. ``runs.py`` running a
    user's tests) must filter by user themselves. This helper does NOT enforce
    isolation because the resolver needs the full set to handle cases where a
    user is running tests they own using a persona they own.
    """
    return [Persona(**p) for p in _load()]


def load_personas_for_user(user: User) -> list[Persona]:
    """Helper for run/generate routers: returns only personas the *user* can see."""
    return [p for p in load_all_personas() if _user_can_see(p, user)]
