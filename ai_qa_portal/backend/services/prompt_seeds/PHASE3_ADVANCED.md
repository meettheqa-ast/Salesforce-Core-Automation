# Phase 3 -- prompt registry advanced features (deferred)

The IA audit plan explicitly gated these features on a usage signal:

> Phase 3: prompt registry advanced features (A/B testing, marketplace,
> model-routing hints, chaining) when 20+ user-edited templates exist

Current state of the registry:

- 8 system seeds shipped (`prompt_seeds/*.md`).
- User-edited templates today: **0** (the registry shipped in the
  prior PR; usage data has not accumulated).

Because shipping marketplace + A/B + chaining + model-routing infrastructure
**before** we have product evidence of what users actually want would:

1. Burn 1-2 weeks on speculative schema + UI surface.
2. Create maintenance burden (multi-variant LLM call paths) with no
   demonstrated lift.
3. Risk locking us into the wrong abstraction -- the prompt that
   needs A/B variants probably has different placeholders than the
   one that needs chaining.

**Activation criteria** (re-open this todo when ANY two are true):

- `prompt_templates` row count where `is_system=False` >= 20
- An admin has explicitly requested A/B testing for at least one
  category
- An external prompt library has been linked from a customer org

Until then, the registry's existing primitives (template versions,
sparse overrides, audit log) cover the observed need.

When we DO build these, the natural starting points are:

- **A/B testing**: add `prompt_overrides.experiment_id` + a 50/50
  resolver that consults a hash of `user_id` to deterministically
  route each user to one variant. Audit the variant_id in
  `prompt_usage_audit` so analytics can attribute outcomes.
- **Marketplace**: add `prompt_templates.public = bool`; a new
  `GET /api/prompts/marketplace?category=` returns every `public=true`
  template across orgs. Adopting a marketplace template clones it
  into the user/org scope (existing CRUD).
- **Model-routing hints**: extend the existing `model_hint` column
  into a JSON `routing_strategy` (fallback chain, cost cap, region).
  `ai_bridge.call_llm_with_metadata` already returns provider +
  model so the audit row knows what actually ran.
- **Prompt chaining**: add `prompt_templates.chain_after =
  another_template_id` so a generation can post-process through a
  follow-up prompt. The compiler stays unchanged; the runner just
  iterates.
