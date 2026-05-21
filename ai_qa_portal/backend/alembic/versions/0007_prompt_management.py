"""Prompt management tables.

Revision ID: 0007_prompt_management
Revises: 0006_import_batches
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_prompt_management"
down_revision: str | None = "0006_import_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def _existing_indexes(insp, table_name: str) -> set[str]:
    if table_name not in set(insp.get_table_names()):
        return set()
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    json_t = _json_type()

    # ---- prompt_templates ----------------------------------------
    if "prompt_templates" not in tables:
        op.create_table(
            "prompt_templates",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("category", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("output_format", sa.String(length=32), nullable=False, server_default="json_array"),
            sa.Column("model_hint", sa.String(length=120), nullable=True),
            sa.Column("placeholders_declared", json_t, nullable=False),
            sa.Column("owner_user_id", sa.String(length=36), nullable=True),
            sa.Column("source_template_id", sa.String(length=36), nullable=True),
            sa.Column("seed_content_sha", sa.String(length=64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["source_template_id"], ["prompt_templates.id"], ondelete="SET NULL",
            ),
        )
    existing = _existing_indexes(insp, "prompt_templates")
    if "ix_prompt_template_category" not in existing:
        op.create_index("ix_prompt_template_category", "prompt_templates", ["category"])
    if "ix_prompt_template_system" not in existing:
        op.create_index("ix_prompt_template_system", "prompt_templates", ["is_system"])
    if "ix_prompt_template_source" not in existing:
        op.create_index(
            "ix_prompt_template_source", "prompt_templates", ["source_template_id"],
        )

    # ---- prompt_versions -----------------------------------------
    if "prompt_versions" not in tables:
        op.create_table(
            "prompt_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("template_id", sa.String(length=36), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("change_note", sa.Text(), nullable=True),
            sa.Column("body_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("body_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["template_id"], ["prompt_templates.id"], ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_user_id"], ["users.id"], ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "template_id", "version_number", name="uq_prompt_version_number",
            ),
        )
    existing = _existing_indexes(insp, "prompt_versions")
    if "ix_prompt_version_template" not in existing:
        op.create_index("ix_prompt_version_template", "prompt_versions", ["template_id"])
    if "ix_prompt_version_created" not in existing:
        op.create_index("ix_prompt_version_created", "prompt_versions", ["created_at"])

    # ---- prompt_overrides ----------------------------------------
    if "prompt_overrides" not in tables:
        op.create_table(
            "prompt_overrides",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("scope_id", sa.String(length=36), nullable=True),
            sa.Column("category", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("template_id", sa.String(length=36), nullable=False),
            sa.Column("active_version_id", sa.String(length=36), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(
                ["template_id"], ["prompt_templates.id"], ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["active_version_id"], ["prompt_versions.id"], ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["updated_by_user_id"], ["users.id"], ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "scope", "scope_id", "category",
                name="uq_prompt_override_scope_category",
            ),
        )
    existing = _existing_indexes(insp, "prompt_overrides")
    if "ix_prompt_override_scope" not in existing:
        op.create_index(
            "ix_prompt_override_scope", "prompt_overrides", ["scope", "scope_id"],
        )
    if "ix_prompt_override_category" not in existing:
        op.create_index(
            "ix_prompt_override_category", "prompt_overrides", ["category"],
        )
    if "ix_prompt_override_template" not in existing:
        op.create_index(
            "ix_prompt_override_template", "prompt_overrides", ["template_id"],
        )

    # ---- prompt_usage_audit --------------------------------------
    if "prompt_usage_audit" not in tables:
        op.create_table(
            "prompt_usage_audit",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("template_version_id", sa.String(length=36), nullable=True),
            sa.Column("category", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("model", sa.String(length=120), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("qa_mode", sa.String(length=32), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("target_type", sa.String(length=32), nullable=True),
            sa.Column("target_id", sa.String(length=120), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["template_version_id"], ["prompt_versions.id"], ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        )
    existing = _existing_indexes(insp, "prompt_usage_audit")
    if "ix_prompt_usage_version" not in existing:
        op.create_index(
            "ix_prompt_usage_version", "prompt_usage_audit", ["template_version_id"],
        )
    if "ix_prompt_usage_user" not in existing:
        op.create_index(
            "ix_prompt_usage_user", "prompt_usage_audit", ["user_id", "created_at"],
        )
    if "ix_prompt_usage_target" not in existing:
        op.create_index(
            "ix_prompt_usage_target",
            "prompt_usage_audit",
            ["target_type", "target_id"],
        )
    if "ix_prompt_usage_category" not in existing:
        op.create_index(
            "ix_prompt_usage_category",
            "prompt_usage_audit",
            ["category", "created_at"],
        )

    # ---- prompt_meta (cache_epoch singleton) ---------------------
    if "prompt_meta" not in set(insp.get_table_names()):
        op.create_table(
            "prompt_meta",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cache_epoch", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        # Insert the single bootstrap row so the resolver can read
        # cache_epoch unconditionally without a "row exists?" branch.
        op.execute(
            "INSERT INTO prompt_meta (id, cache_epoch) VALUES (1, 1)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    for table, idx_names in (
        ("prompt_meta", ()),
        (
            "prompt_usage_audit",
            (
                "ix_prompt_usage_category",
                "ix_prompt_usage_target",
                "ix_prompt_usage_user",
                "ix_prompt_usage_version",
            ),
        ),
        (
            "prompt_overrides",
            (
                "ix_prompt_override_template",
                "ix_prompt_override_category",
                "ix_prompt_override_scope",
            ),
        ),
        (
            "prompt_versions",
            ("ix_prompt_version_created", "ix_prompt_version_template"),
        ),
        (
            "prompt_templates",
            (
                "ix_prompt_template_source",
                "ix_prompt_template_system",
                "ix_prompt_template_category",
            ),
        ),
    ):
        if table not in tables:
            continue
        existing = _existing_indexes(insp, table)
        for name in idx_names:
            if name in existing:
                op.drop_index(name, table_name=table)
        op.drop_table(table)
