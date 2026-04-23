from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
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
    prev_version_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    # Phase 1 isolation: stamped on create. Empty string for legacy records.
    owner_user_id: str = ""


class UserStoryCreate(BaseModel):
    project_id: UUID
    title: str
    description: str


class UserStoryUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
