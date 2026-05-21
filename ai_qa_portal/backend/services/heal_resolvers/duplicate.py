"""Resolver for DUPLICATE_VALUE classification."""

from __future__ import annotations

import random
import string

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class DuplicateResolver(BaseResolver):
    error_type = ErrorType.DUPLICATE_VALUE

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        field_label = error.field_label or "Name"
        strategy = (ctx.duplicate_strategy or "regenerate").strip().lower()
        if strategy != "regenerate":
            return HealingDecision(
                strategy="abort_save",
                target_field_label=field_label,
                fill_value=None,
                requires_llm=False,
                notes=f"Duplicate strategy '{strategy}' requested abort.",
                confidence=0.9,
            )

        suffix = "".join(random.choices(string.digits, k=5))
        value = f"AutoUnique-{suffix}"
        return HealingDecision(
            strategy="regenerate_duplicate",
            target_field_label=field_label,
            fill_value=value,
            requires_llm=False,
            notes="Regenerated conflicting value with uniqueness suffix.",
            confidence=0.88,
        )

