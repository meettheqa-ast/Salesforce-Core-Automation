"""Test case import_batches table.

Revision ID: 0006_import_batches
Revises: 0005_heal_events
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_import_batches"
down_revision: str | None = "0005_heal_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    json_t = _json_type()

    if "import_batches" not in tables:
        op.create_table(
            "import_batches",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_slug", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("source_filename", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="csv"),
            sa.Column("source_path", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("target_project_id", sa.String(length=36), nullable=False, server_default=""),
            sa.Column("target_sprint_id", sa.String(length=36), nullable=True),
            sa.Column("target_story_id", sa.String(length=36), nullable=True),
            sa.Column("mapping_json", json_t, nullable=False),
            sa.Column("duplicate_strategy", sa.String(length=32), nullable=False, server_default="skip"),
            sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("failed_rows_json", json_t, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], ondelete="SET NULL",
            ),
        )

    existing = (
        {idx["name"] for idx in insp.get_indexes("import_batches")}
        if "import_batches" in set(insp.get_table_names())
        else set()
    )
    if "ix_import_batch_project" not in existing:
        op.create_index("ix_import_batch_project", "import_batches", ["project_slug"])
    if "ix_import_batch_user" not in existing:
        op.create_index("ix_import_batch_user", "import_batches", ["user_id"])
    if "ix_import_batch_status" not in existing:
        op.create_index("ix_import_batch_status", "import_batches", ["status"])
    if "ix_import_batch_created" not in existing:
        op.create_index("ix_import_batch_created", "import_batches", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "import_batches" not in tables:
        return
    existing = {idx["name"] for idx in insp.get_indexes("import_batches")}
    for name in (
        "ix_import_batch_created",
        "ix_import_batch_status",
        "ix_import_batch_user",
        "ix_import_batch_project",
    ):
        if name in existing:
            op.drop_index(name, table_name="import_batches")
    op.drop_table("import_batches")
