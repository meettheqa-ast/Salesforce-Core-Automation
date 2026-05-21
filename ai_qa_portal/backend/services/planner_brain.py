"""Planner brain: pre-planning, recall, recovery, and post-success memory.

This module is the orchestration layer that wraps the raw LLM planner
(``ai_bridge.break_prompt_into_steps``) with everything we learned from
the Pentair runs needs to be in the loop:

1. **Verified-recipe lookup** -- if we've already produced a successful
   plan for a similar prompt in this project (or any project as a
   fallback), short-circuit the LLM and replay it. ~0 cost, deterministic.

2. **RF-MCP scenario analysis pre-pass** -- ask RF-MCP's ``analyze_scenario``
   tool what objects/libraries it expects so the planner has a structured
   intent hint, not just the user's prose.

3. **Per-project keyword outcome bias** -- annotate the planner prompt
   with "keyword X has historically failed in this project; prefer Y"
   so the LLM stops repeating known-bad picks.

4. **Optional semantic memory recall** -- when RF-MCP's memory hooks
   are enabled, query ``recall_step`` for past successful sequences.

5. **Plan() with all of the above wired in** + the validate/replan loop
   that ai_bridge already implements.

6. **Per-step recovery replan** -- after a step fails, ask the LLM
   (with current page state + the failed step + what the original plan
   intended next) to either fix this step OR jump to a different
   intermediate step.

7. **Post-success capture** -- record the prompt+plan as a
   ``VerifiedRecipe``, increment per-keyword outcome counters.

The brain is intentionally optional: every helper has a safe no-op
fallback so importing the module never fails even when the DB or
RF-MCP isn't available. ``generate.py`` calls into it but the Stepwise
pipeline still runs end-to-end if every helper here returns empty.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select

from ai_qa_portal.backend.services.db import SessionLocal
from ai_qa_portal.backend.services.db_models.planner import (
    KeywordOutcome,
    VerifiedRecipe,
)

logger = logging.getLogger("ai_qa_portal.planner_brain")


# --- Prompt normalisation -------------------------------------------------


_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "that", "this", "is", "are", "be", "by", "as", "it", "at", "from",
    "into", "via",
})


def normalize_prompt(prompt: str) -> str:
    """Reduce a free-form prompt to a stable comparable shape.

    The point isn't perfection -- it's good enough to detect that
    "Create a Lead with Status Sales Lead in Pentair Sales" and
    "create a lead status sales lead pentair sales app" are the same
    intent. We lowercase, strip non-word chars, drop stopwords, and
    sort + join tokens so word order doesn't matter.
    """
    text = (prompt or "").strip().lower()
    text = _NON_WORD_RE.sub(" ", text)
    tokens = sorted(t for t in text.split() if t and t not in _STOPWORDS)
    return " ".join(tokens)[:512]


def prompt_signature(prompt: str) -> str:
    """Stable 16-char hex signature -- cheaper to index than the full norm."""
    norm = normalize_prompt(prompt)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


# --- Verified recipe recall + capture -------------------------------------


@dataclass
class RecallHit:
    plan: list[dict]
    project_slug: str | None
    success_count: int
    last_used_at: datetime
    similarity: float  # 1.0 == exact normalized match, 0.0 == no overlap


def _jaccard_similarity(a: str, b: str) -> float:
    """Cheap token-set similarity over normalised prompts."""
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_verified_recipe(
    prompt: str,
    *,
    project_slug: str | None = None,
    similarity_threshold: float = 0.7,
) -> RecallHit | None:
    """Return the best verified recipe for this prompt, if any.

    Lookup order:
      1. Exact normalised-prompt match for this project.
      2. Best fuzzy match (>= threshold) for this project.
      3. Exact normalised-prompt match across all projects.
      4. Best fuzzy match (>= threshold) across all projects.

    Per-project hits always win over cross-project hits even if the
    cross-project similarity is higher -- org-specific patterns matter.
    """
    norm = normalize_prompt(prompt)
    if not norm:
        return None

    try:
        with SessionLocal() as session:
            # Project-scoped exact + fuzzy.
            project_hit = _best_match_in_scope(
                session, norm,
                project_slug=project_slug,
                allow_cross_project=False,
                similarity_threshold=similarity_threshold,
            )
            if project_hit is not None:
                return project_hit
            # Cross-project fallback.
            return _best_match_in_scope(
                session, norm,
                project_slug=None,
                allow_cross_project=True,
                similarity_threshold=similarity_threshold,
            )
    except Exception as exc:  # noqa: BLE001 -- recall is best-effort
        logger.debug("find_verified_recipe failed: %s", exc)
        return None


def _best_match_in_scope(
    session, norm: str, *,
    project_slug: str | None,
    allow_cross_project: bool,
    similarity_threshold: float,
) -> RecallHit | None:
    q = select(VerifiedRecipe)
    if not allow_cross_project and project_slug:
        q = q.where(VerifiedRecipe.project_slug == project_slug)
    elif not allow_cross_project:
        q = q.where(VerifiedRecipe.project_slug.is_(None))

    rows = list(session.scalars(q).all())
    if not rows:
        return None

    # Exact matches first (cheap O(n) scan).
    for row in rows:
        if row.prompt_normalized == norm:
            return RecallHit(
                plan=list(row.plan or []),
                project_slug=row.project_slug,
                success_count=int(row.success_count or 0),
                last_used_at=row.last_used_at,
                similarity=1.0,
            )

    # Fuzzy fallback.
    best: tuple[float, VerifiedRecipe] | None = None
    for row in rows:
        sim = _jaccard_similarity(norm, row.prompt_normalized or "")
        if sim < similarity_threshold:
            continue
        if best is None or sim > best[0]:
            best = (sim, row)
    if best is None:
        return None
    sim, row = best
    return RecallHit(
        plan=list(row.plan or []),
        project_slug=row.project_slug,
        success_count=int(row.success_count or 0),
        last_used_at=row.last_used_at,
        similarity=float(sim),
    )


def record_successful_plan(
    prompt: str,
    plan: list[dict],
    *,
    project_slug: str | None = None,
) -> None:
    """Persist a successful plan as a ``VerifiedRecipe`` (or bump its score)."""
    if not prompt or not plan:
        return
    norm = normalize_prompt(prompt)
    try:
        with SessionLocal() as session:
            existing = session.scalars(
                select(VerifiedRecipe)
                .where(VerifiedRecipe.project_slug.is_(project_slug))
                .where(VerifiedRecipe.prompt_normalized == norm)
            ).first()
            if existing is not None:
                existing.success_count = int(existing.success_count or 0) + 1
                existing.last_used_at = datetime.now(UTC)
                # Keep the plan freshest -- newer plan likely better.
                existing.plan = list(plan)
                existing.prompt = prompt
                session.add(existing)
            else:
                session.add(VerifiedRecipe(
                    project_slug=project_slug,
                    prompt=prompt,
                    prompt_normalized=norm,
                    plan=list(plan),
                ))
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_successful_plan failed: %s", exc)


# --- Per-keyword outcome scoring ------------------------------------------


def record_keyword_outcome(
    *,
    project_slug: str | None,
    keyword: str,
    success: bool,
    error: str | None = None,
) -> None:
    """Increment success/failure counters for a (project, keyword) pair."""
    if not keyword:
        return
    slug = (project_slug or "").strip()
    try:
        with SessionLocal() as session:
            row = session.scalars(
                select(KeywordOutcome)
                .where(KeywordOutcome.project_slug == slug)
                .where(KeywordOutcome.keyword == keyword)
            ).first()
            if row is None:
                row = KeywordOutcome(
                    project_slug=slug,
                    keyword=keyword,
                    success_count=1 if success else 0,
                    failure_count=0 if success else 1,
                    last_error=None if success else (error or "")[:1000],
                )
                session.add(row)
            else:
                if success:
                    row.success_count = int(row.success_count or 0) + 1
                else:
                    row.failure_count = int(row.failure_count or 0) + 1
                    if error:
                        row.last_error = error[:1000]
            row.updated_at = datetime.now(UTC)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_keyword_outcome failed: %s", exc)


def project_keyword_warnings(
    project_slug: str | None,
    *,
    min_failures: int = 2,
    failure_ratio: float = 0.5,
    limit: int = 10,
) -> list[dict]:
    """Top keywords that have been unreliable in this project.

    Used to annotate the planner prompt: keywords that have failed
    significantly more than they've succeeded in this project become
    "**warning**: avoid unless necessary" hints in the catalog block.
    """
    slug = (project_slug or "").strip()
    out: list[dict] = []
    try:
        with SessionLocal() as session:
            rows = session.scalars(
                select(KeywordOutcome)
                .where(KeywordOutcome.project_slug == slug)
            ).all()
        for row in rows:
            failures = int(row.failure_count or 0)
            successes = int(row.success_count or 0)
            total = failures + successes
            if failures < min_failures or total == 0:
                continue
            ratio = failures / total
            if ratio < failure_ratio:
                continue
            out.append({
                "keyword": row.keyword,
                "failures": failures,
                "successes": successes,
                "ratio": round(ratio, 3),
                "last_error": (row.last_error or "")[:160],
            })
        out.sort(key=lambda r: (-r["ratio"], -r["failures"]))
        return out[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.debug("project_keyword_warnings failed: %s", exc)
        return []


def format_warnings_block(warnings: list[dict]) -> str:
    """Render warnings as a planner-prompt fragment."""
    if not warnings:
        return ""
    lines = [
        "## Project-specific reliability warnings",
        "",
        "These keywords have failed in this org before. Prefer alternatives "
        "when the catalog offers one; only use them when no other keyword "
        "fits.",
        "",
    ]
    for w in warnings:
        lines.append(
            f"- `{w['keyword']}` -- failed {w['failures']}/{w['failures'] + w['successes']} times"
            + (f" (last error: {w['last_error']})" if w.get("last_error") else "")
        )
    return "\n".join(lines)


def project_field_learning_warnings(
    project_slug: str | None,
    *,
    min_events: int = 2,
    limit: int = 8,
) -> list[dict]:
    slug = (project_slug or "").strip()
    if not slug:
        return []
    try:
        from ai_qa_portal.backend.services.db_models.heal import OrgFieldLearning

        with SessionLocal() as session:
            rows = session.scalars(
                select(OrgFieldLearning)
                .where(OrgFieldLearning.project_slug == slug)
            ).all()
        out: list[dict] = []
        for row in rows:
            total = int(row.total_count or 0)
            if total < min_events:
                continue
            success = int(row.success_count or 0)
            ratio = success / total if total else 0.0
            out.append(
                {
                    "sobject": row.sobject,
                    "field_api_name": row.field_api_name,
                    "error_type": row.error_type,
                    "total": total,
                    "success_ratio": round(ratio, 3),
                    "preferred_strategy": row.last_success_strategy or row.last_failed_strategy or "",
                }
            )
        out.sort(key=lambda r: (r["success_ratio"], -r["total"]))
        return out[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.debug("project_field_learning_warnings failed: %s", exc)
        return []


def format_field_learning_block(warnings: list[dict]) -> str:
    if not warnings:
        return ""
    lines = [
        "## Project field-healing learnings",
        "",
        "These field patterns were observed in this org; prefer plans that set them explicitly.",
        "",
    ]
    for w in warnings:
        lines.append(
            f"- `{w['sobject']}.{w['field_api_name']}` ({w['error_type']}) "
            f"-- success ratio {w['success_ratio']} over {w['total']} heal events"
            + (f", preferred strategy: {w['preferred_strategy']}" if w.get("preferred_strategy") else "")
        )
    return "\n".join(lines)


# --- Verification-intent hint --------------------------------------------
#
# When the user prompt asks for verification of presence/absence, conversion,
# or cleanup, the deterministic SOQL-backed keywords in
# ``Resources/Common/SoqlVerify.robot`` outperform the legacy UI list-view
# chain (``Change List View`` -> ``Search In List View`` ->
# ``Verify Table Cell Record``) every time. The legacy chain reloads the
# page, depends on per-org list-view labels, and ``Search In List View``
# explicitly ``Fail``s on zero matches -- which makes absence assertions
# impossible to express. The SoqlVerify.* keywords reuse the live Selenium
# session via SalesforceApiLibrary's ``frontdoor.jsp`` sid upgrade, so they
# work without any extra credentials.

_VERIFICATION_INTENT_REGEX = re.compile(
    r"(?i)\b(verify|verif(?:y|ied)|confirm|assert|check)\b"
    r"|\b(removed|no longer (?:visible|in)|not (?:visible|present|in the list)|absent)\b"
    r"|\b(converted|conversion|convert the )\b"
    r"|\b(clean ?up|teardown|delete (?:created|test) records)\b"
)


def verification_intent_warnings(prompt: str) -> str:
    """Return a planner-prompt fragment when the user prompt asks for
    record verification or cleanup. Empty string when not applicable.

    The fragment:
      * Names the preferred SoqlVerify.* keywords for verification +
        cleanup intents.
      * Adds an intent-scoped warning next to the UI list-view keywords
        so the LLM does not pick them for "is the record present /
        absent / converted" assertions. Those keywords stay valid for
        actual navigation; the warning is only about using them as
        verification.
    """
    text = (prompt or "").strip()
    if not text:
        return ""
    if not _VERIFICATION_INTENT_REGEX.search(text):
        return ""
    lines = [
        "## Verification + cleanup keyword preferences",
        "",
        "This prompt requests record verification, presence/absence, "
        "conversion, or cleanup. Use the SOQL-first keywords -- they are "
        "deterministic, do not reload the page, and express true "
        "presence/absence semantics:",
        "",
        "- `SoqlVerify.Verify Record Exists By SOQL    <SObject>    <WHERE>` "
        "-- prefer over `GlobalKeywords.Search In List View` + "
        "`GlobalKeywords.Verify Table Cell Record` for *exists* checks.",
        "- `SoqlVerify.Verify Record Absent By SOQL    <SObject>    <WHERE>` "
        "-- the ONLY correct way to assert a record is no longer in a list "
        "view; the legacy `Search In List View` `Fail`s on empty results.",
        "- `SoqlVerify.Verify Lead Was Converted    ${leadId}` -- asserts "
        "`IsConverted=TRUE` + populates `${convertedAccountId}`, "
        "`${convertedContactId}`, `${convertedOpportunityId}` for use in "
        "cleanup or downstream assertions.",
        "- `SoqlVerify.Verify Lead Absent From Active List    ${leadId}` -- "
        "true semantics of the Active Leads filter (`IsConverted=FALSE`).",
        "- `SoqlVerify.Cleanup Captured Records` -- auto-discovers `${leadId}`, "
        "`${accountId}`, `${contactId}`, `${opportunityId}`, `${campaignId}`, "
        "and the converted-* variants. Call from `[Teardown]`; safe when no "
        "records were captured.",
        "",
        "`SalesPO.Save And Heal` auto-captures the Id of the just-saved "
        "record into `${<sobject>Id}` (e.g. `${leadId}`) -- you do NOT "
        "need an explicit capture step after `SalesPO.Create A New X`.",
        "",
        "**warning -- avoid for verification intents:** "
        "`GlobalKeywords.Change List View`, `GlobalKeywords.Search In List "
        "View`, `GlobalKeywords.Verify Table Cell Record`. These keywords "
        "remain valid for plain *navigation* (e.g. opening a saved list "
        "view to click into a record), but they are unreliable for "
        "presence/absence assertions on Lightning -- use the SoqlVerify.* "
        "keywords above instead.",
    ]
    return "\n".join(lines)


# --- RF-MCP scenario analysis pre-pass -----------------------------------


def analyze_scenario_safe(prompt: str) -> dict | None:
    """Best-effort RF-MCP ``analyze_scenario`` call.

    Returns the structured intent dict on success, ``None`` on any
    failure (RF-MCP unreachable, tool missing, parse error). The
    planner can ALWAYS proceed without this.
    """
    if not (os.environ.get("MCP_PLANNER_USE_SCENARIO_ANALYSIS", "1").strip().lower()
            in ("1", "true", "yes", "on")):
        return None
    try:
        import mcp_bridge
        if not mcp_bridge.is_server_running():
            # Don't trigger an MCP startup just for the pre-pass; if it
            # isn't already up the planner will start it during init.
            return None
        result = mcp_bridge.analyze_scenario(prompt)
        # RF-MCP returns the structured result inside the dict; surface
        # it as-is for the planner prompt.
        if isinstance(result, dict) and result.get("available", True):
            return _trim_scenario_for_prompt(result)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("analyze_scenario_safe failed: %s", exc)
        return None


def _trim_scenario_for_prompt(result: dict) -> dict:
    """Cap the scenario analysis size so it doesn't crowd the catalog."""
    keep = {}
    for k in (
        "intent", "objects", "libraries", "suggested_keywords",
        "recommended_libraries", "actions", "summary",
    ):
        v = result.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            v = v[:12]
        elif isinstance(v, str):
            v = v[:600]
        keep[k] = v
    return keep


