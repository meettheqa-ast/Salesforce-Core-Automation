from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from ai_qa_portal.backend.models.generation import GeneratedTestCase
from ai_qa_portal.backend.models.test_case import TestCase as RobotTestCase
from ai_qa_portal.backend.models.user_story import UserStory, UserStoryStatus, UserStoryUpdate
from ai_qa_portal.backend.services.story_versioner import StoryVersioner
from ai_qa_portal.backend.services.test_case_generator import TestCaseGenerator
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend


def test_test_case_generator_parses_llm_json(monkeypatch):
    sample = json.dumps(
        [
            {
                "title": "Login works",
                "steps": ["Open app", "Login"],
                "expected_result": "Dashboard visible",
                "preconditions": None,
                "suggested_tags": ["Smoke"],
            }
        ]
    )

    import ai_bridge

    monkeypatch.setattr(ai_bridge, "call_llm", lambda _s, _u, image_bytes=None: sample)

    gen = TestCaseGenerator()
    story = UserStory(
        id=uuid4(),
        project_id=uuid4(),
        title="Auth",
        description="User logs in",
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    out = asyncio.run(gen.generate(story))
    assert len(out) == 1
    assert isinstance(out[0], GeneratedTestCase)
    assert out[0].title == "Login works"
    assert out[0].steps == ["Open app", "Login"]
    assert out[0].suggested_tags == ["Smoke"]


def test_test_case_generator_invalid_json_raises(monkeypatch):
    import ai_bridge

    monkeypatch.setattr(ai_bridge, "call_llm", lambda _s, _u, image_bytes=None: "not json")

    gen = TestCaseGenerator()
    story = UserStory(
        id=uuid4(),
        project_id=uuid4(),
        title="T",
        description="D",
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        asyncio.run(gen.generate(story))


def test_story_versioner_archives_and_stales(tmp_path):
    store = JsonFileBackend(str(tmp_path))
    pid = uuid4()
    sid = uuid4()
    now = datetime.now(timezone.utc)
    story = UserStory(
        id=sid,
        project_id=pid,
        title="V1",
        description="Old",
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=now,
        updated_at=now,
    )
    store.save_user_story(story.model_dump(mode="json"))

    tc_id = uuid4()
    tc = RobotTestCase(
        id=tc_id,
        user_story_id=sid,
        project_id=pid,
        title="TC1",
        steps=["a"],
        expected_result="ok",
        status="approved",
        stale=False,
        tags=["Smoke"],
        created_at=now,
    )
    store.save_test_case(tc.model_dump(mode="json"))

    ver = StoryVersioner()
    new_story, stale_n = ver.update_story(
        story,
        UserStoryUpdate(description="New body"),
        store,
    )

    old = UserStory.model_validate(store.get_user_story(sid))
    assert old.status == UserStoryStatus.archived
    assert new_story.version == 2
    assert stale_n == 1
    updated_tc = RobotTestCase.model_validate(store.get_test_case(tc_id))
    assert updated_tc.stale is True


def test_get_test_cases_by_tag_smoke_filter(tmp_path):
    store = JsonFileBackend(str(tmp_path))
    pid = uuid4()
    sid = uuid4()
    now = datetime.now(timezone.utc)
    store.save_user_story(
        UserStory(
            id=sid,
            project_id=pid,
            title="S",
            description="D",
            status=UserStoryStatus.active,
            version=1,
            prev_version_id=None,
            created_at=now,
            updated_at=now,
        ).model_dump(mode="json")
    )

    def _tc(tid, tags, title):
        return RobotTestCase(
            id=tid,
            user_story_id=sid,
            project_id=pid,
            title=title,
            steps=["x"],
            expected_result="e",
            status="approved",
            stale=False,
            tags=tags,
            created_at=now,
        ).model_dump(mode="json")

    store.save_test_case(_tc(uuid4(), ["Smoke"], "A"))
    store.save_test_case(_tc(uuid4(), ["Smoke"], "B"))
    store.save_test_case(_tc(uuid4(), ["Regression"], "C"))

    smoke = store.get_test_cases_by_tag(pid, "Smoke")
    assert len(smoke) == 2
