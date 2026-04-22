from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_qa_portal.backend.config import settings
from ..models.persona import Persona, PersonaPublic
from ..services.credential_service import CredentialService
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(prefix="/personas", tags=["personas"])
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
    )


@router.get("", response_model=list[PersonaPublic])
def list_personas(project_id: UUID | None = None, org_id: UUID | None = None):
    items = [Persona(**p) for p in _load()]
    if project_id:
        items = [p for p in items if p.project_id == project_id]
    if org_id:
        items = [p for p in items if p.org_id == org_id]
    return [_to_public(p) for p in items]


@router.post("", response_model=PersonaPublic, status_code=201)
def create_persona(body: PersonaCreateRequest):
    svc = _cred_svc()
    persona = Persona(
        project_id=body.project_id,
        org_id=body.org_id,
        name=body.name,
        username=body.username,
        encrypted_password=svc.encrypt(body.password),
        role_profile=body.role_profile,
        is_default=body.is_default,
    )
    items = _load()
    items.append(persona.model_dump(mode="json"))
    _save(items)
    return _to_public(persona)


@router.get("/{persona_id}", response_model=PersonaPublic)
def get_persona(persona_id: UUID):
    for p in _load():
        if str(p["id"]) == str(persona_id):
            return _to_public(Persona(**p))
    raise HTTPException(404, "Persona not found")


@router.delete("/{persona_id}", status_code=204)
def delete_persona(persona_id: UUID):
    items = _load()
    filtered = [p for p in items if str(p["id"]) != str(persona_id)]
    if len(filtered) == len(items):
        raise HTTPException(404, "Persona not found")
    _save(filtered)


def load_all_personas() -> list[Persona]:
    """Internal helper for persona resolution -- returns full models with encrypted passwords."""
    return [Persona(**p) for p in _load()]
