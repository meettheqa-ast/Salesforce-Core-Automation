"""Scheduled-run tables.

A ``Schedule`` is a cron + target + runner triple:

* **target_kind** picks what to run (a single test case, every case in a
  story, every story in a sprint, or every case carrying a given tag).
* **runner** picks where the run executes:
    - ``local``: APScheduler inside the FastAPI process fires
      ``services/schedule_runner.execute`` at each cron tick.
    - ``github_actions``: APScheduler does NOT fire; instead the schedule
      is materialised as a ``schedule:`` cron entry in the connected repo's
      workflow YAML. Run results come back via the webhook.

``ScheduleRun`` is the per-execution history record. ``local_run_id`` joins
back to the existing ``runs`` table for local executions so the existing run
report UI keeps working.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class ScheduleTargetKind(str, enum.Enum):
    sprint = "sprint"
    story = "story"
    test_case = "test_case"
    tag = "tag"


class ScheduleRunner(str, enum.Enum):
    local = "local"
    github_actions = "github_actions"


class ScheduleStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    error = "error"
    cancelled = "cancelled"


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedule_project_enabled", "project_slug", "enabled"),
        Index("ix_schedule_runner", "runner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cron: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    runner: Mapped[str] = mapped_column(String(32), default=ScheduleRunner.local.value, nullable=False)
    github_repo_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("github_repos.id", ondelete="SET NULL"), nullable=True,
    )
    persona_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
            "project_slug": self.project_slug,
            "name": self.name,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "cron": self.cron,
            "timezone": self.timezone,
            "runner": self.runner,
            "github_repo_id": self.github_repo_id,
            "persona_id": self.persona_id,
            "org_id": self.org_id,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        Index("ix_schedule_run_schedule", "schedule_id"),
        Index("ix_schedule_run_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    schedule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ScheduleStatus.queued.value, nullable=False,
    )
    runner: Mapped[str] = mapped_column(String(32), nullable=False)
    github_workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    github_workflow_run_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    local_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "schedule_id": self.schedule_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "runner": self.runner,
            "github_workflow_run_id": self.github_workflow_run_id,
            "github_workflow_run_url": self.github_workflow_run_url,
            "local_run_id": self.local_run_id,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
        }
