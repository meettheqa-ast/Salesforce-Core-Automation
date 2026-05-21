"""Resolver for INVALID_PICKLIST classification."""

from __future__ import annotations

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class PicklistResolver(BaseResolver):
    error_type = ErrorType.INVALID_PICKLIST

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        field_label = error.field_label
        if not field_label:
            return HealingDecision(
                strategy="skip_field",
                target_field_label=None,
                fill_value=None,
                requires_llm=False,
                notes="Picklist error did not include a field label.",
                confidence=0.35,
            )

        dom_values = []
        if ctx.session_id:
            try:
                dom_values = ctx.live_dom.enumerate_picklist_options(ctx.session_id, field_label)
            except Exception:
                dom_values = []
        if dom_values:
            return HealingDecision(
                strategy="select_picklist",
                target_field_label=field_label,
                fill_value=dom_values[0],
                requires_llm=False,
                notes="Selected first currently-rendered picklist option from live DOM.",
                confidence=0.9,
            )

        meta_values = []
        if error.field_api_name:
            meta_values = ctx.metadata.picklist_values(
                ctx.sobject,
                error.field_api_name,
                org_key=ctx.org_key,
                sandbox_url=ctx.sandbox_url,
                username=ctx.username,
                password=ctx.password,
                security_token=ctx.security_token,
            )
        if meta_values:
            return HealingDecision(
                strategy="select_picklist",
                target_field_label=field_label,
                fill_value=meta_values[0],
                requires_llm=False,
                notes="Selected first active picklist option from metadata.",
                confidence=0.86,
            )

        return HealingDecision(
            strategy="skip_field",
            target_field_label=field_label,
            fill_value=None,
            requires_llm=False,
            notes="No live or metadata picklist values available.",
            confidence=0.4,
        )

