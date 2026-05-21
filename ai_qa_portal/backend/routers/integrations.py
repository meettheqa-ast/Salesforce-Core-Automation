"""Project + org integration endpoints (Jira/GitHub/context/schedules)."""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.models.sprint import Sprint, SprintState
from ai_qa_portal.backend.models.user_story import UserStory, UserStoryStatus
from ai_qa_portal.backend.project_registry import ensure_project_uuid
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    get_current_user,
    require_admin,
)
from ai_qa_portal.backend.services.credential_service import CredentialService
from ai_qa_portal.backend.services.db import ProjectRole, User, get_db
from ai_qa_portal.backend.services.db_models.context import ContextFile, ContextFileRow
from ai_qa_portal.backend.services.db_models.github import (
    GitHubAuthKind,
    GitHubConnection,
    GitHubRepo,
    GitHubScope,
)
from ai_qa_portal.backend.services.db_models.jira import (
    JiraComment,
    JiraConnection,
    JiraIssue,
    JiraProject,
    JiraScope,
    JiraSprint,
)
from ai_qa_portal.backend.services.db_models.schedules import (
    Schedule,
    ScheduleRun,
    ScheduleRunner,
)
from ai_qa_portal.backend.services.db_models.test_data import TestDataRow, TestDataTable
from ai_qa_portal.backend.services.github_sync import push_project_suites, refresh_workflow_yaml
from ai_qa_portal.backend.services.integrations.github import GitHubProvider
from ai_qa_portal.backend.services.integrations.jira import JiraProvider
from ai_qa_portal.backend.services.jira_sync import sync_project
from ai_qa_portal.backend.services.rag_index import (
    enqueue_context_file,
    index_test_data_row,
)
from ai_qa_portal.backend.services.schedule_runner import execute as execute_schedule
from ai_qa_portal.backend.services.scheduler import add_or_replace, delete as scheduler_delete
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)
_cred = CredentialService(settings.fernet_key or None)
_context_dir = Path(settings.data_dir) / "context_files"
_context_dir.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["integrations"])


def _scope_slug(scope: str, slug: str | None = None) -> tuple[str, str]:
    if scope == JiraScope.org.value or scope == GitHubScope.org.value:
        return scope, ""
    return scope, (slug or "").strip()


def _get_jira_connection(db: Session, *, scope: str, slug: str | None = None) -> JiraConnection | None:
    scope_val, slug_val = _scope_slug(scope, slug)
    return (
        db.query(JiraConnection)
        .filter(JiraConnection.scope == scope_val, JiraConnection.project_slug == slug_val)
        .one_or_none()
    )


def _get_github_connection(db: Session, *, scope: str, slug: str | None = None) -> GitHubConnection | None:
    scope_val, slug_val = _scope_slug(scope, slug)
    return (
        db.query(GitHubConnection)
        .filter(GitHubConnection.scope == scope_val, GitHubConnection.project_slug == slug_val)
        .one_or_none()
    )


def _require_project_access(slug: str, user: User, db: Session, role: ProjectRole = ProjectRole.member) -> None:
    assert_project_role_at_least(db, user, slug, role)


def _jira_view(row: JiraConnection | None) -> dict | None:
    if row is None:
        return None
    data = row.to_dict()
    data.pop("encrypted_token", None)
    return data


def _github_view(row: GitHubConnection | None) -> dict | None:
    if row is None:
        return None
    return row.to_dict()


def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# ---- Jira (org defaults) ---------------------------------------------------


