"""SQLAlchemy engine + ORM models for portal core tables.

Backed by **Postgres + pgvector** when ``DATABASE_URL`` is set (production /
Docker default), or by **SQLite** at ``{DATA_DIR}/users.db`` as a local-dev
fallback when ``DATABASE_URL`` is empty.

Tables:
  users                -- one row per Google identity
  project_memberships  -- (project_slug, user_id, role) per project
  project_invitations  -- pending invites / requests
  runs                 -- index of Robot Framework executions
  audit_log            -- append-only security audit trail
  notifications        -- in-app notifications

Schema migrations are owned by Alembic (``ai_qa_portal/backend/alembic/``);
``init_db()`` is kept as an idempotent dev convenience that calls
``Base.metadata.create_all`` so the legacy SQLite path stays self-bootstrapping.
On Postgres, ``init_db()`` ALSO ensures the ``vector`` extension exists when
``settings.pgvector_auto_install`` is True.

The new tables introduced by Phases 2-7 (jira_*, github_*, schedules,
schedule_runs, context_files, context_file_rows, test_data_tables,
test_data_rows, embeddings) live in ``ai_qa_portal.backend.services.db_models``
and are imported below so they register on the shared ``Base.metadata`` and
participate in ``create_all`` / Alembic autogeneration.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ai_qa_portal.backend.config import settings

logger = logging.getLogger("ai_qa_portal.db")


# --- Engine factory --------------------------------------------------------

def _db_url() -> str:
    """Return the SQLAlchemy URL. Prefer ``settings.database_url`` (Postgres
    in production); fall back to a per-user SQLite file under ``data_dir`` so
    local dev keeps working without a Postgres install.
    """
    if settings.database_url:
        return settings.database_url
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'users.db').as_posix()}"


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://"))


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite:")


def _make_engine() -> Engine:
    url = _db_url()
    if _is_postgres(url):
        # ``pool_pre_ping`` recycles dead connections after a Postgres
        # restart or a managed-DB failover. Without it, the first request
        # after a network blip raises ``OperationalError`` until the pool
        # rotates.
        return create_engine(url, future=True, pool_pre_ping=True)
    # SQLite needs `check_same_thread=False` because FastAPI's threadpool
    # dispatches requests across threads and SQLite's per-connection guard
    # would otherwise raise.
    return create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Shared declarative base for every ORM table in the portal."""


# --- Roles -----------------------------------------------------------------

class GlobalRole(str, enum.Enum):
    """User's default role across the portal. Per-project membership can override
    this for that project; Admin is the only role that grants cross-project powers."""

    user = "user"
    team_lead = "tl"
    project_manager = "pm"
    admin = "admin"


class ProjectRole(str, enum.Enum):
    """Role within a single project membership."""

    member = "member"
    lead = "lead"
    pm = "pm"


# Sortable rank: higher number = more powerful. Used by the effective-role
# resolver to pick the strongest applicable role for a given (user, project).
_PROJECT_ROLE_RANK = {
    ProjectRole.member: 1,
    ProjectRole.lead: 2,
    ProjectRole.pm: 3,
}


def project_role_at_least(actual: ProjectRole | str, required: ProjectRole | str) -> bool:
    a = ProjectRole(actual) if isinstance(actual, str) else actual
    r = ProjectRole(required) if isinstance(required, str) else required
    return _PROJECT_ROLE_RANK[a] >= _PROJECT_ROLE_RANK[r]


# --- Models ---------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(1024), default="")
    # Legacy boolean -- kept for compatibility with code that already reads it.
    # Source of truth going forward is `global_role == 'admin'`; the two are
    # kept in sync by ``get_or_create_user`` and by the admin endpoint.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    global_role: Mapped[str] = mapped_column(
        String(16), default=GlobalRole.user.value, nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Admin can force-revoke a user's session by bumping this; tokens with
    # iat < session_revoked_at are rejected by ``get_current_user``.
    session_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "is_admin": self.is_admin,
            "global_role": self.global_role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


