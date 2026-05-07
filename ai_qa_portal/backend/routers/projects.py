"""Project CRUD and filesystem-backed workspaces (Saved_Projects/)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

import project_manager

from ai_qa_portal.backend.config import settings
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
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

from ..models.schemas import ProjectCreate, ProjectMeta, TestInfo
from ..models.test_case import TestCase
from ..models.user_story import UserStory


_store = JsonFileBackend(settings.data_dir)


class CredentialsPayload(BaseModel):
    environment: str = "Dev"
    persona: str = "System Admin"
    sandbox_url: str = ""
    username: str = ""
    password: str = ""
    security_token: str = ""
    slack_webhook_url: str = ""
    # Salesforce app this persona should land in by default. Mirrored into
    # Persona.default_app on sync; injected at run time as
    # ${salesAutomationAppName}. Empty string keeps the project-wide default.
    default_app: str = ""

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

    Admin short-circuit: admins (is_admin or global_role=='admin') already
    have implicit PM rights on every project and see them all under "My
    projects" via list_projects(). Returning [] here prevents the projects
    page from double-listing those projects under "Other projects in the
    org" with a misleading "Request access" CTA.
    """
    if current_user.is_admin or current_user.global_role == "admin":
        return []
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


# --- Test cases (project-wide view, grouped by story) ------------------------


class _TestCaseRow(BaseModel):
    """Trim of `models.test_case.TestCase` for the project overview.
    Excludes fields the project page doesn't need (project_id duplicates
    the URL slug, created_at is implied by the parent story)."""
    id: str
    title: str
    status: str
    stale: bool
    tags: list[str]
    script_path: Optional[str] = None
    script_built_at: Optional[datetime] = None
    heal_attempts: int = 0


class _StoryGroup(BaseModel):
    id: str
    title: str
    version: int
    test_cases: list[_TestCaseRow]


class _ProjectTestCasesResponse(BaseModel):
    project_id: str
    total: int
    by_status: dict[str, int]
    scripts_built: int
    stories: list[_StoryGroup]


@router.get("/{project_name}/test-cases", response_model=_ProjectTestCasesResponse)
def list_project_test_cases(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return every test case for this project, grouped by user story.

    Why this exists: the legacy ``/api/projects/{name}/tests`` endpoint
    only sees ``.robot`` files on disk (after the recursive-glob fix --
    previously it missed every AI-generated file under
    ``Tests/Generated/story_*/``). That's a fine view of "what has a
    materialised script", but the project detail page also needs to
    answer "what test cases exist for this project, in what state, and
    do they have scripts built yet?" -- which lives in the JSON store
    keyed by ``test_case_ids_project:<UUID>``.
    """
    _ensure_can_access_project(project_name, current_user, db)
    project_id = ensure_project_uuid(project_name)

    # All test cases for this project via the per-project index.
    tc_rows = []
    pidx = _store.read(f"test_case_ids_project:{project_id}")
    for tid in pidx.get("ids", []):
        row = _store.read(f"test_case:{tid}")
        if row:
            tc_rows.append(row)

    test_cases = [TestCase.model_validate(r) for r in tc_rows]

    # Resolve story metadata so the UI can render "<Story title> v1" headers
    # without N round-trips. Every test case carries user_story_id, so a
    # single deduplicated lookup covers them all.
    story_ids = {tc.user_story_id for tc in test_cases}
    stories_by_id: dict[str, UserStory] = {}
    for sid in story_ids:
        try:
            story_row = _store.get_user_story(sid)
        except KeyError:
            continue
        story = UserStory.model_validate(story_row)
        stories_by_id[str(story.id)] = story

    # Group + sort. Stories: newest-first by created_at. Within a story:
    # approved first, then draft, then rejected; stale flagged but kept
    # in place so users can see the staleness chip next to its peers.
    status_order = {"approved": 0, "draft": 1, "rejected": 2}
    groups_by_story: dict[str, list[TestCase]] = {}
    for tc in test_cases:
        groups_by_story.setdefault(str(tc.user_story_id), []).append(tc)

    groups: list[_StoryGroup] = []
    for story_id_str, tcs in groups_by_story.items():
        story = stories_by_id.get(story_id_str)
        if story is None:
            # Test cases for a story that's been deleted -- skip silently.
            # The orphan rows can be cleaned up via the test-case PATCH
            # endpoint or a future janitor.
            continue
        tcs.sort(key=lambda t: (status_order.get(t.status.value, 99), t.title.lower()))
        groups.append(
            _StoryGroup(
                id=story_id_str,
                title=story.title,
                version=story.version,
                test_cases=[
                    _TestCaseRow(
                        id=str(t.id),
                        title=t.title,
                        status=t.status.value,
                        stale=t.stale,
                        tags=list(t.tags or []),
                        script_path=t.script_path,
                        script_built_at=t.script_built_at,
                        heal_attempts=int(getattr(t, "heal_attempts", 0) or 0),
                    )
                    for t in tcs
                ],
            )
        )
    groups.sort(
        key=lambda g: (
            stories_by_id[g.id].created_at if g.id in stories_by_id else datetime.min
        ),
        reverse=True,
    )

    by_status = {"draft": 0, "approved": 0, "rejected": 0, "stale": 0}
    scripts_built = 0
    for tc in test_cases:
        if tc.stale:
            by_status["stale"] += 1
        else:
            by_status[tc.status.value] = by_status.get(tc.status.value, 0) + 1
        if tc.script_path:
            scripts_built += 1

    return _ProjectTestCasesResponse(
        project_id=str(project_id),
        total=len(test_cases),
        by_status=by_status,
        scripts_built=scripts_built,
        stories=groups,
    )


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
        default_app=body.default_app or "",
    )
    # If a portal-side Persona row already exists for this env+persona, keep
    # it in sync so the bulk runner picks the right ${salesAutomationAppName}
    # without needing the user to re-edit through /personas. Best-effort -- we
    # never want a credentials save to fail because the portal mirror is off.
    try:
        from ai_qa_portal.backend.project_registry import ensure_project_uuid
        from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend
        from ai_qa_portal.backend.config import settings as _settings
        proj_uuid = ensure_project_uuid(project_name)
        store = JsonFileBackend(_settings.data_dir)
        items = store.read("personas").get("items", [])
        new_app = (body.default_app or "").strip() or None
        changed = False
        for raw in items:
            if (
                str(raw.get("project_id")) == str(proj_uuid)
                and raw.get("name") == persona
            ):
                if raw.get("default_app") != new_app:
                    raw["default_app"] = new_app
                    changed = True
        if changed:
            store.write("personas", {"items": items})
    except Exception:  # pylint: disable=broad-exception-caught
        pass
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
