"""Reusable test-data tables (not credentials).

These are project-scoped reference tables that the AI can pull from when
generating tests: "user types", "account templates", "opportunity stages",
"lead routing rules", etc. They live alongside ``context_files`` rather
than replacing them because they are intentionally addressable by name --
prompts can say "use a user from the Sales Manager row of the User Types
table" and the retrieval layer should find that row deterministically.

Personas (login credentials) keep using the existing JSON-backed Persona
model + Fernet-encrypted password; this table holds NO credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class TestDataTable(Base):
    __tablename__ = "test_data_tables"
    __table_args__ = (
        UniqueConstraint("project_slug", "name", name="uq_test_data_table_project_name"),
        Index("ix_test_data_table_project", "project_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(64), default="generic", nullable=False)
    # Columns, in upload order; populated by the parser and used by the UI
    # so we can render previews without scanning every row.
    columns: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    source_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("context_files.id", ondelete="SET NULL"), nullable=True,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "name": self.name,
            "description": self.description or None,
            "kind": self.kind,
            "columns": self.columns or [],
            "source_file_id": self.source_file_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TestDataRow(Base):
    __tablename__ = "test_data_rows"
    __table_args__ = (
        Index("ix_test_data_row_table", "table_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    table_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_data_tables.id", ondelete="CASCADE"), nullable=False,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONColumn(), nullable=False)
    searchable_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "table_id": self.table_id,
            "row_index": self.row_index,
            "data": self.data,
            "searchable_text": self.searchable_text,
        }
