"""Match a free-text user prompt to a Recipe and extract parameters.

Public entry point::

    match = match_prompt(user_prompt)
    if match and match.confidence == "high":
        rendered = recipe_library.render(match.recipe, match.params)
        # ... validate, ship, no LLM call needed.
    else:
        # ... fall through to local LLM, then cloud.
        pass

Match rules (in order, first one wins):

1. Iterate recipes in registration order. For each recipe, check
   ``Recipe.intent_signals``: ANY signal that fires accepts the
   recipe.
2. On accept, run the recipe's ``extractor`` against the prompt. If
   it returns a non-None dict containing all keys in
   ``Recipe.required_params``, build a ``RecipeMatch`` and return.
3. On any failure (no signal fires, extractor returns None, missing
   required params), fall through to the next recipe.
4. Exhaust the registry -> return None (caller falls through to LLM).

The matcher is intentionally simple. If we need fuzzy matching,
sentence-similarity, or anything beyond regex, that's a strong hint
the prompt is novel and should reach the LLM tier instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ai_qa_portal.backend.services import recipe_library
from ai_qa_portal.backend.services.recipe_library import Recipe

logger = logging.getLogger("ai_qa_portal.recipe_matcher")


@dataclass
class RecipeMatch:
    """Result of a successful recipe match.

    Attributes:
        recipe: the matched ``Recipe`` instance.
        params: parameter dict ready to pass to ``recipe_library.render``.
        confidence: ``"high"`` | ``"medium"`` | ``"low"`` -- copied
            from the IntentSignal that fired. Callers use this to
            decide whether to auto-render (high) or surface a
            confirmation UI (medium) before committing.
    """
    recipe: Recipe
    params: dict[str, Any]
    confidence: str


def match_prompt(prompt: str) -> RecipeMatch | None:
    """Return the highest-confidence recipe match for ``prompt``, or None.

    Iterates the registry in insertion order. Specific recipes (e.g.
    ``lead_routing_by_state``) are registered before general ones
    (``create_lead_basic``) so they win on overlapping prompts.
    """
    if not prompt or not prompt.strip():
        return None

    for recipe in recipe_library.all_recipes():
        confidence = recipe.confidence_for(prompt)
        if confidence is None:
            continue

        try:
            params = recipe.extractor(prompt)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "recipe_matcher: extractor for %r raised, skipping: %s",
                recipe.name, exc,
            )
            continue

        if params is None:
            logger.debug(
                "recipe_matcher: %r intent matched but extractor returned None",
                recipe.name,
            )
            continue

        if recipe.required_params:
            missing = [k for k in recipe.required_params if not params.get(k)]
            if missing:
                logger.debug(
                    "recipe_matcher: %r missing required params %s",
                    recipe.name, missing,
                )
                continue

        logger.info(
            "recipe_matcher: matched recipe=%s confidence=%s",
            recipe.name, confidence,
        )
        return RecipeMatch(recipe=recipe, params=params, confidence=confidence)

    return None


def render_match(match: RecipeMatch) -> str:
    """Convenience: render a match's template. Equivalent to
    ``recipe_library.render(match.recipe, match.params)`` -- here so
    callers don't need to import both modules."""
    return recipe_library.render(match.recipe, match.params)
