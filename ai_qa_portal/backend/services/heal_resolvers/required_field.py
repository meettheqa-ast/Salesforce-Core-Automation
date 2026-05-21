"""Resolver for REQUIRED_FIELD classification."""

from __future__ import annotations

import random
import string

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class RequiredFieldResolver(BaseResolver):
    error_type = ErrorType.REQUIRED_FIELD

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        field_label = error.field_label
        if not field_label:
            return HealingDecision(
                strategy="abort_save",
                target_field_label=None,
                fill_value=None,
                requires_llm=False,
                notes="Required-field error did not include a field label.",
                confidence=0.3,
            )

        field = ctx.metadata.get_field(
            ctx.sobject,
            field_label,
            org_key=ctx.org_key,
            sandbox_url=ctx.sandbox_url,
            username=ctx.username,
            password=ctx.password,
            security_token=ctx.security_token,
        )
        field_type = str((field or {}).get("type") or "").lower()
        picklist_vals = list((field or {}).get("picklist_values") or [])
        if picklist_vals:
            return HealingDecision(
                strategy="select_picklist",
                target_field_label=field_label,
                fill_value=picklist_vals[0],
                requires_llm=False,
                notes="Filled required picklist from org metadata.",
                confidence=0.92,
            )

        value = _value_for_type(field_type)
        strategy = "set_lookup" if field_type == "reference" else "fill_text"
        return HealingDecision(
            strategy=strategy,
            target_field_label=field_label,
            fill_value=value,
            requires_llm=False,
            notes=f"Filled required field using type-aware default ({field_type or 'generic'}).",
            confidence=0.86,
        )


def _value_for_type(field_type: str) -> str:
    token = "".join(random.choices(string.digits, k=5))
    if field_type in ("email",):
        return f"qa.auto.{token}@example.com"
    if field_type in ("phone",):
        return f"555{token}"
    if field_type in ("url",):
        return f"https://example.com/{token}"
    if field_type in ("double", "currency", "percent", "int", "integer", "long"):
        return str(random.randint(10, 9999))
    if field_type in ("date",):
        return "2026-01-15"
    if field_type in ("datetime",):
        return "2026-01-15T12:30:00.000Z"
    if field_type in ("boolean",):
        return "true"
    return f"AutoFill {token}"

