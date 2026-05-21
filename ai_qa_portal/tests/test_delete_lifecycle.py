"""Tests for the two-step soft+hard delete lifecycle across sprints,
user stories, and test cases.

The lifecycle is the same for all three entities:

  first call:  soft delete  (sprint -> cancelled, story -> archived,
                              test case -> rejected)
  permanent=true: hard delete  (JSON row + indexes purged), refused
                                with 409 + blockers when live children
                                remain

The tests exercise:

  - happy path round-trip from sprint -> stories -> test cases bottom-up
  - blocker path: hard-delete a sprint with a live story (refused);
    hard-delete a story with a live test case (refused)
  - bulk-delete: mixed soft + hard outcomes in one call

Each entity gets its own bottom-up smoke; the integration test at the
end stitches them together to prove the storage layer + the per-router
helpers all agree on what "live" means.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch):
    """Point every router's JsonFileBackend at a throwaway directory so
    one test can't poison another. All three routers share the same
    backend type, but each one builds its own instance pointing at
    ``settings.data_dir``; the cleanest swap is the per-router
    ``_store`` attribute."""
    from ai_qa_portal.backend.routers import sprints as sprints_router
    from ai_qa_portal.backend.routers import test_cases as tc_router
    from ai_qa_portal.backend.routers import user_stories as story_router
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    store = JsonFileBackend(str(tmp_path))
    monkeypatch.setattr(sprints_router, "_store", store)
    monkeypatch.setattr(story_router, "_store", store)
    monkeypatch.setattr(tc_router, "_store", store)
    # The story router also writes to the RAG index and audit log on
    # delete; both call paths can run on a real DB but we don't want to
    # depend on one here. log_action and index_user_story both accept
    # `db=None` defensively in the existing tests, so we just stub them.
    monkeypatch.setattr(story_router, "log_action", lambda *a, **kw: None)
    return store


def _mk_user(*, user_id: str = "u-1", admin: bool = False):
    from ai_qa_portal.backend.services.db import User
    return User(id=user_id, email=f"{user_id}@e.com", name=user_id, is_active=True, is_admin=admin)


def _mk_sprint(store, *, owner_id: str = "u-1", project_id=None, state: str = "planned"):
    sid = uuid4()
    pid = project_id or uuid4()
    store.save_sprint({
        "id": str(sid),
        "project_id": str(pid),
        "name": f"Sprint {str(sid)[:8]}",
        "goal": None,
        "state": state,
        "start_date": None,
        "end_date": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "owner_user_id": owner_id,
    })
    return sid, pid


def _mk_story(store, *, owner_id: str = "u-1", project_id, sprint_id=None, status: str = "active"):
    sid = uuid4()
    store.save_user_story({
        "id": str(sid),
        "project_id": str(project_id),
        "sprint_id": str(sprint_id) if sprint_id else None,
        "title": f"Story {str(sid)[:8]}",
        "description": "",
        "status": status,
        "version": 1,
        "prev_version_id": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "owner_user_id": owner_id,
    })
    return sid


def _mk_tc(store, *, project_id, story_id, status: str = "draft", tags=None, script_path: str | None = None):
    tid = uuid4()
    store.save_test_case({
        "id": str(tid),
        "user_story_id": str(story_id),
        "project_id": str(project_id),
        "title": f"TC {str(tid)[:8]}",
        "steps": ["step 1"],
        "expected_result": "ok",
        "preconditions": None,
        "status": status,
        "stale": False,
        "tags": list(tags or []),
        "created_at": datetime.now(UTC).isoformat(),
        "script_path": script_path,
    })
    return tid


# ── Sprint lifecycle ────────────────────────────────────────────────


def test_sprint_soft_delete_cancels_and_unlinks_stories(isolated_store):
    """First DELETE call must flip the sprint to cancelled and clear
    sprint_id on every assigned story so they fall back to the
    backlog. Same behaviour the legacy delete had."""
    from ai_qa_portal.backend.routers import sprints as sprints_router

    user = _mk_user()
    sid, pid = _mk_sprint(isolated_store, owner_id=user.id)
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid, sprint_id=sid)

    resp = sprints_router.delete_sprint(sid, permanent=False, current_user=user)
    assert resp["state"] == "cancelled"

    # Story should be unlinked from the sprint.
    refreshed = isolated_store.get_user_story(story_id)
    assert refreshed["sprint_id"] is None


def test_sprint_hard_delete_refuses_when_not_cancelled(isolated_store):
    from fastapi import HTTPException

    from ai_qa_portal.backend.routers import sprints as sprints_router

    user = _mk_user()
    sid, _ = _mk_sprint(isolated_store, owner_id=user.id, state="planned")
    with pytest.raises(HTTPException) as exc:
        sprints_router.delete_sprint(sid, permanent=True, current_user=user)
    assert exc.value.status_code == 409
    assert "cancelled" in str(exc.value.detail).lower()


def test_sprint_hard_delete_refuses_with_live_stories(isolated_store):
    from fastapi import HTTPException

    from ai_qa_portal.backend.routers import sprints as sprints_router

    user = _mk_user()
    sid, pid = _mk_sprint(isolated_store, owner_id=user.id, state="cancelled")
    # Story still active even though sprint is cancelled (eg. user
    # forgot to archive it before purge attempt).
    live_story = _mk_story(isolated_store, owner_id=user.id, project_id=pid, sprint_id=sid)
    with pytest.raises(HTTPException) as exc:
        sprints_router.delete_sprint(sid, permanent=True, current_user=user)
    assert exc.value.status_code == 409
    assert isinstance(exc.value.detail, dict)
    blockers = exc.value.detail.get("blockers")
    assert blockers and any(b["id"] == str(live_story) for b in blockers)


def test_sprint_hard_delete_succeeds_when_no_live_stories(isolated_store):
    from ai_qa_portal.backend.routers import sprints as sprints_router

    user = _mk_user()
    sid, pid = _mk_sprint(isolated_store, owner_id=user.id, state="cancelled")
    # Story exists but is archived -- counts as soft-deleted, not blocker.
    _mk_story(isolated_store, owner_id=user.id, project_id=pid, sprint_id=sid, status="archived")
    resp = sprints_router.delete_sprint(sid, permanent=True, current_user=user)
    assert resp["status"] == "hard_deleted"
    # JSON row must really be gone.
    assert isolated_store.read(f"sprint:{sid}") == {}


def test_sprint_bulk_delete_mixed_outcomes(isolated_store):
    from ai_qa_portal.backend.routers import sprints as sprints_router

    user = _mk_user()
    # Three sprints, three different starting states + one blocker case.
    planned_id, pid = _mk_sprint(isolated_store, owner_id=user.id, state="planned")
    cancelled_id, _ = _mk_sprint(isolated_store, owner_id=user.id, project_id=pid, state="cancelled")
    blocked_id, _ = _mk_sprint(isolated_store, owner_id=user.id, project_id=pid, state="cancelled")
    _mk_story(isolated_store, owner_id=user.id, project_id=pid, sprint_id=blocked_id)

    body = sprints_router._BulkDeleteRequest(ids=[planned_id, cancelled_id, blocked_id], permanent=True)
    resp = sprints_router.bulk_delete_sprints(body, current_user=user)
    by_id = {r["id"]: r for r in resp["results"]}
    # planned_id is not in cancelled state -> skipped_invalid_state
    assert by_id[str(planned_id)]["status"] == "skipped_invalid_state"
    # cancelled_id is clean -> hard_deleted
    assert by_id[str(cancelled_id)]["status"] == "hard_deleted"
    # blocked_id is cancelled but has a live story -> skipped_blocked
    assert by_id[str(blocked_id)]["status"] == "skipped_blocked"
    assert by_id[str(blocked_id)]["blockers"], "blockers list should not be empty"


# ── Story lifecycle ─────────────────────────────────────────────────


def test_story_soft_delete_archives(isolated_store):
    from ai_qa_portal.backend.routers import user_stories as story_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    resp = story_router.delete_user_story(story_id, permanent=False, current_user=user, db=None)
    assert resp["status"] == "archived"
    refreshed = isolated_store.get_user_story(story_id)
    assert refreshed["status"] == "archived"


def test_story_hard_delete_refuses_with_live_test_cases(isolated_store):
    from fastapi import HTTPException

    from ai_qa_portal.backend.routers import user_stories as story_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid, status="archived")
    live_tc = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")
    with pytest.raises(HTTPException) as exc:
        story_router.delete_user_story(story_id, permanent=True, current_user=user, db=None)
    assert exc.value.status_code == 409
    blockers = exc.value.detail.get("blockers") if isinstance(exc.value.detail, dict) else None
    assert blockers and any(b["id"] == str(live_tc) for b in blockers)


def test_story_hard_delete_succeeds_after_children_rejected(isolated_store):
    from ai_qa_portal.backend.routers import user_stories as story_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid, status="archived")
    _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="rejected")
    resp = story_router.delete_user_story(story_id, permanent=True, current_user=user, db=None)
    assert resp["status"] == "hard_deleted"
    assert isolated_store.read(f"user_story:{story_id}") == {}


# ── Test case lifecycle ─────────────────────────────────────────────


def test_test_case_soft_delete_rejects(isolated_store):
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    tc_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")

    resp = tc_router.delete_test_case(tc_id, permanent=False, current_user=user, db=None)
    assert resp["status"] == "rejected"
    refreshed = isolated_store.get_test_case(tc_id)
    assert refreshed["status"] == "rejected"


def test_test_case_hard_delete_refuses_before_rejected(isolated_store):
    from fastapi import HTTPException

    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    tc_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")
    with pytest.raises(HTTPException) as exc:
        tc_router.delete_test_case(tc_id, permanent=True, current_user=user, db=None)
    assert exc.value.status_code == 409
    assert "rejected" in str(exc.value.detail).lower()


def test_test_case_hard_delete_purges_json_and_script(isolated_store, tmp_path, monkeypatch):
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    # Write a fake script file relative to cwd so the purge can find it.
    script_rel = "Tests/Generated/story_x/case_x.robot"
    full = tmp_path / script_rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("*** Test Cases ***\nNoop\n    Log    ok\n", encoding="utf-8")
    # The TC's script_path is read with Path.cwd() / case.script_path,
    # so chdir into tmp_path so cwd-rooted resolution finds the file.
    monkeypatch.chdir(tmp_path)
    tc_id = _mk_tc(
        isolated_store,
        project_id=pid,
        story_id=story_id,
        status="rejected",
        script_path=script_rel,
    )
    resp = tc_router.delete_test_case(tc_id, permanent=True, current_user=user, db=None)
    assert resp["status"] == "hard_deleted"
    assert isolated_store.read(f"test_case:{tc_id}") == {}
    # Script file should also be gone.
    assert not full.exists()


def test_test_case_bulk_delete(isolated_store):
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    draft_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")
    rejected_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="rejected")

    # Soft delete both -> draft -> rejected, rejected stays rejected.
    body = tc_router._TCBulkDeleteRequest(ids=[draft_id, rejected_id], permanent=False)
    resp = tc_router.bulk_delete_test_cases(body, current_user=user, db=None)
    by_id = {r["id"]: r for r in resp["results"]}
    assert by_id[str(draft_id)]["status"] == "soft_deleted"
    assert by_id[str(rejected_id)]["status"] == "soft_deleted"
    # Now hard delete both -> both should succeed.
    body2 = tc_router._TCBulkDeleteRequest(ids=[draft_id, rejected_id], permanent=True)
    resp2 = tc_router.bulk_delete_test_cases(body2, current_user=user, db=None)
    by_id2 = {r["id"]: r for r in resp2["results"]}
    assert by_id2[str(draft_id)]["status"] == "hard_deleted"
    assert by_id2[str(rejected_id)]["status"] == "hard_deleted"


# ── Restore + include_archived filter (Phase A) ─────────────────────


def test_test_case_restore_round_trip(isolated_store):
    """archive a TC, then restore it; status should land back at draft.

    Why draft and not approved: the restored TC needs to re-enter the
    review pipeline (someone archived it for a reason). The frontend
    Restore button on the story detail page exists exactly for this
    flow."""
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    tc_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="approved")

    # Archive (soft delete) then restore.
    tc_router.delete_test_case(tc_id, permanent=False, current_user=user, db=None)
    assert isolated_store.get_test_case(tc_id)["status"] == "rejected"

    restored = tc_router.restore_test_case(tc_id, current_user=user, db=None)
    assert restored["status"] == "draft", (
        "Restore must move status back to draft, NOT back to approved -- "
        "the user explicitly archived it, so re-approval should be a "
        "deliberate second action."
    )
    assert isolated_store.get_test_case(tc_id)["status"] == "draft"


def test_test_case_restore_refuses_on_live_row(isolated_store):
    """Restore is only legal from the archived state."""
    from fastapi import HTTPException

    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    draft_tc = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")
    approved_tc = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="approved")
    for tid in (draft_tc, approved_tc):
        with pytest.raises(HTTPException) as exc:
            tc_router.restore_test_case(tid, current_user=user, db=None)
        assert exc.value.status_code == 409
        assert "archived" in str(exc.value.detail).lower()


def test_test_case_list_filters_archived_by_default(isolated_store):
    """GET /test-cases hides archived rows unless include_archived=true."""
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")
    _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="approved")
    _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="rejected")

    default_view = tc_router.list_test_cases(
        user_story_id=story_id,
        include_archived=False,
        current_user=user,
    )
    statuses_default = sorted(c["status"] for c in default_view)
    assert statuses_default == ["approved", "draft"], (
        "Default list must exclude archived (rejected) TCs"
    )

    archived_view = tc_router.list_test_cases(
        user_story_id=story_id,
        include_archived=True,
        current_user=user,
    )
    statuses_archived = sorted(c["status"] for c in archived_view)
    assert statuses_archived == ["approved", "draft", "rejected"], (
        "include_archived=True must surface every row regardless of status"
    )


def test_archive_then_restore_then_archive_round_trip(isolated_store):
    """Archive -> Restore -> Archive again. Verifies the full Phase A
    lifecycle including that re-archiving an already-restored TC works
    (i.e. archive isn't a one-shot)."""
    from ai_qa_portal.backend.routers import test_cases as tc_router

    user = _mk_user()
    pid = uuid4()
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid)
    tc_id = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="approved")

    # Step 1: archive
    tc_router.delete_test_case(tc_id, permanent=False, current_user=user, db=None)
    assert isolated_store.get_test_case(tc_id)["status"] == "rejected"

    # Step 2: restore
    tc_router.restore_test_case(tc_id, current_user=user, db=None)
    assert isolated_store.get_test_case(tc_id)["status"] == "draft"

    # Step 3: archive again
    tc_router.delete_test_case(tc_id, permanent=False, current_user=user, db=None)
    assert isolated_store.get_test_case(tc_id)["status"] == "rejected"


