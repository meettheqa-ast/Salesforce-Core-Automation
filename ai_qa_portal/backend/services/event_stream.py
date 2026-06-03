"""Unified event-stream writer (Phase 3 IA audit -- foundation).

Single entry point for emitting events into the new ``events`` table.
Today this is OPT-IN: callers can dual-write to the events stream
alongside the existing audit_log / prompt_usage_audit / heal_events
inserts. Next release cycle will move the existing emitters to mirror
writes; the release after that switches reads off the legacy tables.

Why a thin wrapper (vs. inline ORM in each caller):

  * One choke point for the canonical action-name mapping
    (``audit_actions.canonical_name``).
  * One choke point for failure handling -- a stream-write must NEVER
    block the originating request.
  * Lets us add features (webhooks, NATS / Postgres LISTEN broadcast,
    rate-limit alerts) in one place when they land.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from .audit_actions import canonical_name, humanize
from .db_models.events import Event

logger = logging.getLogger("ai_qa_portal.event_stream")


def emit_event(
    db: Session,
    *,
    kind: str,
    action: str,
    actor_user_id: str | None = None,
    project_slug: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Event | None:
    """Append one row to the unified events stream.

    Best-effort: any SQL failure is logged + swallowed so the calling
    business-logic flow continues. The same defensive pattern the
    existing audit / notification emitters use.

    ``action`` is normalised through ``canonical_name`` so legacy
    snake_case names emitted during the mirror-write period land
    under the same canonical name as new dotted-form emissions.
    ``summary`` defaults to a humanized verb-phrase when not given.
    """
    canon = canonical_name(action)
    try:
        row = Event(
            kind=kind,
            action=canon,
            actor_user_id=actor_user_id,
            project_slug=project_slug,
            target_type=target_type,
            target_id=target_id,
            summary=summary or humanize(canon),
            metadata_json=metadata or {},
        )
        db.add(row)
        db.commit()
        return row
    except Exception as exc:  # noqa: BLE001 -- never break business flow
        logger.warning(
            "event_stream emit failed action=%s target=%s/%s: %s",
            canon, target_type, target_id, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None
