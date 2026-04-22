from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Persona(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    encrypted_password: str
    role_profile: Optional[str] = None
    is_default: bool = False


class PersonaPublic(BaseModel):
    """API response model -- never exposes the encrypted password."""
    id: UUID
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    role_profile: Optional[str] = None
    is_default: bool = False


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
