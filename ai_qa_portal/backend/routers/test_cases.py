"""Test case CRUD + script/read/heal endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
from ai_qa_portal.backend.services.audit import log_action
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import User, get_db
from ai_qa_portal.backend.services.failure_diagnoser import diagnose_run, format_for_prompt
from ai_qa_portal.backend.services.rag_index import index_test_case
from ai_qa_portal.backend.services.test_case_script_builder import TestCaseScriptBuilder
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

from ..models.test_case import BatchApproveRequest, TestCase, TestCaseStatus
from ..models.user_story import UserStory

_store = JsonFileBackend(settings.data_dir)

router = APIRouter(
    prefix="/test-cases",
    tags=["test_cases"],
    dependencies=[Depends(get_current_user)],
)

_MAX_HEAL_PER_HOUR = 2


def _load_story_for_case(case: TestCase) -> UserStory:
    try:
        story_row = _store.get_user_story(case.user_story_id)
    except KeyError as exc:
        raise HTTPException(404, "Parent story not found") from exc
    return UserStory.model_validate(story_row)


def _can_access_case(case: TestCase, current_user: User) -> bool:
    if current_user.is_admin:
        return True
    story = _load_story_for_case(case)
    return bool(story.owner_user_id) and story.owner_user_id == current_user.id


def _load_case_or_404(test_case_id: UUID, current_user: User) -> TestCase:
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError as exc:
        raise HTTPException(404, "Test case not found") from exc
    case = TestCase.model_validate(row)
    if not _can_access_case(case, current_user):
        raise HTTPException(404, "Test case not found")
    return case


@router.get("")
def list_test_cases(
    user_story_id: UUID = Query(...),
    include_archived: bool = Query(
        False,
        description=(
            "When false (default) archived test cases (status=rejected) are "
            "hidden from the response. Flip to true to drive the 'Show "
            "archived' view on the story detail page."
        ),
    ),
    current_user: User = Depends(get_current_user),
):
    try:
        story = UserStory.model_validate(_store.get_user_story(user_story_id))
    except KeyError as exc:
        raise HTTPException(404, "User story not found") from exc
    if not current_user.is_admin and story.owner_user_id != current_user.id:
        raise HTTPException(404, "User story not found")
    rows = _store.get_test_cases_by_story(user_story_id)
    cases = [TestCase.model_validate(r) for r in rows]
    if not include_archived:
        cases = [c for c in cases if c.status != TestCaseStatus.rejected]
    return [c.model_dump(mode="json") for c in cases]


@router.post("", status_code=201)
def create_test_case(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story_id = UUID(str(body.get("user_story_id")))
    try:
        story = UserStory.model_validate(_store.get_user_story(story_id))
    except KeyError as exc:
        raise HTTPException(404, "User story not found") from exc
    if not current_user.is_admin and story.owner_user_id != current_user.id:
        raise HTTPException(403, "Insufficient role for this user story")
    now = datetime.now(UTC)
    tc = TestCase(
        id=uuid4(),
        user_story_id=story.id,
        project_id=story.project_id,
        title=(body.get("title") or "").strip() or "Untitled test case",
        steps=list(body.get("steps") or []),
        expected_result=(body.get("expected_result") or "").strip(),
        preconditions=body.get("preconditions"),
        status=TestCaseStatus(body.get("status") or "draft"),
        stale=False,
        tags=list(body.get("tags") or []),
        created_at=now,
    )
    _store.save_test_case(tc.model_dump(mode="json"))
    slug = slug_for_project_id(tc.project_id)
    if slug:
        index_test_case(
            db,
            project_slug=slug,
            case_id=str(tc.id),
            story_id=str(tc.user_story_id),
            title=tc.title,
            preconditions=tc.preconditions,
            steps=list(tc.steps),
            expected_result=tc.expected_result,
        )
    return tc.model_dump(mode="json")


@router.get("/{test_case_id}")
def get_test_case(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
):
    case = _load_case_or_404(test_case_id, current_user)
    return case.model_dump(mode="json")


@router.patch("/{test_case_id}")
def patch_test_case(
    test_case_id: UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    case = _load_case_or_404(test_case_id, current_user)
    row = _store.get_test_case(test_case_id)
    content_changed = False
    for field in ("title", "steps", "expected_result", "preconditions", "tags"):
        if field in body:
            row[field] = body[field]
            content_changed = True
    if "status" in body:
        row["status"] = body["status"]
    if content_changed:
        row["script_path"] = None
        row["script_built_at"] = None
        row["stale"] = False
    _store.save_test_case(row)
    updated = TestCase.model_validate(row)
    slug = slug_for_project_id(case.project_id)
    if slug:
        index_test_case(
            db,
            project_slug=slug,
            case_id=str(updated.id),
            story_id=str(updated.user_story_id),
            title=updated.title,
            preconditions=updated.preconditions,
            steps=list(updated.steps),
            expected_result=updated.expected_result,
        )
    return updated.model_dump(mode="json")


@router.get("/{test_case_id}/script")
def read_test_case_script(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
):
    case = _load_case_or_404(test_case_id, current_user)
    if not case.script_path:
        raise HTTPException(404, "Script has not been built for this test case")
    path = Path.cwd() / case.script_path
    if not path.is_file():
        raise HTTPException(404, "Script file not found on disk")
    return {
        "test_case_id": str(case.id),
        "path": case.script_path,
        "content": path.read_text(encoding="utf-8"),
        "built_at": case.script_built_at.isoformat() if case.script_built_at else None,
    }


@router.put("/{test_case_id}/script")
def save_test_case_script(
    test_case_id: UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
):
    """Save edited Robot script for one test case with snapshot history."""
    case = _load_case_or_404(test_case_id, current_user)
    content = (body.get("content") or "").replace("\r\n", "\n")
    if not content.strip():
        raise HTTPException(422, "content is required")

    row = _store.get_test_case(test_case_id)
    story = _load_story_for_case(case)
    slug = slug_for_project_id(story.project_id)
    if not slug:
        raise HTTPException(400, "Could not resolve project slug for test case")

    if row.get("script_path"):
        target = Path.cwd() / str(row["script_path"])
    else:
        out_dir = (
            Path(settings.saved_projects_dir)
            / slug
            / "Tests"
            / "Generated"
            / f"story_{str(story.id).replace('-', '')[:12]}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"case_{str(case.id).replace('-', '')[:12]}.robot"
        row["script_path"] = target.resolve().relative_to(Path.cwd().resolve()).as_posix()

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        hist_dir = target.parent / ".history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        hist_path = hist_dir / f"{target.stem}.{stamp}.robot"
        hist_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

    final = content.rstrip() + "\n"
    target.write_text(final, encoding="utf-8")
    row["script_built_at"] = datetime.now(UTC).isoformat()
    _store.save_test_case(row)
    return {
        "ok": True,
        "test_case_id": str(case.id),
        "path": row.get("script_path"),
        "bytes_written": len(final.encode("utf-8")),
    }


@router.get("/{test_case_id}/script/history")
def list_test_case_script_history(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
):
    case = _load_case_or_404(test_case_id, current_user)
    if not case.script_path:
        return {"test_case_id": str(case.id), "items": []}
    target = Path.cwd() / case.script_path
    hist_dir = target.parent / ".history"
    if not hist_dir.is_dir():
        return {"test_case_id": str(case.id), "items": []}
    items = []
    for p in sorted(hist_dir.glob(f"{target.stem}.*.robot"), reverse=True):
        try:
            rel = p.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except Exception:  # noqa: BLE001
            rel = p.as_posix()
        items.append(
            {
                "name": p.name,
                "path": rel,
                "modified_at": datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat(),
                "size": p.stat().st_size,
            }
        )
    return {"test_case_id": str(case.id), "items": items}


@router.get("/{test_case_id}/script/history/{filename}")
def read_test_case_script_history_item(
    test_case_id: UUID,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    case = _load_case_or_404(test_case_id, current_user)
    if not case.script_path:
        raise HTTPException(404, "Script has not been built for this test case")
    target = Path.cwd() / case.script_path
    hist_dir = target.parent / ".history"
    item = (hist_dir / filename).resolve()
    try:
        item.relative_to(hist_dir.resolve())
    except ValueError as exc:
        raise HTTPException(400, "Invalid history filename") from exc
    if not item.is_file():
        raise HTTPException(404, "History item not found")
    return {
        "test_case_id": str(case.id),
        "name": item.name,
        "content": item.read_text(encoding="utf-8"),
    }


@router.post("/{test_case_id}/build-script")
def build_test_case_script(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Materialise/refresh a single test case Robot script on disk.

    Used by row-level and selected-case bulk generation flows in the UI.
    """
    case = _load_case_or_404(test_case_id, current_user)
    if case.status != TestCaseStatus.approved:
        raise HTTPException(400, "Only approved test cases can be built")
    if case.stale:
        raise HTTPException(400, "Test case is stale; update/re-approve it first")

    story = _load_story_for_case(case)
    slug = slug_for_project_id(story.project_id)
    if not slug:
        raise HTTPException(400, "Could not resolve project slug for test case")

    row = _store.get_test_case(test_case_id)
    if row.get("script_path"):
        target = Path.cwd() / str(row["script_path"])
    else:
        out_dir = (
            Path(settings.saved_projects_dir)
            / slug
            / "Tests"
            / "Generated"
            / f"story_{str(story.id).replace('-', '')[:12]}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"case_{str(case.id).replace('-', '')[:12]}.robot"
        row["script_path"] = target.resolve().relative_to(Path.cwd().resolve()).as_posix()

    source = TestCaseScriptBuilder().build_robot_script(case, db=None, project_slug=slug)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.rstrip() + "\n", encoding="utf-8")

    now = datetime.now(UTC)
    row["script_built_at"] = now.isoformat()
    row["stale"] = False
    _store.save_test_case(row)
    return {
        "ok": True,
        "test_case_id": str(case.id),
        "script_path": row.get("script_path"),
        "built_at": row["script_built_at"],
    }


