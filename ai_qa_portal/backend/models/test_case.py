from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TestCaseStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"


class TestCase(BaseModel):
    id: UUID
    user_story_id: UUID
    project_id: UUID
    title: str
    steps: list[str]
    expected_result: str
    preconditions: str | None = None
    status: TestCaseStatus = TestCaseStatus.draft
    stale: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    # Path (relative to repo root) to a saved Robot Framework script for this case.
    # Populated by POST /user-stories/{id}/build-scripts; nullable so legacy rows
    # and never-built cases stay valid. Run-by-story prefers this when present
    # and falls back to building inline.
    script_path: str | None = None
    script_built_at: datetime | None = None
    # Self-healing telemetry (Phase 3 of the AI brain plan). The heal endpoint
    # bumps heal_attempts on each successful rewrite and refuses to act past
    # a per-hour cap so a truly broken case doesn't burn unlimited LLM
    # budget. last_healed_at is informational for the UI.
    heal_attempts: int = 0
    last_healed_at: datetime | None = None
    # External-system passthrough (future Jira / Xray / Zephyr). Today
    # these stay null and the local-only flow never touches them. A
    # future integration writes these on import + sync.
    external_id: str | None = None
    external_source: str | None = None
    external_url: str | None = None
    last_synced_at: datetime | None = None
    external_payload: dict[str, Any] | None = None


class TestCaseApprove(BaseModel):
    test_case_id: UUID
    title: str
    steps: list[str]
    expected_result: str
    preconditions: str | None = None
    tags: list[str] = Field(default_factory=list)


class BatchApproveRequest(BaseModel):
    user_story_id: UUID
    approved: list[TestCaseApprove]
