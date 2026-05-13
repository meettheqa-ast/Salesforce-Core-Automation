"""Compatibility persona helpers and lightweight API surface.

This project snapshot removed the previous personas router, but other modules
(notably `routers.runs`) still depend on `load_personas_for_user`. Keep this
module thin and safe so backend startup does not fail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import settings
from ..models.persona import Persona, PersonaPublic
from ..services.auth import get_current_user
from ..services.db import User
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(
    prefix="/personas",
    tags=["personas"],
    dependencies=[Depends(get_current_user)],
)

_store = JsonFileBackend(settings.data_dir)


def load_personas_for_user(current_user: User) -> list[Persona]:
    """Return personas visible to the current user.

    For compatibility, this currently returns all valid persona rows from the
    JSON store. Invalid rows are skipped so malformed legacy data never blocks
    execution flows.
    """
    rows = (_store.read("personas") or {}).get("items", [])
    out: list[Persona] = []
    for row in rows:
        try:
            out.append(Persona.model_validate(row))
        except Exception:
            continue
    return out


@router.get("", response_model=list[PersonaPublic])
def list_personas(current_user: User = Depends(get_current_user)) -> list[PersonaPublic]:
    rows = load_personas_for_user(current_user)
    return [
        PersonaPublic(
            id=p.id,
            project_id=p.project_id,
            org_id=p.org_id,
            name=p.name,
            username=p.username,
            role_profile=p.role_profile,
            is_default=p.is_default,
            creator_user_id=p.creator_user_id,
            visibility=p.visibility,
            credential_version=p.credential_version,
            credentials_updated_at=p.credentials_updated_at,
            default_app=p.default_app,
            is_mine=(p.creator_user_id == current_user.id),
            can_edit_credentials=(p.creator_user_id == current_user.id),
            can_view_username=True,
        )
        for p in rows
    ]
