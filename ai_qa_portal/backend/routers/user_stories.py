"""User story CRUD + generation + script build endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
from ai_qa_portal.backend.services.auth import (
    assert_user_can_create_in_project,
    get_current_user,
)
from ai_qa_portal.backend.services.audit import log_action
from ai_qa_portal.backend.services.db import AuditLog, User, get_db, get_user_by_email, push_notification
from ai_qa_portal.backend.services.rag_index import index_test_case, index_user_story
from ai_qa_portal.backend.services.test_case_generator import TestCaseGenerator
from ai_qa_portal.backend.services.test_case_script_builder import TestCaseScriptBuilder
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

from ..models.test_case import TestCase, TestCaseStatus
from ..models.user_story import UserStory, UserStoryCreate, UserStoryStatus, UserStoryUpdate

_store = JsonFileBackend(settings.data_dir)
_MENTION_RE = re.compile(r"@([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

router = APIRouter(
    prefix="/user-stories",
    tags=["user_stories"],
    dependencies=[Depends(get_current_user)],
)


def _user_can_see_story(story: UserStory, user: User) -> bool:
    if user.is_admin:
        return True
    return bool(story.owner_user_id) and story.owner_user_id == user.id


def _load_story_or_404(story_id: UUID, user: User) -> UserStory:
    try:
        row = _store.get_user_story(story_id)
    except KeyError as exc:
        raise HTTPException(404, "User story not found") from exc
    story = UserStory.model_validate(row)
    if not _user_can_see_story(story, user):
        raise HTTPException(404, "User story not found")
    return story


@router.post("", response_model=UserStory, status_code=201)
def create_user_story(
    body: UserStoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_user_can_create_in_project(db, current_user, body.project_id)
    now = datetime.now(UTC)
    story = UserStory(
        id=uuid4(),
        project_id=body.project_id,
        title=(body.title or "").strip() or "Untitled story",
        description=(body.description or "").strip(),
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=now,
        updated_at=now,
        owner_user_id=current_user.id,
        sprint_id=body.sprint_id,
    )
    _store.save_user_story(story.model_dump(mode="json"))
    slug = slug_for_project_id(story.project_id)
    if slug:
        index_user_story(
            db,
            project_slug=slug,
            story_id=str(story.id),
            title=story.title,
            description=story.description,
            sprint_id=str(story.sprint_id) if story.sprint_id else None,
        )
    log_action(
        db,
        user=current_user,
        action="story_created",
        target_type="user_story",
        target_id=str(story.id),
        metadata={"project_id": str(story.project_id), "title": story.title},
    )
    return story


@router.get("", response_model=list[UserStory])
def list_user_stories(
    project_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
):
    rows = _store.list_user_stories(project_id)
    stories = [UserStory.model_validate(r) for r in rows]
    visible = [s for s in stories if _user_can_see_story(s, current_user)]
    visible = [s for s in visible if s.status == UserStoryStatus.active]
    visible.sort(key=lambda s: s.updated_at, reverse=True)
    return visible


@router.get("/{story_id}", response_model=UserStory)
def get_user_story(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
):
    return _load_story_or_404(story_id, current_user)


@router.put("/{story_id}", response_model=UserStory)
def update_user_story(
    story_id: UUID,
    body: UserStoryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story = _load_story_or_404(story_id, current_user)
    row = _store.get_user_story(story_id)
    dirty_story = False
    title = body.title
    description = body.description
    if title is not None:
        cleaned = title.strip() or row.get("title") or "Untitled story"
        if cleaned != row.get("title"):
            row["title"] = cleaned
            dirty_story = True
    if description is not None:
        cleaned = description.strip()
        if cleaned != row.get("description"):
            row["description"] = cleaned
            dirty_story = True
    if dirty_story:
        row["version"] = int(row.get("version") or 1) + 1
        row["updated_at"] = datetime.now(UTC).isoformat()
        _store.save_user_story(row)
        for tc_row in _store.get_test_cases_by_story(story_id):
            tc_row["stale"] = True
            _store.save_test_case(tc_row)
    updated = UserStory.model_validate(row)
    slug = slug_for_project_id(updated.project_id)
    if slug:
        index_user_story(
            db,
            project_slug=slug,
            story_id=str(updated.id),
            title=updated.title,
            description=updated.description,
            sprint_id=str(updated.sprint_id) if updated.sprint_id else None,
        )
    if dirty_story:
        log_action(
            db,
            user=current_user,
            action="story_updated",
            target_type="user_story",
            target_id=str(updated.id),
            metadata={"version": updated.version, "title": updated.title},
        )
    return updated


@router.post("/{story_id}/assign", response_model=UserStory)
def assign_user_story_owner(
    story_id: UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Assign story ownership.

    Non-admin users may only assign stories to themselves.
    """
    story = _load_story_or_404(story_id, current_user)
    requested_owner = str(body.get("owner_user_id") or "").strip()
    if not requested_owner:
        requested_owner = current_user.id
    if (not current_user.is_admin) and requested_owner != current_user.id:
        raise HTTPException(403, "Only admins can assign stories to other users")
    row = _store.get_user_story(story.id)
    row["owner_user_id"] = requested_owner
    row["updated_at"] = datetime.now(UTC).isoformat()
    _store.save_user_story(row)
    updated = UserStory.model_validate(row)
    log_action(
        db,
        user=current_user,
        action="story_assigned",
        target_type="user_story",
        target_id=str(updated.id),
        metadata={"owner_user_id": requested_owner},
    )
    return updated


