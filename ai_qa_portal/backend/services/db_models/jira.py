"""Jira ingestion tables.

Five tables, all upsert keyed by the Jira-side ``id`` so re-syncing the same
project is idempotent:

* ``jira_connections`` -- one row per (scope, project). Holds the encrypted
  API token and the default project key to import from.
* ``jira_projects``    -- cached project metadata pulled from /rest/api/3/project.
* ``jira_sprints``     -- sprints pulled from the Agile board API.
* ``jira_issues``      -- every imported issue (Story, Task, Bug, Sub-task,
  Epic, ...). ``payload`` keeps the raw JSON so the RAG layer can re-chunk
  without re-fetching.
* ``jira_comments``    -- per-issue comments; separated so embedding the
  comment thread is independent of the issue body.

Portal entities (Sprint, UserStory, TestCase) link back to these via their
existing ``external_id`` / ``external_source`` fields; the import flow in
``services/jira_sync.py`` is what populates that pairing.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class JiraScope(str, enum.Enum):
    org = "org"
    project = "project"


class JiraConnection(Base):
    """A Jira Cloud auth handle. Either org-wide (one per portal install)
    or per-project (overrides the org-wide default for a single Salesforce
    project). The encrypted API token uses the same Fernet key as
    ``Persona.encrypted_password``.
    """

    __tablename__ = "jira_connections"
    __table_args__ = (
        # At most one org-wide row (project_slug is empty string for org scope).
        UniqueConstraint("scope", "project_slug", name="uq_jira_conn_scope_project"),
        Index("ix_jira_conn_scope", "scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # Empty string when scope == 'org'. We use empty string rather than NULL
    # so the unique constraint applies uniformly on Postgres and SQLite.
    project_slug: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    default_jira_project_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def to_dict(self, *, reveal_token: bool = False) -> dict:
        return {
            "id": self.id,
            "scope": self.scope,
            "project_slug": self.project_slug or None,
            "base_url": self.base_url,
            "email": self.email,
            "default_jira_project_key": self.default_jira_project_key or None,
            "has_token": bool(self.encrypted_token),
            "encrypted_token": self.encrypted_token if reveal_token else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class JiraProject(Base):
    """Cached metadata for a Jira project visible to a connection."""

    __tablename__ = "jira_projects"
    __table_args__ = (
        UniqueConstraint("connection_id", "jira_key", name="uq_jira_project_conn_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jira_connections.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    jira_id: Mapped[str] = mapped_column(String(64), nullable=False)
    jira_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "jira_id": self.jira_id,
            "jira_key": self.jira_key,
            "name": self.name,
            "project_type": self.project_type or None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class JiraSprint(Base):
    """A sprint pulled from Jira's Agile API (``/rest/agile/1.0/board/.../sprint``).

    ``jira_id`` is the canonical key for upserts. ``portal_sprint_id`` is set
    when the user opts into mapping this Jira sprint to a portal Sprint row.
    """

    __tablename__ = "jira_sprints"
    __table_args__ = (
        UniqueConstraint("connection_id", "jira_id", name="uq_jira_sprint_conn_id"),
        Index("ix_jira_sprint_project_key", "jira_project_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jira_connections.id", ondelete="CASCADE"), nullable=False,
    )
    jira_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    jira_project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="future", nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    complete_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_sprint_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "jira_id": self.jira_id,
            "jira_project_key": self.jira_project_key,
            "name": self.name,
            "state": self.state,
            "goal": self.goal or None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "complete_date": self.complete_date.isoformat() if self.complete_date else None,
            "portal_sprint_id": self.portal_sprint_id,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class JiraIssue(Base):
    """One row per Jira issue (Story/Task/Bug/Sub-task/Epic/...).

    ``payload`` is the full ``GET /rest/api/3/issue/{key}?expand=...`` body;
    re-chunking for RAG should always read from this column rather than
    re-fetching, so a single import covers every downstream consumer.
    """

    __tablename__ = "jira_issues"
    __table_args__ = (
        UniqueConstraint("connection_id", "jira_id", name="uq_jira_issue_conn_id"),
        Index("ix_jira_issue_jira_key", "jira_key"),
        Index("ix_jira_issue_sprint", "sprint_jira_id"),
        Index("ix_jira_issue_parent", "parent_jira_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jira_connections.id", ondelete="CASCADE"), nullable=False,
    )
    jira_id: Mapped[str] = mapped_column(String(64), nullable=False)
    jira_key: Mapped[str] = mapped_column(String(64), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    issue_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    summary: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    assignee: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    reporter: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    labels: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    sprint_jira_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_jira_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    portal_story_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    jira_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    jira_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "jira_id": self.jira_id,
            "jira_key": self.jira_key,
            "jira_project_key": self.jira_project_key,
            "issue_type": self.issue_type or None,
            "status": self.status or None,
            "summary": self.summary or None,
            "description": self.description or None,
            "assignee": self.assignee or None,
            "reporter": self.reporter or None,
            "priority": self.priority or None,
            "labels": self.labels or [],
            "sprint_jira_id": self.sprint_jira_id,
            "parent_jira_id": self.parent_jira_id,
            "portal_story_id": self.portal_story_id,
            "jira_created_at": self.jira_created_at.isoformat() if self.jira_created_at else None,
            "jira_updated_at": self.jira_updated_at.isoformat() if self.jira_updated_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


class JiraComment(Base):
    """A single comment on a Jira issue."""

    __tablename__ = "jira_comments"
    __table_args__ = (
        UniqueConstraint("issue_jira_id", "jira_id", name="uq_jira_comment_issue_id"),
        Index("ix_jira_comment_issue", "issue_jira_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    issue_jira_id: Mapped[str] = mapped_column(String(64), nullable=False)
    jira_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    jira_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    jira_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "issue_jira_id": self.issue_jira_id,
            "jira_id": self.jira_id,
            "author": self.author or None,
            "body": self.body or None,
            "jira_created_at": self.jira_created_at.isoformat() if self.jira_created_at else None,
            "jira_updated_at": self.jira_updated_at.isoformat() if self.jira_updated_at else None,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


# Re-export Boolean import so static analysis doesn't flag it as unused if a
# future migration adds boolean columns to these tables.
__all__ = [
    "Boolean",
    "JiraComment",
    "JiraConnection",
    "JiraIssue",
    "JiraProject",
    "JiraScope",
    "JiraSprint",
]
