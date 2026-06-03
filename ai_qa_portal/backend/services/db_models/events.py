"""Unified event stream (Phase 3 IA audit -- foundation only).

Future replacement for the four current observability tables:

  * ``audit_log``           (security-relevant user actions)
  * ``prompt_usage_audit``  (LLM generation provenance)
  * ``heal_events``         (form-healing telemetry)
  * story `activity_log` JSON (per-story timeline rendered on the
    story detail page)

The migration model is **mirror-write** for the first release cycle:

  1. THIS PR: ship the ``events`` table + an ``emit_event()`` writer
     that any service can call.
  2. Next release: existing emitters (``log_action``,
     ``record_usage``, heal feedback aggregator, story activity
     appender) ALSO write to ``events``. Activity feed + notifications
     read from ``events``; legacy tables continue to fill so we can
     verify parity.
  3. Release after that: switch reads off the legacy tables; keep
     writes for one cycle in case a rollback is needed.
  4. Final release: stop writing to the legacy tables; mark them
     deprecated in DEPLOY.md. A future PR drops the columns when no
     queries reference them.

Why we don't switch in one PR: the legacy tables have ~6 different
shapes + different naming conventions; converting every writer +
reader in a single change is the kind of HIGH-RISK migration the
audit plan said to avoid.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class Event(Base):
    """One row per observable thing-that-happened.

    Schema is deliberately broad so the four legacy tables can map
    onto it without information loss:

      * ``kind`` -- coarse category (audit / prompt_usage / heal /
        story_activity). Indexed for filtered reads.
      * ``action`` -- canonical dotted name from audit_actions.py.
      * ``actor_user_id`` -- the user who triggered it (nullable).
      * ``project_slug`` -- denormalised for fast project-feed reads.
      * ``target_type`` + ``target_id`` -- what the action acted on.
      * ``metadata`` -- JSON blob; per-kind shape lives in
        audit_actions.py (or in the emitter's docstring).
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_kind_created", "kind", "created_at"),
        Index("ix_events_actor_created", "actor_user_id", "created_at"),
        Index("ix_events_project_created", "project_slug", "created_at"),
        Index("ix_events_target", "target_type", "target_id"),
        Index("ix_events_action_created", "action", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="audit")
    action: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    project_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONColumn(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "action": self.action,
            "actor_user_id": self.actor_user_id,
            "project_slug": self.project_slug,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "metadata": dict(self.metadata_json or {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