@router.get("/{story_id}/comments")
def list_story_comments(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
):
    _load_story_or_404(story_id, current_user)
    rows = _store.read(f"user_story_comments:{story_id}").get("items", [])
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


@router.post("/{story_id}/comments")
def create_story_comment(
    story_id: UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story = _load_story_or_404(story_id, current_user)
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(422, "body is required")
    mentions = sorted({m.group(1).lower() for m in _MENTION_RE.finditer(text)})
    row = {
        "id": str(uuid4()),
        "story_id": str(story.id),
        "author_user_id": current_user.id,
        "author_email": current_user.email,
        "body": text,
        "mentions": mentions,
        "created_at": datetime.now(UTC).isoformat(),
    }
    key = f"user_story_comments:{story_id}"
    blob = _store.read(key)
    items = list(blob.get("items", []))
    items.insert(0, row)
    _store.write(key, {"items": items})
    for email in mentions:
        u = get_user_by_email(db, email)
        if not u or u.id == current_user.id:
            continue
        push_notification(
            db,
            user_id=u.id,
            type="story_mention",
            title=f"Mentioned in story: {story.title}",
            body=f"{current_user.email} mentioned you in a comment.",
            action_url=f"/user-stories/{story.id}",
        )
    log_action(
        db,
        user=current_user,
        action="story_comment_added",
        target_type="user_story",
        target_id=str(story.id),
        metadata={"mentions": mentions, "preview": text[:120]},
    )
    return row


@router.get("/{story_id}/activity")
def story_activity(
    story_id: UUID,
    limit: int = Query(30, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story = _load_story_or_404(story_id, current_user)
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.target_type == "user_story",
            AuditLog.target_id == str(story.id),
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    out = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "user_id": r.user_id,
            "metadata_json": r.metadata_json or "",
        }
        for r in rows
    ]
    out.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return {"story_id": str(story.id), "items": out[:limit]}


@router.post("/{story_id}/generate")
async def generate_test_cases(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story = _load_story_or_404(story_id, current_user)
    generator = TestCaseGenerator()
    slug = slug_for_project_id(story.project_id)
    generated = await generator.generate(story, db=db, project_slug=slug)
    now = datetime.now(UTC)
    created: list[dict] = []
    for item in generated:
        tc = TestCase(
            id=uuid4(),
            user_story_id=story.id,
            project_id=story.project_id,
            title=item.title,
            steps=list(item.steps or []),
            expected_result=item.expected_result,
            preconditions=item.preconditions,
            status=TestCaseStatus.draft,
            stale=False,
            tags=list(item.tags or []),
            created_at=now,
            script_path=None,
            script_built_at=None,
        )
        _store.save_test_case(tc.model_dump(mode="json"))
        created.append(tc.model_dump(mode="json"))
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
    log_action(
        db,
        user=current_user,
        action="story_cases_generated",
        target_type="user_story",
        target_id=str(story.id),
        metadata={"count": len(created)},
    )
    return {"user_story_id": str(story.id), "generated": created, "count": len(created)}


@router.post("/{story_id}/build-scripts")
def build_story_scripts(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    story = _load_story_or_404(story_id, current_user)
    builder = TestCaseScriptBuilder()
    project_slug = slug_for_project_id(story.project_id)
    if not project_slug:
        raise HTTPException(400, "Could not resolve project slug for story")
    out_dir = (
        Path(settings.saved_projects_dir)
        / project_slug
        / "Tests"
        / "Generated"
        / f"story_{str(story.id).replace('-', '')[:12]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    built: list[dict] = []
    skipped: list[dict] = []
    for tc_row in _store.get_test_cases_by_story(story_id):
        tc = TestCase.model_validate(tc_row)
        if tc.status != TestCaseStatus.approved:
            skipped.append(
                {"test_case_id": str(tc.id), "title": tc.title, "reason": "status_not_approved"}
            )
            continue
        if tc.stale:
            skipped.append(
                {"test_case_id": str(tc.id), "title": tc.title, "reason": "story_changed_marked_stale"}
            )
            continue
        try:
            source = builder.build_robot_script(tc, db=None, project_slug=project_slug)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"test_case_id": str(tc.id), "title": tc.title, "reason": str(exc)})
            continue
        target = out_dir / f"case_{str(tc.id).replace('-', '')[:12]}.robot"
        final = source.rstrip() + "\n"
        target.write_text(final, encoding="utf-8")
        rel = target.resolve().relative_to(Path.cwd().resolve()).as_posix()
        tc_row["script_path"] = rel
        tc_row["script_built_at"] = datetime.now(UTC).isoformat()
        _store.save_test_case(tc_row)
        built.append(
            {"test_case_id": str(tc.id), "script_path": rel, "bytes_written": len(final.encode("utf-8"))}
        )
    log_action(
        db,
        user=current_user,
        action="story_scripts_built",
        target_type="user_story",
        target_id=str(story.id),
        metadata={"built": len(built), "skipped": len(skipped)},
    )

    return {
        "story_id": str(story.id),
        "project_slug": project_slug,
        "output_dir": str(out_dir),
        "built": built,
        "skipped": skipped,
    }
