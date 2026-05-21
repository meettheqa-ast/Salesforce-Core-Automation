"""Runtime form-healing persistence models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class HealEvent(Base):
    __tablename__ = "heal_events"
    __table_args__ = (
        Index("ix_heal_event_project_sobject_type", "project_slug", "sobject", "error_type"),
        Index("ix_heal_event_job", "generation_job_id"),
        Index("ix_heal_event_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    sobject: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    field_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="aborted")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSONColumn(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )


class OrgFieldLearning(Base):
    __tablename__ = "org_field_learnings"
    __table_args__ = (
        UniqueConstraint(
            "project_slug",
            "sobject",
            "field_api_name",
            "error_type",
            name="uq_org_field_learnings_scope",
        ),
        Index("ix_org_field_learning_project", "project_slug"),
        Index("ix_org_field_learning_field", "sobject", "field_api_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    sobject: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    field_api_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    error_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_failed_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

