"""Vector + lexical retrieval over the ``embeddings`` table.

Public entrypoint: :func:`retrieve`. Returns a list of :class:`Passage`
objects ranked by relevance to ``query``, scoped to one portal project.
Callers (the prompt assembler, generate router, builders) pass these
through ``format_passages_block`` for inclusion in the system / user
prompt.

Ranking strategy:

1. Embed ``query`` via the configured embedding provider.
2. Run a pgvector cosine-distance KNN over rows matching
   ``project_slug`` + the requested ``source_kinds``.
3. Optionally boost rows that match the active ``sprint_id`` /
   ``story_id`` (the LLM cares more about "this story's recent comments"
   than a random other comment on the same project).
4. Truncate to ``limit`` and decorate with provenance strings.

Lexical fallback: on SQLite (dev), pgvector doesn't exist, so we fall
back to a substring match over ``embeddings.text`` -- enough to keep
unit tests honest without standing up Postgres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.db_models.embeddings import Embedding, EmbeddingSourceKind
from ai_qa_portal.backend.services.embedding_provider import (
    EmbeddingError,
    get_provider,
)

logger = logging.getLogger("ai_qa_portal.rag_retrieval")


_DEFAULT_SOURCE_KINDS: tuple[str, ...] = (
    EmbeddingSourceKind.jira_issue.value,
    EmbeddingSourceKind.jira_comment.value,
    EmbeddingSourceKind.user_story.value,
    EmbeddingSourceKind.test_case.value,
    EmbeddingSourceKind.context_chunk.value,
    EmbeddingSourceKind.test_data_row.value,
)


@dataclass(slots=True)
class Passage:
    """A retrieved chunk ready for prompt injection."""

    source_kind: str
    source_id: str
    text: str
    score: float
    sprint_id: str | None = None
    story_id: str | None = None
    case_id: str | None = None
    chunk_index: int = 0

    def provenance_label(self) -> str:
        """Short tag the LLM can cite back from."""
        kind_human = {
            "jira_issue": "Jira issue",
            "jira_comment": "Jira comment",
            "user_story": "story",
            "test_case": "test case",
            "context_chunk": "context doc",
            "test_data_row": "test data row",
        }.get(self.source_kind, self.source_kind)
        return f"{kind_human}:{self.source_id[:12]}"


def retrieve(
    db: Session,
    *,
    project_slug: str,
    query: str,
    source_kinds: Sequence[str] | None = None,
    sprint_id: str | None = None,
    story_id: str | None = None,
    limit: int = 8,
) -> list[Passage]:
    """Top-K passages relevant to ``query`` from ``project_slug``.

    Empty inputs return ``[]`` cleanly (no provider calls, no DB hits).
    On any embedding-provider failure we fall back to the lexical path
    so prompts stay grounded in *something* rather than nothing.
    """
    query = (query or "").strip()
    if not query or limit <= 0:
        return []
    kinds = list(source_kinds or _DEFAULT_SOURCE_KINDS)

    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    if not is_postgres:
        return _retrieve_lexical(db, project_slug=project_slug, query=query, kinds=kinds, limit=limit)

    try:
        provider = get_provider()
        vec = provider.embed_texts([query])[0]
    except EmbeddingError as exc:
        logger.warning("Embedding query failed, falling back to lexical: %s", exc)
        return _retrieve_lexical(db, project_slug=project_slug, query=query, kinds=kinds, limit=limit)

    # pgvector cosine_distance is exposed by the SQLAlchemy column as a
    # ``cosine_distance`` operator method via the pgvector wheel.
    distance = Embedding.embedding.cosine_distance(vec)  # type: ignore[attr-defined]
    q = (
        db.query(Embedding, distance.label("distance"))
        .filter(Embedding.project_slug == project_slug)
        .filter(Embedding.source_kind.in_(kinds))
    )

    # Boost active scope by pre-filtering for it and running TWO queries
    # we union client-side; cheaper than implementing a re-rank SQL.
    boosted: list[tuple[Embedding, float]] = []
    if sprint_id or story_id:
        bq = q
        clauses = []
        if sprint_id:
            clauses.append(Embedding.sprint_id == sprint_id)
        if story_id:
            clauses.append(Embedding.story_id == story_id)
        bq = bq.filter(or_(*clauses)).order_by("distance").limit(limit)
        boosted = [(e, float(d)) for e, d in bq.all()]

    general = (
        q.order_by("distance").limit(limit * 2).all()
    )
    seen: set[str] = set()
    merged: list[tuple[Embedding, float]] = []
    for e, d in boosted:
        if e.id not in seen:
            seen.add(e.id)
            merged.append((e, float(d)))
        if len(merged) >= limit:
            break
    for e, d in general:
        if len(merged) >= limit:
            break
        if e.id not in seen:
            seen.add(e.id)
            merged.append((e, float(d)))

    return [_to_passage(e, score=1.0 - float(d)) for e, d in merged]


def _retrieve_lexical(
    db: Session,
    *,
    project_slug: str,
    query: str,
    kinds: list[str],
    limit: int,
) -> list[Passage]:
    """Cheap fallback when pgvector is unavailable. We split the query
    into whitespace tokens and prefer rows whose ``text`` contains the
    most tokens; ties broken by recency."""
    tokens = [t for t in query.lower().split() if len(t) >= 3]
    if not tokens:
        return []
    rows = (
        db.query(Embedding)
        .filter(Embedding.project_slug == project_slug)
        .filter(Embedding.source_kind.in_(kinds))
        .order_by(Embedding.created_at.desc())
        .limit(500)
        .all()
    )
    scored: list[tuple[Embedding, float]] = []
    for row in rows:
        haystack = row.text.lower()
        hits = sum(1 for t in tokens if t in haystack)
        if hits == 0:
            continue
        scored.append((row, hits / len(tokens)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [_to_passage(e, score=s) for e, s in scored[:limit]]


def _to_passage(e: Embedding, *, score: float) -> Passage:
    return Passage(
        source_kind=e.source_kind,
        source_id=e.source_id,
        text=e.text,
        score=score,
        sprint_id=e.sprint_id,
        story_id=e.story_id,
        case_id=e.case_id,
        chunk_index=e.chunk_index,
    )


def format_passages_block(passages: Sequence[Passage], *, max_chars: int = 6000) -> str:
    """Render passages into a markdown block ready for prompt injection.

    Empty input -> empty string so callers can unconditionally
    concatenate. We truncate each passage to a fair share of the
    ``max_chars`` budget so a single oversized chunk can't crowd out the
    rest.
    """
    if not passages:
        return ""
    per = max(400, max_chars // max(len(passages), 1))
    lines = ["## Project context\n"]
    for p in passages:
        text = p.text.strip()
        if len(text) > per:
            text = text[: per - 3].rstrip() + "..."
        lines.append(f"### {p.provenance_label()} (score {p.score:.2f})\n\n{text}\n")
    return "\n".join(lines) + "\n"
