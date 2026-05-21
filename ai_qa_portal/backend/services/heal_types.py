"""Shared types for runtime Salesforce form healing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class ErrorType(str, Enum):
    REQUIRED_FIELD = "REQUIRED_FIELD"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_PICKLIST = "INVALID_PICKLIST"
    VALIDATION_RULE = "VALIDATION_RULE"
    CONDITIONAL_REQUIRED = "CONDITIONAL_REQUIRED"
    DUPLICATE_VALUE = "DUPLICATE_VALUE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass(frozen=True)
class RawError:
    source: Literal["modal_snag", "inline_required", "page_banner", "error_toast", "duplicate_modal"]
    raw_text: str
    field_label: str | None
    severity: Literal["error", "warning"]
    locator_used: str


@dataclass(frozen=True)
class ClassifiedError:
    raw: RawError
    error_type: ErrorType
    field_label: str | None
    field_api_name: str | None
    detected_value: str | None
    confidence: float


@dataclass(frozen=True)
class HealingDecision:
    strategy: str
    target_field_label: str | None
    fill_value: str | None
    requires_llm: bool
    notes: str
    confidence: float


@dataclass
class HealAttempt:
    attempt_n: int
    started_at: datetime
    raw_errors: list[RawError] = field(default_factory=list)
    classified: list[ClassifiedError] = field(default_factory=list)
    decisions: list[HealingDecision] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    save_outcome: Literal["passed", "failed_with_new_errors", "failed_same_errors", "aborted"] = "aborted"
    latency_ms: int = 0


@dataclass
class HealResult:
    outcome: Literal["passed", "aborted", "budget_exhausted", "skipped"]
    reason: str = ""
    attempts: list[HealAttempt] = field(default_factory=list)
    healed_fields: list[str] = field(default_factory=list)

