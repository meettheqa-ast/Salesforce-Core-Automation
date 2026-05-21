"""Resolver for INVALID_FORMAT classification."""

from __future__ import annotations

import random
import string

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class FormatResolver(BaseResolver):
    error_type = ErrorType.INVALID_FORMAT

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        field_label = error.field_label
        field_type = ""
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
            field_type = str((field or {}).get("type") or "").lower()

        message = (error.raw.raw_text or "").lower()
        if "email" in message or field_type == "email":
            value = f"auto.fix.{_digits(6)}@example.com"
        elif "phone" in message or "fax" in message or field_type == "phone":
            value = f"555{_digits(7)}"
        elif "url" in message or "website" in message or field_type == "url":
            value = f"https://example.com/{_digits(6)}"
        else:
            value = f"AutoFmt{_digits(5)}"

        return HealingDecision(
            strategy="fill_text",
            target_field_label=field_label,
            fill_value=value,
            requires_llm=False,
            notes="Regenerated value to satisfy format constraints.",
            confidence=0.82,
        )


def _digits(n: int) -> str:
    return "".join(random.choices(string.digits, k=n))

