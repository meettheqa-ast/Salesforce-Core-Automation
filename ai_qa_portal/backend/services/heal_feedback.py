"""Aggregation helpers for heal_events -> org_field_learnings."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from ai_qa_portal.backend.services.db import SessionLocal

logger = logging.getLogger("ai_qa_portal.heal_feedback")


def aggregate_org_field_learnings(*, project_slug: str | None = None, lookback_days: int = 90) -> dict[str, int]:
    """Recompute learning aggregates from heal_events for the lookback window."""
    from ai_qa_portal.backend.services.db_models.heal import HealEvent, OrgFieldLearning

    since = datetime.now(UTC) - timedelta(days=max(1, lookback_days))
    with SessionLocal() as session:
        q = session.query(HealEvent).filter(HealEvent.created_at >= since)
        if project_slug:
            q = q.filter(HealEvent.project_slug == project_slug)
        events = q.all()

        bucket: dict[tuple[str, str, str, str], dict[str, int | str]] = defaultdict(
            lambda: {
                "total": 0,
                "success": 0,
                "failure": 0,
                "last_success_strategy": "",
                "last_failed_strategy": "",
            }
        )
        for ev in events:
            payload = dict(ev.payload or {})
            classified = payload.get("classified") or []
            field_api_name = ""
            error_type = ev.error_type or ""
            if classified and isinstance(classified, list):
                first = classified[0] or {}
                field_api_name = str(first.get("field_api_name") or first.get("field_label") or "")
                error_type = str(first.get("error_type") or error_type)
            if not field_api_name:
                continue
            key = (str(ev.project_slug or ""), str(ev.sobject or ""), field_api_name, error_type)
            row = bucket[key]
            row["total"] = int(row["total"]) + 1
            success = ev.outcome == "passed"
            if success:
                row["success"] = int(row["success"]) + 1
                row["last_success_strategy"] = ev.strategy or str(row["last_success_strategy"])
            else:
                row["failure"] = int(row["failure"]) + 1
                row["last_failed_strategy"] = ev.strategy or str(row["last_failed_strategy"])

        updated = 0
        for (slug, sobject, field_api_name, error_type), row in bucket.items():
            target = (
                session.query(OrgFieldLearning)
                .filter(OrgFieldLearning.project_slug == slug)
                .filter(OrgFieldLearning.sobject == sobject)
                .filter(OrgFieldLearning.field_api_name == field_api_name)
                .filter(OrgFieldLearning.error_type == error_type)
                .one_or_none()
            )
            if target is None:
                target = OrgFieldLearning(
                    project_slug=slug,
                    sobject=sobject,
                    field_api_name=field_api_name,
                    error_type=error_type,
                )
            target.total_count = int(row["total"])
            target.success_count = int(row["success"])
            target.failure_count = int(row["failure"])
            target.last_success_strategy = str(row["last_success_strategy"] or "") or None
            target.last_failed_strategy = str(row["last_failed_strategy"] or "") or None
            target.updated_at = datetime.now(UTC)
            session.add(target)
            updated += 1
        session.commit()
        return {"events_scanned": len(events), "learning_rows_updated": updated}

