"""Tests for the create-side RBAC helper + the ``sprint_id``-on-create
fix, both of which landed together in the create-anywhere UX pass.

Coverage:
  * ``user_can_create_in_project`` resolution order: admin > filesystem
    owner (legacy) > DB membership at >= min role > deny.
  * ``POST /user-stories`` honors ``body.sprint_id`` (used to be silently
    dropped) and rejects cross-project sprint_ids with a 400.

The RBAC tests swap the singleton DB ``engine`` to an in-memory SQLite
for the duration of the test so we don't touch the real users.db that
the dev backend writes to.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai_qa_portal.backend.services import db as db_module
from ai_qa_portal.backend.services.db import (
    Base,
    ProjectMembership,
    ProjectRole,
    User,
)

# ── Shared in-memory DB fixture ──────────────────────────────────────


@pytest.fixture
def memdb(monkeypatch):
    """Yield a SQLAlchemy ``Session`` bound to a throwaway in-memory
    SQLite. Keeps the real users.db pristine and gives every test a
    clean slate."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_local()
    # Some helpers (``get_or_create_user`` etc.) reference the module's
    # ``SessionLocal`` indirectly via ``get_db``. We never call that path
    # in these tests, but if a future test does, monkeypatching keeps it
    # safe.
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    try:
        yield db
    finally:
        db.close()


def _mk_user(memdb, *, email: str, is_admin: bool = False) -> User:
    user = User(
        email=email.lower(),
        name=email.split("@")[0],
        is_admin=is_admin,
        global_role="admin" if is_admin else "user",
        is_active=True,
    )
    memdb.add(user)
    memdb.commit()
    memdb.refresh(user)
    return user


def _mk_membership(memdb, *, slug: str, user_id: str, role: ProjectRole) -> None:
    memdb.add(ProjectMembership(
        project_slug=slug,
        user_id=user_id,
        role=role.value,
    ))
    memdb.commit()


# ── user_can_create_in_project resolution order ─────────────────────


def test_admin_can_create_in_any_project(memdb, monkeypatch):
    """Admin short-circuits before any slug/membership lookup."""
    from ai_qa_portal.backend.services import auth

    admin = _mk_user(memdb, email="admin@example.com", is_admin=True)
    pid = uuid4()
    # Ensure we'd FAIL the slug lookup, to prove admin really did
    # short-circuit -- if the helper called slug_for_project_id it'd
    # return None and we'd reach the False branch.
    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: None,
    )
    assert auth.user_can_create_in_project(memdb, admin, pid) is True


def test_filesystem_owner_can_create_legacy(memdb, monkeypatch):
    """Projects that pre-date the DB membership table only have an
    owner on disk; that fallback path must keep working."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="legacy@example.com")
    pid = uuid4()
    slug = "legacy-project"

    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: slug,
    )
    # Stub out project_manager.get_project_owner to return our user.
    import project_manager
    monkeypatch.setattr(project_manager, "get_project_owner", lambda s: user.id if s == slug else None)

    # No DB membership at all -- owner-on-disk should still pass.
    assert auth.user_can_create_in_project(memdb, user, pid) is True


def test_db_lead_member_can_create(memdb, monkeypatch):
    """A user with ``lead`` role on a project (granted via the
    members UI) can create sprints/stories even if they are not the
    filesystem owner."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="lead@example.com")
    pid = uuid4()
    slug = "team-project"

    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: slug,
    )
    import project_manager
    # Different user owns the filesystem (the original creator).
    monkeypatch.setattr(project_manager, "get_project_owner", lambda _s: "another-user-id")

    _mk_membership(memdb, slug=slug, user_id=user.id, role=ProjectRole.lead)
    assert auth.user_can_create_in_project(memdb, user, pid) is True


def test_db_pm_member_can_create(memdb, monkeypatch):
    """PM is above the default lead threshold so it must also pass."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="pm@example.com")
    pid = uuid4()
    slug = "pm-project"

    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: slug,
    )
    import project_manager
    monkeypatch.setattr(project_manager, "get_project_owner", lambda _s: "another-user-id")

    _mk_membership(memdb, slug=slug, user_id=user.id, role=ProjectRole.pm)
    assert auth.user_can_create_in_project(memdb, user, pid) is True


def test_plain_member_cannot_create(memdb, monkeypatch):
    """Plain ``member`` is below the lead threshold -- explicitly denied."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="member@example.com")
    pid = uuid4()
    slug = "ic-project"

    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: slug,
    )
    import project_manager
    monkeypatch.setattr(project_manager, "get_project_owner", lambda _s: "another-user-id")

    _mk_membership(memdb, slug=slug, user_id=user.id, role=ProjectRole.member)
    assert auth.user_can_create_in_project(memdb, user, pid) is False


