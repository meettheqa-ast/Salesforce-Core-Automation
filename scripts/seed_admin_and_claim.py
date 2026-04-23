"""One-time migration: create the initial admin user and claim all pre-auth data.

Run this AFTER deploying the Phase 1 backend changes but BEFORE the first user
logs in via Google. Without it, every existing project / persona / user-story
will show empty for everyone (only an admin would see them, and you have no
admin yet).

Usage (from the repo root, with .env loaded so DATA_DIR points where it should):

    # Inside Docker / Fly:
    docker compose exec backend python scripts/seed_admin_and_claim.py m.sheth@astounddigital.com

    # Locally (Windows PowerShell), backend container running:
    docker compose exec backend python scripts/seed_admin_and_claim.py m.sheth@astounddigital.com

    # Without Docker (uses your local venv):
    .\\venv\\Scripts\\python.exe scripts\\seed_admin_and_claim.py m.sheth@astounddigital.com

What it does:
    1. Creates (or finds) a User row for the given email, sets is_admin=True.
    2. Walks every Saved_Projects/<slug>/project.json and stamps owner_user_id.
    3. Walks every record in personas.json, orgs.json, and user_story:*.json
       and stamps owner_user_id.
    4. Prints a summary.

Idempotent: safe to re-run. Existing owner_user_id values are NEVER overwritten
unless --force is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import project_manager  # noqa: E402  (sys.path injected above)
from ai_qa_portal.backend.config import settings  # noqa: E402
from ai_qa_portal.backend.services.db import (  # noqa: E402
    SessionLocal,
    User,
    init_db,
)


def _ensure_admin(email: str) -> User:
    db = SessionLocal()
    try:
        email = email.strip().lower()
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            user = User(email=email, name=email.split("@")[0], picture="", is_admin=True)
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"[users] created admin {email}  id={user.id}")
        else:
            if not user.is_admin:
                user.is_admin = True
                db.commit()
                print(f"[users] promoted existing user {email} to admin  id={user.id}")
            else:
                print(f"[users] admin {email} already exists  id={user.id}")
        return user
    finally:
        db.close()


def _claim_projects(admin_id: str, force: bool) -> tuple[int, int]:
    """Stamp owner_user_id into every project.json. Returns (claimed, skipped)."""
    claimed = skipped = 0
    for name in project_manager.list_projects():
        try:
            meta = project_manager.read_project_meta(name)
        except FileNotFoundError:
            continue
        existing = (meta.get("owner_user_id") or "").strip()
        if existing and not force:
            skipped += 1
            continue
        meta["owner_user_id"] = admin_id
        proj_dir = project_manager.get_project_path(name)
        (proj_dir / "project.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        claimed += 1
    return claimed, skipped


def _claim_json_collection(file_path: Path, admin_id: str, force: bool) -> tuple[int, int]:
    """Generic claim for the {"items": [...]} pattern (personas.json, orgs.json)."""
    if not file_path.is_file():
        return 0, 0
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    items = raw.get("items", [])
    claimed = skipped = 0
    for item in items:
        existing = (item.get("owner_user_id") or "").strip()
        if existing and not force:
            skipped += 1
            continue
        item["owner_user_id"] = admin_id
        claimed += 1
    file_path.write_text(json.dumps({"items": items}, indent=2, default=str), encoding="utf-8")
    return claimed, skipped


def _claim_user_stories(data_dir: Path, admin_id: str, force: bool) -> tuple[int, int]:
    """user_story:<uuid>.json one file per story."""
    claimed = skipped = 0
    for p in data_dir.glob("user_story_*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        existing = (row.get("owner_user_id") or "").strip()
        if existing and not force:
            skipped += 1
            continue
        row["owner_user_id"] = admin_id
        p.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        claimed += 1
    return claimed, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Email of the admin to create / promote")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite owner_user_id on records that already have one (default: skip)",
    )
    args = parser.parse_args()

    init_db()
    admin = _ensure_admin(args.email)

    data_dir = Path(settings.data_dir)
    print(f"[paths] data_dir={data_dir}")
    print(f"[paths] saved_projects={project_manager.SAVED_PROJECTS_ROOT}")

    p_claimed, p_skipped = _claim_projects(admin.id, args.force)
    print(f"[projects]      claimed={p_claimed}  skipped(existing)={p_skipped}")

    pers_claimed, pers_skipped = _claim_json_collection(
        data_dir / "personas.json", admin.id, args.force
    )
    print(f"[personas]      claimed={pers_claimed}  skipped(existing)={pers_skipped}")

    orgs_claimed, orgs_skipped = _claim_json_collection(
        data_dir / "orgs.json", admin.id, args.force
    )
    print(f"[orgs]          claimed={orgs_claimed}  skipped(existing)={orgs_skipped}")

    us_claimed, us_skipped = _claim_user_stories(data_dir, admin.id, args.force)
    print(f"[user_stories]  claimed={us_claimed}  skipped(existing)={us_skipped}")

    print("\nDone. The admin can now log in and see all legacy data.")
    print("New users will see empty workspaces until they create their own data,")
    print("or the admin shares (Phase 3) / adds them as members (Phase 2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
