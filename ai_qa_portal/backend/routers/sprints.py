"""Sprint CRUD + story-assignment + flatten-test-cases-for-sprint.

Sprints sit between Project and UserStory. Stories can also live outside
any sprint (sprint_id = None on UserStory) -- those rows are the
"backlog". Ownership is inherited from the parent project, mirroring
how user_stories.py and personas.py inherit ownership.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session  # pylint: disable=unused-import  # used in Depends-typed params

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import (
    assert_user_can_create_in_project,  # pylint: disable=unused-import  # used at create_sprint
    get_current_user,
)
from ai_qa_portal.backend.services.db import (  # pylint: disable=unused-import  # get_db used in Depends
    User,
    get_db,
)

from ..models.sprint import Sprint, SprintCreate, SprintState, SprintUpdate
from ..models.test_case import TestCase
from ..models.user_story import UserStory, UserStoryStatus
from ..storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)

router = APIRouter(
    prefix="/sprints",
    tags=["sprints"],
    dependencies=[Depends(get_current_user)],
)


# --- Helpers -----------------------------------------------------------


def _user_can_see_sprint(s: Sprint, user: User) -> bool:
    """Same Phase-1 ownership rule as UserStory: each user sees only
    their own. Admin sees all. Legacy unowned rows are admin-only."""
    if user.is_admin:
        return True
    return bool(s.owner_user_id) and s.owner_user_id == user.id


def _load_sprint_or_403(sprint_id: UUID, user: User) -> Sprint:
    try:
        row = _store.get_sprint(sprint_id)
    except KeyError:
        raise HTTPException(404, "Sprint not found") from None
    sprint = Sprint.model_validate(row)
    if not _user_can_see_sprint(sprint, user):
        # 404 not 403, to avoid leaking existence.
        raise HTTPException(404, "Sprint not found")
    return sprint


def _load_story_for_owner(story_id: UUID, user: User) -> UserStory:
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = UserStory.model_validate(row)
    if not user.is_admin and (
        not story.owner_user_id or story.owner_user_id != user.id
    ):
        raise HTTPException(404, "User story not found")
    return story


# --- CRUD --------------------------------------------------------------


@router.post("", response_model=Sprint, status_code=201)
def create_sprint(
    body: SprintCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a sprint under a project. Admins, filesystem owners (legacy),
    and TL+ DB members may create."""
    assert_user_can_create_in_project(db, current_user, body.project_id)
    now = datetime.now(UTC)
    sprint = Sprint(
        id=uuid4(),
        project_id=body.project_id,
        name=body.name.strip() or "Untitled Sprint",
        goal=(body.goal or "").strip() or None,
        state=body.state,
        start_date=body.start_date,
        end_date=body.end_date,
        created_at=now,
        updated_at=now,
        owner_user_id=current_user.id,
    )
    _store.save_sprint(sprint.model_dump(mode="json"))
    return sprint