# --- Semantic memory recall (RF-MCP) -------------------------------------


def recall_similar_steps(prompt: str, *, top_k: int = 3) -> list[dict] | None:
    """Best-effort RF-MCP ``recall_step`` call.

    Memory is opt-in via ``ROBOTMCP_MEMORY_ENABLED=true`` (we default
    it off in mcp_bridge to avoid the cold model download). When the
    feature isn't enabled this returns ``None`` and the planner runs
    without memory hints.
    """
    if not (os.environ.get("MCP_PLANNER_USE_RECALL", "1").strip().lower()
            in ("1", "true", "yes", "on")):
        return None
    try:
        import mcp_bridge
        if not mcp_bridge.is_server_running():
            return None
        result = mcp_bridge.recall_step(prompt, top_k=top_k)
        if not isinstance(result, dict) or not result.get("available", True):
            return None
        items = result.get("results") or result.get("items") or result.get("hits")
        if not isinstance(items, list):
            return None
        # Cap each item so the planner prompt stays small.
        trimmed: list[dict] = []
        for hit in items[:top_k]:
            if not isinstance(hit, dict):
                continue
            trimmed.append({
                "scenario": (hit.get("scenario") or hit.get("query") or "")[:200],
                "steps": hit.get("steps") or hit.get("plan") or [],
                "score": hit.get("score") or hit.get("similarity"),
            })
        return trimmed or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_similar_steps failed: %s", exc)
        return None


