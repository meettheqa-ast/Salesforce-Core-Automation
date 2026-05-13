from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class UserStoryStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class UserStory(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str
    status: UserStoryStatus = UserStoryStatus.draft
    version: int = 1
    prev_version_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    # Phase 1 isolation: stamped on create. Empty string for legacy records.
    owner_user_id: str = ""
    # Sprint hierarchy. Optional: a story may live in a sprint OR in the
    # backlog (sprint_id = None). Existing rows pre-date this field, so
    # the default keeps them valid without migration.
    sprint_id: UUID | None = None
    # External-system passthrough fields (future Jira / Azure DevOps).
    # Today these stay null; the local-only flow never reads or writes
    # them. See `models/sprint.py` for the rationale.
    external_id: str | None = None
    external_source: str | None = None
    external_url: str | None = None
    last_synced_at: datetime | None = None
    external_payload: dict[str, Any] | None = None


class UserStoryCreate(BaseModel):
    project_id: UUID
    title: str
    description: str
    # Optional sprint to drop the new story into. Omit for backlog.
    sprint_id: UUID | None = None


class UserStoryUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
