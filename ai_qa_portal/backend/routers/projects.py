"""Project CRUD and filesystem-backed workspaces (Saved_Projects/)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

import project_manager

from ai_qa_portal.backend.project_registry import ensure_project_uuid
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    effective_project_role,
    get_current_user,
)
from ai_qa_portal.backend.services.db import (
    ProjectRole,
    User,
    get_db,
    list_memberships_for_user,
    upsert_membership,
)

from ..models.schemas import ProjectCreate, ProjectMeta, TestInfo


class CredentialsPayload(BaseModel):
    environment: str = "Dev"
    persona: str = "System Admin"
    sandbox_url: str = ""
    username: str = ""
    password: str = ""
    security_token: str = ""
    slack_webhook_url: str = ""

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)


def _ensure_project(name: str) -> None:
    if not project_manager.project_exists(name):
        raise HTTPException(404, f"Project '{name}' not found")


def _ensure_can_access_project(
    name: str, user: User, db: Session, required: ProjectRole = ProjectRole.member,
) -> None:
    """Raise 404 if the project doesn't exist OR the user has no role on it
    (404 over 403 to avoid leaking project existence). 403 if the user is a
    member but with insufficient role for the action.
    """
    if not project_manager.project_exists(name):
        raise HTTPException(404, f"Project '{name}' not found")
    assert_project_role_at_least(db, user, name, required)


@router.get("", response_model=list[str])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The user's own projects. For discovering OTHER projects in the org
    (Phase 2c), see GET /api/projects/discoverable."""
    all_names = project_manager.list_projects()
    if current_user.is_admin or current_user.global_role == "admin":
        return all_names
    # Non-admins see projects they're a member of (any role) PLUS legacy
    # owned-by-them projects from before Phase 2a (membership row gets
    # created lazily on next access).
    membership_slugs = {
        m.project_slug for m in list_memberships_for_user(db, current_user.id)
    }
    visible = [
        n for n in all_names
        if n in membership_slugs
        or project_manager.get_project_owner(n) == current_user.id
    ]
    # Lazy backfill: ensure a PM membership exists for any owned-but-unrostered project.
    for n in visible:
        if n not in membership_slugs and project_manager.get_project_owner(n) == current_user.id:
            upsert_membership(db, project_slug=n, user_id=current_user.id, role=ProjectRole.pm)
    return visible


@router.get("/discoverable")
def list_discoverable_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Projects in the org the user is NOT a member of.

    Each entry shows just enough metadata to decide whether to request access:
    name, description, member_count. Per the locked architecture decision
    (gap C5), these are visible to encourage collaboration; access still
    requires a Request Access -> PM/TL approval handshake.
    """
    all_names = set(project_manager.list_projects())
    membership_slugs = {
        m.project_slug for m in list_memberships_for_user(db, current_user.id)
    }
    not_member = sorted(all_names - membership_slugs)
    out = []
    for slug in not_member:
        # Skip legacy owned-by-them projects -- those show up under "My projects".
        if project_manager.get_project_owner(slug) == current_user.id:
            continue
        try:
            meta = project_manager.read_project_meta(slug)
        except FileNotFoundError:
            continue
        # Member count (cheap; the membership table is small).
        from ai_qa_portal.backend.services.db import list_memberships_for_project
        members = list_memberships_for_project(db, slug)
        out.append({
            "name": slug,
            "display_name": meta.get("display_name") or slug,
            "description": meta.get("description") or "",
            "member_count": len(members),
        })
    return out


@router.get("/registry/{project_name}")
def get_portal_project_id(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stable UUID for JSON-backed features (user stories, orgs). Additive; seeds static tags."""
    _ensure_can_access_project(project_name, current_user, db)
    pid = ensure_project_uuid(project_name)
    return {"project_id": str(pid), "slug": project_name}


