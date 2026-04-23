"""Project CRUD and filesystem-backed workspaces (Saved_Projects/)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import project_manager

from ai_qa_portal.backend.project_registry import ensure_project_uuid
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import User

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


def _ensure_can_access_project(name: str, user: User) -> None:
    """Raise 404 if the project doesn't exist, 403 if it exists but the user
    isn't its owner (and isn't admin). 404 over 403 on missing keeps URL
    enumeration from leaking project names."""
    if not project_manager.project_exists(name):
        raise HTTPException(404, f"Project '{name}' not found")
    if user.is_admin:
        return
    owner = project_manager.get_project_owner(name)
    if owner and owner != user.id:
        raise HTTPException(403, "You do not have access to this project")


@router.get("", response_model=list[str])
def list_projects(current_user: User = Depends(get_current_user)):
    all_names = project_manager.list_projects()
    if current_user.is_admin:
        return all_names
    # Non-admins see only projects whose stamped owner matches them.
    # Legacy unowned projects (empty owner_user_id) are admin-only until the
    # seed-and-claim migration assigns them.
    return [n for n in all_names if project_manager.get_project_owner(n) == current_user.id]


@router.get("/registry/{project_name}")
def get_portal_project_id(project_name: str, current_user: User = Depends(get_current_user)):
    """Stable UUID for JSON-backed features (user stories, orgs). Additive; seeds static tags."""
    _ensure_can_access_project(project_name, current_user)
    pid = ensure_project_uuid(project_name)
    return {"project_id": str(pid), "slug": project_name}


@router.post("", status_code=201)
def create_project(body: ProjectCreate, current_user: User = Depends(get_current_user)):
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
    return {
        "name": slug,
        "display_name": body.name.strip(),
        "path": str(proj_dir),
        "project_id": str(project_id),
    }


@router.delete("/{project_name}", status_code=204)
def delete_project(project_name: str, current_user: User = Depends(get_current_user)):
    _ensure_can_access_project(project_name, current_user)
    if not project_manager.delete_project(project_name):
        raise HTTPException(404, f"Project '{project_name}' not found")


@router.get("/{project_name}", response_model=ProjectMeta)
def get_project_meta(project_name: str, current_user: User = Depends(get_current_user)):
    _ensure_can_access_project(project_name, current_user)
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
def list_environments(project_name: str, current_user: User = Depends(get_current_user)):
    _ensure_can_access_project(project_name, current_user)
    return project_manager.list_environments(project_name)


@router.get("/{project_name}/tests", response_model=list[TestInfo])
def list_tests(project_name: str, current_user: User = Depends(get_current_user)):
    _ensure_can_access_project(project_name, current_user)
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
):
    _ensure_can_access_project(project_name, current_user)
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
):
    _ensure_can_access_project(project_name, current_user)
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
):
    _ensure_can_access_project(project_name, current_user)
    return project_manager.list_personas(project_name, environment)


@router.put("/{project_name}/credentials")
def save_credentials(
    project_name: str,
    body: CredentialsPayload,
    current_user: User = Depends(get_current_user),
):
    _ensure_can_access_project(project_name, current_user)
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
):
    _ensure_can_access_project(project_name, current_user)
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
):
    _ensure_can_access_project(project_name, current_user)
    if not project_manager.delete_persona(project_name, environment, persona):
        raise HTTPException(404, f"Persona '{persona}' not found in '{environment}'")
