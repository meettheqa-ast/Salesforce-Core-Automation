from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ai_qa_portal.backend.models.user_story import UserStory, UserStoryStatus
from ai_qa_portal.backend.routers import user_stories as story_router
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend


def _seed_story(store: JsonFileBackend, *, story_id, project_id, owner_id: str) -> None:
    now = datetime.now(UTC)
    store.save_user_story(
        UserStory(
            id=story_id,
            project_id=project_id,
            title="Story for comments",
            description="Desc",
            status=UserStoryStatus.active,
            version=1,
            prev_version_id=None,
            created_at=now,
            updated_at=now,
            owner_user_id=owner_id,
        ).model_dump(mode="json")
    )


def test_create_story_comment_extracts_mentions_and_notifies(tmp_path, monkeypatch):
    store = JsonFileBackend(str(tmp_path))
    story_id = uuid4()
    project_id = uuid4()
    owner = SimpleNamespace(id="u-1", is_admin=False, email="owner@example.com")
    _seed_story(store, story_id=story_id, project_id=project_id, owner_id=owner.id)
    monkeypatch.setattr(story_router, "_store", store)

    notified: list[str] = []
    monkeypatch.setattr(
        story_router,
        "get_user_by_email",
        lambda _db, email: (
            SimpleNamespace(id="u-2", email=email)
            if email == "teammate@example.com"
            else SimpleNamespace(id="u-1", email=email)
            if email == "owner@example.com"
            else None
        ),
    )
    monkeypatch.setattr(
        story_router,
        "push_notification",
        lambda _db, **kwargs: notified.append(kwargs["user_id"]),
    )
    monkeypatch.setattr(story_router, "log_action", lambda *_a, **_k: None)

    out = story_router.create_story_comment(
        story_id,
        {
            "body": (
                "Needs review from @Teammate@example.com and @owner@example.com. "
                "Duplicate @teammate@example.com should dedupe."
            )
        },
        current_user=owner,
        db=object(),
    )

    assert out["mentions"] == ["owner@example.com", "teammate@example.com"]
    # Mentioning self should not create a notification.
    assert notified == ["u-2"]

    saved = story_router.list_story_comments(story_id, current_user=owner)
    assert len(saved) == 1
    assert saved[0]["body"].startswith("Needs review")


def test_create_story_comment_requires_body(tmp_path, monkeypatch):
    store = JsonFileBackend(str(tmp_path))
    story_id = uuid4()
    project_id = uuid4()
    owner = SimpleNamespace(id="u-1", is_admin=False, email="owner@example.com")
    _seed_story(store, story_id=story_id, project_id=project_id, owner_id=owner.id)
    monkeypatch.setattr(story_router, "_store", store)

    with pytest.raises(HTTPException) as exc:
        story_router.create_story_comment(
            story_id,
            {"body": "   "},
            current_user=owner,
            db=object(),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "body is required"


def test_story_activity_returns_desc_timestamp_order(tmp_path, monkeypatch):
    store = JsonFileBackend(str(tmp_path))
    story_id = uuid4()
    project_id = uuid4()
    owner = SimpleNamespace(id="u-1", is_admin=False, email="owner@example.com")
    _seed_story(store, story_id=story_id, project_id=project_id, owner_id=owner.id)
    monkeypatch.setattr(story_router, "_store", store)

    now = datetime.now(UTC)
    rows = [
        SimpleNamespace(
            id="a1",
            timestamp=now - timedelta(minutes=10),
            action="story_updated",
            target_type="user_story",
            target_id=str(story_id),
            user_id="u-1",
            metadata_json=json.dumps({"version": 2}),
        ),
        SimpleNamespace(
            id="a2",
            timestamp=now - timedelta(minutes=2),
            action="story_comment_added",
            target_type="user_story",
            target_id=str(story_id),
            user_id="u-2",
            metadata_json=json.dumps({"preview": "LGTM"}),
        ),
        SimpleNamespace(
            id="a3",
            timestamp=now - timedelta(minutes=1),
            action="story_assigned",
            target_type="user_story",
            target_id=str(story_id),
            user_id="u-3",
            metadata_json=json.dumps({"owner_user_id": "u-3"}),
        ),
    ]

    class FakeQuery:
        def __init__(self, all_rows):
            self._rows = all_rows
            self._limit = None

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            self._rows = sorted(
                self._rows,
                key=lambda r: r.timestamp,
                reverse=True,
            )
            return self

        def limit(self, n):
            self._limit = n
            return self

        def all(self):
            if self._limit is None:
                return list(self._rows)
            return list(self._rows)[: self._limit]

    class FakeDB:
        def query(self, _model):
            return FakeQuery(rows)

    out = story_router.story_activity(
        story_id,
        limit=2,
        current_user=owner,
        db=FakeDB(),
    )

    assert out["story_id"] == str(story_id)
    assert [it["id"] for it in out["items"]] == ["a3", "a2"]
    assert out["items"][0]["action"] == "story_assigned"
