"""Fuzzy-match story-named navigation targets against real org metadata.

This is Tier 2 of the navigation resilience strategy: instead of letting
the LLM invent a tab/object/quick-action name from free-text story wording
and finding out it's wrong only when the generated test fails, resolve the
name against the org's actual Tabs / AppMenuItems / QuickActions / Flows /
Reports *before* generation (and again, targeted at the failing keyword's
argument, before healing) so the LLM is told the correct name and how to
reach it instead of guessing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .models import NavCandidate, ResolvedNavTarget, TargetType

_TTL_SECONDS = 600
_MATCH_THRESHOLD = 0.72


@dataclass
class _CacheEntry:
    value: ResolvedNavTarget
    expires_at: float


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


class NavTargetResolver:
    """Wraps ``OrgMetadataService``'s nav-metadata queries with fuzzy
    matching and its own TTL cache (same dataclass/lock shape as
    ``org_metadata.OrgMetadataService``)."""

    def __init__(self, org_metadata: Any) -> None:
        self._org_metadata = org_metadata
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}

    def resolve(
        self,
        requested_name: str,
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> ResolvedNavTarget:
        name = (requested_name or "").strip()
        if not name:
            return ResolvedNavTarget(
                requested_name=requested_name,
                name=requested_name,
                type=TargetType.UNKNOWN,
                how_to_reach="(empty target name)",
                confidence=0.0,
            )

        cache_key = ((org_key or "").strip().lower(), name.lower())
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and hit.expires_at > now:
                return hit.value

        pool = self._candidate_pool(org_key=org_key)
        best: NavCandidate | None = None
        near: list[NavCandidate] = []
        for label, ttype in pool:
            score = _similarity(name, label)
            if score < 0.4:
                continue
            cand = NavCandidate(name=label, type=ttype, similarity=round(score, 3))
            near.append(cand)
            if best is None or score > best.similarity:
                best = cand
        near.sort(key=lambda c: c.similarity, reverse=True)
        near = near[:5]

        if best is not None and best.similarity >= _MATCH_THRESHOLD:
            resolved = ResolvedNavTarget(
                requested_name=name,
                name=best.name,
                type=best.type,
                how_to_reach=_how_to_reach(best.type, best.name),
                confidence=best.similarity,
                candidates=[c for c in near if c.name != best.name],
            )
        else:
            resolved = ResolvedNavTarget(
                requested_name=name,
                name=name,
                type=TargetType.UNKNOWN,
                how_to_reach="No confident match found in org metadata (tabs, app items, quick actions, flows, reports).",
                confidence=(best.similarity if best else 0.0),
                candidates=near,
            )

        with self._lock:
            self._cache[cache_key] = _CacheEntry(value=resolved, expires_at=now + _TTL_SECONDS)
        return resolved

    def resolve_many(
        self,
        requested_names: list[str],
        *,
        org_key: str = "",
        sandbox_url: str = "",
        username: str = "",
        password: str = "",
        security_token: str = "",
    ) -> list[ResolvedNavTarget]:
        seen: set[str] = set()
        out: list[ResolvedNavTarget] = []
        for raw in requested_names:
            name = (raw or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(
                self.resolve(
                    name,
                    org_key=org_key,
                    sandbox_url=sandbox_url,
                    username=username,
                    password=password,
                    security_token=security_token,
                )
            )
        return out

    def _candidate_pool(self, *, org_key: str) -> list[tuple[str, TargetType]]:
        pool: list[tuple[str, TargetType]] = []
        for row in self._org_metadata.list_tabs(org_key=org_key):
            label = row.get("label")
            if label:
                pool.append((label, TargetType.TAB))
        for row in self._org_metadata.list_app_menu_items(org_key=org_key):
            label = row.get("label")
            if label:
                pool.append((label, TargetType.TAB if row.get("type") == "Tab" else TargetType.OBJECT))
        for row in self._org_metadata.list_quick_actions(org_key=org_key):
            label = row.get("label")
            if label:
                pool.append((label, TargetType.QUICK_ACTION))
        for row in self._org_metadata.list_flows(org_key=org_key):
            label = row.get("label")
            if label:
                pool.append((label, TargetType.FLOW))
        for row in self._org_metadata.list_reports(org_key=org_key):
            label = row.get("label")
            if label:
                pool.append((label, TargetType.REPORT))
        return pool


def _how_to_reach(ttype: TargetType, name: str) -> str:
    if ttype == TargetType.TAB:
        return f'"{name}" is a tab. Use `Select App Tab    {name}` (it already falls back through the nav bar, overflow menu, App Launcher, Setup Quick Find, and global search).'
    if ttype == TargetType.OBJECT:
        return f'"{name}" is an App Launcher item (not necessarily a pinned tab). Use `Open Item    {name}`.'
    if ttype == TargetType.QUICK_ACTION:
        return f'"{name}" is a Quick Action, not a tab/object -- open the relevant record first, then invoke the quick action from its page/global actions. Do not use `Select App Tab` for this.'
    if ttype == TargetType.FLOW:
        return f'"{name}" is a Flow -- it is launched from a button/quick action/Setup, not a standalone tab. Do not use `Select App Tab` for this.'
    if ttype == TargetType.REPORT:
        return f'"{name}" is a Report -- reach it via the Reports tab/App Launcher and then open it by name, not via `Select App Tab` directly.'
    return f'"{name}" could not be matched to a tab, app item, quick action, flow, or report in this org.'


def format_resolved_targets_block(resolved_targets: list[ResolvedNavTarget]) -> str:
    """Render resolved targets as a prompt block, mirroring the shape of
    ``assembler.render_persona_context`` / the RAG context block. Returns
    ``""`` when there's nothing to say so callers can unconditionally pass
    it into ``build_user_prompt_with_catalog``."""
    targets = [t for t in (resolved_targets or []) if t]
    if not targets:
        return ""

    lines = [
        "## Resolved Navigation Targets (ground truth from org metadata -- use these, do not invent names)",
        "",
    ]
    for t in targets:
        if t.type == TargetType.UNKNOWN:
            near = ", ".join(f'"{c.name}" ({c.type.value}, {c.similarity:.2f})' for c in t.candidates[:3])
            lines.append(f'- "{t.requested_name}" -> {t.how_to_reach}' + (f" Nearest candidates: {near}." if near else ""))
        else:
            renamed = f' (org calls it "{t.name}")' if t.name.lower() != t.requested_name.lower() else ""
            lines.append(f'- "{t.requested_name}"{renamed} -> {t.how_to_reach}')
    return "\n".join(lines) + "\n"
