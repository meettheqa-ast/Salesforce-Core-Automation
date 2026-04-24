"""SQLAlchemy engine + ORM models for users + RBAC tables.

Tables:
  users               -- one row per Google identity (Phase 1 base)
  project_memberships -- (project_id <- slug, user_id, role) per project (Phase 2a)

SQLite file lives next to the existing JSON stores at ``{DATA_DIR}/users.db`` so
the same Fly volume / Docker mount keeps it persistent.

Schema migrations are minimal: ``Base.metadata.create_all`` is idempotent, and
new columns on existing tables are added by a tiny inline ALTER pass in
``init_db`` so we don't have to ship Alembic for two columns.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ai_qa_portal.backend.config import settings


def _db_url() -> str:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'users.db').as_posix()}"


# `check_same_thread=False` is required because FastAPI's threadpool may dispatch
# requests across threads; SQLite's per-connection guard would otherwise raise.
engine = create_engine(
    _db_url(),
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


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
        default=lambda: datetime.now(timezone.utc),
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
        default=lambda: datetime.now(timezone.utc),
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


# --- Init + tiny inline migrations ----------------------------------------

def _ensure_user_columns() -> None:
    """Add Phase 2a columns to an existing `users` table without Alembic.

    SQLite is permissive about adding nullable columns. We check `PRAGMA
    table_info(users)` and ALTER TABLE for any missing column. Idempotent.
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


def init_db() -> None:
    """Create tables if they do not exist. Idempotent; safe to call on every boot."""
    Base.metadata.create_all(bind=engine)
    _ensure_user_columns()


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