@router.get("", response_model=list[Sprint])
def list_sprints(
    project_id: UUID = Query(..., description="Project UUID"),
    state: SprintState | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    rows = _store.list_sprints(project_id)
    sprints = [Sprint.model_validate(r) for r in rows]
    sprints = [s for s in sprints if _user_can_see_sprint(s, current_user)]
    if state is not None:
        sprints = [s for s in sprints if s.state == state]
    # Active first, then planned, then completed, then cancelled. Within
    # each state, newest created_at first.
    state_order = {
        SprintState.active: 0,
        SprintState.planned: 1,
        SprintState.completed: 2,
        SprintState.cancelled: 3,
    }
    sprints.sort(key=lambda s: (state_order.get(s.state, 99), -s.created_at.timestamp()))
    return sprints


@router.get("/{sprint_id}", response_model=Sprint)
def get_sprint(sprint_id: UUID, current_user: User = Depends(get_current_user)):
    return _load_sprint_or_403(sprint_id, current_user)


@router.put("/{sprint_id}", response_model=Sprint)
def update_sprint(
    sprint_id: UUID,
    body: SprintUpdate,
    current_user: User = Depends(get_current_user),
):
    sprint = _load_sprint_or_403(sprint_id, current_user)
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        return sprint

    row = _store.get_sprint(sprint_id)
    for k, v in payload.items():
        if k == "name" and isinstance(v, str):
            v = v.strip() or row.get("name") or "Untitled Sprint"
        elif k == "goal" and isinstance(v, str):
            v = v.strip() or None
        elif k == "state" and hasattr(v, "value"):
            v = v.value
        elif k in {"start_date", "end_date"} and v is not None:
            # Normalise to ISO date string for JSON round-trip
            v = v.isoformat() if hasattr(v, "isoformat") else v
        row[k] = v
    row["updated_at"] = datetime.now(UTC).isoformat()
    _store.save_sprint(row)
    return Sprint.model_validate(row)


@router.delete("/{sprint_id}", status_code=200)
def delete_sprint(
    sprint_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Soft-delete: state -> cancelled, AND every story in this sprint
    has its sprint_id cleared back to null (so the stories aren't
    orphaned -- they just move back to the backlog)."""
    sprint = _load_sprint_or_403(sprint_id, current_user)
    # Gather stories before mutating, to avoid index churn during the loop.
    story_rows = _store.get_user_stories_by_sprint(sprint_id)
    cleared = 0
    for srow in story_rows:
        srow["sprint_id"] = None
        srow["updated_at"] = datetime.now(UTC).isoformat()
        _store.save_user_story(srow)
        cleared += 1

    row = _store.get_sprint(sprint_id)
    row["state"] = SprintState.cancelled.value
    row["updated_at"] = datetime.now(UTC).isoformat()
    _store.save_sprint(row)
    return {
        "sprint_id": str(sprint.id),
        "state": SprintState.cancelled.value,
        "stories_cleared": cleared,
    }


# --- Story assignment --------------------------------------------------


@router.post("/{sprint_id}/stories/{story_id}/assign", response_model=UserStory)
def assign_story_to_sprint(
    sprint_id: UUID,
    story_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Move a story into this sprint. The story must belong to the same
    project as the sprint -- cross-project moves aren't supported."""
    sprint = _load_sprint_or_403(sprint_id, current_user)
    story = _load_story_for_owner(story_id, current_user)
    if story.project_id != sprint.project_id:
        raise HTTPException(
            400,
            "Story and sprint belong to different projects -- cannot assign across projects.",
        )
    row = _store.get_user_story(story_id)
    row["sprint_id"] = str(sprint_id)
    row["updated_at"] = datetime.now(UTC).isoformat()
    _store.save_user_story(row)  # save_user_story handles index moves
    return UserStory.model_validate(row)


@router.delete("/{sprint_id}/stories/{story_id}", response_model=UserStory)
def remove_story_from_sprint(
    sprint_id: UUID,
    story_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Clear sprint_id back to null. The story moves to the backlog."""
    _load_sprint_or_403(sprint_id, current_user)
    story = _load_story_for_owner(story_id, current_user)
    if str(story.sprint_id) != str(sprint_id):
        # Idempotent-ish: if the story is already not in this sprint,
        # don't error. Surface as 200 + unchanged story so the UI can
        # treat the action as done.
        return story
    row = _store.get_user_story(story_id)
    row["sprint_id"] = None
    row["updated_at"] = datetime.now(UTC).isoformat()
    _store.save_user_story(row)
    return UserStory.model_validate(row)


# --- Sprint test-case rollup -------------------------------------------


class _TestCaseRow(BaseModel):
    id: str
    title: str
    status: str
    stale: bool
    tags: list[str]
    script_path: str | None = None
    script_built_at: datetime | None = None
    heal_attempts: int = 0


class _StoryGroup(BaseModel):
    id: str
    title: str
    version: int
    test_cases: list[_TestCaseRow]


class _SprintTestCasesResponse(BaseModel):
    sprint_id: str
    project_id: str
    total: int
    by_status: dict[str, int]
    scripts_built: int
    stories: list[_StoryGroup]


@router.get("/{sprint_id}/test-cases", response_model=_SprintTestCasesResponse)
def list_sprint_test_cases(
    sprint_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Return every test case for every story in the sprint, grouped by
    story. Same shape as `/api/projects/{name}/test-cases` so the
    existing frontend rendering can be reused on the sprint detail page.
    """
    sprint = _load_sprint_or_403(sprint_id, current_user)

    story_rows = _store.get_user_stories_by_sprint(sprint_id)
    stories = [UserStory.model_validate(r) for r in story_rows]
    # Filter out stories the current user can't see (admin always passes).
    stories = [
        s for s in stories
        if current_user.is_admin or (s.owner_user_id and s.owner_user_id == current_user.id)
    ]
    # Drop archived stories so the sprint summary matches the
    # user-stories list on /user-stories which only shows active stories.
    stories = [s for s in stories if s.status == UserStoryStatus.active]

    # Collect test cases, grouped by story.
    cases_by_story: dict[str, list[TestCase]] = defaultdict(list)
    all_cases: list[TestCase] = []
    for story in stories:
        rows = _store.get_test_cases_by_story(story.id)
        for r in rows:
            tc = TestCase.model_validate(r)
            cases_by_story[str(story.id)].append(tc)
            all_cases.append(tc)

    by_status = {"draft": 0, "approved": 0, "rejected": 0, "stale": 0}
    scripts_built = 0
    for tc in all_cases:
        if tc.stale:
            by_status["stale"] += 1
        else:
            by_status[tc.status.value] = by_status.get(tc.status.value, 0) + 1
        if tc.script_path:
            scripts_built += 1

    status_order = {"approved": 0, "draft": 1, "rejected": 2}
    groups: list[_StoryGroup] = []
    for story in stories:
        tcs = cases_by_story.get(str(story.id), [])
        tcs.sort(key=lambda t: (status_order.get(t.status.value, 99), t.title.lower()))
        groups.append(
            _StoryGroup(
                id=str(story.id),
                title=story.title,
                version=story.version,
                test_cases=[
                    _TestCaseRow(
                        id=str(t.id),
                        title=t.title,
                        status=t.status.value,
                        stale=t.stale,
                        tags=list(t.tags or []),
                        script_path=t.script_path,
                        script_built_at=t.script_built_at,
                        heal_attempts=int(getattr(t, "heal_attempts", 0) or 0),
                    )
                    for t in tcs
                ],
            )
        )

    return _SprintTestCasesResponse(
        sprint_id=str(sprint.id),
        project_id=str(sprint.project_id),
        total=len(all_cases),
        by_status=by_status,
        scripts_built=scripts_built,
        stories=groups,
    )
