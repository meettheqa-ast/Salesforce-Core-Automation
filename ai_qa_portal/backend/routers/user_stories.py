"""User stories, generated test-case review, and tag listing (additive routes)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query

from ai_qa_portal.backend.config import settings
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

router = APIRouter(prefix="/user-stories", tags=["user-stories"])
test_cases_router = APIRouter(prefix="/test-cases", tags=["test-cases"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])


def _story_model(row: dict) -> UserStory:
    return UserStory.model_validate(row)


@router.post("", response_model=UserStory, status_code=201)
async def create_user_story(body: UserStoryCreate):
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
    )
    _store.save_user_story(story.model_dump(mode="json"))
    return story


@router.get("", response_model=list[UserStory])
def list_user_stories(project_id: UUID = Query(..., description="Project UUID")):
    rows = _store.list_user_stories(project_id)
    items = [_story_model(r) for r in rows if r.get("status") == UserStoryStatus.active.value]
    items.sort(key=lambda s: s.created_at, reverse=True)
    return items


@router.get("/{story_id}", response_model=UserStory)
def get_user_story_by_id(story_id: UUID):
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    return _story_model(row)


@router.put("/{story_id}")
def update_user_story(story_id: UUID, body: UserStoryUpdate):
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    existing = _story_model(row)
    if existing.status != UserStoryStatus.active:
        raise HTTPException(400, "Only active stories can be updated this way")
    new_story, stale_count = _versioner.update_story(existing, body, _store)
    return {
        "new_story": new_story.model_dump(mode="json"),
        "stale_count": stale_count,
        "message": f"Story updated to v{new_story.version}. {stale_count} test cases marked stale.",
    }


@router.post("/{story_id}/generate", response_model=GenerationResponse)
async def generate_test_cases(story_id: UUID):
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = _story_model(row)
    generated = await _generator.generate(story)
    return GenerationResponse(user_story_id=story.id, generated=generated)


@test_cases_router.post("/batch-approve")
def batch_approve(body: BatchApproveRequest):
    try:
        story_row = _store.get_user_story(body.user_story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = _story_model(story_row)
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
def list_test_cases(user_story_id: UUID = Query(...)):
    rows = _store.get_test_cases_by_story(user_story_id)
    return [TestCase.model_validate(r) for r in rows]


@tags_router.get("", response_model=list[Tag])
def list_tags(project_id: UUID = Query(...)):
    rows = _store.list_tags(project_id)
    tags = [Tag.model_validate(r) for r in rows]
    static = sorted([t for t in tags if t.scope == TagScope.static], key=lambda t: t.name.lower())
    custom = sorted([t for t in tags if t.scope == TagScope.custom], key=lambda t: t.name.lower())
    return static + custom
