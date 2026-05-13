from __future__ import annotations

from datetime import UTC, datetime
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
                "updated_at": datetime.now(UTC),
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
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            owner_user_id=existing.owner_user_id,
            # Preserve sprint membership across versions. Without this, editing
            # any story currently in a sprint would silently drop it back to
            # the backlog -- the new row's ``sprint_id`` would default to None.
            sprint_id=existing.sprint_id,
        )
        storage.save_user_story(new_story.model_dump(mode="json"))

        old_tcs = storage.get_test_cases_by_story(existing.id)
        for tc in old_tcs:
            tc["stale"] = True
            storage.save_test_case(tc)

        return new_story, len(old_tcs)
