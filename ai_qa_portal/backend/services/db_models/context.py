"""Context-file storage tables.

A ``ContextFile`` is any user-uploaded artefact that adds business context
for the AI: a list of user types, an account-template CSV, a process doc,
a release brief PDF, the team's testing playbook in Markdown. Files are
parsed once at upload time:

* CSV / XLSX rows land in ``context_file_rows`` (structured, queryable).
* PDF / DOCX / MD / TXT free text is chunked and embedded into the
  ``embeddings`` table for semantic retrieval.

The raw bytes are stored under ``{data_dir}/context_files/{file_id}.<ext>``;
``size`` and ``sha256`` are the only blob metadata kept in SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class ContextFile(Base):
    __tablename__ = "context_files"
    __table_args__ = (
        Index("ix_context_file_project", "project_slug"),
        Index("ix_context_file_sha", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "filename": self.filename,
            "mime": self.mime,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "chunk_count": self.chunk_count,
            "description": self.description or None,
            "uploaded_by_user_id": self.uploaded_by_user_id,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class ContextFileRow(Base):
    """One row per CSV/XLSX record. ``data`` is the JSON-encoded row keyed
    by column name; ``searchable_text`` is a flattened ``key: value | ...``
    string used for lexical / embedding hits."""

    __tablename__ = "context_file_rows"
    __table_args__ = (
        Index("ix_context_row_file", "context_file_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    context_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("context_files.id", ondelete="CASCADE"), nullable=False,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONColumn(), nullable=False)
    searchable_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "context_file_id": self.context_file_id,
            "row_index": self.row_index,
            "data": self.data,
            "searchable_text": self.searchable_text,
        }
