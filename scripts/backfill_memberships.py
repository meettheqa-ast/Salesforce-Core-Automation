"""Phase 2a migration: ensure every project has at least one PM membership.

For each project on disk:
  - If project.json has owner_user_id and that user exists, ensure they have
    a PM membership row in `project_memberships`.
  - If owner_user_id is empty (legacy unowned project), assign each
    INITIAL_ADMINS user as a PM (so admins always have access).

Idempotent. Safe to re-run.

Usage:
    docker compose exec backend python scripts/backfill_memberships.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import project_manager  # noqa: E402
from ai_qa_portal.backend.config import settings  # noqa: E402
from ai_qa_portal.backend.services.db import (  # noqa: E402
    GlobalRole,
    ProjectRole,
    SessionLocal,
    User,
    get_user_by_email,
    get_user_by_id,
    init_db,
    upsert_membership,
)


def main() -> int:
    init_db()

    db = SessionLocal()
    try:
        # Sync drift: any user already flagged is_admin should also have
        # global_role='admin'. SQLAlchemy 2.0 needs .is_(True) here, not == True.
        admin_users = (
            db.query(User)
            .filter(User.is_admin.is_(True), User.global_role != GlobalRole.admin.value)
            .all()
        )
        for u in admin_users:
            u.global_role = GlobalRole.admin.value
        if admin_users:
            db.commit()
            print(f"[users] synced global_role=admin for {len(admin_users)} existing is_admin user(s)")

        admins = []
        for raw in settings.initial_admins.split(","):
            email = raw.strip().lower()
            if not email:
                continue
            user = get_user_by_email(db, email)
            if user is None:
                # Pre-create the admin user row so they have something to attach
                # memberships to even before they ever log in.
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    is_admin=True,
                    global_role=GlobalRole.admin.value,
                    is_active=True,
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                print(f"[users] pre-created admin {email} id={user.id}")
            elif not user.is_admin:
                user.is_admin = True
                user.global_role = GlobalRole.admin.value
                db.commit()
                db.refresh(user)
                print(f"[users] promoted existing user {email} to admin")
            admins.append(user)

        counts = {"projects": 0, "owner_pm_seeded": 0, "admin_pm_seeded": 0, "skipped": 0}

        for slug in project_manager.list_projects():
            counts["projects"] += 1
            owner_id = project_manager.get_project_owner(slug)
            if owner_id:
                owner = get_user_by_id(db, owner_id)
                if owner is not None:
                    upsert_membership(
                        db, project_slug=slug, user_id=owner.id, role=ProjectRole.pm,
                    )
                    counts["owner_pm_seeded"] += 1
                    continue
            # Fallback: no recognised owner -> seed admins as PM so the project
            # isn't orphaned. Admins also have implicit PM access via
            # effective_project_role, so this is belt-and-braces.
            for admin in admins:
                upsert_membership(
                    db, project_slug=slug, user_id=admin.id, role=ProjectRole.pm,
                )
                counts["admin_pm_seeded"] += 1
            if not admins:
                counts["skipped"] += 1

        print(
            f"[memberships] projects={counts['projects']} "
            f"owner_pm={counts['owner_pm_seeded']} "
            f"admin_pm={counts['admin_pm_seeded']} "
            f"skipped(no admins)={counts['skipped']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