@router.post("", status_code=201)
def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        proj_dir = project_manager.create_project(
            body.name,
            body.description or "",
            owner_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    slug = proj_dir.name
    project_manager.write_project_credentials(
        project_name=slug,
        sandbox_url=body.sandbox_url or "",
        username=body.username or "",
        password=body.password or "",
        environment=body.environment or "Dev",
        persona=project_manager.DEFAULT_PERSONA,
    )
    project_id = ensure_project_uuid(slug)
    # Creator becomes PM of the project they created (Phase 2a).
    upsert_membership(db, project_slug=slug, user_id=current_user.id, role=ProjectRole.pm)
    return {
        "name": slug,
        "display_name": body.name.strip(),
        "path": str(proj_dir),
        "project_id": str(project_id),
    }


@router.delete("/{project_name}", status_code=204)
def delete_project(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # PM-of-this-project or admin only -- TL/TM cannot delete.
    _ensure_can_access_project(project_name, current_user, db, ProjectRole.pm)
    if not project_manager.delete_project(project_name):
        raise HTTPException(404, f"Project '{project_name}' not found")


@router.get("/{project_name}", response_model=ProjectMeta)
def get_project_meta(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    try:
        meta = project_manager.read_project_meta(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ProjectMeta(
        name=meta.get("name", project_name),
        display_name=meta.get("display_name", ""),
        description=meta.get("description", ""),
        owner=meta.get("owner", ""),
        created_at=str(meta.get("created_at", "")),
    )


@router.get("/{project_name}/environments", response_model=list[str])
def list_environments(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    return project_manager.list_environments(project_name)


@router.get("/{project_name}/tests", response_model=list[TestInfo])
def list_tests(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    rows = project_manager.list_project_tests(project_name)
    return [
        TestInfo(name=r["name"], path=str(Path(r["path"]).resolve()), modified=r["modified"])
        for r in rows
    ]


@router.get("/{project_name}/tests/{test_name}/source")
def get_test_source(
    project_name: str,
    test_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    try:
        source = project_manager.load_test_source(project_name, test_name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"source": source}


@router.get("/{project_name}/config")
def get_project_config(
    project_name: str,
    environment: str = Query("Dev"),
    persona: str = Query("System Admin"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    try:
        return project_manager.read_project_config(
            project_name, environment=environment, persona=persona
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{project_name}/environments/{environment}/personas", response_model=list[str])
def list_personas_for_environment(
    project_name: str,
    environment: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db)
    return project_manager.list_personas(project_name, environment)


@router.put("/{project_name}/credentials")
def save_credentials(
    project_name: str,
    body: CredentialsPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Saving credentials requires Lead+ (Member is read-only).
    _ensure_can_access_project(project_name, current_user, db, ProjectRole.lead)
    env = (body.environment or "Dev").strip() or "Dev"
    persona = (body.persona or project_manager.DEFAULT_PERSONA).strip() or project_manager.DEFAULT_PERSONA
    project_manager.write_project_credentials(
        project_name=project_name,
        sandbox_url=body.sandbox_url or "",
        username=body.username or "",
        password=body.password or "",
        security_token=body.security_token or "",
        slack_webhook_url=body.slack_webhook_url or "",
        environment=env,
        persona=persona,
    )
    return {"environment": env, "persona": persona, "status": "saved"}


@router.delete("/{project_name}/environments/{environment}", status_code=204)
def delete_environment(
    project_name: str,
    environment: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db, ProjectRole.pm)
    if not project_manager.delete_environment(project_name, environment):
        raise HTTPException(404, f"Environment '{environment}' not found")


@router.delete(
    "/{project_name}/environments/{environment}/personas/{persona}",
    status_code=204,
)
def delete_persona(
    project_name: str,
    environment: str,
    persona: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_can_access_project(project_name, current_user, db, ProjectRole.pm)
    if not project_manager.delete_persona(project_name, environment, persona):
        raise HTTPException(404, f"Persona '{persona}' not found in '{environment}'")
