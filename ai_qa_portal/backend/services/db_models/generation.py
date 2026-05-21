"""Long-running MCP Stepwise generation tables.

``GenerationJob`` persists job lifecycle + replayable event history so the UI
can reconnect to a running generation stream after refresh.

``GenerationMetric`` stores per-run timing telemetry and fallback reasons for
diagnostics and trend analysis.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class GenerationStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index("ix_generation_job_owner_status", "owner_user_id", "status"),
        Index("ix_generation_job_project_status", "project_slug", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    project_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="mcp_stepwise")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    request_payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=GenerationStatus.queued.value,
    )
    current_phase: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    phase_log: Mapped[list] = mapped_column(JSONColumn(), nullable=False, default=list)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    robot_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class GenerationMetric(Base):
    __tablename__ = "generation_metrics"
    __table_args__ = (
        Index("ix_generation_metric_started", "started_at"),
        Index("ix_generation_metric_mode_status", "mode", "final_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(64), nullable=False, default="mcp_stepwise")
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_timings: Mapped[dict] = mapped_column(JSONColumn(), nullable=False, default=dict)
    final_status: Mapped[str] = mapped_column(String(16), nullable=False, default="error")
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    robot_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_count_planned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_count_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_count_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
