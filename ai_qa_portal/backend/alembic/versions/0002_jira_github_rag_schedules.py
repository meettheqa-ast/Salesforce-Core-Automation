"""Jira ingestion + GitHub connections + RAG context store + schedules.

Adds the pgvector extension and twelve new tables:

* ``jira_connections``, ``jira_projects``, ``jira_sprints``, ``jira_issues``,
  ``jira_comments``
* ``github_connections``, ``github_repos``
* ``schedules``, ``schedule_runs``
* ``context_files``, ``context_file_rows``
* ``test_data_tables``, ``test_data_rows``
* ``embeddings`` with an IVFFlat cosine index on the vector column

Embedding dimension is read from ``settings.embedding_dim`` (default 1536 to
match OpenAI ``text-embedding-3-small``). If you change the embedding model
to a different dimension, you MUST also re-run this migration after
dropping the ``embeddings`` table -- pgvector won't reshape an existing
column.

Revision ID: 0002_jira_github_rag_schedules
Revises: 0001_baseline
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from ai_qa_portal.backend.config import settings

revision: str = "0002_jira_github_rag_schedules"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type():
    """JSONB on Postgres, plain JSON elsewhere."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def _vector_type(dim: int):
    """pgvector.Vector on Postgres, JSON elsewhere."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from pgvector.sqlalchemy import Vector

        return Vector(dim)
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Best-effort: needs CREATE privilege on the database. Operators
        # without it should install the extension out of band and set
        # PGVECTOR_AUTO_INSTALL=false in the runtime env.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    json_t = _json_type()

    # ---- Jira ---------------------------------------------------------
    op.create_table(
        "jira_connections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("project_slug", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("encrypted_token", sa.Text, nullable=False),
        sa.Column("default_jira_project_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "project_slug", name="uq_jira_conn_scope_project"),
    )
    op.create_index("ix_jira_conn_scope", "jira_connections", ["scope"])

    op.create_table(
        "jira_projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(length=36),
            sa.ForeignKey("jira_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("jira_id", sa.String(length=64), nullable=False),
        sa.Column("jira_key", sa.String(length=64), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("project_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", json_t, nullable=True),
        sa.UniqueConstraint("connection_id", "jira_key", name="uq_jira_project_conn_key"),
    )

    op.create_table(
        "jira_sprints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(length=36),
            sa.ForeignKey("jira_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jira_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("jira_project_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="future"),
        sa.Column("goal", sa.Text, nullable=False, server_default=""),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("complete_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("portal_sprint_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("payload", json_t, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "jira_id", name="uq_jira_sprint_conn_id"),
    )
    op.create_index("ix_jira_sprint_project_key", "jira_sprints", ["jira_project_key"])

    op.create_table(
        "jira_issues",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(length=36),
            sa.ForeignKey("jira_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jira_id", sa.String(length=64), nullable=False),
        sa.Column("jira_key", sa.String(length=64), nullable=False),
        sa.Column("jira_project_key", sa.String(length=64), nullable=False, index=True),
        sa.Column("issue_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("summary", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("assignee", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("reporter", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("priority", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("labels", json_t, nullable=True),
        sa.Column("sprint_jira_id", sa.String(length=64), nullable=True),
        sa.Column("parent_jira_id", sa.String(length=64), nullable=True),
        sa.Column("portal_story_id", sa.String(length=36), nullable=True, index=True),
        sa.Column("payload", json_t, nullable=True),
        sa.Column("jira_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jira_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "jira_id", name="uq_jira_issue_conn_id"),
    )
    op.create_index("ix_jira_issue_jira_key", "jira_issues", ["jira_key"])
    op.create_index("ix_jira_issue_sprint", "jira_issues", ["sprint_jira_id"])
    op.create_index("ix_jira_issue_parent", "jira_issues", ["parent_jira_id"])

    op.create_table(
        "jira_comments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("issue_jira_id", sa.String(length=64), nullable=False),
        sa.Column("jira_id", sa.String(length=64), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("jira_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jira_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", json_t, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("issue_jira_id", "jira_id", name="uq_jira_comment_issue_id"),
    )
    op.create_index("ix_jira_comment_issue", "jira_comments", ["issue_jira_id"])

    # ---- GitHub -------------------------------------------------------
    op.create_table(
        "github_connections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("project_slug", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("auth_kind", sa.String(length=8), nullable=False),
        sa.Column("owner_login", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("app_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("installation_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("encrypted_private_key", sa.Text, nullable=False, server_default=""),
        sa.Column("encrypted_access_token", sa.Text, nullable=False, server_default=""),
        sa.Column("webhook_secret_hash", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "project_slug", name="uq_gh_conn_scope_project"),
    )
    op.create_index("ix_gh_conn_scope", "github_connections", ["scope"])

    op.create_table(
        "github_repos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(length=36),
            sa.ForeignKey("github_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_slug", sa.String(length=120), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("default_branch", sa.String(length=120), nullable=False, server_default="main"),
        sa.Column("is_connected", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "workflow_path",
            sa.String(length=255),
            nullable=False,
            server_default=".github/workflows/portal-automation.yml",
        ),
        sa.Column(
            "suites_root_path",
            sa.String(length=255),
            nullable=False,
            server_default="tests/portal",
        ),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "connection_id", "project_slug", "owner", "name",
            name="uq_gh_repo_conn_project_full_name",
        ),
    )
    op.create_index("ix_gh_repo_project", "github_repos", ["project_slug"])

    # ---- Schedules ----------------------------------------------------
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("runner", sa.String(length=32), nullable=False, server_default="local"),
        sa.Column(
            "github_repo_id",
            sa.String(length=36),
            sa.ForeignKey("github_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("persona_id", sa.String(length=36), nullable=True),
        sa.Column("org_id", sa.String(length=36), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_schedule_project_enabled", "schedules", ["project_slug", "enabled"])
    op.create_index("ix_schedule_runner", "schedules", ["runner"])

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.String(length=36),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("runner", sa.String(length=32), nullable=False),
        sa.Column("github_workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("github_workflow_run_url", sa.String(length=512), nullable=True),
        sa.Column("local_run_id", sa.String(length=36), nullable=True),
        sa.Column("result_summary", json_t, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_schedule_run_schedule", "schedule_runs", ["schedule_id"])
    op.create_index("ix_schedule_run_status", "schedule_runs", ["status"])

    # ---- Context files ------------------------------------------------
    op.create_table(
        "context_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False, server_default="application/octet-stream"),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("uploaded_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_context_file_project", "context_files", ["project_slug"])
    op.create_index("ix_context_file_sha", "context_files", ["sha256"])

    op.create_table(
        "context_file_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "context_file_id",
            sa.String(length=36),
            sa.ForeignKey("context_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer, nullable=False),
        sa.Column("data", json_t, nullable=False),
        sa.Column("searchable_text", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_context_row_file", "context_file_rows", ["context_file_id"])

    # ---- Test data ----------------------------------------------------
    op.create_table(
        "test_data_tables",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default="generic"),
        sa.Column("columns", json_t, nullable=True),
        sa.Column(
            "source_file_id",
            sa.String(length=36),
            sa.ForeignKey("context_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_slug", "name", name="uq_test_data_table_project_name"),
    )
    op.create_index("ix_test_data_table_project", "test_data_tables", ["project_slug"])

    op.create_table(
        "test_data_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "table_id",
            sa.String(length=36),
            sa.ForeignKey("test_data_tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer, nullable=False),
        sa.Column("data", json_t, nullable=False),
        sa.Column("searchable_text", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_test_data_row_table", "test_data_rows", ["table_id"])

    # ---- Embeddings ---------------------------------------------------
    op.create_table(
        "embeddings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_slug", sa.String(length=120), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding", _vector_type(settings.embedding_dim), nullable=False),
        sa.Column("sprint_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("story_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("case_id", sa.String(length=64), nullable=True, index=True),
        sa.Column(
            "parent_file_id",
            sa.String(length=36),
            sa.ForeignKey("context_files.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_embedding_project_source", "embeddings", ["project_slug", "source_kind"])
    op.create_index("ix_embedding_source", "embeddings", ["source_kind", "source_id"])

    if is_postgres:
        # IVFFlat needs ANALYZE-equivalent data to perform well; tune
        # ``lists`` upward for large corpora (rule of thumb: sqrt(rows)).
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_embedding_vec_cos "
            "ON embeddings USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_embedding_vec_cos")
    op.drop_table("embeddings")
    op.drop_table("test_data_rows")
    op.drop_table("test_data_tables")
    op.drop_table("context_file_rows")
    op.drop_table("context_files")
    op.drop_table("schedule_runs")
    op.drop_table("schedules")
    op.drop_table("github_repos")
    op.drop_table("github_connections")
    op.drop_table("jira_comments")
    op.drop_table("jira_issues")
    op.drop_table("jira_sprints")
    op.drop_table("jira_projects")
    op.drop_table("jira_connections")
