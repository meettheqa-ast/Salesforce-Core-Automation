"""Jira -> Postgres upsert orchestrator.

Takes a configured :class:`~ai_qa_portal.backend.services.integrations.jira.JiraProvider`
and an SQLAlchemy session, and walks the boards / sprints / issues / comments
graph for a single Jira project key. Each entity is upserted by Jira-side id,
so re-running the sync is idempotent.

This module is intentionally synchronous and avoids ``asyncio``: a single
sync call walks hundreds of issues sequentially under a thread (FastAPI's
threadpool worker), keeping the operational surface tiny -- no Celery,
no Redis. Long syncs block one worker; document this in the user-facing
"Sync now" button.

After upsert, the caller (the router) optionally calls
:func:`enqueue_rag_indexing` to schedule embedding work for the new /
changed rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.db_models.jira import (
    JiraComment,
    JiraConnection,
    JiraIssue,
    JiraProject,
    JiraSprint,
)
from ai_qa_portal.backend.services.integrations.jira import JiraProvider

logger = logging.getLogger("ai_qa_portal.jira_sync")


@dataclass(slots=True)
class SyncStats:
    """Lightweight summary returned by ``sync_project``."""

    project_key: str
    projects_seen: int = 0
    sprints_upserted: int = 0
    issues_upserted: int = 0
    comments_upserted: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_key": self.project_key,
            "projects_seen": self.projects_seen,
            "sprints_upserted": self.sprints_upserted,
            "issues_upserted": self.issues_upserted,
            "comments_upserted": self.comments_upserted,
            "errors": list(self.errors or []),
        }


# ---- helpers --------------------------------------------------------------

def _parse_jira_dt(value: str | None) -> datetime | None:
    """Jira returns ISO 8601 with milliseconds, often in tenant-local TZ
    (e.g. ``2024-08-01T13:25:00.000+0530``). Python's ``fromisoformat``
    handles this from 3.11+ once we normalise the missing colon in the
    offset."""
    if not value:
        return None
    s = value.strip()
    # Common tail forms: '+0530' or '+05:30' -- normalise to the latter.
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = f"{s[:-2]}:{s[-2:]}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        logger.debug("Could not parse Jira datetime: %r", value)
        return None


def adf_to_text(node: Any) -> str:
    """Recursively flatten an Atlassian Document Format tree to plain text.

    ADF nodes that carry text live under ``text``; container nodes have a
    ``content`` array of children. We add a newline between block-level
    nodes so paragraphs stay separated in the RAG chunks.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (adf_to_text(c) for c in node)))
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type") or ""
    text = node.get("text")
    if text:
        return str(text)
    children = node.get("content")
    inner = adf_to_text(children) if children else ""
    # Block-level containers get their own line; inline ones don't.
    block_types = {
        "paragraph", "heading", "bulletList", "orderedList", "listItem",
        "blockquote", "codeBlock", "panel", "table", "tableRow", "tableCell",
    }
    if node_type in block_types and inner:
        return inner + "\n"
    return inner


def _issue_description_text(issue: dict[str, Any]) -> str:
    """Best-effort plain-text description for an issue. Prefers the
    ``renderedFields.description`` HTML stripped of tags? No: ADF is
    structured, so we walk ``fields.description`` instead -- but if Jira
    has returned a plain string (older API mode), use it directly."""
    fields = issue.get("fields") or {}
    desc = fields.get("description")
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return adf_to_text(desc).strip()
    return ""


# ---- upsert primitives ----------------------------------------------------

def _upsert_jira_project(
    db: Session,
    *,
    connection_id: str,
    raw: dict[str, Any],
) -> JiraProject:
    jira_id = str(raw.get("id"))
    jira_key = str(raw.get("key") or "")
    existing = (
        db.query(JiraProject)
        .filter(JiraProject.connection_id == connection_id, JiraProject.jira_key == jira_key)
        .one_or_none()
    )
    payload = {
        "jira_id": jira_id,
        "jira_key": jira_key,
        "name": str(raw.get("name") or jira_key),
        "project_type": str(raw.get("projectTypeKey") or ""),
        "last_synced_at": datetime.now(UTC),
        "payload": raw,
    }
    if existing is None:
        row = JiraProject(connection_id=connection_id, **payload)
        db.add(row)
        return row
    for k, v in payload.items():
        setattr(existing, k, v)
    return existing


