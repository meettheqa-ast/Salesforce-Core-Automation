"""User stories, generated test-case review, and tag listing (additive routes)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import User
from ..models.generation import GenerationResponse
from ..models.tag import Tag, TagScope
from ..models.test_case import BatchApproveRequest, TestCase, TestCaseStatus
from ..models.user_story import UserStory, UserStoryCreate, UserStoryStatus, UserStoryUpdate
from ..services.story_versioner import StoryVersioner
from ..services.test_case_generator import TestCaseGenerator
from ..storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)
_versioner = StoryVersioner()
_generator = TestCaseGenerator()

router = APIRouter(
    prefix="/user-stories",
    tags=["user-stories"],
    dependencies=[Depends(get_current_user)],
)
test_cases_router = APIRouter(
    prefix="/test-cases",
    tags=["test-cases"],
    dependencies=[Depends(get_current_user)],
)
tags_router = APIRouter(
    prefix="/tags",
    tags=["tags"],
    dependencies=[Depends(get_current_user)],
)


def _story_model(row: dict) -> UserStory:
    return UserStory.model_validate(row)


def _user_can_see_story(s: UserStory, user: User) -> bool:
    """Phase 1 isolation: each user sees only their own stories. Admin sees all.
    Legacy unowned stories (empty owner_user_id) are admin-only."""
    if user.is_admin:
        return True
    return bool(s.owner_user_id) and s.owner_user_id == user.id


def _load_story_or_403(story_id: UUID, user: User) -> UserStory:
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = _story_model(row)
    if not _user_can_see_story(story, user):
        raise HTTPException(404, "User story not found")
    return story


@router.post("", response_model=UserStory, status_code=201)
async def create_user_story(
    body: UserStoryCreate,
    current_user: User = Depends(get_current_user),
):
    _store.seed_static_tags(body.project_id)
    now = datetime.now(timezone.utc)
    story = UserStory(
        id=uuid4(),
        project_id=body.project_id,
        title=body.title.strip(),
        description=body.description.strip(),
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=now,
        updated_at=now,
        owner_user_id=current_user.id,
    )
    _store.save_user_story(story.model_dump(mode="json"))
    return story


@router.get("", response_model=list[UserStory])
def list_user_stories(
    project_id: UUID = Query(..., description="Project UUID"),
    current_user: User = Depends(get_current_user),
):
    rows = _store.list_user_stories(project_id)
    items = [_story_model(r) for r in rows if r.get("status") == UserStoryStatus.active.value]
    items = [s for s in items if _user_can_see_story(s, current_user)]
    items.sort(key=lambda s: s.created_at, reverse=True)
    return items


@router.get("/{story_id}", response_model=UserStory)
def get_user_story_by_id(story_id: UUID, current_user: User = Depends(get_current_user)):
    return _load_story_or_403(story_id, current_user)


@router.put("/{story_id}")
def update_user_story(
    story_id: UUID,
    body: UserStoryUpdate,
    current_user: User = Depends(get_current_user),
):
    existing = _load_story_or_403(story_id, current_user)
    if existing.status != UserStoryStatus.active:
        raise HTTPException(400, "Only active stories can be updated this way")
    new_story, stale_count = _versioner.update_story(existing, body, _store)
    return {
        "new_story": new_story.model_dump(mode="json"),
        "stale_count": stale_count,
        "message": f"Story updated to v{new_story.version}. {stale_count} test cases marked stale.",
    }


@router.post("/{story_id}/generate", response_model=GenerationResponse)
async def generate_test_cases(story_id: UUID, current_user: User = Depends(get_current_user)):
    story = _load_story_or_403(story_id, current_user)
    generated = await _generator.generate(story)
    return GenerationResponse(user_story_id=story.id, generated=generated)


@test_cases_router.post("/batch-approve")
def batch_approve(body: BatchApproveRequest, current_user: User = Depends(get_current_user)):
    story = _load_story_or_403(body.user_story_id, current_user)
    project_id = story.project_id
    _store.seed_static_tags(project_id)

    saved_ids: list[UUID] = []

    def _ensure_tag(name: str) -> None:
        key = f"tags:{project_id}:{name}"
        if _store.read(key):
            return
        tag = Tag(
            id=uuid4(),
            project_id=project_id,
            name=name,
            color="#8B5CF6",
            scope=TagScope.custom,
        )
        _store.save_tag(tag.model_dump(mode="json"))

    for ap in body.approved:
        for tname in ap.tags:
            if tname.strip():
                _ensure_tag(tname.strip())
        now = datetime.now(timezone.utc)
        tc = TestCase(
            id=uuid4(),
            user_story_id=body.user_story_id,
            project_id=project_id,
            title=ap.title.strip(),
            steps=list(ap.steps),
            expected_result=ap.expected_result.strip(),
            preconditions=(ap.preconditions or "").strip() or None,
            status=TestCaseStatus.approved,
            stale=False,
            tags=[t.strip() for t in ap.tags if t.strip()],
            created_at=now,
        )
        _store.save_test_case(tc.model_dump(mode="json"))
        saved_ids.append(tc.id)

    return {"saved": len(saved_ids), "test_case_ids": saved_ids}


@test_cases_router.get("", response_model=list[TestCase])
def list_test_cases(
    user_story_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
):
    # Inherit ownership from parent story.
    _load_story_or_403(user_story_id, current_user)
    rows = _store.get_test_cases_by_story(user_story_id)
    return [TestCase.model_validate(r) for r in rows]


@tags_router.get("", response_model=list[Tag])
def list_tags(
    project_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
):
    """Tags are project-scoped metadata. Phase 1: return tags for any project_id
    the user has at least one story in (cheap proxy for "owns the project").
    Admin sees all. Static tags are always visible to anyone with project access.
    """
    rows = _store.list_tags(project_id)
    tags = [Tag.model_validate(r) for r in rows]
    if not current_user.is_admin:
        # Cheap ownership probe: do we have any story in this project?
        my_stories = [
            s for s in (_story_model(r) for r in _store.list_user_stories(project_id))
            if s.owner_user_id == current_user.id
        ]
        if not my_stories:
            return []
    static = sorted([t for t in tags if t.scope == TagScope.static], key=lambda t: t.name.lower())
    custom = sorted([t for t in tags if t.scope == TagScope.custom], key=lambda t: t.name.lower())
    return static + custom
