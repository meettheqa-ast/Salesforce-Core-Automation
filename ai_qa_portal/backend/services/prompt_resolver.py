"""Prompt resolver -- "what prompt do I use right now".

The resolver answers one question: for ``(category, user_id,
project_id, org_id)``, return the active ``PromptVersion`` body to use
in the next LLM call.

Resolution order (first hit wins; sparse rows mean a missing layer is
just "no row found"):

  1. ``scope='user'``    + ``scope_id=user_id``
  2. ``scope='project'`` + ``scope_id=project_id``
  3. ``scope='org'``     + ``scope_id=None`` (single-org deployments)
  4. System seed for that category (default, no override row needed)

The system seed is identified by ``(is_system=True, category=...)`` and
the seeder guarantees one row per category. When multiple system seeds
exist for one category (e.g. the two Zephyr variants under
``test_case_drafter``), the resolver picks the one marked
``is_active=True`` and with the most recent ``updated_at`` -- operators
can flip the org default by activating a different system template via
the org-scope override.

Caching: per-process ``LRU`` keyed on
``(cache_epoch, category, user_id, project_id, org_id)``. Every
mutation in ``prompt_registry`` bumps the cache_epoch so the next
resolve sees a different key and falls through to a fresh read.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.db_models.prompts import (
    PromptOverride,
    PromptTemplate,
    PromptVersion,
)
from ai_qa_portal.backend.services.prompt_registry import (
    get_cache_epoch,
    get_latest_version,
    get_version,
)

logger = logging.getLogger("ai_qa_portal.prompt_resolver")

Scope = Literal["user", "project", "org", "system"]


@dataclass(frozen=True)
class ResolvedPrompt:
    """What the resolver hands back to the generation layer.

    ``source_scope`` lets the audit log explain WHY this prompt was
    picked ("user override" vs "system default") and the UI tag rows
    in the settings list page with the right badge.
    """

    template_id: str
    template_name: str
    category: str
    version_id: str
    version_number: int
    body: str
    output_format: str
    model_hint: str | None
    source_scope: Scope


# ---------- internal lookup helpers ------------------------------


def _override_for(
    db: Session, scope: str, scope_id: str | None, category: str,
) -> PromptOverride | None:
    q = db.query(PromptOverride).filter(
        PromptOverride.scope == scope,
        PromptOverride.category == category,
    )
    if scope_id is None:
        q = q.filter(PromptOverride.scope_id.is_(None))
    else:
        q = q.filter(PromptOverride.scope_id == scope_id)
    return q.one_or_none()


def _system_default_for(db: Session, category: str) -> PromptTemplate | None:
    """Pick the system template that ships as the out-of-the-box
    default for a category.

    Tie-break: oldest ``created_at`` wins. The seeder inserts manifest
    entries in declared order, so the FIRST seed listed in
    ``SEED_MANIFEST`` for each category is the OOTB default. Operators
    who want a different default (e.g. flip drafter from "Default JSON"
    to "Enterprise Zephyr") set an ``org`` scope override -- the
    resolver checks overrides before falling here, so the system
    default is purely a "no one has chosen anything" fallback.
    """
    q = (
        db.query(PromptTemplate)
        .filter(PromptTemplate.is_system.is_(True))
        .filter(PromptTemplate.category == category)
        .filter(PromptTemplate.deleted_at.is_(None))
        .filter(PromptTemplate.is_active.is_(True))
        .order_by(PromptTemplate.created_at.asc(), PromptTemplate.id.asc())
    )
    return q.first()


def _resolved_from_override(
    db: Session, override: PromptOverride, source_scope: Scope,
) -> ResolvedPrompt | None:
    template = db.get(PromptTemplate, override.template_id)
    if template is None or template.deleted_at is not None:
        # Stale pointer -- ignore, fall through to next layer.
        logger.warning(
            "prompt override %s points at missing/deleted template %s",
            override.id, override.template_id,
        )
        return None
    version = get_version(db, override.active_version_id)
    if version is None:
        # The router protects active_version_id with ondelete=RESTRICT
        # but cover the race anyway.
        logger.warning(
            "prompt override %s points at missing version %s",
            override.id, override.active_version_id,
        )
        return None
    return ResolvedPrompt(
        template_id=template.id,
        template_name=template.name,
        category=template.category,
        version_id=version.id,
        version_number=int(version.version_number),
        body=version.body,
        output_format=template.output_format,
        model_hint=template.model_hint,
        source_scope=source_scope,
    )


def _resolved_from_system_default(
    db: Session, category: str,
) -> ResolvedPrompt | None:
    template = _system_default_for(db, category)
    if template is None:
        return None
    version = get_latest_version(db, template.id)
    if version is None:
        return None
    return ResolvedPrompt(
        template_id=template.id,
        template_name=template.name,
        category=template.category,
        version_id=version.id,
        version_number=int(version.version_number),
        body=version.body,
        output_format=template.output_format,
        model_hint=template.model_hint,
        source_scope="system",
    )


# ---------- process-local cache ----------------------------------


# Bounded LRU keyed on (cache_epoch, category, user_id, project_id,
# org_id). Stale entries (entries with an older epoch) are pruned
# lazily inside `resolve()` -- we don't have to actively expire them
# because the lookup key includes the epoch, so they simply never
# match a future lookup. Pruning keeps memory flat under churn.
#
# Capacity is generous -- ~10 categories * ~100 users + projects =
# 1000 entries -- but each entry is small (one ResolvedPrompt, ~1 KB
# bodies on average, larger for the Zephyr seed). At capacity we drop
# the oldest insertion order, which on most workloads coincides with
# entries for the previous cache_epoch.
_CACHE_CAPACITY = 1024
_RESOLVE_CACHE: dict[tuple, ResolvedPrompt] = {}
_RESOLVE_LOCK = threading.Lock()


def _cache_get(key: tuple) -> ResolvedPrompt | None:
    with _RESOLVE_LOCK:
        return _RESOLVE_CACHE.get(key)


def _cache_put(key: tuple, value: ResolvedPrompt) -> None:
    with _RESOLVE_LOCK:
        if len(_RESOLVE_CACHE) >= _CACHE_CAPACITY:
            # Drop the oldest insertion -- dicts preserve insertion
            # order in Python 3.7+, so popitem(last=False) equivalent.
            try:
                first_key = next(iter(_RESOLVE_CACHE))
                _RESOLVE_CACHE.pop(first_key, None)
            except StopIteration:
                pass
        _RESOLVE_CACHE[key] = value


def clear_cache() -> None:
    """Drop the entire cache. Useful for tests; production rarely
    needs it because the epoch in the key invalidates entries
    naturally on mutation."""
    with _RESOLVE_LOCK:
        _RESOLVE_CACHE.clear()


# ---------- public API -------------------------------------------


def resolve(
    db: Session,
    *,
    category: str,
    user_id: str | None = None,
    project_id: str | None = None,
    org_id: str | None = None,
) -> ResolvedPrompt | None:
    """Return the active ``ResolvedPrompt`` for the given scope, or
    ``None`` when no system seed exists for the category (the caller
    should fall back to whatever inline default it has -- e.g. the
    pre-registry hard-coded string)."""
    epoch = get_cache_epoch(db)
    key = (epoch, category, user_id, project_id, org_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Cache miss -> do the actual SQL walk.
    candidates: list[tuple[str, str | None, Scope]] = []
    if user_id:
        candidates.append(("user", user_id, "user"))
    if project_id:
        candidates.append(("project", project_id, "project"))
    candidates.append(("org", org_id, "org"))

    resolved: ResolvedPrompt | None = None
    for scope, scope_id, source in candidates:
        override = _override_for(db, scope, scope_id, category)
        if override is None:
            continue
        resolved = _resolved_from_override(db, override, source)
        if resolved is not None:
            break

    if resolved is None:
        resolved = _resolved_from_system_default(db, category)

    if resolved is None:
        logger.warning("no system seed for category %r", category)
        return None

    _cache_put(key, resolved)
    return resolved
