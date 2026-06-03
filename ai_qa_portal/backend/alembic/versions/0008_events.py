"""Unified events table (Phase 3 IA audit -- foundation only).

Revision ID: 0008_events
Revises: 0007_prompt_management

See ``services/db_models/events.py`` for the migration model. This
revision only CREATES the table; legacy tables (audit_log,
prompt_usage_audit, heal_events) stay untouched and active for at
least the next two release cycles.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008_events"
down_revision: str | None = "0007_prompt_management"
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

    if "events" not in tables:
        op.create_table(
            "events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="audit"),
            sa.Column("action", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("actor_user_id", sa.String(length=36), nullable=True),
            sa.Column("project_slug", sa.String(length=120), nullable=True),
            sa.Column("target_type", sa.String(length=32), nullable=True),
            sa.Column("target_id", sa.String(length=120), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("metadata_json", json_t, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["actor_user_id"], ["users.id"], ondelete="SET NULL",
            ),
        )

    existing = (
        {idx["name"] for idx in insp.get_indexes("events")}
        if "events" in set(insp.get_table_names())
        else set()
    )
    for idx_name, cols in (
        ("ix_events_kind_created", ["kind", "created_at"]),
        ("ix_events_actor_created", ["actor_user_id", "created_at"]),
        ("ix_events_project_created", ["project_slug", "created_at"]),
        ("ix_events_target", ["target_type", "target_id"]),
        ("ix_events_action_created", ["action", "created_at"]),
    ):
        if idx_name not in existing:
            op.create_index(idx_name, "events", cols)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "events" not in tables:
        return
    existing = {idx["name"] for idx in insp.get_indexes("events")}
    for idx_name in (
        "ix_events_action_created",
        "ix_events_target",
        "ix_events_project_created",
        "ix_events_actor_created",
        "ix_events_kind_created",
    ):
        if idx_name in existing:
            op.drop_index(idx_name, table_name="events")
    op.drop_table("events")