def _upsert_jira_sprint(
    db: Session,
    *,
    connection_id: str,
    project_key: str,
    raw: dict[str, Any],
) -> tuple[JiraSprint, bool]:
    jira_id = str(raw.get("id"))
    existing = (
        db.query(JiraSprint)
        .filter(JiraSprint.connection_id == connection_id, JiraSprint.jira_id == jira_id)
        .one_or_none()
    )
    payload = {
        "jira_project_key": project_key,
        "name": str(raw.get("name") or "(unnamed sprint)"),
        "state": str(raw.get("state") or "future"),
        "goal": str(raw.get("goal") or ""),
        "start_date": _parse_jira_dt(raw.get("startDate")),
        "end_date": _parse_jira_dt(raw.get("endDate")),
        "complete_date": _parse_jira_dt(raw.get("completeDate")),
        "payload": raw,
        "last_synced_at": datetime.now(UTC),
    }
    is_new = existing is None
    if is_new:
        row = JiraSprint(connection_id=connection_id, jira_id=jira_id, **payload)
        db.add(row)
        return row, True
    for k, v in payload.items():
        setattr(existing, k, v)
    return existing, False


def _extract_sprint_jira_id(fields: dict[str, Any]) -> str | None:
    """Find the active sprint id on an issue.

    Jira stores sprint info under a custom field whose id varies by
    tenant (commonly ``customfield_10020``). The Agile add-on also
    surfaces it as ``sprint``. We try the known keys and pick the most
    recent (state in {active, closed}) sprint id we find.
    """
    candidates: list[dict[str, Any]] = []
    for key, val in fields.items():
        if not key.startswith("customfield_"):
            continue
        if isinstance(val, list) and val and isinstance(val[0], dict) and "boardId" in val[0]:
            candidates = val
            break
    if not candidates:
        agile = fields.get("sprint")
        if isinstance(agile, dict) and "id" in agile:
            return str(agile["id"])
        if isinstance(agile, list) and agile:
            return str(agile[-1].get("id"))
        return None
    # Prefer active sprints, then most recent end date.
    candidates.sort(key=lambda s: (s.get("state") != "active", s.get("endDate") or ""))
    return str(candidates[0].get("id"))


def _upsert_jira_issue(
    db: Session,
    *,
    connection_id: str,
    project_key: str,
    raw: dict[str, Any],
) -> tuple[JiraIssue, bool]:
    jira_id = str(raw.get("id"))
    fields = raw.get("fields") or {}
    existing = (
        db.query(JiraIssue)
        .filter(JiraIssue.connection_id == connection_id, JiraIssue.jira_id == jira_id)
        .one_or_none()
    )
    parent = fields.get("parent") or {}
    payload = {
        "jira_key": str(raw.get("key") or ""),
        "jira_project_key": project_key,
        "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
        "status": str((fields.get("status") or {}).get("name") or ""),
        "summary": str(fields.get("summary") or "")[:1024],
        "description": _issue_description_text(raw),
        "assignee": str((fields.get("assignee") or {}).get("displayName") or ""),
        "reporter": str((fields.get("reporter") or {}).get("displayName") or ""),
        "priority": str((fields.get("priority") or {}).get("name") or ""),
        "labels": fields.get("labels") or [],
        "sprint_jira_id": _extract_sprint_jira_id(fields),
        "parent_jira_id": str(parent.get("id")) if parent else None,
        "payload": raw,
        "jira_created_at": _parse_jira_dt(fields.get("created")),
        "jira_updated_at": _parse_jira_dt(fields.get("updated")),
        "last_synced_at": datetime.now(UTC),
    }
    if existing is None:
        row = JiraIssue(connection_id=connection_id, jira_id=jira_id, **payload)
        db.add(row)
        return row, True
    for k, v in payload.items():
        setattr(existing, k, v)
    return existing, False


def _upsert_jira_comments(
    db: Session,
    *,
    issue_jira_id: str,
    comments_raw: Iterable[dict[str, Any]],
) -> int:
    count = 0
    for raw in comments_raw:
        comment_jira_id = str(raw.get("id") or "")
        if not comment_jira_id:
            continue
        existing = (
            db.query(JiraComment)
            .filter(
                JiraComment.issue_jira_id == issue_jira_id,
                JiraComment.jira_id == comment_jira_id,
            )
            .one_or_none()
        )
        body_field = raw.get("body")
        if isinstance(body_field, str):
            body_text = body_field
        elif isinstance(body_field, dict):
            body_text = adf_to_text(body_field).strip()
        else:
            body_text = ""
        author = (raw.get("author") or {}).get("displayName") or ""
        payload = {
            "author": str(author),
            "body": body_text,
            "jira_created_at": _parse_jira_dt(raw.get("created")),
            "jira_updated_at": _parse_jira_dt(raw.get("updated")),
            "payload": raw,
            "last_synced_at": datetime.now(UTC),
        }
        if existing is None:
            row = JiraComment(issue_jira_id=issue_jira_id, jira_id=comment_jira_id, **payload)
            db.add(row)
        else:
            for k, v in payload.items():
                setattr(existing, k, v)
        count += 1
    return count


# ---- public orchestration -------------------------------------------------