@router.get("/integrations/jira/connection")
def get_jira_org_connection(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _jira_view(_get_jira_connection(db, scope=JiraScope.org.value))


@router.put("/integrations/jira/connection")
def upsert_jira_org_connection(
    body: dict,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_jira_connection(db, scope=JiraScope.org.value)
    if row is None:
        row = JiraConnection(scope=JiraScope.org.value, project_slug="")
        db.add(row)
    row.base_url = (body.get("base_url") or "").strip().rstrip("/")
    row.email = (body.get("email") or "").strip().lower()
    token = (body.get("api_token") or "").strip()
    if token:
        row.encrypted_token = _cred.encrypt(token)
    row.default_jira_project_key = (body.get("default_jira_project_key") or "").strip()
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _jira_view(row)


@router.delete("/integrations/jira/connection", status_code=204)
def delete_jira_org_connection(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_jira_connection(db, scope=JiraScope.org.value)
    if row is not None:
        db.delete(row)
        db.commit()


# ---- Jira (project) --------------------------------------------------------


@router.get("/projects/{slug}/integrations/jira/connection")
def get_jira_project_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    project = _get_jira_connection(db, scope=JiraScope.project.value, slug=slug)
    if project:
        return _jira_view(project)
    # Intentionally do not fall back to org-scoped credentials here.
    # The project integrations form should only prefill when a project-level
    # connection is explicitly saved for this slug.
    return None


@router.put("/projects/{slug}/integrations/jira/connection")
def upsert_jira_project_connection(
    slug: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = _get_jira_connection(db, scope=JiraScope.project.value, slug=slug)
    if row is None:
        row = JiraConnection(scope=JiraScope.project.value, project_slug=slug)
        db.add(row)
    row.base_url = (body.get("base_url") or "").strip().rstrip("/")
    row.email = (body.get("email") or "").strip().lower()
    token = (body.get("api_token") or "").strip()
    if token:
        row.encrypted_token = _cred.encrypt(token)
    row.default_jira_project_key = (body.get("default_jira_project_key") or "").strip()
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _jira_view(row)


@router.delete("/projects/{slug}/integrations/jira/connection", status_code=204)
def delete_jira_project_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = _get_jira_connection(db, scope=JiraScope.project.value, slug=slug)
    if row is not None:
        db.delete(row)
        db.commit()


def _active_jira_connection(db: Session, slug: str) -> JiraConnection | None:
    return _get_jira_connection(db, scope=JiraScope.project.value, slug=slug) or _get_jira_connection(
        db, scope=JiraScope.org.value
    )


@router.post("/projects/{slug}/integrations/jira/test")
def jira_test_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    provider = JiraProvider(
        base_url=conn.base_url,
        email=conn.email,
        api_token=_cred.decrypt(conn.encrypted_token),
    )
    with provider:
        me = provider.test_connection()
    return {
        "ok": True,
        "account_id": me.get("accountId"),
        "display_name": me.get("displayName"),
        "email": me.get("emailAddress"),
    }


@router.get("/projects/{slug}/integrations/jira/projects")
def jira_list_projects(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    provider = JiraProvider(
        base_url=conn.base_url,
        email=conn.email,
        api_token=_cred.decrypt(conn.encrypted_token),
    )
    with provider:
        projects = provider.list_projects()
    return [
        {
            "id": str(p.get("id") or ""),
            "key": str(p.get("key") or ""),
            "name": str(p.get("name") or ""),
            "project_type_key": p.get("projectTypeKey"),
            "lead": (p.get("lead") or {}).get("displayName"),
        }
        for p in projects
    ]


@router.post("/projects/{slug}/integrations/jira/sync")
def jira_sync_now(
    slug: str,
    jira_project_key: str = Query(...),
    include_comments: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    provider = JiraProvider(
        base_url=conn.base_url,
        email=conn.email,
        api_token=_cred.decrypt(conn.encrypted_token),
    )
    try:
        with provider:
            stats = sync_project(
                db,
                connection=conn,
                jira_project_key=jira_project_key,
                provider=provider,
                include_comments=include_comments,
                portal_project_slug=slug,
                index_for_rag=True,
            )
    except Exception as exc:  # noqa: BLE001 - surface external API failures clearly
        # Include connection_id + jira_project_key in the detail so we can
        # debug from the UI banner alone (the previous shape only showed
        # the underlying httpx error, with no hint of which connection /
        # project the failure belonged to).
        raise HTTPException(
            502,
            (
                f"Jira sync failed for project={jira_project_key!r} "
                f"connection_id={conn.id}: {exc}"
            ),
        ) from exc
    return stats.to_dict()


@router.get("/projects/{slug}/integrations/jira/sprints")
def jira_synced_sprints(
    slug: str,
    jira_project_key: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        return []
    q = db.query(JiraSprint).filter(JiraSprint.connection_id == conn.id)
    if jira_project_key:
        q = q.filter(JiraSprint.jira_project_key == jira_project_key)
    rows = q.order_by(JiraSprint.start_date.desc().nullslast()).all()
    return [r.to_dict() for r in rows]


@router.get("/projects/{slug}/integrations/jira/issues")
def jira_synced_issues(
    slug: str,
    jira_project_key: str | None = Query(None),
    sprint_jira_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        return []
    q = db.query(JiraIssue).filter(JiraIssue.connection_id == conn.id)
    if jira_project_key:
        q = q.filter(JiraIssue.jira_project_key == jira_project_key)
    if sprint_jira_id:
        q = q.filter(JiraIssue.sprint_jira_id == sprint_jira_id)
    rows = q.order_by(JiraIssue.jira_updated_at.desc().nullslast()).offset(offset).limit(limit).all()
    return [r.to_dict() for r in rows]


# --- Jira mirror row deletes ------------------------------------------
#
# Two-step lifecycle does NOT apply here -- synced rows are a local
# mirror of Atlassian, so "delete" means "remove from the mirror". The
# next /sync re-pulls anything the user removed (and updates anything
# they kept). The button on the frontend is labelled "Remove from
# mirror" so users don't think it's a destructive Jira-side delete.
#
# Scope: row's ``connection_id`` MUST match the active connection for
# the slug, so a project lead can't delete another tenant's rows even
# if they craft the request manually.

class _JiraRowDeleteBody(BaseModel):
    ids: list[str]


@router.delete("/projects/{slug}/integrations/jira/sprints/{jira_sprint_row_id}", status_code=200)
def jira_delete_synced_sprint(
    slug: str,
    jira_sprint_row_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    row = (
        db.query(JiraSprint)
        .filter(JiraSprint.id == jira_sprint_row_id, JiraSprint.connection_id == conn.id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Jira sprint not found for this connection")
    db.delete(row)
    db.commit()
    return {"id": jira_sprint_row_id, "status": "deleted"}


@router.post("/projects/{slug}/integrations/jira/sprints/bulk-delete", status_code=200)
def jira_bulk_delete_synced_sprints(
    slug: str,
    body: _JiraRowDeleteBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    if not body.ids:
        return {"deleted": 0, "skipped": 0}
    # Constrain to this connection so a malformed request can't reach
    # another tenant's mirrored rows.
    rows = (
        db.query(JiraSprint)
        .filter(JiraSprint.connection_id == conn.id, JiraSprint.id.in_(body.ids))
        .all()
    )
    deleted_ids = [r.id for r in rows]
    for r in rows:
        db.delete(r)
    db.commit()
    skipped = [i for i in body.ids if i not in set(deleted_ids)]
    return {"deleted": len(deleted_ids), "deleted_ids": deleted_ids, "skipped": skipped}


@router.delete("/projects/{slug}/integrations/jira/issues/{jira_issue_row_id}", status_code=200)
def jira_delete_synced_issue(
    slug: str,
    jira_issue_row_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    row = (
        db.query(JiraIssue)
        .filter(JiraIssue.id == jira_issue_row_id, JiraIssue.connection_id == conn.id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Jira issue not found for this connection")
    # jira_comments has no FK to jira_issues -- clean them explicitly so
    # the next sync starts from a known-empty state for this issue.
    db.query(JiraComment).filter(JiraComment.issue_jira_id == row.jira_id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"id": jira_issue_row_id, "status": "deleted"}


@router.post("/projects/{slug}/integrations/jira/issues/bulk-delete", status_code=200)
def jira_bulk_delete_synced_issues(
    slug: str,
    body: _JiraRowDeleteBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_jira_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No Jira connection configured")
    if not body.ids:
        return {"deleted": 0, "skipped": 0}
    rows = (
        db.query(JiraIssue)
        .filter(JiraIssue.connection_id == conn.id, JiraIssue.id.in_(body.ids))
        .all()
    )
    deleted_ids = [r.id for r in rows]
    issue_jira_ids = [r.jira_id for r in rows]
    if issue_jira_ids:
        db.query(JiraComment).filter(
            JiraComment.issue_jira_id.in_(issue_jira_ids),
        ).delete(synchronize_session=False)
    for r in rows:
        db.delete(r)
    db.commit()
    skipped = [i for i in body.ids if i not in set(deleted_ids)]
    return {"deleted": len(deleted_ids), "deleted_ids": deleted_ids, "skipped": skipped}


@router.post("/projects/{slug}/integrations/jira/import")
def jira_import_to_portal(
    slug: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    project_id = ensure_project_uuid(slug)
    sprint_ids = list(body.get("sprint_jira_ids") or [])
    issue_ids = list(body.get("issue_jira_ids") or [])

    imported_sprints: list[str] = []
    imported_stories: list[str] = []
    jira_sprints = (
        db.query(JiraSprint).filter(JiraSprint.jira_id.in_(sprint_ids)).all() if sprint_ids else []
    )
    for js in jira_sprints:
        if js.portal_sprint_id:
            imported_sprints.append(js.portal_sprint_id)
            continue
        sprint = Sprint(
            id=uuid4(),
            project_id=project_id,
            name=js.name,
            goal=js.goal or None,
            state=SprintState.active if js.state == "active" else SprintState.planned,
            start_date=js.start_date.date() if js.start_date else None,
            end_date=js.end_date.date() if js.end_date else None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            owner_user_id=current_user.id,
            external_id=js.jira_id,
            external_source="jira",
            external_payload=js.payload,
        )
        _store.save_sprint(sprint.model_dump(mode="json"))
        js.portal_sprint_id = str(sprint.id)
        imported_sprints.append(str(sprint.id))
    db.commit()

    jira_issues = (
        db.query(JiraIssue).filter(JiraIssue.jira_id.in_(issue_ids)).all() if issue_ids else []
    )
    for ji in jira_issues:
        if ji.portal_story_id:
            imported_stories.append(ji.portal_story_id)
            continue
        sprint_uuid = None
        if ji.sprint_jira_id:
            sprint_map = (
                db.query(JiraSprint)
                .filter(JiraSprint.connection_id == ji.connection_id, JiraSprint.jira_id == ji.sprint_jira_id)
                .one_or_none()
            )
            if sprint_map and sprint_map.portal_sprint_id:
                sprint_uuid = UUID(sprint_map.portal_sprint_id)
        story = UserStory(
            id=uuid4(),
            project_id=project_id,
            title=ji.summary or ji.jira_key,
            description=ji.description or "",
            status=UserStoryStatus.active,
            version=1,
            prev_version_id=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            owner_user_id=current_user.id,
            sprint_id=sprint_uuid,
            external_id=ji.jira_id,
            external_source="jira",
            external_url=None,
            external_payload=ji.payload,
            last_synced_at=datetime.now(UTC),
        )
        _store.save_user_story(story.model_dump(mode="json"))
        ji.portal_story_id = str(story.id)
        imported_stories.append(str(story.id))
    db.commit()
    return {"imported_sprint_ids": imported_sprints, "imported_story_ids": imported_stories}


# ---- GitHub ---------------------------------------------------------------


@router.get("/integrations/github/app-install-url")
def github_app_install_url(_admin: User = Depends(require_admin)):
    app_id = (settings.github_app_id or "").strip() or "unknown"
    return {"install_url": "https://github.com/apps", "app_id": app_id}


@router.post("/integrations/github/connections/app")
def github_org_app_connection(
    body: dict,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = _get_github_connection(db, scope=GitHubScope.org.value)
    if row is None:
        row = GitHubConnection(scope=GitHubScope.org.value, project_slug="", auth_kind=GitHubAuthKind.app.value)
        db.add(row)
    webhook_secret = secrets.token_urlsafe(32)
    row.auth_kind = GitHubAuthKind.app.value
    row.owner_login = (body.get("owner_login") or "").strip()
    row.app_id = (settings.github_app_id or body.get("app_id") or "").strip()
    row.installation_id = str(body.get("installation_id") or "").strip()
    row.encrypted_private_key = _cred.encrypt((settings.github_app_private_key_pem or "").strip())
    row.webhook_secret_hash = hashlib.sha256(webhook_secret.encode("utf-8")).hexdigest()
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    out = row.to_dict()
    out["webhook_secret_plaintext"] = webhook_secret
    return out


def _active_github_connection(db: Session, slug: str) -> GitHubConnection | None:
    return _get_github_connection(db, scope=GitHubScope.project.value, slug=slug) or _get_github_connection(
        db, scope=GitHubScope.org.value
    )


@router.get("/projects/{slug}/integrations/github/connection")
def github_get_project_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    return _github_view(_active_github_connection(db, slug))


@router.post("/projects/{slug}/integrations/github/connection/pat")
def github_project_pat_connection(
    slug: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    token = (body.get("access_token") or "").strip()
    owner_login = (body.get("owner_login") or "").strip()
    if not token or not owner_login:
        raise HTTPException(422, "owner_login and access_token are required")
    row = _get_github_connection(db, scope=GitHubScope.project.value, slug=slug)
    if row is None:
        row = GitHubConnection(scope=GitHubScope.project.value, project_slug=slug, auth_kind=GitHubAuthKind.pat.value)
        db.add(row)
    webhook_secret = secrets.token_urlsafe(32)
    row.auth_kind = GitHubAuthKind.pat.value
    row.owner_login = owner_login
    row.encrypted_access_token = _cred.encrypt(token)
    row.webhook_secret_hash = hashlib.sha256(webhook_secret.encode("utf-8")).hexdigest()
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    out = row.to_dict()
    out["webhook_secret_plaintext"] = webhook_secret
    return out


@router.delete("/projects/{slug}/integrations/github/connection", status_code=204)
def github_delete_project_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = _get_github_connection(db, scope=GitHubScope.project.value, slug=slug)
    if row is not None:
        db.delete(row)
        db.commit()


def _provider_from_connection(conn: GitHubConnection) -> GitHubProvider:
    if conn.auth_kind == GitHubAuthKind.app.value:
        return GitHubProvider(
            app_id=conn.app_id,
            installation_id=conn.installation_id,
            private_key_pem=_cred.decrypt(conn.encrypted_private_key),
            owner_login=conn.owner_login,
        )
    return GitHubProvider(access_token=_cred.decrypt(conn.encrypted_access_token), owner_login=conn.owner_login)


@router.post("/projects/{slug}/integrations/github/test")
def github_test_connection(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_github_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No GitHub connection configured")
    provider = _provider_from_connection(conn)
    with provider:
        result = provider.test_connection()
    return {"ok": True, "result": result}


@router.get("/projects/{slug}/integrations/github/repos")
def github_list_repos(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    conn = _active_github_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No GitHub connection configured")
    provider = _provider_from_connection(conn)
    with provider:
        return provider.list_repos()


@router.post("/projects/{slug}/integrations/github/repos")
def github_connect_repo(
    slug: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    conn = _active_github_connection(db, slug)
    if conn is None:
        raise HTTPException(404, "No GitHub connection configured")
    owner = (body.get("owner") or "").strip()
    name = (body.get("name") or "").strip()
    if not owner or not name:
        raise HTTPException(422, "owner and name are required")
    row = (
        db.query(GitHubRepo)
        .filter(
            GitHubRepo.connection_id == conn.id,
            GitHubRepo.project_slug == slug,
            GitHubRepo.owner == owner,
            GitHubRepo.name == name,
        )
        .one_or_none()
    )
    if row is None:
        row = GitHubRepo(
            connection_id=conn.id,
            project_slug=slug,
            owner=owner,
            name=name,
            default_branch=(body.get("default_branch") or "main").strip(),
            suites_root_path=(body.get("suites_root_path") or "tests/portal").strip(),
            is_connected=True,
        )
        db.add(row)
    else:
        row.default_branch = (body.get("default_branch") or row.default_branch).strip()
        row.suites_root_path = (body.get("suites_root_path") or row.suites_root_path).strip()
        row.is_connected = True
    db.commit()
    db.refresh(row)
    return row.to_dict()


@router.get("/projects/{slug}/integrations/github/connected-repos")
def github_connected_repos(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    rows = (
        db.query(GitHubRepo)
        .filter(GitHubRepo.project_slug == slug, GitHubRepo.is_connected.is_(True))
        .order_by(GitHubRepo.created_at.desc())
        .all()
    )
    return [r.to_dict() for r in rows]


@router.post("/projects/{slug}/integrations/github/repos/{repo_id}/push")
def github_push_scripts(
    slug: str,
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    repo = (
        db.query(GitHubRepo)
        .filter(GitHubRepo.id == repo_id, GitHubRepo.project_slug == slug)
        .one_or_none()
    )
    if repo is None:
        raise HTTPException(404, "Connected repo not found")
    conn = db.query(GitHubConnection).filter(GitHubConnection.id == repo.connection_id).one_or_none()
    if conn is None:
        raise HTTPException(404, "GitHub connection not found")
    project_id = ensure_project_uuid(slug)
    result = push_project_suites(db, repo=repo, connection=conn, project_id=project_id)
    return result


@router.post("/projects/{slug}/integrations/github/repos/{repo_id}/workflow")
def github_refresh_workflow(
    slug: str,
    repo_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    repo = (
        db.query(GitHubRepo)
        .filter(GitHubRepo.id == repo_id, GitHubRepo.project_slug == slug)
        .one_or_none()
    )
    if repo is None:
        raise HTTPException(404, "Connected repo not found")
    conn = db.query(GitHubConnection).filter(GitHubConnection.id == repo.connection_id).one_or_none()
    if conn is None:
        raise HTTPException(404, "GitHub connection not found")
    yaml = refresh_workflow_yaml(db, repo=repo, connection=conn)
    return {"path": repo.workflow_path, "yaml": yaml}


# ---- Context files ---------------------------------------------------------


@router.get("/projects/{slug}/context-files")
def list_context_files(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    rows = (
        db.query(ContextFile)
        .filter(ContextFile.project_slug == slug)
        .order_by(ContextFile.uploaded_at.desc())
        .all()
    )
    return [r.to_dict() for r in rows]


@router.post("/projects/{slug}/context-files")
async def upload_context_file(
    slug: str,
    file: UploadFile = File(...),
    description: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ai_qa_portal.backend.services.context_parser import parse_bytes

    _require_project_access(slug, current_user, db, ProjectRole.lead)
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "Uploaded file is empty")
    parsed = parse_bytes(filename=file.filename or "upload.bin", blob=blob)
    file_id = str(uuid4())
    ext = Path(file.filename or "upload.bin").suffix.lower() or ".bin"
    storage_path = _context_dir / f"{file_id}{ext}"
    storage_path.write_bytes(blob)

    row = ContextFile(
        id=file_id,
        project_slug=slug,
        filename=file.filename or f"{file_id}{ext}",
        mime=file.content_type or "application/octet-stream",
        kind=parsed.kind,
        size=len(blob),
        sha256=parsed.sha256,
        storage_path=str(storage_path),
        row_count=len(parsed.rows),
        chunk_count=len(parsed.chunks),
        description=(description or "").strip(),
        uploaded_by_user_id=current_user.id,
    )
    db.add(row)
    for r in parsed.rows:
        db.add(
            ContextFileRow(
                context_file_id=row.id,
                row_index=r.row_index,
                data=r.data,
                searchable_text=r.searchable_text,
            )
        )
    db.commit()
    db.refresh(row)
    if parsed.chunks:
        enqueue_context_file(db, file_row=row, chunks=parsed.chunks)
    out = row.to_dict()
    out["columns"] = list(parsed.columns)
    return out


@router.get("/projects/{slug}/context-files/{file_id}/preview")
def preview_context_file(
    slug: str,
    file_id: str,
    limit: int = Query(20, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    row = db.query(ContextFile).filter(ContextFile.id == file_id, ContextFile.project_slug == slug).one_or_none()
    if row is None:
        raise HTTPException(404, "Context file not found")
    rows = (
        db.query(ContextFileRow)
        .filter(ContextFileRow.context_file_id == file_id)
        .order_by(ContextFileRow.row_index.asc())
        .limit(limit)
        .all()
    )
    return {"file": row.to_dict(), "rows": [r.to_dict() for r in rows]}


@router.delete("/projects/{slug}/context-files/{file_id}", status_code=204)
def delete_context_file(
    slug: str,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = db.query(ContextFile).filter(ContextFile.id == file_id, ContextFile.project_slug == slug).one_or_none()
    if row is None:
        return
    with Path(row.storage_path).open("rb") if Path(row.storage_path).is_file() else io.BytesIO() as _f:
        pass
    path = Path(row.storage_path)
    if path.is_file():
        path.unlink(missing_ok=True)
    db.delete(row)
    db.commit()


# ---- Test data tables ------------------------------------------------------


@router.get("/projects/{slug}/test-data-tables")
def list_test_data_tables(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    rows = (
        db.query(TestDataTable)
        .filter(TestDataTable.project_slug == slug)
        .order_by(TestDataTable.updated_at.desc())
        .all()
    )
    return [r.to_dict() for r in rows]


_SAMPLES = {
    "user_types": "role,profile,locale\nSales Manager,Standard User,en_US\nQA Lead,System Administrator,en_US\n",
    "accounts": "name,industry,billing_country\nAcme Corp,Manufacturing,US\nZenith Labs,Healthcare,US\n",
    "opportunities": "name,stage,amount\nRenewal Q3,Prospecting,25000\nExpansion East,Qualification,50000\n",
    "leads": "first_name,last_name,company,email\nMia,Shah,Acme Corp,mia@acme.test\nAarav,Patel,Zenith Labs,aarav@zenith.test\n",
}


@router.get("/projects/{slug}/test-data-tables/sample-csv")
def sample_test_data_csv(
    slug: str,
    kind: str = Query("user_types"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    content = _SAMPLES.get(kind, _SAMPLES["user_types"])
    return PlainTextResponse(content=content, media_type="text/csv")


@router.post("/projects/{slug}/test-data-tables")
async def create_test_data_table(
    slug: str,
    name: str = Form(...),
    description: str = Form(""),
    kind: str = Form("generic"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    blob = await file.read()
    if not blob:
        raise HTTPException(400, "Uploaded file is empty")
    text = blob.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = [c for c in (reader.fieldnames or []) if c]
    table = TestDataTable(
        project_slug=slug,
        name=name.strip(),
        description=(description or "").strip(),
        kind=(kind or "generic").strip() or "generic",
        columns=columns,
        created_by_user_id=current_user.id,
    )
    db.add(table)
    db.flush()
    for idx, row in enumerate(reader):
        clean = {k: (v or "").strip() for k, v in row.items() if k}
        searchable = " | ".join(f"{k}: {v}" for k, v in clean.items() if v)
        td_row = TestDataRow(table_id=table.id, row_index=idx, data=clean, searchable_text=searchable)
        db.add(td_row)
        index_test_data_row(
            db,
            project_slug=slug,
            row_id=td_row.id,
            table_name=table.name,
            row_text=searchable,
        )
    db.commit()
    db.refresh(table)
    return table.to_dict()


@router.get("/projects/{slug}/test-data-tables/{table_id}")
def get_test_data_table(
    slug: str,
    table_id: str,
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    table = db.query(TestDataTable).filter(TestDataTable.id == table_id, TestDataTable.project_slug == slug).one_or_none()
    if table is None:
        raise HTTPException(404, "Test data table not found")
    rows = (
        db.query(TestDataRow)
        .filter(TestDataRow.table_id == table_id)
        .order_by(TestDataRow.row_index.asc())
        .limit(limit)
        .all()
    )
    return {"table": table.to_dict(), "rows": [r.to_dict() for r in rows]}


@router.delete("/projects/{slug}/test-data-tables/{table_id}", status_code=204)
def delete_test_data_table(
    slug: str,
    table_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    table = db.query(TestDataTable).filter(TestDataTable.id == table_id, TestDataTable.project_slug == slug).one_or_none()
    if table is not None:
        db.delete(table)
        db.commit()


# ---- Schedules -------------------------------------------------------------


@router.get("/projects/{slug}/schedules")
def list_schedules(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    rows = (
        db.query(Schedule)
        .filter(Schedule.project_slug == slug)
        .order_by(Schedule.created_at.desc())
        .all()
    )
    return [r.to_dict() for r in rows]


@router.post("/projects/{slug}/schedules", status_code=201)
def create_schedule(
    slug: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = Schedule(
        project_slug=slug,
        name=(body.get("name") or "").strip() or "Untitled schedule",
        target_kind=(body.get("target_kind") or "test_case").strip(),
        target_id=str(body.get("target_id") or "").strip(),
        cron=(body.get("cron") or "0 9 * * *").strip(),
        timezone=(body.get("timezone") or "UTC").strip() or "UTC",
        runner=(body.get("runner") or ScheduleRunner.local.value).strip(),
        github_repo_id=body.get("github_repo_id"),
        persona_id=body.get("persona_id"),
        org_id=body.get("org_id"),
        enabled=bool(body.get("enabled", True)),
        created_by_user_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if row.runner == ScheduleRunner.local.value and row.enabled:
        add_or_replace(row)
    return row.to_dict()


@router.patch("/projects/{slug}/schedules/{schedule_id}")
def update_schedule(
    slug: str,
    schedule_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = (
        db.query(Schedule)
        .filter(Schedule.id == schedule_id, Schedule.project_slug == slug)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Schedule not found")
    for field in (
        "name",
        "target_kind",
        "target_id",
        "cron",
        "timezone",
        "runner",
        "github_repo_id",
        "persona_id",
        "org_id",
        "enabled",
    ):
        if field in body:
            setattr(row, field, body[field])
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    if row.runner == ScheduleRunner.local.value and row.enabled:
        add_or_replace(row)
    else:
        scheduler_delete(row.id)
    return row.to_dict()


@router.delete("/projects/{slug}/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    slug: str,
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = (
        db.query(Schedule)
        .filter(Schedule.id == schedule_id, Schedule.project_slug == slug)
        .one_or_none()
    )
    if row is not None:
        scheduler_delete(row.id)
        db.delete(row)
        db.commit()


@router.post("/projects/{slug}/schedules/{schedule_id}/run-now")
def run_schedule_now(
    slug: str,
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db, ProjectRole.lead)
    row = (
        db.query(Schedule)
        .filter(Schedule.id == schedule_id, Schedule.project_slug == slug)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Schedule not found")
    run = execute_schedule(row.id)
    return run.to_dict()


@router.get("/projects/{slug}/schedules/{schedule_id}/runs")
def schedule_history(
    slug: str,
    schedule_id: str,
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_project_access(slug, current_user, db)
    schedule = (
        db.query(Schedule)
        .filter(Schedule.id == schedule_id, Schedule.project_slug == slug)
        .one_or_none()
    )
    if schedule is None:
        raise HTTPException(404, "Schedule not found")
    rows = (
        db.query(ScheduleRun)
        .filter(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in rows]
