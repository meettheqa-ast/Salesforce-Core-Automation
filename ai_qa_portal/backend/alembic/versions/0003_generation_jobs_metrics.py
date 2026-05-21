"""Generation jobs and telemetry metrics.

Revision ID: 0003_generation_jobs_metrics
Revises: 0002_jira_github_rag_schedules
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_generation_jobs_metrics"
down_revision: str | None = "0002_jira_github_rag_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_t = _json_type()

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_slug", sa.String(length=120), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="mcp_stepwise"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("request_payload", json_t, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("current_phase", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("phase_log", json_t, nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("robot_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_generation_job_owner_status",
        "generation_jobs",
        ["owner_user_id", "status"],
    )
    op.create_index(
        "ix_generation_job_project_status",
        "generation_jobs",
        ["project_slug", "status"],
    )

    op.create_table(
        "generation_metrics",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(length=64), nullable=False, server_default="mcp_stepwise"),
        sa.Column("prompt_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase_timings", json_t, nullable=False),
        sa.Column("final_status", sa.String(length=16), nullable=False, server_default="error"),
        sa.Column("fallback_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("robot_chars", sa.Integer(), nullable=True),
        sa.Column("step_count_planned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_count_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_count_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_used", sa.String(length=64), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_generation_metric_started",
        "generation_metrics",
        ["started_at"],
    )
    op.create_index(
        "ix_generation_metric_mode_status",
        "generation_metrics",
        ["mode", "final_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_metric_mode_status", table_name="generation_metrics")
    op.drop_index("ix_generation_metric_started", table_name="generation_metrics")
    op.drop_table("generation_metrics")

    op.drop_index("ix_generation_job_project_status", table_name="generation_jobs")
    op.drop_index("ix_generation_job_owner_status", table_name="generation_jobs")
    op.drop_table("generation_jobs")
