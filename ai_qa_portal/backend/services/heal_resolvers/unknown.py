"""Resolver for UNKNOWN_ERROR using a capped LLM fallback."""

from __future__ import annotations

import json
import re

from ai_qa_portal.backend.services.heal_types import ClassifiedError, ErrorType, HealingDecision

from .base import BaseResolver, ResolverContext


class UnknownResolver(BaseResolver):
    error_type = ErrorType.UNKNOWN_ERROR

    def propose_fix(self, error: ClassifiedError, ctx: ResolverContext) -> HealingDecision:
        if ctx.llm_budget_cb is not None and not ctx.llm_budget_cb():
            return HealingDecision(
                strategy="skip_field",
                target_field_label=error.field_label,
                fill_value=None,
                requires_llm=True,
                notes="LLM budget exhausted for unknown-error healing.",
                confidence=0.2,
            )
        try:
            from ai_bridge import call_llm

            system_prompt = (
                "You are a Salesforce test-healing assistant. Return strict JSON with keys: "
                "strategy, target_field_label, fill_value, notes, confidence. "
                "Allowed strategies: fill_text, select_picklist, set_lookup, skip_field, abort_save."
            )
            user_content = (
                f"SObject: {ctx.sobject}\n"
                f"Error text: {error.raw.raw_text}\n"
                f"Field label: {error.field_label or ''}\n"
                "Return only JSON."
            )
            raw = call_llm(system_prompt, user_content)
            text = (raw or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text).strip()
            obj = json.loads(text)
            strategy = str(obj.get("strategy") or "skip_field")
            if strategy not in {"fill_text", "select_picklist", "set_lookup", "skip_field", "abort_save"}:
                strategy = "skip_field"
            return HealingDecision(
                strategy=strategy,
                target_field_label=str(obj.get("target_field_label") or error.field_label or "") or None,
                fill_value=(str(obj.get("fill_value")) if obj.get("fill_value") is not None else None),
                requires_llm=True,
                notes=str(obj.get("notes") or "LLM fallback decision."),
                confidence=float(obj.get("confidence") or 0.55),
            )
        except Exception:
            pass
        return HealingDecision(
            strategy="skip_field",
            target_field_label=error.field_label,
            fill_value=error.detected_value,
            requires_llm=True,
            notes="LLM fallback unavailable; skipping unknown error.",
            confidence=0.25,
        )

