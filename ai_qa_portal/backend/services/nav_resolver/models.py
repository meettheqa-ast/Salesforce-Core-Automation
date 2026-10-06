"""Shared types for grounded navigation-target resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TargetType(str, Enum):
    TAB = "tab"
    OBJECT = "object"
    RELATED_LIST = "related_list"
    QUICK_ACTION = "quick_action"
    SETUP_PAGE = "setup_page"
    FLOW = "flow"
    REPORT = "report"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NavCandidate:
    name: str
    type: TargetType
    similarity: float


@dataclass
class ResolvedNavTarget:
    """What the resolver concluded about one story-named target.

    ``requested_name`` is what the story/LLM asked for; ``name`` is the
    org's real label for the best match (equal to ``requested_name`` when
    ``type`` is UNKNOWN -- there's nothing to rename it to).
    """

    requested_name: str
    name: str
    type: TargetType
    how_to_reach: str
    confidence: float
    api_name: str | None = None
    candidates: list[NavCandidate] = field(default_factory=list)