# ── End-to-end bottom-up round-trip ─────────────────────────────────


def test_round_trip_bottom_up(isolated_store):
    """sprint > story > 2 test cases -> reject TCs, archive story,
    cancel sprint, then permanently delete each level in reverse."""
    from ai_qa_portal.backend.routers import sprints as sprints_router
    from ai_qa_portal.backend.routers import test_cases as tc_router
    from ai_qa_portal.backend.routers import user_stories as story_router

    user = _mk_user()
    sid, pid = _mk_sprint(isolated_store, owner_id=user.id)
    story_id = _mk_story(isolated_store, owner_id=user.id, project_id=pid, sprint_id=sid)
    tc1 = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="approved")
    tc2 = _mk_tc(isolated_store, project_id=pid, story_id=story_id, status="draft")

    # Soft-delete the TCs.
    tc_router.delete_test_case(tc1, permanent=False, current_user=user)
    tc_router.delete_test_case(tc2, permanent=False, current_user=user)
    # Now they're rejected, hard-delete should succeed.
    tc_router.delete_test_case(tc1, permanent=True, current_user=user)
    tc_router.delete_test_case(tc2, permanent=True, current_user=user)
    assert isolated_store.read(f"test_case:{tc1}") == {}
    assert isolated_store.read(f"test_case:{tc2}") == {}

    # Archive then purge the story.
    story_router.delete_user_story(story_id, permanent=False, current_user=user, db=None)
    story_router.delete_user_story(story_id, permanent=True, current_user=user, db=None)
    assert isolated_store.read(f"user_story:{story_id}") == {}

    # Cancel then purge the sprint.
    sprints_router.delete_sprint(sid, permanent=False, current_user=user)
    sprints_router.delete_sprint(sid, permanent=True, current_user=user)
    assert isolated_store.read(f"sprint:{sid}") == {}
