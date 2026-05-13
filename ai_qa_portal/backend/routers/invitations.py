"""Project invitations + self-service join requests (Phase 2c).

Routes:
  POST   /api/projects/{name}/invitations               TL+ to invite by email
  POST   /api/projects/{name}/invitations/batch         TL+ bulk invite
  POST   /api/projects/{name}/invitations/request-access  any logged-in user (self-request)
  GET    /api/projects/{name}/invitations               PM/TL: list pending invites + requests for this project
  POST   /api/invitations/{id}/accept                   invitee only, on a `pending` invite
  POST   /api/invitations/{id}/reject                   invitee/PM/TL/sender depending on direction
  POST   /api/invitations/{id}/approve                  PM/TL on a `requested` request
  POST   /api/invitations/{id}/revoke                   sender / PM / admin
  GET    /api/me/invitations                             my open invites (direction=invite, status=pending) for me to act on
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

import project_manager
from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    effective_project_role,
    get_current_user,
)
from ai_qa_portal.backend.services.db import (
    InvitationDirection,
    InvitationStatus,
    ProjectInvitation,
    ProjectRole,
    User,
    get_db,
    get_membership,
    get_user_by_email,
    list_memberships_for_project,
    push_notification,
    upsert_membership,
)

_DEFAULT_INVITE_TTL_DAYS = 7


def _new_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=_DEFAULT_INVITE_TTL_DAYS)


def _now() -> datetime:
    return datetime.now(UTC)


def _domain_ok(email: str) -> bool:
    """Ensure invited email matches the configured Workspace domain.
    An invitation to an email that can never be accepted (wrong domain) is a bug,
    so we fail closed at create-time."""
    domain = settings.allowed_email_domain.strip().lower()
    if not domain:
        return True
    return email.lower().endswith("@" + domain)


def _project_or_404(name: str) -> None:
    if not project_manager.project_exists(name):
        raise HTTPException(404, "Project not found")


def _expire_if_due(db: Session, inv: ProjectInvitation) -> ProjectInvitation:
    """Lazy expiry check on read -- avoids needing a background sweeper."""
    if inv.status in (InvitationStatus.pending.value, InvitationStatus.requested.value):
        if inv.expires_at and inv.expires_at < _now():
            inv.status = InvitationStatus.expired.value
            inv.resolved_at = _now()
            db.commit()
            db.refresh(inv)
    return inv


def _notify_project_managers(
    db: Session, project_slug: str, *, type: str, title: str, body: str, action_url: str,
) -> None:
    for m in list_memberships_for_project(db, project_slug):
        if m.role in (ProjectRole.pm.value, ProjectRole.lead.value):
            push_notification(
                db, user_id=m.user_id, type=type, title=title, body=body, action_url=action_url,
            )


# -------------------------------------------------------------------------
# Project-scoped routes (under /api/projects/{name}/invitations)
# -------------------------------------------------------------------------

project_router = APIRouter(
    prefix="/api/projects/{project_name}/invitations",
    tags=["invitations"],
    dependencies=[Depends(get_current_user)],
)


class CreateInviteBody(BaseModel):
    email: EmailStr
    role: str = ProjectRole.member.value


class BatchInviteBody(BaseModel):
    invites: list[CreateInviteBody]


def _create_one_invite(
    db: Session,
    *,
    current_user: User,
    project_name: str,
    email: str,
    role: str,
) -> ProjectInvitation:
    if not _domain_ok(email):
        raise HTTPException(
            400,
            f"Invitations are restricted to @{settings.allowed_email_domain} addresses",
        )

    try:
        role_enum = ProjectRole(role)
    except ValueError:
        raise HTTPException(400, f"role must be one of: {', '.join(r.value for r in ProjectRole)}")

    if role_enum == ProjectRole.pm:
        # Same guard as direct add: only PM/admin can grant PM.
        assert_project_role_at_least(db, current_user, project_name, ProjectRole.pm)

    # If the invitee already exists and is already a member, short-circuit.
    existing_user = get_user_by_email(db, email)
    if existing_user is not None:
        m = get_membership(db, project_slug=project_name, user_id=existing_user.id)
        if m is not None:
            raise HTTPException(409, f"{email} is already a {m.role} on this project")

    # If a pending invite already exists for this email/project, refresh expiry instead of duplicating.
    existing_inv = (
        db.query(ProjectInvitation)
        .filter(
            ProjectInvitation.project_slug == project_name,
            ProjectInvitation.email == email.lower(),
            ProjectInvitation.direction == InvitationDirection.invite.value,
            ProjectInvitation.status == InvitationStatus.pending.value,
        )
        .one_or_none()
    )
    if existing_inv is not None:
        existing_inv.role = role_enum.value
        existing_inv.expires_at = _new_expiry()
        existing_inv.invited_by_user_id = current_user.id
        db.commit()
        db.refresh(existing_inv)
        inv = existing_inv
    else:
        inv = ProjectInvitation(
            project_slug=project_name,
            email=email.lower(),
            role=role_enum.value,
            direction=InvitationDirection.invite.value,
            status=InvitationStatus.pending.value,
            invited_by_user_id=current_user.id,
            expires_at=_new_expiry(),
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)

    # If the invitee already has a User row, drop them an in-app notification.
    if existing_user is not None:
        push_notification(
            db,
            user_id=existing_user.id,
            type="project_invitation",
            title=f"You've been invited to {project_name}",
            body=f"{current_user.name or current_user.email} added you as {role_enum.value}.",
            action_url="/invitations",
        )
    return inv


@project_router.post("", status_code=201)
def invite_member(
    project_name: str,
    body: CreateInviteBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.lead)
    inv = _create_one_invite(
        db, current_user=current_user, project_name=project_name,
        email=body.email, role=body.role,
    )
    return inv.to_dict()


@project_router.post("/batch")
def batch_invite(
    project_name: str,
    body: BatchInviteBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.lead)
    created: list[dict] = []
    errors: list[dict] = []
    for entry in body.invites:
        try:
            inv = _create_one_invite(
                db, current_user=current_user, project_name=project_name,
                email=entry.email, role=entry.role,
            )
            created.append(inv.to_dict())
        except HTTPException as exc:
            errors.append({"email": entry.email, "error": exc.detail})
    return {"created": created, "errors": errors}


@project_router.post("/request-access", status_code=201)
def request_access(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service: an authenticated user asks PM/TL of a project to add them as a member."""
    _project_or_404(project_name)

    # Already a member? Nothing to request.
    existing = get_membership(db, project_slug=project_name, user_id=current_user.id)
    if existing is not None:
        raise HTTPException(409, "You are already a member of this project")

    # Already pending request from same user?
    existing_req = (
        db.query(ProjectInvitation)
        .filter(
            ProjectInvitation.project_slug == project_name,
            ProjectInvitation.email == current_user.email,
            ProjectInvitation.direction == InvitationDirection.request.value,
            ProjectInvitation.status == InvitationStatus.requested.value,
        )
        .one_or_none()
    )
    if existing_req is not None:
        return existing_req.to_dict()

    inv = ProjectInvitation(
        project_slug=project_name,
        email=current_user.email,
        role=ProjectRole.member.value,
        direction=InvitationDirection.request.value,
        status=InvitationStatus.requested.value,
        invited_by_user_id=current_user.id,
        expires_at=_new_expiry(),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)

    _notify_project_managers(
        db, project_name,
        type="project_access_request",
        title=f"{current_user.name or current_user.email} requested access to {project_name}",
        body="Open the project's Members page to approve or reject.",
        action_url=f"/projects/{project_name}/members",
    )
    return inv.to_dict()


