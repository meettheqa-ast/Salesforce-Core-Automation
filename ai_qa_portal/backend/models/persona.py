from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class PersonaVisibility(str, enum.Enum):
    private = "private"   # only creator can view username/password; others can use
    public = "public"     # all members can view username/password and use


class Persona(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    encrypted_password: str
    role_profile: Optional[str] = None
    is_default: bool = False
    # Renamed from owner_user_id in Phase 2d for clarity (still read-back-compatible
    # via the migration script). The creator is the one who can edit credentials.
    creator_user_id: str = ""
    # Phase 2d: who can see the username/password for this persona.
    # All project members can USE any persona regardless of visibility (the
    # password is only ever decrypted server-side, never returned to non-creators).
    visibility: str = PersonaVisibility.private.value
    # Bumps every time the password is rotated. Lets the UI / audit log notice
    # rotations and invalidate any cached "last revealed at" prompts.
    credential_version: int = 1
    credentials_updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_owner_field(cls, data: Any) -> Any:
        """Personas saved before Phase 2d use `owner_user_id`. Map it onto
        `creator_user_id` if the new field is absent. Also default visibility
        for legacy rows to `private` (least-privilege)."""
        if isinstance(data, dict):
            if not data.get("creator_user_id"):
                legacy = data.get("owner_user_id")
                if legacy:
                    data["creator_user_id"] = legacy
            data.setdefault("visibility", PersonaVisibility.private.value)
            data.setdefault("credential_version", 1)
        return data


class PersonaPublic(BaseModel):
    """API response shape -- NEVER includes the encrypted_password.

    `username` is omitted by the router for private personas the caller doesn't
    own (set to empty string in that case). The model itself accepts it so
    the creator's view round-trips correctly.
    """
    id: UUID
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    role_profile: Optional[str] = None
    is_default: bool = False
    creator_user_id: str = ""
    visibility: str = PersonaVisibility.private.value
    credential_version: int = 1
    credentials_updated_at: Optional[datetime] = None
    # Convenience flags computed by the router so the UI doesn't have to recompute.
    is_mine: bool = False
    can_edit_credentials: bool = False  # only the creator
    can_view_username: bool = False     # creator OR (visibility=public AND any member)


class RunRequest(BaseModel):
    project_id: UUID
    org_id: UUID
    prompt: str
    persona_id: Optional[UUID] = None


class RunResponse(BaseModel):
    run_id: UUID
    resolved_persona: str
    resolution_method: str
    status: str
    log_url: Optional[str] = None
