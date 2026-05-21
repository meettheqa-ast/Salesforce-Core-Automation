"""Resolver for VALIDATION_RULE classification."""

from __future__ import annotations

import random
import string

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext
from .unknown import UnknownResolver


class ValidationRuleResolver(BaseResolver):
    error_type = ErrorType.VALIDATION_RULE

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        msg = (error.raw.raw_text or "").strip()
        field_label = error.field_label
        if not field_label:
            # Attempt a cheap metadata-backed message match against validation rules.
            rules = ctx.metadata.validation_rules(ctx.sobject, org_key=ctx.org_key)
            for rule in rules:
                em = str(rule.get("errorMessage") or "").strip()
                if em and em.lower() in msg.lower():
                    field_label = str(rule.get("errorDisplayField") or "").strip() or None
                    break

        if field_label:
            field = ctx.metadata.get_field(
                ctx.sobject,
                field_label,
                org_key=ctx.org_key,
                sandbox_url=ctx.sandbox_url,
                username=ctx.username,
                password=ctx.password,
                security_token=ctx.security_token,
            )
            pick_vals = list((field or {}).get("picklist_values") or [])
            if pick_vals:
                return HealingDecision(
                    strategy="select_picklist",
                    target_field_label=field_label,
                    fill_value=pick_vals[0],
                    requires_llm=False,
                    notes="Matched validation rule to field and selected valid picklist option.",
                    confidence=0.72,
                )
            field_type = str((field or {}).get("type") or "").lower()
            return HealingDecision(
                strategy="fill_text",
                target_field_label=field_label,
                fill_value=_default_value(field_type),
                requires_llm=False,
                notes="Applied deterministic validation-rule fallback for known target field.",
                confidence=0.68,
            )

        return UnknownResolver().propose_fix(error, ctx)


def _default_value(field_type: str) -> str:
    token = "".join(random.choices(string.digits, k=5))
    if field_type == "email":
        return f"auto.vr.{token}@example.com"
    if field_type == "phone":
        return f"555{token[:3]}{token[3:]}"
    if field_type == "url":
        return f"https://example.com/{token}"
    return f"AutoRule {token}"

