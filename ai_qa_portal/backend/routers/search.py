"""Cross-entity search endpoint.

The Cmd+K palette previously fanned out 3 separate list endpoints
client-side and filtered in JavaScript, which:
  * excluded test cases entirely (out of scope)
  * scales poorly past ~50 projects
  * lost the chance to rank results by relevance

This module replaces that with one server-side ``GET /api/search?q=``
that searches projects / sprints / stories / test cases / runs in
parallel and returns typed hits ranked by simple match heuristics.

Visibility: every result is filtered by the same RBAC the per-entity
endpoints enforce -- admins see all, users see what their
memberships + per-entity ownership permits.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import (
    AuditLog,  # noqa: F401 -- kept for future audit hooks
    RunRecord,
    User,
    get_db,
    list_memberships_for_user,
)

import project_manager  # repo-root module, not under ai_qa_portal package
from ..models.sprint import Sprint
from ..models.test_case import TestCase
from ..models.user_story import UserStory, UserStoryStatus
from ..project_registry import ensure_project_uuid
from ..storage.json_file_backend import JsonFileBackend

logger = logging.getLogger("ai_qa_portal.search")

_store = JsonFileBackend(settings.data_dir)

router = APIRouter(
    prefix="/api/search",
    tags=["search"],
    dependencies=[Depends(get_current_user)],
)


HitKind = Literal["project", "sprint", "story", "test_case", "run"]


class _Hit(BaseModel):
    kind: HitKind
    id: str
    title: str
    subtitle: str | None = None
    url: str
    project_slug: str | None = None
    # Higher = better match. Computed client-side too but we expose
    # the server score so the UI can sort consistently regardless of
    # tie-break order between hit kinds.
    score: float = 1.0


class _SearchResponse(BaseModel):
    q: str
    hits: list[_Hit]
    truncated: bool = False


def _accessible_slugs(db: Session, user: User) -> set[str]:
    """Project slugs the user can see.

    Admin = every project on disk. Everyone else = projects they hold
    a membership in PLUS projects they own on the filesystem (the
    legacy ownership model). We deliberately keep this permissive so
    a user who just got invited can search immediately, before their
    first list-projects call has refreshed.
    """
    slugs: set[str] = set()
    if user.is_admin:
        try:
            slugs.update(project_manager.list_projects())
        except Exception:
            pass
        return slugs
    # Membership-derived slugs.
    try:
        for m in list_memberships_for_user(db, str(user.id)):
            if m.project_slug:
                slugs.add(m.project_slug)
    except Exception:
        pass
    # Filesystem-owner fallback (legacy ownership model -- owner_id on
    # the on-disk project metadata).
    try:
        for slug in project_manager.list_projects():
            try:
                owner = project_manager.get_project_owner(slug)
            except Exception:
                continue
            if owner and owner == str(user.id):
                slugs.add(slug)
    except Exception:
        pass
    return slugs


def _score(needle: str, haystack: str) -> float:
    """Tiny relevance score: prefix match > substring > weak match.

    Both inputs are pre-lowercased by the caller. Returns 0.0 when no
    match -- the caller drops zero-score rows so we don't pad the
    payload with noise.
    """
    if not haystack:
        return 0.0
    if haystack == needle:
        return 100.0
    if haystack.startswith(needle):
        return 50.0
    if f" {needle}" in haystack or f"-{needle}" in haystack or f"_{needle}" in haystack:
        return 20.0
    if needle in haystack:
        return 10.0
    return 0.0


def _search_projects(needle: str, slugs: set[str], limit: int) -> list[_Hit]:
    hits: list[_Hit] = []
    for slug in slugs:
        s = _score(needle, slug.lower())
        if s == 0:
            continue
        hits.append(_Hit(
            kind="project",
            id=slug,
            title=slug,
            url=f"/p/{slug}",
            project_slug=slug,
            score=s,
        ))
    return sorted(hits, key=lambda h: -h.score)[:limit]


def _search_sprints(needle: str, slugs: set[str], limit: int) -> list[_Hit]:
    hits: list[_Hit] = []
    seen = 0
    for slug in slugs:
        try:
            pid = ensure_project_uuid(slug)
        except Exception:
            continue
        if not pid:
            continue
        idx = _store.read(f"sprints_by_project:{pid}")
        for sid in idx.get("ids", []):
            seen += 1
            row = _store.read(f"sprint:{sid}")
            if not row:
                continue
            try:
                sprint = Sprint.model_validate(row)
            except Exception:
                continue
            s = _score(needle, (sprint.name or "").lower())
            if s == 0:
                continue
            hits.append(_Hit(
                kind="sprint",
                id=str(sprint.id),
                title=sprint.name,
                subtitle=f"{slug} · {sprint.state.value if hasattr(sprint.state, 'value') else sprint.state}",
                url=f"/sprints/{sprint.id}?project={slug}",
                project_slug=slug,
                score=s,
            ))
            if seen > 2000:
                break
    return sorted(hits, key=lambda h: -h.score)[:limit]


def _search_stories(needle: str, slugs: set[str], limit: int, user: User) -> list[_Hit]:
    hits: list[_Hit] = []
    seen = 0
    for slug in slugs:
        try:
            pid = ensure_project_uuid(slug)
        except Exception:
            continue
        if not pid:
            continue
        idx = _store.read(f"user_stories_by_project:{pid}")
        for sid in idx.get("ids", []):
            seen += 1
            row = _store.read(f"user_story:{sid}")
            if not row:
                continue
            try:
                story = UserStory.model_validate(row)
            except Exception:
                continue
            # Per-entity owner gate (legacy story ownership model;
            # admin always passes).
            if not user.is_admin and story.owner_user_id and story.owner_user_id != user.id:
                continue
            if story.status != UserStoryStatus.active:
                continue
            s = _score(needle, (story.title or "").lower())
            if s == 0:
                continue
            hits.append(_Hit(
                kind="story",
                id=str(story.id),
                title=story.title,
                subtitle=f"{slug} · v{story.version}",
                url=f"/user-stories/{story.id}?project={slug}",
                project_slug=slug,
                score=s,
            ))
            if seen > 2000:
                break
    return sorted(hits, key=lambda h: -h.score)[:limit]


def _search_test_cases(needle: str, slugs: set[str], limit: int) -> list[_Hit]:
    """First-class test case search -- the IA audit flagged that the
    palette excluded these entirely."""
    hits: list[_Hit] = []
    seen = 0
    for slug in slugs:
        try:
            pid = ensure_project_uuid(slug)
        except Exception:
            continue
        if not pid:
            continue
        idx = _store.read(f"test_case_ids_project:{pid}")
        for tid in idx.get("ids", []):
            seen += 1
            row = _store.read(f"test_case:{tid}")
            if not row:
                continue
            try:
                tc = TestCase.model_validate(row)
            except Exception:
                continue
            # Skip archived TCs from default search.
            if str(tc.status.value) == "rejected":
                continue
            s = _score(needle, (tc.title or "").lower())
            if s == 0:
                continue
            hits.append(_Hit(
                kind="test_case",
                id=str(tc.id),
                title=tc.title,
                subtitle=f"{slug} · {tc.status.value}",
                url=f"/test-cases/{tc.id}?project={slug}",
                project_slug=slug,
                score=s,
            ))
            if seen > 3000:
                break
    return sorted(hits, key=lambda h: -h.score)[:limit]


def _search_runs(needle: str, slugs: set[str], limit: int, db: Session) -> list[_Hit]:
    """Search SQL `runs` rows by run folder name + project slug
    substring. Cheaper than the JSON-store walks above because runs
    are SQL-native."""
    hits: list[_Hit] = []
    q = (
        db.query(RunRecord)
        .filter(RunRecord.project_slug.in_(slugs) if slugs else True)
        .order_by(RunRecord.started_at.desc())
        .limit(500)
    )
    for r in q.all():
        haystack = (r.output_dir or "").lower()
        s = _score(needle, haystack.split("/")[-1])
        if s == 0:
            continue
        folder = (r.output_dir or "").rstrip("/").split("/")[-1] or r.id
        hits.append(_Hit(
            kind="run",
            id=r.id,
            title=folder,
            subtitle=f"{r.project_slug or '?'} · {r.status or 'unknown'}",
            url=f"/runs/{folder}",
            project_slug=r.project_slug,
            score=s,
        ))
    return sorted(hits, key=lambda h: -h.score)[:limit]


@router.get("", response_model=_SearchResponse)
def search(
    q: str = Query("", description="Query string -- substring matched per entity."),
    limit: int = Query(8, ge=1, le=25, description="Max hits per entity kind."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cross-entity typed search.

    Returns up to ``limit`` hits per kind (project / sprint / story /
    test_case / run). The Cmd+K palette uses this in place of its
    legacy client-side fan-out so test cases are now searchable.

    Empty / very short queries (<2 chars) return no rows -- the palette
    shows its always-on actions instead.
    """
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return _SearchResponse(q=q, hits=[], truncated=False)

    slugs = _accessible_slugs(db, current_user)
    if not slugs:
        return _SearchResponse(q=q, hits=[], truncated=False)

    all_hits: list[_Hit] = []
    all_hits.extend(_search_projects(needle, slugs, limit))
    all_hits.extend(_search_sprints(needle, slugs, limit))
    all_hits.extend(_search_stories(needle, slugs, limit, current_user))
    all_hits.extend(_search_test_cases(needle, slugs, limit))
    try:
        all_hits.extend(_search_runs(needle, slugs, limit, db))
    except Exception as exc:
        logger.debug("run search failed: %s", exc)

    # Soft cap on total payload so a wildly-popular term doesn't blow
    # up the palette render.
    truncated = len(all_hits) > limit * 5
    return _SearchResponse(q=q, hits=all_hits[: limit * 5], truncated=truncated)
