"""GitHub integration tables.

Two tables:

* ``github_connections`` -- one row per (scope, project). Supports two auth
  flavours: a **GitHub App installation** (preferred; ``auth_kind='app'``,
  encrypted PEM private key + installation id) or a **classic / fine-grained
  PAT** (``auth_kind='pat'``, encrypted token). Both column groups are
  nullable; exactly one set should be populated per row.
* ``github_repos`` -- repos that a portal project has opted into. We persist
  the default branch and the path to the portal-owned workflow YAML so a
  schedule update can rewrite the right file without re-walking the repo.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base


class GitHubScope(str, enum.Enum):
    org = "org"
    project = "project"


class GitHubAuthKind(str, enum.Enum):
    app = "app"
    pat = "pat"


class GitHubConnection(Base):
    """A GitHub auth handle. Either org-wide (one for the whole portal
    install -- typically the GitHub App) or per-project (a project-level
    PAT override). The encrypted secret(s) use the same Fernet key as the
    rest of the portal.
    """

    __tablename__ = "github_connections"
    __table_args__ = (
        UniqueConstraint("scope", "project_slug", name="uq_gh_conn_scope_project"),
        Index("ix_gh_conn_scope", "scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    project_slug: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    auth_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    owner_login: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # GitHub App fields (populated when auth_kind == 'app')
    app_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    installation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    encrypted_private_key: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # PAT field (populated when auth_kind == 'pat')
    encrypted_access_token: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Per-connection webhook secret (hashed, never reversible). The plaintext
    # is shown once on creation so the operator can paste it into the GitHub
    # App / repo webhook config.
    webhook_secret_hash: Mapped[str] = mapped_column(String(255), default="", nullable=False)

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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scope": self.scope,
            "project_slug": self.project_slug or None,
            "auth_kind": self.auth_kind,
            "owner_login": self.owner_login or None,
            "app_id": self.app_id or None,
            "installation_id": self.installation_id or None,
            "has_private_key": bool(self.encrypted_private_key),
            "has_access_token": bool(self.encrypted_access_token),
            "has_webhook_secret": bool(self.webhook_secret_hash),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class GitHubRepo(Base):
    """A repo a portal project is allowed to push to / trigger runs on."""

    __tablename__ = "github_repos"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "project_slug", "owner", "name",
            name="uq_gh_repo_conn_project_full_name",
        ),
        Index("ix_gh_repo_project", "project_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("github_connections.id", ondelete="CASCADE"), nullable=False,
    )
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(120), default="main", nullable=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    workflow_path: Mapped[str] = mapped_column(
        String(255),
        default=".github/workflows/portal-automation.yml",
        nullable=False,
    )
    suites_root_path: Mapped[str] = mapped_column(
        String(255), default="tests/portal", nullable=False,
    )
    last_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    last_workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "project_slug": self.project_slug,
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "is_connected": self.is_connected,
            "workflow_path": self.workflow_path,
            "suites_root_path": self.suites_root_path,
            "last_pushed_at": self.last_pushed_at.isoformat() if self.last_pushed_at else None,
            "last_workflow_run_id": self.last_workflow_run_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
