from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
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
    preconditions: Optional[str] = None
    status: TestCaseStatus = TestCaseStatus.draft
    stale: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: datetime


class TestCaseApprove(BaseModel):
    test_case_id: UUID
    title: str
    steps: list[str]
    expected_result: str
    preconditions: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class BatchApproveRequest(BaseModel):
    user_story_id: UUID
    approved: list[TestCaseApprove]
