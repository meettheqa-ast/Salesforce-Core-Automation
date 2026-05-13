from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GeneratedTestCase(BaseModel):
    title: str
    steps: list[str]
    expected_result: str
    preconditions: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)


class GenerationResponse(BaseModel):
    user_story_id: UUID
    generated: list[GeneratedTestCase]
