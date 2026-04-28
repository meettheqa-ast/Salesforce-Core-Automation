"""Sprint model.

A Sprint sits between Project and UserStory in the hierarchy:

    Project -> Sprint -> UserStory -> TestCase

`sprint_id` on UserStory is OPTIONAL (nullable), so a story can also live
outside any sprint -- the "backlog" -- without a forced migration of
existing data. The `external_*` fields are passthrough metadata that a
future external-issue-tracker integration (Jira, Azure DevOps, etc.)
will populate. Today they stay null; the local-only flow never reads or
writes them, so adding them costs nothing for current users.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SprintState(str, enum.Enum):
    planned = "planned"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class Sprint(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    name: str
    goal: Optional[str] = None
    state: SprintState = SprintState.planned
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    # Phase 1 isolation: stamped on create. Empty string for legacy records.
    owner_user_id: str = ""

    # External-system passthrough fields. Today these stay null. A future
    # Jira / Azure DevOps integration writes these on import + sync.
    # Nothing in the local-only flow ever touches them, so adding them is
    # a no-op for existing behaviour.
    external_id: Optional[str] = None
    external_source: Optional[str] = None  # "jira" | reserved for "azure-devops" etc.
    external_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    # Raw source-system payload kept verbatim. Useful for two-way sync
    # so we can detect "is the external state divergent from ours?"
    # without re-fetching every field individually.
    external_payload: Optional[dict[str, Any]] = None


class SprintCreate(BaseModel):
    project_id: UUID
    name: str
    goal: Optional[str] = None
    state: SprintState = SprintState.planned
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class SprintUpdate(BaseModel):
    """Partial update; every field optional. PUT /sprints/{id} applies
    only the keys actually provided."""
    name: Optional[str] = None
    goal: Optional[str] = None
    state: Optional[SprintState] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
