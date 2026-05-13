"""Authenticated user-directory search.

Powers the peer combobox in the Add Member form. Distinct from
[`admin.list_users`](admin.py) which is admin-gated and returns the full
admin shape (memberships, last_login, etc.). This endpoint:

  - is open to any signed-in user (no admin / PM gate),
  - never returns admin/internal fields,
  - filters to `settings.allowed_email_domain` so the dropdown only ever
    surfaces people who can actually accept an invite,
  - optionally annotates each hit with project-scoped flags
    (`is_member`, `pending_invite_id`) so the UI can render
    "Already a member" / "Invite pending" badges in one round-trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import (
    InvitationDirection,
    InvitationStatus,
    ProjectInvitation,
    ProjectMembership,
    User,
    get_db,
)

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)],
)


class UserSearchHit(BaseModel):
    id: str
    email: str
    name: str
    picture: str
    is_member: bool = False
    pending_invite_id: str | None = None


@router.get("/search", response_model=list[UserSearchHit])
def search_users(
    q: str = Query("", description="Substring match on email or name (case-insensitive)."),
    project_name: str | None = Query(
        None,
        description=(
            "Optional project slug. When set, each hit is annotated with "
            "`is_member` and `pending_invite_id` so the UI can disable rows "
            "that are already on the project or already invited."
        ),
    ),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[UserSearchHit]:
    # Empty / whitespace q -> empty list. We deliberately don't firehose the
    # full directory; the caller is the Add-Member combobox which only fires
    # after the user has typed at least one character.
    needle = (q or "").strip()
    if not needle:
        return []

    like = f"%{needle.lower()}%"
    query = db.query(User).filter(
        User.is_active.is_(True),
        or_(User.email.ilike(like), User.name.ilike(like)),
    )

    # Same-domain filter so the dropdown matches who can actually log in.
    # Skip the filter when the domain isn't configured (open-to-any-Google
    # account dev mode). Stored emails are lower-cased on first login, but
    # the LIKE here is also lower-bound so this is robust either way.
    domain = (settings.allowed_email_domain or "").strip().lower()
    if domain:
        query = query.filter(User.email.ilike(f"%@{domain}"))

    rows = query.order_by(User.name.asc()).limit(limit).all()

    # Bulk-fetch membership + pending-invite rows for the requested project
    # in one shot rather than N+1 per hit. Both tables key off the slug
    # (not the project UUID -- see ProjectMembership / ProjectInvitation
    # in services/db.py), so no UUID resolution is needed.
    member_user_ids: set[str] = set()
    pending_by_email: dict[str, str] = {}
    slug = (project_name or "").strip()
    if slug and rows:
        user_ids = [u.id for u in rows]
        member_rows = (
            db.query(ProjectMembership.user_id)
            .filter(
                ProjectMembership.project_slug == slug,
                ProjectMembership.user_id.in_(user_ids),
            )
            .all()
        )
        member_user_ids = {r[0] for r in member_rows}

        emails_lower = [u.email.lower() for u in rows]
        invite_rows = (
            db.query(ProjectInvitation.id, ProjectInvitation.email)
            .filter(
                ProjectInvitation.project_slug == slug,
                ProjectInvitation.direction == InvitationDirection.invite.value,
                ProjectInvitation.status == InvitationStatus.pending.value,
                ProjectInvitation.email.in_(emails_lower),
            )
            .all()
        )
        # If somehow there are multiple pending invites for the same email,
        # the most recent one wins (last write wins is fine for "show pill").
        for inv_id, inv_email in invite_rows:
            pending_by_email[(inv_email or "").lower()] = inv_id

    out: list[UserSearchHit] = []
    for u in rows:
        out.append(
            UserSearchHit(
                id=u.id,
                email=u.email,
                name=u.name or "",
                picture=u.picture or "",
                is_member=u.id in member_user_ids,
                pending_invite_id=pending_by_email.get(u.email.lower()),
            )
        )
    return out
