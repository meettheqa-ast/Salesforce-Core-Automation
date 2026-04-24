"""Append-only audit logging helper. Used across routers via `log_action()`."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .db import AuditLog, User

logger = logging.getLogger("ai_qa_portal.audit")


def log_action(
    db: Session,
    *,
    user: User | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Record one row in the audit log. Caller does NOT need to commit
    separately -- this commits on its own so an audit-write is independent
    from the surrounding transaction.

    Best-effort: if the DB write fails (rare), we log a warning but do not
    raise -- losing an audit row should not 500 the actual user request.
    """
    try:
        row = AuditLog(
            user_id=user.id if user else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            metadata_json=json.dumps(metadata, default=str)[:2000] if metadata else "",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        logger.warning("audit write failed action=%s target=%s/%s err=%s",
                       action, target_type, target_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return AuditLog(
            user_id=user.id if user else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
        )
