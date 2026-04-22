from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel


class TagScope(str, enum.Enum):
    static = "static"
    custom = "custom"


class Tag(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    color: str = "#888780"
    scope: TagScope = TagScope.custom


STATIC_TAGS: list[str] = ["Smoke", "Regression", "Sanity", "E2E"]
