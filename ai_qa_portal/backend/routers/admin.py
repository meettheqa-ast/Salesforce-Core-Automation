"""Admin Console (Phase 2f) -- system-wide views + user management.

All endpoints are gated on `is_admin`. Admin's powers (per the locked
architecture decision):

  CAN, system-wide:
    - See all projects + member counts
    - List/search/manage all users (promote/demote global_role, deactivate, soft delete)
    - See cross-cut user-project membership matrix
    - View audit log + persona reveal log (just an audit-log filter for now)
    - Force-revoke a user's session
    - Transfer a project's PM-ship when a user leaves the company

  CANNOT (deliberate -- credential vault is sealed even from admins):
    - Reveal another user's persona password
    - Edit credentials of a persona they didn't create
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

import project_manager
from ai_qa_portal.backend.services.audit import log_action
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import (
    AuditLog,
    GlobalRole,
    ProjectMembership,
    ProjectRole,
    User,
    get_db,
    get_user_by_id,
    list_memberships_for_project,
    list_memberships_for_user,
    upsert_membership,
)


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Inline guard -- 403 unless the user is admin (is_admin or global_role)."""
    if not current_user.is_admin and current_user.global_role != GlobalRole.admin.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return current_user


router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(_require_admin)],
)


# --- Schemas --------------------------------------------------------------

class AdminUserOut(BaseModel):
    id: str
    email: str
    name: str
    picture: str
    is_admin: bool
    global_role: str
    is_active: bool
    created_at: str | None = None
    last_login_at: str | None = None
    membership_count: int = 0


class AdminUserDetailOut(AdminUserOut):
    memberships: list[dict]


class PatchUserBody(BaseModel):
    global_role: str | None = None
    is_active: bool | None = None


class TransferProjectsBody(BaseModel):
    """Reassign every project the source user is sole PM of, to the target user.
    The target user is added to those projects as PM if they're not already
    a member. Used when an employee leaves the company."""

    to_user_id: str


class AdminProjectOut(BaseModel):
    name: str
    display_name: str
    description: str
    member_count: int
    pm_count: int
    pms: list[dict]   # {user_id, email, name}


# --- /users ---------------------------------------------------------------

