"""Planner-brain tables: verified recipes + per-keyword outcomes.

Revision ID: 0004_planner_brain
Revises: 0003_generation_jobs_metrics
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004_planner_brain"
down_revision: str | None = "0003_generation_jobs_metrics"
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
        "verified_recipes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_normalized", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("plan", json_t, nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_verified_recipe_project_norm",
        "verified_recipes",
        ["project_slug", "prompt_normalized"],
    )
    op.create_index(
        "ix_verified_recipe_score",
        "verified_recipes",
        ["success_count"],
    )

    op.create_table(
        "keyword_outcomes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("keyword", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_slug", "keyword", name="uq_keyword_outcome_project_keyword",
        ),
    )
    op.create_index(
        "ix_keyword_outcome_project",
        "keyword_outcomes",
        ["project_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_keyword_outcome_project", table_name="keyword_outcomes")
    op.drop_table("keyword_outcomes")
    op.drop_index("ix_verified_recipe_score", table_name="verified_recipes")
    op.drop_index("ix_verified_recipe_project_norm", table_name="verified_recipes")
    op.drop_table("verified_recipes")