def sync_project(
    db: Session,
    *,
    connection: JiraConnection,
    jira_project_key: str,
    provider: JiraProvider,
    include_comments: bool = True,
    issue_jql_extra: str = "",
    portal_project_slug: str | None = None,
    index_for_rag: bool = True,
) -> SyncStats:
    """Upsert every project / sprint / issue / comment for one Jira project.

    Commits at the end so the entire sync is atomic from the caller's
    perspective. On partial failure (e.g. mid-pagination Jira error)
    we ``rollback`` and re-raise; the next "Sync now" click retries.

    When ``portal_project_slug`` is provided and ``index_for_rag`` is true,
    each upserted issue + comment is also pushed to the RAG index so the
    next prompt assembly can see it without a separate backfill.
    """
    stats = SyncStats(project_key=jira_project_key, errors=[])

    indexed_ids: list[tuple[str, str, str, str | None]] = []  # (kind, source_id, body, sprint_jira_id)

    try:
        project_raw = provider.get_project(jira_project_key)
        _upsert_jira_project(db, connection_id=connection.id, raw=project_raw)
        stats.projects_seen = 1

        # Sprint enumeration is best-effort: a single bad board (Kanban,
        # SM, WM business project, or any board that 4xxs on /sprint)
        # should never block issue import, which is what users actually
        # care about. The provider already skips Kanban boards at the
        # `list_sprints_for_project` level; this try/except handles any
        # remaining structural surprises (e.g. Agile API gated off the
        # token) by recording them in stats.errors and continuing.
        try:
            sprints_raw = provider.list_sprints_for_project(jira_project_key)
        except Exception as exc:  # noqa: BLE001 -- best-effort
            stats.errors.append(f"sprints {jira_project_key}: {exc}")
            sprints_raw = []
        for sprint_raw in sprints_raw:
            try:
                _upsert_jira_sprint(
                    db,
                    connection_id=connection.id,
                    project_key=jira_project_key,
                    raw=sprint_raw,
                )
                stats.sprints_upserted += 1
            except Exception as exc:  # noqa: BLE001 -- per-sprint resilience
                sid = sprint_raw.get("id") if isinstance(sprint_raw, dict) else "?"
                stats.errors.append(f"sprint {sid}: {exc}")

        jql = f'project = "{jira_project_key}"'
        if issue_jql_extra:
            jql = f"{jql} AND ({issue_jql_extra})"
        for issue_raw in provider.search_issues(jql=jql, expand=["renderedFields", "names"]):
            issue_row, _is_new = _upsert_jira_issue(
                db,
                connection_id=connection.id,
                project_key=jira_project_key,
                raw=issue_raw,
            )
            stats.issues_upserted += 1
            if portal_project_slug and index_for_rag:
                indexed_ids.append((
                    "issue",
                    issue_row.jira_id,
                    f"{issue_row.summary}\n\n{issue_row.description}",
                    issue_row.sprint_jira_id,
                ))
            if include_comments:
                try:
                    comments_raw = provider.list_comments(str(issue_raw.get("key") or issue_raw.get("id")))
                except Exception as exc:  # noqa: BLE001 -- per-issue failure shouldn't abort the whole sync
                    stats.errors.append(f"comments {issue_raw.get('key')}: {exc}")
                    continue
                stats.comments_upserted += _upsert_jira_comments(
                    db,
                    issue_jira_id=str(issue_raw.get("id")),
                    comments_raw=comments_raw,
                )
                if portal_project_slug and index_for_rag:
                    for c in comments_raw:
                        body_field = c.get("body")
                        if isinstance(body_field, dict):
                            body_text = adf_to_text(body_field).strip()
                        else:
                            body_text = str(body_field or "")
                        indexed_ids.append(("comment", str(c.get("id")), body_text, issue_row.jira_id))

        db.commit()
    except Exception:
        db.rollback()
        raise

    # RAG indexing happens after the SQL transaction commits so an
    # embedding-provider blip can't roll back the imported data. Failures
    # here are surfaced via ``stats.errors`` rather than re-raising.
    if portal_project_slug and index_for_rag and indexed_ids:
        try:
            from .embedding_provider import get_provider
            from .rag_index import index_jira_comment, index_jira_issue
            provider_ = get_provider()
            for kind, sid, body, parent in indexed_ids:
                if not body.strip():
                    continue
                if kind == "issue":
                    index_jira_issue(
                        db,
                        project_slug=portal_project_slug,
                        issue_id=sid,
                        summary="",  # already concatenated into body above
                        description=body,
                        sprint_jira_id=parent,
                        provider=provider_,
                    )
                else:
                    index_jira_comment(
                        db,
                        project_slug=portal_project_slug,
                        comment_id=sid,
                        issue_id=parent or "",
                        body=body,
                        provider=provider_,
                    )
        except Exception as exc:  # noqa: BLE001 -- RAG miss is non-fatal
            stats.errors.append(f"rag_index: {exc}")

    return stats