def store_recall_safe(prompt: str, plan: list[dict]) -> None:
    """Best-effort: persist the (scenario, plan) pair into RF-MCP memory."""
    if not (os.environ.get("MCP_PLANNER_USE_RECALL", "1").strip().lower()
            in ("1", "true", "yes", "on")):
        return
    if not prompt or not plan:
        return
    try:
        import mcp_bridge
        if not mcp_bridge.is_server_running():
            return
        mcp_bridge.store_knowledge(
            "successful_plan",
            json.dumps({"prompt": prompt, "plan": plan}, ensure_ascii=False),
            scenario=prompt[:200],
            step_count=len(plan),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("store_recall_safe failed: %s", exc)


# --- Plan + Replan -------------------------------------------------------


def plan_steps(
    prompt: str,
    *,
    default_app: str = "",
    project_slug: str | None = None,
) -> tuple[list[dict], dict]:
    """Generate a step plan with all the brain integrations active.

    Returns ``(steps, meta)`` where ``meta`` carries diagnostics the
    caller can surface as SSE notes (recipe-hit, scenario-analysis-used,
    recall-hits, project-warnings).
    """
    meta: dict[str, Any] = {
        "recipe_hit": None,
        "scenario_analysis_used": False,
        "recall_hits": 0,
        "project_warnings": 0,
    }

    # 1. Try verified-recipe replay first -- 0-cost when there's a hit.
    recipe = find_verified_recipe(prompt, project_slug=project_slug)
    if recipe is not None:
        meta["recipe_hit"] = {
            "similarity": recipe.similarity,
            "success_count": recipe.success_count,
            "project_slug": recipe.project_slug,
        }
        return list(recipe.plan), meta

    # 2. Pre-plan: scenario analysis + memory recall + project warnings.
    scenario = analyze_scenario_safe(prompt)
    if scenario:
        meta["scenario_analysis_used"] = True
    recall = recall_similar_steps(prompt)
    if recall:
        meta["recall_hits"] = len(recall)
    warnings = project_keyword_warnings(project_slug)
    field_warnings = project_field_learning_warnings(project_slug)
    verify_block = verification_intent_warnings(prompt)
    if warnings or field_warnings:
        meta["project_warnings"] = len(warnings) + len(field_warnings)
    if verify_block:
        meta["verification_hint_applied"] = True

    # 3. Hand off to the LLM planner with all hints attached.
    from ai_bridge import break_prompt_into_steps

    # The warnings get appended to the user prompt via a "## Project
    # warnings" block so they're highly visible. They could go to the
    # system prompt too, but keeping them in the user prompt is
    # cheaper -- system prompts are cached aggressively by some providers.
    enriched_prompt = prompt
    if warnings:
        enriched_prompt = (
            prompt.rstrip()
            + "\n\n"
            + format_warnings_block(warnings)
        )
    if field_warnings:
        enriched_prompt = (
            enriched_prompt.rstrip()
            + "\n\n"
            + format_field_learning_block(field_warnings)
        )
    if verify_block:
        enriched_prompt = (
            enriched_prompt.rstrip()
            + "\n\n"
            + verify_block
        )

    # 3a. Optional: tool-calling planner. Falls through to the JSON
    # planner when disabled or when the active provider doesn't support
    # function calling.
    try:
        from ai_qa_portal.backend.services import planner_tools as _ptools
        if _ptools.is_enabled():
            try:
                from ai_qa_portal.backend.prompts import assembler as _assembler
                stepwise_system = _assembler.build_system_prompt("stepwise")
            except Exception:  # noqa: BLE001
                stepwise_system = (
                    "You are a Robot Framework step planner. Use the "
                    "provided tools to explore the catalog and submit a plan."
                )
            tool_steps = _ptools.plan_with_tools(
                enriched_prompt,
                system_prompt=stepwise_system,
                default_app=default_app,
                project_slug=project_slug,
            )
            if tool_steps is not None:
                meta["planner_mode"] = "function_calling"
                # Run the same validator + sequence linter the JSON planner
                # uses so a tool-using plan can't bypass our quality gates.
                from ai_qa_portal.backend.services import planner_quality as _pq
                from ai_bridge import _sanitize_step

                sanitized = [_sanitize_step(s) for s in tool_steps]
                step_issues = _pq.validate_steps(sanitized)
                seq_issues = _pq.lint_sequence(sanitized)
                critical = [iss for iss in step_issues if iss.severity == "error"]
                if not critical and not seq_issues:
                    return sanitized, meta
                logger.warning(
                    "planner_tools: tool-call plan failed validation "
                    "(%d issue(s), %d seq issue(s)); falling back to "
                    "JSON planner",
                    len(critical), len(seq_issues),
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("planner_tools path failed (%s); using JSON planner", exc)

    meta["planner_mode"] = "json"
    steps = break_prompt_into_steps(
        enriched_prompt,
        catalog_json=None,
        scenario_analysis=scenario,
        default_app=default_app,
        project_slug=project_slug,
        recall_hints=recall,
    )
    return steps, meta


# --- Per-step recovery ---------------------------------------------------


_STEP_RECOVERY_SYSTEM = (
    "You are a Robot Framework step healer. A planned step just failed "
    "in a live Salesforce browser session. Your job is to suggest a "
    "SINGLE recovery action.\n\n"
    "Output rules:\n"
    "- Return ONLY a JSON object with one of these shapes:\n"
    "    {\"action\": \"replace\", \"keyword\": \"<DifferentKeyword>\", \"args\": [...]}\n"
    "    {\"action\": \"skip\"}\n"
    "    {\"action\": \"abort\", \"reason\": \"<why>\"}\n"
    "- No markdown, no prose, no explanation outside the JSON.\n"
    "- The replacement keyword MUST be in the catalog and use the right "
    "  argument shape (named args use name=value; do NOT wrap names in ${}).\n"
    "- HARD RULE: NEVER return the SAME keyword that just failed as your "
    "  'replace' choice. If you can't find a different keyword that fits, "
    "  return {\"action\": \"skip\"} so the run continues with the next "
    "  planned step. Returning the same keyword is a guaranteed loop and "
    "  the runtime will reject it.\n"
    "- Prefer 'skip' over 'replace' when the failed step was a verification "
    "  ('Verify ...', 'Should Be Equal', etc.) -- the run can usually "
    "  continue and the next step's behaviour will reveal whether the "
    "  underlying record exists.\n"
    "- 'abort' is the right answer only when continuing would corrupt "
    "  state (e.g. login failed, browser dead).\n"
)


def replan_failed_step(
    *,
    failed_keyword: str,
    failed_args: list[str],
    error_message: str,
    page_state: dict | None,
    full_plan: list[dict],
    failed_index: int,
    project_slug: str | None = None,
) -> dict | None:
    """Ask the LLM for a single replacement step.

    Returns one of:
      - ``{"action": "replace", "keyword": ..., "args": [...]}``
      - ``{"action": "skip"}``
      - ``{"action": "abort", "reason": ...}``
      - ``None`` on any failure (caller should fall back to skip).
    """
    if not (os.environ.get("MCP_PLANNER_REPLAN_ON_FAILURE", "1").strip().lower()
            in ("1", "true", "yes", "on")):
        return None

    try:
        from ai_bridge import call_llm
        from ai_qa_portal.backend.services import planner_quality
    except Exception as exc:  # noqa: BLE001
        logger.debug("replan_failed_step: dependencies unavailable: %s", exc)
        return None

    # Compact catalog projection -- recovery doesn't need full context.
    try:
        catalog_lines = planner_quality.signatures_for_prompt(max_doc_chars=80).splitlines()
        # Cap to the first ~150 keywords so we don't blow the budget when
        # the catalog grows.
        catalog = "\n".join(catalog_lines[:150])
    except Exception:  # noqa: BLE001
        catalog = ""

    page_snippet = ""
    if isinstance(page_state, dict):
        # Trim the page state aggressively -- LLM only needs hints.
        keep = {
            "url": page_state.get("url"),
            "title": page_state.get("title"),
            "current_app": page_state.get("current_app"),
            "modal_open": page_state.get("modal_open"),
            "visible_elements": (page_state.get("visible_elements") or [])[:15],
            "variables": list((page_state.get("variables") or {}).keys())[:30],
        }
        page_snippet = json.dumps(
            {k: v for k, v in keep.items() if v not in (None, "", [], {})},
            indent=2, ensure_ascii=False,
        )

    upcoming = full_plan[failed_index : failed_index + 5]
    user_content = (
        f"Failed step (index {failed_index + 1}):\n"
        f"  keyword: {failed_keyword}\n"
        f"  args: {failed_args}\n\n"
        f"Error message:\n  {error_message}\n\n"
        f"Upcoming planned steps (next 5):\n"
        f"{json.dumps(upcoming, indent=2, ensure_ascii=False)}\n\n"
        f"Current page state:\n{page_snippet or '(unavailable)'}\n\n"
        f"## Catalog (top entries)\n{catalog}\n\n"
        "Pick the most useful single recovery action."
    )

    try:
        raw = call_llm(_STEP_RECOVERY_SYSTEM, user_content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("replan_failed_step: LLM call failed: %s", exc)
        return None

    raw = (raw or "").strip()
    # Strip code fences if the model insisted.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract the first JSON object.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            decision = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    if not isinstance(decision, dict):
        return None
    action = (decision.get("action") or "").strip().lower()
    if action not in ("replace", "skip", "abort"):
        return None

    if action == "replace":
        kw = (decision.get("keyword") or "").strip()
        args = decision.get("args") or []
        if not kw:
            return None
        # Validate the replacement against the catalog before returning.
        # If we have to substitute a closest-match for an unrecognised
        # name AND the substitute happens to be the same keyword that
        # just failed, treat the decision as a skip rather than looping.
        from ai_qa_portal.backend.services import planner_quality as _pq
        canonical = _pq.resolve_keyword(kw)
        if canonical is None:
            close = _pq.closest_keywords(kw)
            if not close:
                return {"action": "skip"}
            kw = close[0]
            canonical = _pq.resolve_keyword(kw)
        # If the resolved canonical matches the failed keyword exactly,
        # the LLM is suggesting a tautological retry. Reject and skip.
        if canonical and canonical.strip().lower() == failed_keyword.strip().lower():
            logger.info(
                "replan_failed_step: LLM suggested re-running '%s'; "
                "rewriting decision as skip",
                failed_keyword,
            )
            return {"action": "skip"}
        return {"action": "replace", "keyword": kw, "args": list(args)}
    return decision


# --- Page state probe -----------------------------------------------------


def get_page_state_safe(session_id: str) -> dict | None:
    """Best-effort RF-MCP ``get_session_state`` call (minimal detail)."""
    if not session_id:
        return None
    try:
        import mcp_bridge
        return mcp_bridge.get_page_state(session_id, detail_level="minimal")
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_page_state_safe failed: %s", exc)
        return None