def test_no_membership_no_ownership_denied(memdb, monkeypatch):
    """User with no membership and not the filesystem owner -> deny."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="stranger@example.com")
    pid = uuid4()
    slug = "private-project"

    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: slug,
    )
    import project_manager
    monkeypatch.setattr(project_manager, "get_project_owner", lambda _s: "owner-elsewhere")

    assert auth.user_can_create_in_project(memdb, user, pid) is False


def test_unknown_project_denied(memdb, monkeypatch):
    """When the slug resolver returns None (project not in registry),
    the helper must fail closed -- no slug means no way to verify
    ownership or membership safely."""
    from ai_qa_portal.backend.services import auth

    user = _mk_user(memdb, email="user@example.com")
    monkeypatch.setattr(
        "ai_qa_portal.backend.project_registry.slug_for_project_id",
        lambda _pid: None,
    )
    assert auth.user_can_create_in_project(memdb, user, uuid4()) is False


# ── POST /user-stories sprint_id wiring ──────────────────────────────


def test_create_user_story_handler_honors_sprint_id(monkeypatch, tmp_path):
    """The handler used to silently drop ``body.sprint_id`` -- prompts
    like 'add a story to this sprint' silently dropped to backlog. The
    fix is to copy ``body.sprint_id`` onto the new row after a
    cross-project guard."""
    from ai_qa_portal.backend.models.user_story import UserStoryCreate
    from ai_qa_portal.backend.routers import user_stories as ust
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    pid = uuid4()
    sprint_id = uuid4()
    # Seed a sprint row in the same project so the cross-project guard
    # finds it. Backend stores sprints in the same JsonFileBackend.
    store = JsonFileBackend(str(tmp_path))
    store.save_sprint({
        "id": str(sprint_id),
        "project_id": str(pid),
        "name": "Test Sprint",
        "goal": None,
        "state": "planned",
        "start_date": None,
        "end_date": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "owner_user_id": "u-1",
    })
    monkeypatch.setattr(ust, "_store", store)
    # Bypass the auth helper -- we're testing the handler logic, not
    # the RBAC check (covered above).
    monkeypatch.setattr(ust, "assert_user_can_create_in_project", lambda *a, **kw: None)

    user = User(id="u-1", email="u@e.com", name="U", is_active=True)

    body = UserStoryCreate(
        project_id=pid,
        title="Story in sprint",
        description="desc",
        sprint_id=sprint_id,
    )
    story = asyncio.run(ust.create_user_story(body, current_user=user, db=None))
    assert story.sprint_id == sprint_id, (
        "POST /user-stories must persist body.sprint_id; the bug was a "
        "silent drop to backlog"
    )
    assert story.project_id == pid


def test_create_user_story_rejects_cross_project_sprint(monkeypatch, tmp_path):
    """A sprint from project A cannot be used as the home sprint for a
    story being created in project B. Mirrors the same guard the
    sprint-assign endpoint enforces."""
    from fastapi import HTTPException

    from ai_qa_portal.backend.models.user_story import UserStoryCreate
    from ai_qa_portal.backend.routers import user_stories as ust
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    project_a = uuid4()
    project_b = uuid4()
    sprint_in_a = uuid4()
    store = JsonFileBackend(str(tmp_path))
    store.save_sprint({
        "id": str(sprint_in_a),
        "project_id": str(project_a),
        "name": "Sprint in A",
        "goal": None,
        "state": "planned",
        "start_date": None,
        "end_date": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "owner_user_id": "u-1",
    })
    monkeypatch.setattr(ust, "_store", store)
    monkeypatch.setattr(ust, "assert_user_can_create_in_project", lambda *a, **kw: None)

    user = User(id="u-1", email="u@e.com", name="U", is_active=True)

    body = UserStoryCreate(
        project_id=project_b,  # different project than the sprint
        title="Cross-project attempt",
        description="should fail",
        sprint_id=sprint_in_a,
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ust.create_user_story(body, current_user=user, db=None))
    assert exc_info.value.status_code == 400
    assert "different project" in str(exc_info.value.detail).lower()


def test_create_user_story_no_sprint_works(monkeypatch, tmp_path):
    """Backlog stories (no sprint_id) keep working with the same
    handler -- the sprint check is skipped when sprint_id is None."""
    from ai_qa_portal.backend.models.user_story import UserStoryCreate
    from ai_qa_portal.backend.routers import user_stories as ust
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    store = JsonFileBackend(str(tmp_path))
    monkeypatch.setattr(ust, "_store", store)
    monkeypatch.setattr(ust, "assert_user_can_create_in_project", lambda *a, **kw: None)

    user = User(id="u-1", email="u@e.com", name="U", is_active=True)
    body = UserStoryCreate(
        project_id=uuid4(),
        title="Backlog story",
        description="desc",
    )
    story = asyncio.run(ust.create_user_story(body, current_user=user, db=None))
    assert story.sprint_id is None
