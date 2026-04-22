from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ..models.user_story import UserStory, UserStoryStatus, UserStoryUpdate
from ..storage.json_file_backend import JsonFileBackend


class StoryVersioner:
    def update_story(
        self,
        existing: UserStory,
        update: UserStoryUpdate,
        storage: JsonFileBackend,
    ) -> tuple[UserStory, int]:
        """Archive old story, create new version, mark old test cases stale."""
        archived = existing.model_copy(
            update={
                "status": UserStoryStatus.archived,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        storage.save_user_story(archived.model_dump(mode="json"))

        new_story = UserStory(
            id=uuid4(),
            project_id=existing.project_id,
            title=update.title if update.title is not None else existing.title,
            description=update.description if update.description is not None else existing.description,
            status=UserStoryStatus.active,
            version=existing.version + 1,
            prev_version_id=existing.id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        storage.save_user_story(new_story.model_dump(mode="json"))

        old_tcs = storage.get_test_cases_by_story(existing.id)
        for tc in old_tcs:
            tc["stale"] = True
            storage.save_test_case(tc)

        return new_story, len(old_tcs)
