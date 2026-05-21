"""Form-healing persistence tables.

Revision ID: 0005_heal_events
Revises: 0004_planner_brain
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_heal_events"
down_revision: str | None = "0004_planner_brain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_t = _json_type()
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "heal_events" not in tables:
        op.create_table(
            "heal_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_slug", sa.String(length=120), nullable=True),
            sa.Column("run_id", sa.String(length=120), nullable=True),
            sa.Column("generation_job_id", sa.String(length=36), nullable=True),
            sa.Column("sobject", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("session_id", sa.String(length=120), nullable=True),
            sa.Column("step_index", sa.Integer(), nullable=True),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("error_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("field_label", sa.String(length=255), nullable=True),
            sa.Column("strategy", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("outcome", sa.String(length=32), nullable=False, server_default="aborted"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload", json_t, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["generation_job_id"],
                ["generation_jobs.id"],
                ondelete="SET NULL",
            ),
        )
    existing = {idx["name"] for idx in insp.get_indexes("heal_events")} if "heal_events" in set(insp.get_table_names()) else set()
    if "ix_heal_event_project_sobject_type" not in existing:
        op.create_index(
            "ix_heal_event_project_sobject_type",
            "heal_events",
            ["project_slug", "sobject", "error_type"],
        )
    if "ix_heal_event_job" not in existing:
        op.create_index("ix_heal_event_job", "heal_events", ["generation_job_id"])
    if "ix_heal_event_run" not in existing:
        op.create_index("ix_heal_event_run", "heal_events", ["run_id"])

    if "org_field_learnings" not in tables:
        op.create_table(
            "org_field_learnings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_slug", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("sobject", sa.String(length=80), nullable=False, server_default=""),
            sa.Column("field_api_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("error_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_success_strategy", sa.String(length=64), nullable=True),
            sa.Column("last_failed_strategy", sa.String(length=64), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "project_slug",
                "sobject",
                "field_api_name",
                "error_type",
                name="uq_org_field_learnings_scope",
            ),
        )
    existing2 = {idx["name"] for idx in insp.get_indexes("org_field_learnings")} if "org_field_learnings" in set(insp.get_table_names()) else set()
    if "ix_org_field_learning_project" not in existing2:
        op.create_index("ix_org_field_learning_project", "org_field_learnings", ["project_slug"])
    if "ix_org_field_learning_field" not in existing2:
        op.create_index(
            "ix_org_field_learning_field",
            "org_field_learnings",
            ["sobject", "field_api_name"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "org_field_learnings" in tables:
        existing = {idx["name"] for idx in insp.get_indexes("org_field_learnings")}
        if "ix_org_field_learning_field" in existing:
            op.drop_index("ix_org_field_learning_field", table_name="org_field_learnings")
        if "ix_org_field_learning_project" in existing:
            op.drop_index("ix_org_field_learning_project", table_name="org_field_learnings")
        op.drop_table("org_field_learnings")

    if "heal_events" in tables:
        existing = {idx["name"] for idx in insp.get_indexes("heal_events")}
        if "ix_heal_event_run" in existing:
            op.drop_index("ix_heal_event_run", table_name="heal_events")
        if "ix_heal_event_job" in existing:
            op.drop_index("ix_heal_event_job", table_name="heal_events")
        if "ix_heal_event_project_sobject_type" in existing:
            op.drop_index("ix_heal_event_project_sobject_type", table_name="heal_events")
        op.drop_table("heal_events")