@router.post("/{test_case_id}/heal")
def heal_test_case(
    test_case_id: UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
):
    case = _load_case_or_404(test_case_id, current_user)
    run_folder = (body.get("run_folder") or "").strip()
    if not run_folder:
        raise HTTPException(422, "run_folder is required")
    case_row = _store.get_test_case(test_case_id)

    last_healed_at_raw = case_row.get("last_healed_at")
    attempts = int(case_row.get("heal_attempts") or 0)
    if last_healed_at_raw:
        try:
            last_healed_at = datetime.fromisoformat(str(last_healed_at_raw))
        except ValueError:
            last_healed_at = None
    else:
        last_healed_at = None
    if last_healed_at and datetime.now(UTC) - last_healed_at < timedelta(hours=1) and attempts >= _MAX_HEAL_PER_HOUR:
        raise HTTPException(429, "Heal attempt limit reached for this test case (max 2/hour)")

    diagnosis = None
    run_dir = Path(settings.results_dir) / run_folder
    if run_dir.is_dir():
        diagnosis = diagnose_run(run_dir)

    builder = TestCaseScriptBuilder()
    story = _load_story_for_case(case)
    slug = slug_for_project_id(story.project_id)
    if not slug:
        raise HTTPException(400, "Could not resolve project slug for story")
    source = builder.build_robot_script(case, db=None, project_slug=slug)

    if case.script_path:
        target = Path.cwd() / case.script_path
    else:
        out_dir = (
            Path(settings.saved_projects_dir)
            / slug
            / "Tests"
            / "Generated"
            / f"story_{str(story.id).replace('-', '')[:12]}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"case_{str(case.id).replace('-', '')[:12]}.robot"
        case_row["script_path"] = target.resolve().relative_to(Path.cwd().resolve()).as_posix()
    final = source.rstrip() + "\n"
    target.write_text(final, encoding="utf-8")
    case_row["script_built_at"] = datetime.now(UTC).isoformat()
    case_row["heal_attempts"] = attempts + 1
    case_row["last_healed_at"] = datetime.now(UTC).isoformat()
    _store.save_test_case(case_row)

    return {
        "ok": True,
        "test_case_id": str(case.id),
        "script_path": case_row.get("script_path"),
        "diagnosis": diagnosis.to_dict() if diagnosis else {
            "test_name": case.title,
            "test_message": "",
            "first_failure": None,
            "hint": format_for_prompt(diagnosis) if diagnosis else "",
        },
        "attempts": int(case_row["heal_attempts"]),
        "message": "Script healed and rebuilt",
    }


@router.post("/batch-approve")
def batch_approve(
    body: BatchApproveRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        story = UserStory.model_validate(_store.get_user_story(body.user_story_id))
    except KeyError as exc:
        raise HTTPException(404, "User story not found") from exc
    if not current_user.is_admin and story.owner_user_id != current_user.id:
        raise HTTPException(403, "Insufficient role for this user story")
    updated = 0
    for item in body.approved:
        try:
            row = _store.get_test_case(item.test_case_id)
        except KeyError:
            continue
        row["title"] = item.title
        row["steps"] = list(item.steps)
        row["expected_result"] = item.expected_result
        row["preconditions"] = item.preconditions
        row["tags"] = list(item.tags)
        row["status"] = TestCaseStatus.approved.value
        row["stale"] = False
        _store.save_test_case(row)
        updated += 1
    return {"user_story_id": str(body.user_story_id), "approved_count": updated}


# --- Delete lifecycle --------------------------------------------------
#
# Soft delete: status -> rejected (matches the existing patch path that
# the UI's "Reject" button already uses). Hard delete: row + indexes
# + on-disk Robot script + history are purged. Hard delete refuses if
# the case is not in the rejected state -- callers reject first, then
# permanently delete.

class _TCDeleteResult(BaseModel):
    id: str
    status: str
    detail: str | None = None


class _TCBulkDeleteRequest(BaseModel):
    ids: list[UUID]
    permanent: bool = False


def _soft_delete_test_case(test_case_id: UUID) -> None:
    row = _store.get_test_case(test_case_id)
    row["status"] = TestCaseStatus.rejected.value
    _store.save_test_case(row)


def _purge_test_case_script(row: dict) -> None:
    """Remove the on-disk ``.robot`` script + its ``.history`` siblings
    so the hard delete leaves no orphan files behind. Failures here are
    swallowed -- the JSON row is the source of truth; we don't want a
    stale file blocking a clean delete."""
    rel = row.get("script_path")
    if not rel:
        return
    target = Path.cwd() / str(rel)
    try:
        if target.is_file():
            target.unlink()
        hist_dir = target.parent / ".history"
        if hist_dir.is_dir():
            stem = target.stem
            for hist in hist_dir.glob(f"{stem}.*.robot"):
                try:
                    hist.unlink()
                except OSError:
                    pass
            # Remove empty .history dir to keep the tree tidy.
            try:
                next(hist_dir.iterdir())
            except StopIteration:
                try:
                    hist_dir.rmdir()
                except OSError:
                    pass
    except OSError:
        # Filesystem errors must not block the JSON-level delete.
        pass


def _delete_test_case_one(
    test_case_id: UUID,
    user: User,
    *,
    permanent: bool,
) -> _TCDeleteResult:
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError:
        return _TCDeleteResult(id=str(test_case_id), status="skipped_not_found")
    case = TestCase.model_validate(row)
    if not _can_access_case(case, user):
        return _TCDeleteResult(id=str(test_case_id), status="skipped_not_found")

    if not permanent:
        _soft_delete_test_case(test_case_id)
        return _TCDeleteResult(
            id=str(test_case_id),
            status="soft_deleted",
            detail="test case rejected",
        )

    if case.status != TestCaseStatus.rejected:
        return _TCDeleteResult(
            id=str(test_case_id),
            status="skipped_invalid_state",
            detail=f"test case must be rejected before permanent delete (current status={case.status.value})",
        )
    # Purge any associated on-disk script BEFORE removing the JSON row,
    # so we always have the path while we still need it.
    _purge_test_case_script(row)
    _store.hard_delete_test_case(test_case_id)
    return _TCDeleteResult(id=str(test_case_id), status="hard_deleted")


def _audit_tc_lifecycle(
    *,
    db: Session,
    user: User,
    action: str,
    test_case_id: UUID,
    extra: dict | None = None,
) -> None:
    """Best-effort audit log entry for TC lifecycle transitions.

    Wrapped because the existing log_action already swallows failures;
    we keep our own try/except so a missing row (e.g. already-purged
    TC) doesn't break the request either. Metadata is intentionally
    small: title + story_id + project_id, which lets an admin trace
    "who archived this story's test cases" without bloating the audit
    table.
    """
    try:
        row = _store.read(f"test_case:{test_case_id}")
    except Exception:
        row = {}
    metadata = {
        "title": row.get("title") if isinstance(row, dict) else None,
        "story_id": row.get("user_story_id") if isinstance(row, dict) else None,
        "project_id": row.get("project_id") if isinstance(row, dict) else None,
    }
    if extra:
        metadata.update(extra)
    try:
        log_action(
            db,
            user=user,
            action=action,
            target_type="test_case",
            target_id=str(test_case_id),
            metadata=metadata,
        )
    except Exception:
        # Audit failures must never block a user action.
        pass


@router.delete("/{test_case_id}", status_code=200)
def delete_test_case(
    test_case_id: UUID,
    permanent: bool = Query(False, description="When true, hard-deletes an already-rejected test case (purges JSON + on-disk script + history)."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft archive (``status -> rejected``) by default;
    ``permanent=true`` hard-deletes (JSON row + indexes + on-disk
    Robot script + history). Both transitions are audit-logged as
    ``tc_archived`` / ``tc_purged`` respectively.
    """
    # Snapshot title before delete so the audit metadata survives the
    # row vanishing on a hard delete.
    pre_row = _store.read(f"test_case:{test_case_id}") or {}
    result = _delete_test_case_one(test_case_id, current_user, permanent=permanent)
    if result.status == "skipped_not_found":
        raise HTTPException(404, "Test case not found")
    if result.status == "skipped_invalid_state":
        raise HTTPException(409, result.detail or "Cannot permanently delete this test case yet")
    if result.status == "soft_deleted":
        _audit_tc_lifecycle(
            db=db,
            user=current_user,
            action="tc_archived",
            test_case_id=test_case_id,
        )
        return {
            "test_case_id": str(test_case_id),
            "status": TestCaseStatus.rejected.value,
            "detail": result.detail,
        }
    # Hard delete: pre_row is the last memory we have of this TC.
    try:
        log_action(
            db,
            user=current_user,
            action="tc_purged",
            target_type="test_case",
            target_id=str(test_case_id),
            metadata={
                "title": pre_row.get("title"),
                "story_id": pre_row.get("user_story_id"),
                "project_id": pre_row.get("project_id"),
            },
        )
    except Exception:
        pass
    return {"test_case_id": str(test_case_id), "status": "hard_deleted"}


@router.post("/bulk-delete", status_code=200)
def bulk_delete_test_cases(
    body: _TCBulkDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft- or hard-delete a list of test case ids."""
    # Snapshot every targeted row before we mutate so the per-id audit
    # entries on a hard-delete have the title/story_id/project_id even
    # for rows that get purged.
    pre_rows: dict[str, dict] = {
        str(tid): _store.read(f"test_case:{tid}") or {} for tid in body.ids
    }
    results = [
        _delete_test_case_one(tid, current_user, permanent=body.permanent)
        for tid in body.ids
    ]
    for r in results:
        if r.status not in ("soft_deleted", "hard_deleted"):
            continue
        pre = pre_rows.get(str(r.id), {})
        action = "tc_archived" if r.status == "soft_deleted" else "tc_purged"
        try:
            log_action(
                db,
                user=current_user,
                action=action,
                target_type="test_case",
                target_id=str(r.id),
                metadata={
                    "title": pre.get("title"),
                    "story_id": pre.get("user_story_id"),
                    "project_id": pre.get("project_id"),
                    "bulk": True,
                },
            )
        except Exception:
            pass
    return {"results": [r.model_dump() for r in results]}


@router.post("/{test_case_id}/restore", status_code=200)
def restore_test_case(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Restore an archived test case (``status: rejected -> draft``).

    Dedicated endpoint rather than a PATCH so the audit log has a
    clean ``tc_restored`` action -- it would otherwise collide with
    "user changed status to draft" semantically. Restored cases come
    back as ``draft`` (not ``approved``) so the user re-validates
    them before they enter the script-build pipeline; staleness flag
    is left alone because the story version hasn't changed.
    """
    case = _load_case_or_404(test_case_id, current_user)
    if case.status != TestCaseStatus.rejected:
        raise HTTPException(
            409,
            f"Only archived test cases can be restored (current status={case.status.value}).",
        )
    row = _store.get_test_case(test_case_id)
    row["status"] = TestCaseStatus.draft.value
    _store.save_test_case(row)
    updated = TestCase.model_validate(row)
    _audit_tc_lifecycle(
        db=db,
        user=current_user,
        action="tc_restored",
        test_case_id=test_case_id,
    )
    return updated.model_dump(mode="json")
