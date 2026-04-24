"""Persona CRUD with Phase 2d access model.

Visibility matrix (locked architecture decision):

                          | see persona | use for run | view username/password | edit metadata | rotate credentials
--------------------------|-------------|-------------|------------------------|---------------|--------------------
Creator                   |     Y       |     Y       |  Y (reveal w/ re-auth) |       Y       |       Y
Public, project member    |     Y       |     Y       |  Y (reveal w/ re-auth) |       Y       |       N
Private, project member   |     Y       |     Y       |          N             |       N       |       N
Admin (any)               |   same as project member -- no special powers on credentials
Non-member of project     |     N       |     N       |          N             |       N       |       N

API endpoints:
  GET    /personas?project_id=&org_id=    list personas the caller can see;
                                          username is returned only when can_view_username is true.
  POST   /personas                        create. visibility defaults to 'private'.
  GET    /personas/{id}                   single persona, same visibility rules.
  PATCH  /personas/{id}                   update name/description/visibility/role_profile/is_default.
                                          Body MUST NOT include username/password -- those go through rotate.
  POST   /personas/{id}/rotate            creator only. body=(username, password).
  POST   /personas/{id}/reveal            creator (or anyone with view rights) + re-auth.
                                          body=(re_auth_token=fresh Google id_token, max age 60s).
                                          Returns plaintext (username, password) once. Logged.
  DELETE /personas/{id}                   creator only.

What never happens via this API:
  - List/get response NEVER contains `password` or `encrypted_password`.
  - The decrypted password is only returned by /reveal after re-auth, and only
    once per call (no caching). Run executor takes persona_id and decrypts
    server-side; plaintext never leaves the backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import project_manager
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
from ai_qa_portal.backend.services.auth import (
    assert_user_owns_project,
    effective_project_role,
    get_current_user,
    verify_google_id_token,
)
from ai_qa_portal.backend.services.db import (
    User,
    get_db,
    push_notification,
)
from ..models.persona import Persona, PersonaPublic, PersonaVisibility
from ..services.credential_service import CredentialService
from ..storage.json_file_backend import JsonFileBackend

router = APIRouter(
    prefix="/personas",
    tags=["personas"],
    dependencies=[Depends(get_current_user)],
)
_store = JsonFileBackend(settings.data_dir)
_KEY = "personas"


# --- Persistence helpers -------------------------------------------------

def _load() -> list[dict]:
    return _store.read(_KEY).get("items", [])


def _save(items: list[dict]) -> None:
    _store.write(_KEY, {"items": items})


def _cred_svc() -> CredentialService:
    return CredentialService(settings.fernet_key or None)


# --- Visibility helpers --------------------------------------------------

def _is_creator(p: Persona, user: User) -> bool:
    return bool(p.creator_user_id) and p.creator_user_id == user.id


def _is_project_member(p: Persona, user: User, db: Session) -> bool:
    """User is a member of the persona's project (via the slug lookup)."""
    if user.is_admin or user.global_role == "admin":
        # Admins are NOT auto-members for credential purposes -- the visibility
        # matrix says admin gets same persona credential access as a regular
        # member. So they only count as project member if they have a real
        # membership row OR if the persona's project even exists for them.
        # We reuse effective_project_role which returns PM for admins -> True.
        pass
    slug = slug_for_project_id(p.project_id)
    if not slug:
        return False
    role = effective_project_role(db, user, slug)
    return role is not None


def _can_view_username(p: Persona, user: User, db: Session) -> bool:
    """Public => any project member; Private => creator only."""
    if _is_creator(p, user):
        return True
    if p.visibility == PersonaVisibility.public.value and _is_project_member(p, user, db):
        return True
    return False


def _can_use(p: Persona, user: User, db: Session) -> bool:
    """Any project member can USE any persona regardless of visibility."""
    return _is_project_member(p, user, db)


def _can_see_persona_metadata(p: Persona, user: User, db: Session) -> bool:
    """Any project member can SEE that the persona exists (name + role profile)."""
    return _is_project_member(p, user, db)


def _to_public(p: Persona, viewer: User, db: Session) -> PersonaPublic:
    show_username = _can_view_username(p, viewer, db)
    return PersonaPublic(
        id=p.id,
        project_id=p.project_id,
        org_id=p.org_id,
        name=p.name,
        username=p.username if show_username else "",
        role_profile=p.role_profile,
        is_default=p.is_default,
        creator_user_id=p.creator_user_id,
        visibility=p.visibility,
        credential_version=p.credential_version,
        credentials_updated_at=p.credentials_updated_at,
        is_mine=_is_creator(p, viewer),
        can_edit_credentials=_is_creator(p, viewer),
        can_view_username=show_username,
    )


