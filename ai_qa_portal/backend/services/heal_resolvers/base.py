"""Resolver contract and shared context for healing strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision


class SupportsMetadata(Protocol):
    def get_field(self, object_name: str, field_label: str, **kwargs: Any) -> dict[str, Any] | None: ...
    def picklist_values(self, object_name: str, field_api_name: str, **kwargs: Any) -> list[str]: ...


class SupportsLiveDOM(Protocol):
    def enumerate_picklist_options(self, session_id: str, field_label: str) -> list[str]: ...


@dataclass
class ResolverContext:
    session_id: str
    sobject: str
    org_key: str
    sandbox_url: str
    username: str
    password: str
    security_token: str
    duplicate_strategy: str
    metadata: SupportsMetadata
    live_dom: SupportsLiveDOM
    llm_budget_cb: Callable[[], bool] | None = None


class BaseResolver:
    error_type: ErrorType

    def applies_to(self, error: ClassifiedError) -> bool:
        return error.error_type == self.error_type

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        raise NotImplementedError

