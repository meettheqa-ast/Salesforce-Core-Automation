"""In-app notifications for the current user (Phase 2c).

Routes:
  GET    /api/me/notifications?unread=true       list, optionally only unread
  POST   /api/me/notifications/{id}/read         mark single as read
  POST   /api/me/notifications/read-all          mark all my notifications as read
  GET    /api/me/notifications/unread-count      cheap badge query for the navbar bell
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import (
    Notification,
    User,
    get_db,
)


router = APIRouter(
    prefix="/api/me/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("")
def list_notifications(
    unread: bool = Query(False, description="If true, return only unread"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread:
        q = q.filter(Notification.read_at.is_(None))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return [r.to_dict() for r in rows]


@router.get("/unread-count")
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .count()
    )
    return {"count": n}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .one_or_none()
    )
    if n is None:
        raise HTTPException(404, "Notification not found")
    if n.read_at is None:
        n.read_at = _now()
        db.commit()
        db.refresh(n)
    return n.to_dict()


@router.post("/read-all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .all()
    )
    now = _now()
    for r in rows:
        r.read_at = now
    if rows:
        db.commit()
    return {"marked_read": len(rows)}
