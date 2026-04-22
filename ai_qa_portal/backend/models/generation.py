from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class GeneratedTestCase(BaseModel):
    title: str
    steps: list[str]
    expected_result: str
    preconditions: Optional[str] = None
    suggested_tags: list[str] = Field(default_factory=list)


class GenerationResponse(BaseModel):
    user_story_id: UUID
    generated: list[GeneratedTestCase]