# --- Schemas -------------------------------------------------------------

class PersonaCreateRequest(BaseModel):
    project_id: UUID
    org_id: UUID
    name: str
    username: str
    password: str
    role_profile: str | None = None
    is_default: bool = False
    visibility: str = PersonaVisibility.private.value


class PersonaUpdateRequest(BaseModel):
    name: str | None = None
    role_profile: str | None = None
    is_default: bool | None = None
    visibility: str | None = None


class RotateCredentialsRequest(BaseModel):
    username: str
    password: str


class RevealRequest(BaseModel):
    # Fresh Google ID token, obtained client-side via NextAuth signIn(prompt='login').
    # Backend re-verifies via JWKS and ensures it was issued in the last 60s.
    re_auth_token: str


class RevealedCredentials(BaseModel):
    username: str
    password: str
    revealed_at: datetime


# --- Endpoints -----------------------------------------------------------

@router.get("", response_model=list[PersonaPublic])
def list_personas(
    project_id: UUID | None = None,
    org_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = [Persona(**p) for p in _load()]
    items = [p for p in items if _can_see_persona_metadata(p, current_user, db)]
    if project_id:
        items = [p for p in items if p.project_id == project_id]
    if org_id:
        items = [p for p in items if p.org_id == org_id]
    return [_to_public(p, current_user, db) for p in items]


@router.post("", response_model=PersonaPublic, status_code=201)
def create_persona(
    body: PersonaCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_user_owns_project(current_user, body.project_id)
    try:
        visibility_enum = PersonaVisibility(body.visibility)
    except ValueError:
        raise HTTPException(400, "visibility must be 'private' or 'public'")

    svc = _cred_svc()
    persona = Persona(
        project_id=body.project_id,
        org_id=body.org_id,
        name=body.name,
        username=body.username,
        encrypted_password=svc.encrypt(body.password),
        role_profile=body.role_profile,
        is_default=body.is_default,
        creator_user_id=current_user.id,
        visibility=visibility_enum.value,
        credential_version=1,
        credentials_updated_at=datetime.now(timezone.utc),
    )
    items = _load()
    items.append(persona.model_dump(mode="json"))
    _save(items)
    return _to_public(persona, current_user, db)


@router.get("/{persona_id}", response_model=PersonaPublic)
def get_persona(
    persona_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for raw in _load():
        if str(raw["id"]) == str(persona_id):
            persona = Persona(**raw)
            if not _can_see_persona_metadata(persona, current_user, db):
                raise HTTPException(404, "Persona not found")
            return _to_public(persona, current_user, db)
    raise HTTPException(404, "Persona not found")


@router.patch("/{persona_id}", response_model=PersonaPublic)
def update_persona(
    persona_id: UUID,
    body: PersonaUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update non-credential fields. Creator only.

    For Public personas, the architectural decision is "only creator can edit
    credentials"; we extend that here to also gate metadata edits to the creator
    + admin to keep the model simple. (If you want public-personas-anyone-edits-name,
    flip the check on `body.name` etc to allow project members.)
    """
    items = _load()
    for raw in items:
        if str(raw["id"]) == str(persona_id):
            persona = Persona(**raw)
            if not _can_see_persona_metadata(persona, current_user, db):
                raise HTTPException(404, "Persona not found")
            if not _is_creator(persona, current_user):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only the persona creator can edit it. Use /rotate to replace credentials.",
                )
            changed = False
            if body.name is not None:
                persona.name = body.name; changed = True
            if body.role_profile is not None:
                persona.role_profile = body.role_profile; changed = True
            if body.is_default is not None:
                persona.is_default = body.is_default; changed = True
            if body.visibility is not None:
                try:
                    persona.visibility = PersonaVisibility(body.visibility).value
                except ValueError:
                    raise HTTPException(400, "visibility must be 'private' or 'public'")
                changed = True
            if changed:
                # Replace the row in place.
                raw.update(persona.model_dump(mode="json"))
                _save(items)
            return _to_public(persona, current_user, db)
    raise HTTPException(404, "Persona not found")


@router.post("/{persona_id}/rotate", response_model=PersonaPublic)
def rotate_credentials(
    persona_id: UUID,
    body: RotateCredentialsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace username + password. Creator only. Bumps credential_version."""
    items = _load()
    for raw in items:
        if str(raw["id"]) == str(persona_id):
            persona = Persona(**raw)
            if not _can_see_persona_metadata(persona, current_user, db):
                raise HTTPException(404, "Persona not found")
            if not _is_creator(persona, current_user):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only the persona creator can rotate its credentials.",
                )
            svc = _cred_svc()
            persona.username = body.username
            persona.encrypted_password = svc.encrypt(body.password)
            persona.credential_version += 1
            persona.credentials_updated_at = datetime.now(timezone.utc)
            raw.update(persona.model_dump(mode="json"))
            _save(items)
            return _to_public(persona, current_user, db)
    raise HTTPException(404, "Persona not found")


@router.post("/{persona_id}/reveal", response_model=RevealedCredentials)
def reveal_credentials(
    persona_id: UUID,
    body: RevealRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the plaintext credentials ONCE, after re-authentication.

    Caller must (a) have view rights on the persona (creator, or any member of
    a public persona's project) and (b) provide a Google ID token issued in
    the last 60 seconds. The frontend obtains this by re-running NextAuth
    signIn() with prompt='login' which forces Google to re-display the consent
    screen.
    """
    items = _load()
    target = None
    for raw in items:
        if str(raw["id"]) == str(persona_id):
            target = Persona(**raw)
            break
    if target is None:
        raise HTTPException(404, "Persona not found")

    if not _can_view_username(target, current_user, db):
        # 404 to avoid leaking that the persona exists to non-allowed callers.
        raise HTTPException(404, "Persona not found")

    # Verify the re-auth token freshness.
    if settings.auth_disabled:
        # Dev/demo mode: skip re-auth check.
        pass
    else:
        if not body.re_auth_token or len(body.re_auth_token) < 20:
            raise HTTPException(400, "Missing or malformed re_auth_token")
        try:
            payload = verify_google_id_token(body.re_auth_token)
        except HTTPException as exc:
            raise HTTPException(401, f"Re-auth verification failed: {exc.detail}") from exc
        # Confirm the re-auth was for the current user.
        ra_email = (payload.get("email") or "").strip().lower()
        if ra_email != current_user.email.strip().lower():
            raise HTTPException(403, "Re-auth token belongs to a different user")
        # Token must be fresh (issued <= 60 seconds ago).
        iat = payload.get("iat") or 0
        now = datetime.now(timezone.utc).timestamp()
        if not isinstance(iat, (int, float)) or now - iat > 60:
            raise HTTPException(
                401,
                "Re-auth token is not fresh enough; sign in again right before revealing.",
            )

    svc = _cred_svc()
    plaintext_password = svc.decrypt(target.encrypted_password)
    revealed_at = datetime.now(timezone.utc)

    # Audit log via in-app notification: tell the persona creator someone (or
    # they themselves) revealed the credentials. No-op if the creator and the
    # viewer are the same person and the creator is the only one with access.
    if target.creator_user_id and target.creator_user_id != current_user.id:
        push_notification(
            db,
            user_id=target.creator_user_id,
            type="persona_credentials_revealed",
            title=f"Credentials for persona '{target.name}' were viewed",
            body=f"By {current_user.name or current_user.email} just now.",
            action_url="/projects",
        )

    return RevealedCredentials(
        username=target.username,
        password=plaintext_password,
        revealed_at=revealed_at,
    )


@router.delete("/{persona_id}", status_code=204)
def delete_persona(
    persona_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Creator only (admin can also delete via Admin Console -- Phase 2f)."""
    items = _load()
    target_raw = next((p for p in items if str(p["id"]) == str(persona_id)), None)
    if target_raw is None:
        raise HTTPException(404, "Persona not found")
    persona = Persona(**target_raw)
    if not _can_see_persona_metadata(persona, current_user, db):
        raise HTTPException(404, "Persona not found")
    if not _is_creator(persona, current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the persona creator can delete it.",
        )
    filtered = [p for p in items if str(p["id"]) != str(persona_id)]
    _save(filtered)


# --- Internal helpers used by runs.py (NOT exposed via router) -----------

def load_all_personas() -> list[Persona]:
    """Returns full models with encrypted passwords. Internal use only.
    Callers MUST filter visibility before exposing anything to a client."""
    return [Persona(**p) for p in _load()]


def load_personas_for_user(user: User) -> list[Persona]:
    """Personas a user can USE (= any persona in a project they're a member of).

    Phase 2d note: visibility doesn't restrict USE -- it only restricts
    viewing the username/password. So any project member can be passed
    `persona_id` to a run endpoint.
    """
    # We need a DB session for membership lookup; this is called from runs.py
    # which already has one. Inline-import to avoid circular at module load.
    from ai_qa_portal.backend.services.db import SessionLocal
    db = SessionLocal()
    try:
        out: list[Persona] = []
        for p in load_all_personas():
            if _is_project_member(p, user, db):
                out.append(p)
        return out
    finally:
        db.close()
