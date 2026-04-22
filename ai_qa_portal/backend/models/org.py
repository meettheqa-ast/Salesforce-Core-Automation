from __future__ import annotations

import enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class OrgType(str, enum.Enum):
    Dev = "Dev"
    QA = "QA"
    UAT = "UAT"
    Prod = "Prod"


class SalesforceOrg(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    name: str
    login_url: str
    org_type: OrgType
    default_persona_id: Optional[UUID] = None
