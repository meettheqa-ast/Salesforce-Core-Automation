"""Deterministic Robot Framework script templates for known scenarios.

Recipes are the first tier of the generation pipeline (recipe -> local
LLM -> external LLM). When a user prompt matches a recipe's intent
patterns at high confidence, the recipe renders a parameterized
``.robot`` file directly with no LLM call. This is the fast/free/
reliable path for the 80% of recurring scenarios (lead routing, lead
create, campaign create, conversion).

Templates live in ``templates/`` as Jinja2 ``.robot.j2`` files. The
Recipe registry in ``services/recipe_library.py`` binds them to
parameter schemas and intent patterns.
"""
