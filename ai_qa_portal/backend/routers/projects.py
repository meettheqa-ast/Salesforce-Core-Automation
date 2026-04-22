"""Project CRUD and filesystem-backed workspaces (Saved_Projects/)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import project_manager

from ai_qa_portal.backend.project_registry import ensure_project_uuid

from ..models.schemas import ProjectCreate, ProjectMeta, TestInfo


class CredentialsPayload(BaseModel):
    environment: str = "Dev"
    persona: str = "System Admin"
    sandbox_url: str = ""
    username: str = ""
    password: str = ""
    security_token: str = ""
    slack_webhook_url: str = ""

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _ensure_project(name: str) -> None:
    if not project_manager.project_exists(name):
        raise HTTPException(404, f"Project '{name}' not found")


@router.get("", response_model=list[str])
def list_projects():
    return project_manager.list_projects()


@router.get("/registry/{project_name}")
def get_portal_project_id(project_name: str):
    """Stable UUID for JSON-backed features (user stories, orgs). Additive; seeds static tags."""
    _ensure_project(project_name)
    pid = ensure_project_uuid(project_name)
    return {"project_id": str(pid), "slug": project_name}


@router.post("", status_code=201)
def create_project(body: ProjectCreate):
    try:
        proj_dir = project_manager.create_project(body.name, body.description or "")
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
def delete_project(project_name: str):
    if not project_manager.delete_project(project_name):
        raise HTTPException(404, f"Project '{project_name}' not found")


@router.get("/{project_name}", response_model=ProjectMeta)
def get_project_meta(project_name: str):
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
def list_environments(project_name: str):
    _ensure_project(project_name)
    return project_manager.list_environments(project_name)


@router.get("/{project_name}/tests", response_model=list[TestInfo])
def list_tests(project_name: str):
    _ensure_project(project_name)
    rows = project_manager.list_project_tests(project_name)
    return [
        TestInfo(name=r["name"], path=str(Path(r["path"]).resolve()), modified=r["modified"])
        for r in rows
    ]


@router.get("/{project_name}/tests/{test_name}/source")
def get_test_source(project_name: str, test_name: str):
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
):
    try:
        return project_manager.read_project_config(
            project_name, environment=environment, persona=persona
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{project_name}/environments/{environment}/personas", response_model=list[str])
def list_personas_for_environment(project_name: str, environment: str):
    _ensure_project(project_name)
    return project_manager.list_personas(project_name, environment)


@router.put("/{project_name}/credentials")
def save_credentials(project_name: str, body: CredentialsPayload):
    _ensure_project(project_name)
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
def delete_environment(project_name: str, environment: str):
    _ensure_project(project_name)
    if not project_manager.delete_environment(project_name, environment):
        raise HTTPException(404, f"Environment '{environment}' not found")


@router.delete(
    "/{project_name}/environments/{environment}/personas/{persona}",
    status_code=204,
)
def delete_persona(project_name: str, environment: str, persona: str):
    _ensure_project(project_name)
    if not project_manager.delete_persona(project_name, environment, persona):
        raise HTTPException(404, f"Persona '{persona}' not found in '{environment}'")
