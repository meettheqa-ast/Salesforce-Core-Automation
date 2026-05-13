"""Baseline schema for the AI QA Portal.

Mirrors the tables that were previously created via ``Base.metadata.create_all``
on SQLite: ``users``, ``project_memberships``, ``project_invitations``, ``runs``,
``audit_log``, ``notifications``. Brand-new Postgres installs run this first;
SQLite users migrating to Postgres run it via the backfill script in
``scripts/migrate_sqlite_to_postgres.py`` and then ``stamp`` this revision so
Alembic skips the create on the next ``upgrade``.

Revision ID: 0001_baseline
Revises:
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("picture", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("global_role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("session_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "project_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_slug", "user_id", name="uq_project_user"),
    )

    op.create_table(
        "project_invitations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False, index=True),
        sa.Column("email", sa.String(length=255), nullable=False, index=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="invite"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending", index=True),
        sa.Column("invited_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False, index=True),
        sa.Column("run_folder", sa.String(length=255), nullable=False, index=True),
        sa.Column("triggered_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("persona_used_id", sa.String(length=36), nullable=True),
        sa.Column("persona_owner_at_time", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="started"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("action", sa.String(length=64), nullable=False, index=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False, index=True),
        sa.Column("metadata_json", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("action_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("audit_log")
    op.drop_table("runs")
    op.drop_table("project_invitations")
    op.drop_table("project_memberships")
    op.drop_table("users")
