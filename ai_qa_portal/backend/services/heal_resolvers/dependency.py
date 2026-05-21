"""Resolver for CONDITIONAL_REQUIRED classification."""

from __future__ import annotations

import random
import re
import string

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class DependencyResolver(BaseResolver):
    error_type = ErrorType.CONDITIONAL_REQUIRED

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        # Expected shape: "X is required when Y = Z"
        msg = error.raw.raw_text or ""
        m = re.search(
            r"(?i)([A-Za-z][A-Za-z0-9 /&_-]{1,60}) is required when ([A-Za-z][A-Za-z0-9 /&_-]{1,60}) (?:=|is|equals) (.+)",
            msg,
        )
        field_label = error.field_label
        if m:
            field_label = m.group(1).strip()
        if not field_label:
            return HealingDecision(
                strategy="skip_field",
                target_field_label=None,
                fill_value=None,
                requires_llm=False,
                notes="Conditional-required rule did not expose target field label.",
                confidence=0.35,
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
        pick_vals = list((field or {}).get("picklist_values") or [])
        if pick_vals:
            return HealingDecision(
                strategy="select_picklist",
                target_field_label=field_label,
                fill_value=pick_vals[0],
                requires_llm=False,
                notes="Filled conditionally-required field from picklist values.",
                confidence=0.84,
            )
        return HealingDecision(
            strategy="fill_text",
            target_field_label=field_label,
            fill_value=_default_value(field_type),
            requires_llm=False,
            notes="Filled conditionally-required field with metadata-aware value.",
            confidence=0.8,
        )


def _default_value(field_type: str) -> str:
    token = "".join(random.choices(string.digits, k=4))
    if field_type == "email":
        return f"auto.dep.{token}@example.com"
    if field_type == "phone":
        return f"555{token}12"
    if field_type == "url":
        return f"https://example.com/{token}"
    return f"AutoCond {token}"

