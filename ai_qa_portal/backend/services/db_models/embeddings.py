"""pgvector-backed embedding store.

A single ``embeddings`` table is the unified RAG index. Every retrievable
chunk lands here regardless of its origin (Jira issue body, Jira comment,
portal user story, portal test case, context-file chunk, test-data row).
Retrieval filters by ``project_slug + source_kind`` then ranks by cosine
distance.

The column is ``VECTOR(N)`` on Postgres (where N = ``settings.embedding_dim``)
and a JSON list on SQLite, so unit tests can exercise the round-trip on
SQLite even though similarity queries only work on Postgres.

Indexing strategy: an IVFFlat index over the embedding column with
``vector_cosine_ops``. The index is created in the Alembic migration with
``WITH (lists = 100)``; production deployments with >1M rows should bump
``lists`` and re-analyse.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.db import Base

from .types import VectorColumn


class EmbeddingSourceKind(str, enum.Enum):
    jira_issue = "jira_issue"
    jira_comment = "jira_comment"
    user_story = "user_story"
    test_case = "test_case"
    context_chunk = "context_chunk"
    test_data_row = "test_data_row"


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embedding_project_source", "project_slug", "source_kind"),
        Index("ix_embedding_source", "source_kind", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VectorColumn(settings.embedding_dim), nullable=False)
    # Optional grouping by sprint/story so retrieval can boost the active
    # context (e.g. "this generation is for story X" -> upweight chunks
    # tied to story X).
    sprint_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    story_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Pointer back to the originating file (for context_chunk) or row
    # (for test_data_row); null for jira_*/user_story/test_case.
    parent_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("context_files.id", ondelete="CASCADE"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    def to_dict(self, *, include_embedding: bool = False) -> dict:
        d = {
            "id": self.id,
            "project_slug": self.project_slug,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "token_count": self.token_count,
            "embedding_model": self.embedding_model,
            "sprint_id": self.sprint_id,
            "story_id": self.story_id,
            "case_id": self.case_id,
            "parent_file_id": self.parent_file_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_embedding:
            d["embedding"] = list(self.embedding) if self.embedding is not None else None
        return d
