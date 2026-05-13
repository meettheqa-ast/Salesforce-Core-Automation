"""Embed + upsert text into the ``embeddings`` table.

Public entrypoints:

* :func:`enqueue_context_file` -- called by the context-file uploader for
  PDF/DOCX/MD/TXT chunks.
* :func:`index_jira_issue` / :func:`index_jira_comment` -- called by the
  Jira sync orchestrator after upsert.
* :func:`index_user_story` / :func:`index_test_case` -- called by the
  story/test-case routers when content changes.
* :func:`index_test_data_row` -- called by the test-data router.
* :func:`reindex_project` -- nuclear option that rebuilds every embedding
  scoped to one project. Used by the "Re-index" admin button.

All entries:

1. Use :mod:`embedding_provider` to compute vectors.
2. Delete any prior embeddings keyed by ``(project_slug, source_kind,
   source_id)`` so re-running indexers is idempotent.
3. Insert one row per chunk.

The implementation is synchronous and per-request: it consumes one
FastAPI worker for the duration of the embedding HTTP call. Large
backfills should call these from a background script rather than from
a request handler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.context_parser import ParsedChunk, chunk_text
from ai_qa_portal.backend.services.db_models.context import ContextFile
from ai_qa_portal.backend.services.db_models.embeddings import Embedding, EmbeddingSourceKind
from ai_qa_portal.backend.services.embedding_provider import (
    EmbeddingError,
    EmbeddingProvider,
    get_provider,
)

logger = logging.getLogger("ai_qa_portal.rag_index")


@dataclass(slots=True)
class IndexResult:
    source_kind: str
    source_id: str
    indexed: int
    skipped_reason: str | None = None


# ---- helpers --------------------------------------------------------------

def _delete_existing(
    db: Session,
    *,
    project_slug: str,
    source_kind: str,
    source_id: str,
) -> int:
    """Delete prior embeddings for this source, returning the row count."""
    q = db.query(Embedding).filter(
        Embedding.project_slug == project_slug,
        Embedding.source_kind == source_kind,
        Embedding.source_id == source_id,
    )
    deleted = q.delete(synchronize_session=False)
    return deleted


def _insert_chunks(
    db: Session,
    *,
    project_slug: str,
    source_kind: str,
    source_id: str,
    chunks: list[ParsedChunk],
    provider: EmbeddingProvider,
    parent_file_id: str | None = None,
    sprint_id: str | None = None,
    story_id: str | None = None,
    case_id: str | None = None,
) -> int:
    if not chunks:
        return 0
    try:
        vectors = provider.embed_texts([c.text for c in chunks])
    except EmbeddingError as exc:
        logger.warning(
            "Embedding call failed for %s/%s (%d chunks): %s",
            source_kind, source_id, len(chunks), exc,
        )
        return 0
    if len(vectors) != len(chunks):
        logger.warning(
            "Embedding provider returned %d vectors for %d chunks (%s/%s)",
            len(vectors), len(chunks), source_kind, source_id,
        )
        # Truncate so we don't insert mismatched rows.
        n = min(len(vectors), len(chunks))
        vectors = vectors[:n]
        chunks = chunks[:n]

    for chunk, vec in zip(chunks, vectors, strict=True):
        db.add(
            Embedding(
                project_slug=project_slug,
                source_kind=source_kind,
                source_id=source_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                token_count=chunk.token_count,
                embedding_model=provider.model,
                embedding=vec,
                sprint_id=sprint_id,
                story_id=story_id,
                case_id=case_id,
                parent_file_id=parent_file_id,
            )
        )
    return len(chunks)


def _index_text(
    db: Session,
    *,
    project_slug: str,
    source_kind: str,
    source_id: str,
    text: str,
    provider: EmbeddingProvider | None = None,
    parent_file_id: str | None = None,
    sprint_id: str | None = None,
    story_id: str | None = None,
    case_id: str | None = None,
) -> IndexResult:
    text = (text or "").strip()
    if not text:
        return IndexResult(source_kind=source_kind, source_id=source_id, indexed=0, skipped_reason="empty")
    chunks = chunk_text(text)
    if not chunks:
        return IndexResult(source_kind=source_kind, source_id=source_id, indexed=0, skipped_reason="no_chunks")
    provider = provider or get_provider()
    _delete_existing(db, project_slug=project_slug, source_kind=source_kind, source_id=source_id)
    indexed = _insert_chunks(
        db,
        project_slug=project_slug,
        source_kind=source_kind,
        source_id=source_id,
        chunks=chunks,
        provider=provider,
        parent_file_id=parent_file_id,
        sprint_id=sprint_id,
        story_id=story_id,
        case_id=case_id,
    )
    db.commit()
    return IndexResult(source_kind=source_kind, source_id=source_id, indexed=indexed)


# ---- public entrypoints ---------------------------------------------------

def enqueue_context_file(
    db: Session,
    *,
    file_row: ContextFile,
    chunks: Iterable[ParsedChunk],
) -> IndexResult:
    """Called from the context-file uploader once a file lands. Indexes
    every free-text chunk; structured CSV/XLSX rows are NOT indexed here
    (use :func:`index_test_data_row` on the addressable table copy)."""
    chunks_list = list(chunks)
    if not chunks_list:
        return IndexResult(
            source_kind=EmbeddingSourceKind.context_chunk.value,
            source_id=file_row.id,
            indexed=0,
            skipped_reason="no_chunks",
        )
    provider = get_provider()
    _delete_existing(
        db,
        project_slug=file_row.project_slug,
        source_kind=EmbeddingSourceKind.context_chunk.value,
        source_id=file_row.id,
    )
    indexed = _insert_chunks(
        db,
        project_slug=file_row.project_slug,
        source_kind=EmbeddingSourceKind.context_chunk.value,
        source_id=file_row.id,
        chunks=chunks_list,
        provider=provider,
        parent_file_id=file_row.id,
    )
    db.commit()
    return IndexResult(
        source_kind=EmbeddingSourceKind.context_chunk.value,
        source_id=file_row.id,
        indexed=indexed,
    )


def index_jira_issue(
    db: Session,
    *,
    project_slug: str,
    issue_id: str,
    summary: str,
    description: str,
    sprint_jira_id: str | None,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    body = f"{summary}\n\n{description}".strip()
    return _index_text(
        db,
        project_slug=project_slug,
        source_kind=EmbeddingSourceKind.jira_issue.value,
        source_id=issue_id,
        text=body,
        sprint_id=sprint_jira_id,
        provider=provider,
    )


def index_jira_comment(
    db: Session,
    *,
    project_slug: str,
    comment_id: str,
    issue_id: str,
    body: str,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    text = f"(Comment on {issue_id})\n\n{body}".strip()
    return _index_text(
        db,
        project_slug=project_slug,
        source_kind=EmbeddingSourceKind.jira_comment.value,
        source_id=comment_id,
        text=text,
        story_id=issue_id,
        provider=provider,
    )


def index_user_story(
    db: Session,
    *,
    project_slug: str,
    story_id: str,
    title: str,
    description: str,
    sprint_id: str | None = None,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    body = f"{title}\n\n{description}".strip()
    return _index_text(
        db,
        project_slug=project_slug,
        source_kind=EmbeddingSourceKind.user_story.value,
        source_id=story_id,
        text=body,
        sprint_id=sprint_id,
        story_id=story_id,
        provider=provider,
    )


def index_test_case(
    db: Session,
    *,
    project_slug: str,
    case_id: str,
    story_id: str | None,
    title: str,
    preconditions: str | None,
    steps: list[str],
    expected_result: str | None,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    parts = [title]
    if preconditions:
        parts.append(f"Preconditions: {preconditions}")
    if steps:
        parts.append("Steps:\n" + "\n".join(f"- {s}" for s in steps))
    if expected_result:
        parts.append(f"Expected: {expected_result}")
    return _index_text(
        db,
        project_slug=project_slug,
        source_kind=EmbeddingSourceKind.test_case.value,
        source_id=case_id,
        text="\n\n".join(parts),
        story_id=story_id,
        case_id=case_id,
        provider=provider,
    )


def index_test_data_row(
    db: Session,
    *,
    project_slug: str,
    row_id: str,
    table_name: str,
    row_text: str,
    provider: EmbeddingProvider | None = None,
) -> IndexResult:
    text = f"(From test-data table '{table_name}')\n{row_text}".strip()
    return _index_text(
        db,
        project_slug=project_slug,
        source_kind=EmbeddingSourceKind.test_data_row.value,
        source_id=row_id,
        text=text,
        provider=provider,
    )