class ProjectMembership(Base):
    """Joins a user to a project with a per-project role.

    `project_slug` references the filesystem slug under Saved_Projects/ rather
    than a UUID, because the existing project model is folder-based. Slug is
    immutable for the lifetime of a project (rename = create+delete) so this is
    safe.
    """

    __tablename__ = "project_memberships"
    __table_args__ = (
        UniqueConstraint("project_slug", "user_id", name="uq_project_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), default=ProjectRole.member.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "user_id": self.user_id,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# --- Phase 2c: invitations + notifications --------------------------------

class InvitationDirection(str, enum.Enum):
    invite = "invite"     # PM/TL -> user, awaiting user accept
    request = "request"   # would-be member -> project, awaiting PM/TL approval


class InvitationStatus(str, enum.Enum):
    pending = "pending"      # invite waiting for user accept
    requested = "requested"  # request waiting for PM/TL approval
    accepted = "accepted"    # invitation accepted by user (terminal)
    approved = "approved"    # request approved by PM/TL (terminal -- membership created)
    rejected = "rejected"    # rejected by either side (terminal)
    revoked = "revoked"      # invite/request cancelled by sender (terminal)
    expired = "expired"      # passed expires_at without action (terminal)


class ProjectInvitation(Base):
    """Either an invite (PM/TL -> user) or a self-service request (user -> project).

    The two directions share a table because the lifecycle and approval flow
    are nearly identical -- distinguishing them via the `direction` column
    avoids a second join everywhere we list "open invitations / requests for me".
    """

    __tablename__ = "project_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    # Email of the invited user (direction=invite) OR the requester (direction=request).
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=ProjectRole.member.value, nullable=False)
    direction: Mapped[str] = mapped_column(
        String(16), default=InvitationDirection.invite.value, nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), default=InvitationStatus.pending.value, nullable=False, index=True,
    )
    # User who initiated this row. Null for self-requests where the user
    # initiated themselves and we haven't created their User row yet.
    invited_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    # Set when status moves to accepted/approved/rejected/revoked.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "email": self.email,
            "role": self.role,
            "direction": self.direction,
            "status": self.status,
            "invited_by_user_id": self.invited_by_user_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class RunRecord(Base):
    """One row per Robot Framework execution. The actual artefacts (log.html,
    report.html, output.xml, screenshots) still live on disk under Results/;
    this table is the index plus attribution."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    run_folder: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    triggered_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True,
    )
    persona_used_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    persona_owner_at_time: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="started", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "run_folder": self.run_folder,
            "triggered_by_user_id": self.triggered_by_user_id,
            "persona_used_id": self.persona_used_id,
            "persona_owner_at_time": self.persona_owner_at_time,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class AuditLog(Base):
    """Append-only audit trail for security-relevant actions.

    Used by the Admin Console (Phase 2f) and is implicitly populated by
    helpers across the backend (`log_action()` in services/audit.py).
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    metadata_json: Mapped[str] = mapped_column(String(2048), default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "metadata_json": self.metadata_json,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class Notification(Base):
    """In-app notification. Phase 2c keeps these to invitations + approvals;
    later phases (audit log, persona reveal alerts) can reuse the same table."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True, nullable=False,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(1024), default="")
    action_url: Mapped[str] = mapped_column(String(512), default="")
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "action_url": self.action_url,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# --- Init + dialect-aware migration helpers --------------------------------

def _ensure_user_columns_sqlite() -> None:
    """Legacy SQLite-only path. On Postgres, Alembic owns schema migrations
    so this is skipped.
    """
    expected = {
        "global_role": "VARCHAR(16) NOT NULL DEFAULT 'user'",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
        "session_revoked_at": "DATETIME",
        "last_login_at": "DATETIME",
    }
    with engine.begin() as conn:
        existing_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        for col, ddl in expected.items():
            if col not in existing_cols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))


def _ensure_pgvector_extension() -> None:
    """Best-effort ``CREATE EXTENSION IF NOT EXISTS vector``. Requires the DB
    user to hold CREATE on the database (Postgres grants this implicitly for
    DB owners). Failures are logged and swallowed so a missing-privilege
    situation doesn't block startup -- if the extension is installed out of
    band, the embeddings table will still work.
    """
    if not settings.pgvector_auto_install:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension is available.")
    except Exception as exc:  # noqa: BLE001 -- best-effort
        logger.warning(
            "Could not ensure pgvector extension (%s). If embeddings fail at "
            "query time, install it manually with `CREATE EXTENSION vector;` "
            "as a superuser, or set PGVECTOR_AUTO_INSTALL=false to silence "
            "this warning.",
            exc,
        )


def _import_extension_models() -> None:
    """Import the new SQLAlchemy models so they register on ``Base.metadata``.

    These live in their own package to keep this file readable; they cover
    Jira ingestion, GitHub connections, schedules, context files, test data
    tables, and the pgvector-backed embeddings table.
    """
    # Imported for side effect of registering tables; pyflakes is silenced
    # by the noqa marker.
    from ai_qa_portal.backend.services import db_models  # noqa: F401


def init_db() -> None:
    """Create tables if they do not exist. Idempotent; safe to call on every boot.

    Behaviour matrix:
      * Postgres: ensures the pgvector extension, then runs ``create_all`` so
        a fresh deploy without Alembic still boots. Production deployments
        should run ``alembic upgrade head`` explicitly and treat
        ``create_all`` as a backstop.
      * SQLite (legacy local dev): runs ``create_all`` plus the inline
        ALTER-TABLE pass for Phase 2a columns.
    """
    _import_extension_models()
    url = _db_url()
    if _is_postgres(url):
        _ensure_pgvector_extension()
        Base.metadata.create_all(bind=engine)
        return
    Base.metadata.create_all(bind=engine)
    if _is_sqlite(url):
        _ensure_user_columns_sqlite()


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a SQLAlchemy session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_create_user(
    db: Session,
    *,
    email: str,
    name: str = "",
    picture: str = "",
) -> User:
    """Return existing user by email or create one. Auto-promotes to admin if the email
    appears in ``settings.initial_admins`` (comma-separated, case-insensitive).
    """
    email_norm = email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).one_or_none()
    if user is not None:
        # Refresh display fields opportunistically (Google returns latest each login).
        changed = False
        if name and user.name != name:
            user.name = name
            changed = True
        if picture and user.picture != picture:
            user.picture = picture
            changed = True
        # Keep is_admin and global_role in sync.
        if user.is_admin and user.global_role != GlobalRole.admin.value:
            user.global_role = GlobalRole.admin.value
            changed = True
        elif not user.is_admin and user.global_role == GlobalRole.admin.value:
            user.is_admin = True  # don't downgrade silently if global_role says admin
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
        return user

    is_initial_admin = email_norm in {
        e.strip().lower() for e in settings.initial_admins.split(",") if e.strip()
    }
    user = User(
        email=email_norm,
        name=name or "",
        picture=picture or "",
        is_admin=is_initial_admin,
        global_role=GlobalRole.admin.value if is_initial_admin else GlobalRole.user.value,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_id(db: Session, user_id: str | UUID) -> User | None:
    return db.query(User).filter(User.id == str(user_id)).one_or_none()


def get_user_by_email(db: Session, email: str) -> User | None:
    email_norm = (email or "").strip().lower()
    if not email_norm:
        return None
    return db.query(User).filter(User.email == email_norm).one_or_none()


# --- Membership helpers ---------------------------------------------------

def get_membership(db: Session, *, project_slug: str, user_id: str) -> ProjectMembership | None:
    return (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.project_slug == project_slug,
            ProjectMembership.user_id == str(user_id),
        )
        .one_or_none()
    )


def list_memberships_for_user(db: Session, user_id: str) -> list[ProjectMembership]:
    return (
        db.query(ProjectMembership)
        .filter(ProjectMembership.user_id == str(user_id))
        .all()
    )


def list_memberships_for_project(db: Session, project_slug: str) -> list[ProjectMembership]:
    return (
        db.query(ProjectMembership)
        .filter(ProjectMembership.project_slug == project_slug)
        .all()
    )


def push_notification(
    db: Session,
    *,
    user_id: str,
    type: str,
    title: str,
    body: str = "",
    action_url: str = "",
) -> Notification:
    """Insert an in-app notification for a user. Caller commits."""
    n = Notification(
        user_id=str(user_id),
        type=type,
        title=title,
        body=body,
        action_url=action_url,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def upsert_membership(
    db: Session,
    *,
    project_slug: str,
    user_id: str,
    role: ProjectRole | str = ProjectRole.member,
) -> ProjectMembership:
    """Idempotent: create or update the (project, user) membership with the given role."""
    role_val = role.value if isinstance(role, ProjectRole) else role
    existing = get_membership(db, project_slug=project_slug, user_id=user_id)
    if existing:
        if existing.role != role_val:
            existing.role = role_val
            db.commit()
            db.refresh(existing)
        return existing
    m = ProjectMembership(project_slug=project_slug, user_id=str(user_id), role=role_val)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m
