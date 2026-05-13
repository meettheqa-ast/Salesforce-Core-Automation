"""Project membership management (Phase 2b).

Endpoints all live under /api/projects/{project_name}/members. Permission
matrix on the routes themselves:

    Action            | TM | TL | PM | Admin
    list members      | Y  | Y  | Y  | Y     (any project member can see who else is in)
    add member        | N  | Y  | Y  | Y     (TL can add; TM cannot)
    change role       | N  | N  | Y  | Y     (PM only -- role changes are sensitive)
    remove member     | N  | N  | Y  | Y     (PM only)

Last-PM constraint: a project must always have at least one PM. Removing or
demoting the last PM returns 409 with a guiding error.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

import project_manager
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    get_current_user,
)
from ai_qa_portal.backend.services.db import (
    ProjectMembership,
    ProjectRole,
    User,
    get_db,
    get_membership,
    get_user_by_email,
    get_user_by_id,
    list_memberships_for_project,
    upsert_membership,
)

router = APIRouter(
    prefix="/api/projects/{project_name}/members",
    tags=["memberships"],
    dependencies=[Depends(get_current_user)],
)


# --- Schemas --------------------------------------------------------------

class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    picture: str
    role: str
    joined_at: str | None = None


class AddMemberBody(BaseModel):
    """Add by email -- the user must already exist (have logged in at least once).
    Phase 2c will introduce invitations for not-yet-logged-in users."""

    email: EmailStr
    role: str = ProjectRole.member.value


class PatchMemberBody(BaseModel):
    role: str


# --- Helpers --------------------------------------------------------------

def _project_or_404(name: str) -> None:
    if not project_manager.project_exists(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


def _to_member_out(m: ProjectMembership, user: User) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name or user.email,
        picture=user.picture or "",
        role=m.role,
        joined_at=m.created_at.isoformat() if m.created_at else None,
    )


def _count_pms(memberships: list[ProjectMembership]) -> int:
    return sum(1 for m in memberships if m.role == ProjectRole.pm.value)


# --- Endpoints ------------------------------------------------------------

@router.get("", response_model=list[MemberOut])
def list_members(
    project_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Any project member can see the roster."""
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.member)
    rows = list_memberships_for_project(db, project_name)
    out: list[MemberOut] = []
    for m in rows:
        u = get_user_by_id(db, m.user_id)
        if u is None:
            continue
        out.append(_to_member_out(m, u))
    # Sort: PM first, Lead next, Member last; alphabetical inside each group.
    rank = {ProjectRole.pm.value: 0, ProjectRole.lead.value: 1, ProjectRole.member.value: 2}
    out.sort(key=lambda x: (rank.get(x.role, 9), x.name.lower()))
    return out


@router.post("", response_model=MemberOut, status_code=201)
def add_member(
    project_name: str,
    body: AddMemberBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """TL/PM/Admin can add a member by email. The invitee must already exist
    in the users table (i.e. have signed in once). Phase 2c will add the
    "invite-by-email-not-yet-registered" flow.
    """
    _project_or_404(project_name)
    # TL is the minimum -- enforces "TM cannot invite".
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.lead)

    role = (body.role or "").strip().lower()
    try:
        role_enum = ProjectRole(role)
    except ValueError:
        raise HTTPException(400, f"role must be one of: {', '.join(r.value for r in ProjectRole)}")

    # Only PM/admin can grant the PM role to someone else (privilege escalation guard).
    if role_enum == ProjectRole.pm:
        assert_project_role_at_least(db, current_user, project_name, ProjectRole.pm)

    target = get_user_by_email(db, body.email)
    if target is None:
        raise HTTPException(
            404,
            "User not found. They must sign in to the portal at least once before "
            "you can add them. (Email-invite for new users lands in Phase 2c.)",
        )
    if not target.is_active:
        raise HTTPException(409, "Target user is deactivated")

    existing = get_membership(db, project_slug=project_name, user_id=target.id)
    if existing:
        raise HTTPException(409, f"{target.email} is already a {existing.role} on this project")

    m = upsert_membership(
        db, project_slug=project_name, user_id=target.id, role=role_enum,
    )
    return _to_member_out(m, target)


@router.patch("/{user_id}", response_model=MemberOut)
def update_member_role(
    project_name: str,
    user_id: str,
    body: PatchMemberBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PM/Admin only. Enforces the last-PM constraint."""
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.pm)

    new_role = (body.role or "").strip().lower()
    try:
        new_role_enum = ProjectRole(new_role)
    except ValueError:
        raise HTTPException(400, f"role must be one of: {', '.join(r.value for r in ProjectRole)}")

    target_membership = get_membership(db, project_slug=project_name, user_id=user_id)
    if target_membership is None:
        raise HTTPException(404, "Membership not found")
    target_user = get_user_by_id(db, user_id)
    if target_user is None:
        raise HTTPException(404, "User not found")

    # Last-PM guard: don't allow demoting the only PM.
    if (
        target_membership.role == ProjectRole.pm.value
        and new_role_enum != ProjectRole.pm
    ):
        all_members = list_memberships_for_project(db, project_name)
        if _count_pms(all_members) <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot demote the last PM of this project. Promote another member to PM first.",
            )

    target_membership.role = new_role_enum.value
    db.commit()
    db.refresh(target_membership)
    return _to_member_out(target_membership, target_user)


@router.delete("/{user_id}", status_code=204)
def remove_member(
    project_name: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PM/Admin only. Enforces the last-PM constraint. PMs can remove themselves
    iff there's another PM on the project; otherwise they have to promote first."""
    _project_or_404(project_name)
    assert_project_role_at_least(db, current_user, project_name, ProjectRole.pm)

    target = get_membership(db, project_slug=project_name, user_id=user_id)
    if target is None:
        raise HTTPException(404, "Membership not found")

    if target.role == ProjectRole.pm.value:
        all_members = list_memberships_for_project(db, project_name)
        if _count_pms(all_members) <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot remove the last PM of this project. Promote another member to PM first, "
                "or delete the project entirely.",
            )

    db.delete(target)
    db.commit()
