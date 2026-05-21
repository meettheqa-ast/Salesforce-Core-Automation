"""Resolver registry for runtime form healing."""

from __future__ import annotations

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import ResolverContext
from .dependency import DependencyResolver
from .duplicate import DuplicateResolver
from .format import FormatResolver
from .picklist import PicklistResolver
from .required_field import RequiredFieldResolver
from .unknown import UnknownResolver
from .validation_rule import ValidationRuleResolver


class StrategyRegistry:
    def __init__(self) -> None:
        self._resolvers = {
            ErrorType.REQUIRED_FIELD: RequiredFieldResolver(),
            ErrorType.INVALID_FORMAT: FormatResolver(),
            ErrorType.INVALID_PICKLIST: PicklistResolver(),
            ErrorType.VALIDATION_RULE: ValidationRuleResolver(),
            ErrorType.CONDITIONAL_REQUIRED: DependencyResolver(),
            ErrorType.DUPLICATE_VALUE: DuplicateResolver(),
            ErrorType.UNKNOWN_ERROR: UnknownResolver(),
        }

    def resolve_each(
        self,
        errors: list[ClassifiedError],
        ctx: ResolverContext,
    ) -> list[HealingDecision]:
        decisions: list[HealingDecision] = []
        for err in errors:
            resolver = self._resolvers.get(err.error_type) or self._resolvers[ErrorType.UNKNOWN_ERROR]
            decisions.append(resolver.propose_fix(err, ctx))
        return decisions

