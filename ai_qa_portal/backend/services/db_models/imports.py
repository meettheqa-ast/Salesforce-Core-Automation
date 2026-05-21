"""CSV / Excel test-case import persistence.

One ImportBatch row per uploaded file. The two-step wizard works as
follows:

  1. POST /api/imports/test-cases/parse
     - creates the row with status='pending' + persists the upload to
       {data_dir}/imports/{batch_id}{ext}
     - response includes suggested_mapping + preview rows (kept in
       memory; not persisted per-row in v1 to avoid table bloat)

  2. POST /api/imports/test-cases/commit
     - re-reads the file using the user-confirmed mapping
     - creates test cases under the target story
     - flips status to 'committed' / 'partial' / 'failed' with the
       per-row outcome counts

The schema is deliberately extensible: ``source_kind`` can grow beyond
``csv`` / ``xlsx`` (e.g. ``jira``, ``zephyr``, ``xray``, ``testrail``,
``azure_devops``) so a future external-system importer plugs into the
same row without a migration. The same applies to ``mapping_json``
which stores arbitrary column-mapping dicts.

A future ImportRow table can join here on ``batch_id`` if admins start
asking "why didn't row 47 land"; for now we keep failed rows in the
commit response body and reload them via the ``GET /api/imports/{id}``
endpoint when needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class ImportBatch(Base):
    """A single CSV / Excel test-case upload + its commit outcome.

    Lifecycle ``status`` values:

      - ``pending``      after /parse, before /commit
      - ``committed``    after /commit succeeded for every row
      - ``partial``      /commit ran but some rows failed validation /
                         dedupe
      - ``failed``       /commit aborted (parse error, no rows mapped,
                         exception)
      - ``rolled_back``  /rollback hard-deleted every TC this batch
                         created
    """

    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batch_project", "project_slug"),
        Index("ix_import_batch_user", "user_id"),
        Index("ix_import_batch_status", "status"),
        Index("ix_import_batch_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    # Project this import lives under -- the user must have member-or-higher
    # access to this slug for parse + commit and lead-or-higher for rollback.
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # User who uploaded the file. FK so we keep historical attribution even
    # if a row goes orphan after the user is hard-deleted.
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    # File metadata captured at upload time.
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # csv | xlsx | xls today; jira | zephyr | xray | testrail in future.
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="csv")
    # Disk path under {data_dir}/imports/. Cleared (set to "") after
    # successful commit so storage doesn't grow indefinitely; rollback
    # therefore cannot re-read the source -- it works off external_id
    # markers on the TCs it created instead.
    source_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # Target context. project_id is required; sprint/story optional --
    # when story_id is null and the source has a 'story_id' column,
    # commit maps per-row instead.
    target_project_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    target_sprint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_story_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # User-confirmed column mapping -- {source_column: canonical_field}.
    # Keys are the literal CSV header strings; values are one of the
    # canonical names from import_mapping.CANONICAL_FIELDS (or null to
    # skip the column).
    mapping_json: Mapped[dict] = mapped_column(JSONColumn(), nullable=False, default=dict)

    # Strategy used by the dedupe engine on commit.
    # skip | overwrite | create_new
    duplicate_strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="skip",
    )

    # Row counts. total_rows is set at /parse; the others land at /commit.
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When /commit returns 'partial' or 'failed', we keep the per-row
    # error list here so the frontend can offer "download failed rows
    # as CSV" without re-running the parse.
    failed_rows_json: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_slug": self.project_slug,
            "user_id": self.user_id,
            "source_filename": self.source_filename,
            "source_kind": self.source_kind,
            "target_project_id": self.target_project_id,
            "target_sprint_id": self.target_sprint_id,
            "target_story_id": self.target_story_id,
            "mapping": self.mapping_json or {},
            "duplicate_strategy": self.duplicate_strategy,
            "total_rows": int(self.total_rows or 0),
            "imported_count": int(self.imported_count or 0),
            "skipped_count": int(self.skipped_count or 0),
            "failed_count": int(self.failed_count or 0),
            "status": self.status,
            "error_message": self.error_message,
            "failed_rows": list(self.failed_rows_json or []),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "committed_at": self.committed_at.isoformat() if self.committed_at else None,
        }
