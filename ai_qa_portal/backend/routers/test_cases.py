"""Test case CRUD + script/read/heal endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
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