@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    q: str | None = Query(None, description="Search by email or name (case-insensitive substring)"),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(User.email.ilike(like), User.name.ilike(like)))
    rows = query.order_by(User.created_at.desc()).all()
    out: list[AdminUserOut] = []
    for u in rows:
        m_count = len(list_memberships_for_user(db, u.id))
        d = u.to_dict()
        d["membership_count"] = m_count
        out.append(AdminUserOut(**d))
    return out


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
):
    u = get_user_by_id(db, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    memberships = [m.to_dict() for m in list_memberships_for_user(db, u.id)]
    d = u.to_dict()
    d["membership_count"] = len(memberships)
    d["memberships"] = memberships
    return AdminUserDetailOut(**d)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def patch_user(
    user_id: str,
    body: PatchUserBody,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    u = get_user_by_id(db, user_id)
    if u is None:
        raise HTTPException(404, "User not found")

    changed_fields: dict[str, object] = {}

    if body.global_role is not None:
        try:
            role_enum = GlobalRole(body.global_role)
        except ValueError:
            raise HTTPException(
                400,
                f"global_role must be one of: {', '.join(r.value for r in GlobalRole)}",
            )
        if u.global_role != role_enum.value:
            u.global_role = role_enum.value
            # Keep is_admin in sync.
            u.is_admin = role_enum == GlobalRole.admin
            changed_fields["global_role"] = role_enum.value

    if body.is_active is not None and body.is_active != u.is_active:
        u.is_active = body.is_active
        changed_fields["is_active"] = body.is_active

    if changed_fields:
        # Last-admin guard: don't let the only admin demote/deactivate themselves
        # into an admin-less state.
        if (changed_fields.get("global_role") not in (None, GlobalRole.admin.value)
                or changed_fields.get("is_active") is False):
            other_active_admins = (
                db.query(User)
                .filter(
                    User.id != u.id,
                    User.is_admin.is_(True),
                    User.is_active.is_(True),
                )
                .count()
            )
            if other_active_admins == 0 and (u.id == current_user.id or not u.is_admin):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Cannot demote/deactivate the last active admin. "
                    "Promote another user to admin first.",
                )
        db.commit()
        db.refresh(u)
        log_action(
            db, user=current_user,
            action="admin_patch_user", target_type="user", target_id=u.id,
            metadata=changed_fields,
        )

    d = u.to_dict()
    d["membership_count"] = len(list_memberships_for_user(db, u.id))
    return AdminUserOut(**d)


@router.post("/users/{user_id}/revoke-session")
def revoke_session(
    user_id: str,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Bump session_revoked_at so any token issued before now is rejected."""
    u = get_user_by_id(db, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    u.session_revoked_at = datetime.now(UTC)
    db.commit()
    db.refresh(u)
    log_action(
        db, user=current_user,
        action="admin_revoke_session", target_type="user", target_id=u.id,
        metadata={"session_revoked_at": u.session_revoked_at.isoformat()},
    )
    return {"user_id": u.id, "session_revoked_at": u.session_revoked_at.isoformat()}


@router.post("/users/{user_id}/transfer-projects")
def transfer_projects(
    user_id: str,
    body: TransferProjectsBody,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """For every project this user is the sole PM of, add the target user as PM.
    Does NOT remove the source user's membership -- that's a separate decision
    (often you want to keep them around for audit attribution after they leave)."""
    src = get_user_by_id(db, user_id)
    if src is None:
        raise HTTPException(404, "Source user not found")
    tgt = get_user_by_id(db, body.to_user_id)
    if tgt is None:
        raise HTTPException(404, "Target user not found")
    if src.id == tgt.id:
        raise HTTPException(400, "Source and target are the same user")

    transferred: list[str] = []
    for src_m in list_memberships_for_user(db, src.id):
        if src_m.role != ProjectRole.pm.value:
            continue
        all_pms = [
            m for m in list_memberships_for_project(db, src_m.project_slug)
            if m.role == ProjectRole.pm.value
        ]
        if len(all_pms) > 1:
            continue  # not sole PM, no need to transfer
        # Promote target to PM on this project.
        upsert_membership(
            db, project_slug=src_m.project_slug, user_id=tgt.id, role=ProjectRole.pm,
        )
        transferred.append(src_m.project_slug)

    log_action(
        db, user=current_user,
        action="admin_transfer_projects", target_type="user", target_id=src.id,
        metadata={"to_user_id": tgt.id, "transferred": transferred},
    )
    return {"transferred": transferred, "count": len(transferred)}


# --- /projects (system-wide list) ----------------------------------------

@router.get("/projects", response_model=list[AdminProjectOut])
def list_all_projects(db: Session = Depends(get_db)):
    out: list[AdminProjectOut] = []
    for slug in project_manager.list_projects():
        try:
            meta = project_manager.read_project_meta(slug)
        except FileNotFoundError:
            continue
        members = list_memberships_for_project(db, slug)
        pm_rows = [m for m in members if m.role == ProjectRole.pm.value]
        pms = []
        for m in pm_rows:
            u = get_user_by_id(db, m.user_id)
            if u is None:
                continue
            pms.append({"user_id": u.id, "email": u.email, "name": u.name or u.email})
        out.append(AdminProjectOut(
            name=slug,
            display_name=meta.get("display_name") or slug,
            description=meta.get("description") or "",
            member_count=len(members),
            pm_count=len(pm_rows),
            pms=pms,
        ))
    return out


# --- /membership-matrix --------------------------------------------------

@router.get("/membership-matrix")
def membership_matrix(db: Session = Depends(get_db)):
    """Denormalised dump of (user, project, role) for the cross-cut admin view."""
    rows = []
    for m in db.query(ProjectMembership).all():
        u = get_user_by_id(db, m.user_id)
        if u is None:
            continue
        rows.append({
            "user_id": u.id,
            "email": u.email,
            "name": u.name or u.email,
            "project_slug": m.project_slug,
            "role": m.role,
        })
    return rows


# --- /audit ---------------------------------------------------------------

@router.get("/audit")
def list_audit(
    user_id: str | None = Query(None),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    rows = q.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    out = []
    for r in rows:
        d = r.to_dict()
        if r.user_id:
            u = get_user_by_id(db, r.user_id)
            if u:
                d["user_email"] = u.email
                d["user_name"] = u.name
        out.append(d)
    return out