@project_router.get("")
def list_project_invitations(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open invites + access requests for this project. PM/TL only."""
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.lead)
    rows = (
        db.query(ProjectInvitation)
        .filter(
            ProjectInvitation.project_slug == project_name,
            ProjectInvitation.status.in_(
                [InvitationStatus.pending.value, InvitationStatus.requested.value]
            ),
        )
        .order_by(ProjectInvitation.created_at.desc())
        .all()
    )
    return [_expire_if_due(db, r).to_dict() for r in rows]


# -------------------------------------------------------------------------
# Invitation-scoped routes (under /api/invitations/{id})
# -------------------------------------------------------------------------

inv_router = APIRouter(
    prefix="/api/invitations",
    tags=["invitations"],
    dependencies=[Depends(get_current_user)],
)


def _get_invitation(db: Session, inv_id: str) -> ProjectInvitation:
    inv = (
        db.query(ProjectInvitation)
        .filter(ProjectInvitation.id == inv_id)
        .one_or_none()
    )
    if inv is None:
        raise HTTPException(404, "Invitation not found")
    return _expire_if_due(db, inv)


@inv_router.post("/{inv_id}/accept")
def accept_invitation(
    inv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invitee accepts a pending invite. Direction must be `invite`."""
    inv = _get_invitation(db, inv_id)
    if inv.direction != InvitationDirection.invite.value:
        raise HTTPException(400, "Only invitations can be accepted; access requests are approved by PM/TL")
    if inv.status != InvitationStatus.pending.value:
        raise HTTPException(409, f"Invitation is {inv.status}, not pending")
    if inv.email != current_user.email:
        raise HTTPException(403, "This invitation isn't addressed to you")

    upsert_membership(
        db, project_slug=inv.project_slug, user_id=current_user.id,
        role=ProjectRole(inv.role),
    )
    inv.status = InvitationStatus.accepted.value
    inv.resolved_at = _now()
    db.commit()
    db.refresh(inv)

    if inv.invited_by_user_id:
        push_notification(
            db,
            user_id=inv.invited_by_user_id,
            type="invitation_accepted",
            title=f"{current_user.name or current_user.email} joined {inv.project_slug}",
            body=f"They were added as {inv.role}.",
            action_url=f"/projects/{inv.project_slug}/members",
        )
    return inv.to_dict()


@inv_router.post("/{inv_id}/reject")
def reject_invitation(
    inv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject. For `invite`: only the invitee can reject. For `request`: PM/TL of the project can reject."""
    inv = _get_invitation(db, inv_id)
    if inv.status not in (InvitationStatus.pending.value, InvitationStatus.requested.value):
        raise HTTPException(409, f"Invitation is {inv.status}; cannot reject")

    if inv.direction == InvitationDirection.invite.value:
        if inv.email != current_user.email:
            raise HTTPException(403, "Only the invitee can reject this invitation")
    else:  # request
        assert_project_role_at_least(
            db, current_user, inv.project_slug, ProjectRole.lead,
        )

    inv.status = InvitationStatus.rejected.value
    inv.resolved_at = _now()
    db.commit()
    db.refresh(inv)

    # Notify the other side
    if inv.direction == InvitationDirection.invite.value and inv.invited_by_user_id:
        push_notification(
            db, user_id=inv.invited_by_user_id, type="invitation_rejected",
            title=f"{current_user.email} declined the {inv.project_slug} invitation",
            body="", action_url=f"/projects/{inv.project_slug}/members",
        )
    elif inv.direction == InvitationDirection.request.value:
        # Notify the requester (their User row may not exist yet -- guard).
        requester = get_user_by_email(db, inv.email)
        if requester is not None:
            push_notification(
                db, user_id=requester.id, type="access_request_rejected",
                title=f"Your access request for {inv.project_slug} was declined",
                body="", action_url="/projects",
            )
    return inv.to_dict()


@inv_router.post("/{inv_id}/approve")
def approve_request(
    inv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PM/TL approves a self-service access request. Creates the membership."""
    inv = _get_invitation(db, inv_id)
    if inv.direction != InvitationDirection.request.value:
        raise HTTPException(400, "approve is for self-service requests; invitations are accept-ed by the invitee")
    if inv.status != InvitationStatus.requested.value:
        raise HTTPException(409, f"Request is {inv.status}, not requested")
    assert_project_role_at_least(db, current_user, inv.project_slug, ProjectRole.lead)

    requester = get_user_by_email(db, inv.email)
    if requester is None:
        raise HTTPException(404, "Requester user no longer exists")

    upsert_membership(
        db, project_slug=inv.project_slug, user_id=requester.id,
        role=ProjectRole(inv.role),
    )
    inv.status = InvitationStatus.approved.value
    inv.resolved_at = _now()
    db.commit()
    db.refresh(inv)

    push_notification(
        db, user_id=requester.id, type="access_request_approved",
        title=f"Your access to {inv.project_slug} was approved",
        body=f"You were added as {inv.role}.",
        action_url=f"/projects/{inv.project_slug}",
    )
    return inv.to_dict()


@inv_router.post("/{inv_id}/revoke")
def revoke_invitation(
    inv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sender or any PM of the project can cancel a pending invite/request."""
    inv = _get_invitation(db, inv_id)
    if inv.status not in (InvitationStatus.pending.value, InvitationStatus.requested.value):
        raise HTTPException(409, f"Invitation is {inv.status}; cannot revoke")

    can_revoke = (
        inv.invited_by_user_id == current_user.id
        or current_user.is_admin
        or effective_project_role(db, current_user, inv.project_slug) == ProjectRole.pm
    )
    if not can_revoke:
        raise HTTPException(403, "Only the sender or a PM/admin can revoke this")

    inv.status = InvitationStatus.revoked.value
    inv.resolved_at = _now()
    db.commit()
    db.refresh(inv)
    return inv.to_dict()


# -------------------------------------------------------------------------
# Per-user routes (under /api/me/invitations)
# -------------------------------------------------------------------------

me_router = APIRouter(
    prefix="/api/me",
    tags=["invitations"],
    dependencies=[Depends(get_current_user)],
)


@me_router.get("/invitations")
def my_open_invitations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open invites addressed to me, awaiting my accept."""
    rows = (
        db.query(ProjectInvitation)
        .filter(
            ProjectInvitation.email == current_user.email,
            ProjectInvitation.direction == InvitationDirection.invite.value,
            ProjectInvitation.status == InvitationStatus.pending.value,
        )
        .order_by(ProjectInvitation.created_at.desc())
        .all()
    )
    return [_expire_if_due(db, r).to_dict() for r in rows]
