"""SQLAlchemy engine + ORM models for users and (later) RBAC tables.

Phase 1 ships only the `User` table. Phase 2 will add `ProjectMembership`.
SQLite file lives next to the existing JSON stores at ``{DATA_DIR}/users.db`` so
the same Fly volume / Docker mount keeps it persistent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, create_engine
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


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str] = mapped_column(String(1024), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "is_admin": self.is_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db() -> None:
    """Create tables if they do not exist. Idempotent; safe to call on every boot."""
    Base.metadata.create_all(bind=engine)


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
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_id(db: Session, user_id: str | UUID) -> User | None:
    return db.query(User).filter(User.id == str(user_id)).one_or_none()
