"""Prompt management persistence models.

Five tables:

  * ``prompt_templates``    -- one row per named prompt (system seeds +
                               user/project/org clones).
  * ``prompt_versions``     -- append-only history of bodies for a
                               template (every save = new row).
  * ``prompt_overrides``    -- sparse "which version is active under
                               which scope" lookup. Only present when a
                               non-system scope is actively overriding.
  * ``prompt_usage_audit``  -- one row per generation call so we can
                               trace any test case / script / plan back
                               to the exact template version + model.
  * ``prompt_meta``         -- single-row table holding a monotonic
                               ``cache_epoch`` counter. Every mutation
                               bumps it; the resolver checks it before
                               using its in-process LRU cache so
                               multi-process / multi-worker deploys stay
                               coherent without an external pub/sub.

Why SQL (not the JSON store): version history + sparse-override lookup
both want an actual relational engine; the JSON store is great for
denormalised single-doc reads but terrible at "what's the active version
of category X for user Y across N templates". The other recently-added
features (heal_events, import_batches, generation_jobs) all picked SQL
for the same reason; we follow suit.

The schema is deliberately tolerant of future categories: the
``category`` column is just a short string, not a DB enum, so adding
"defect_analysis" / "regression_suite" / etc. is a Python-side change
only. Same for ``output_format`` and ``scope``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class PromptTemplate(Base):
    """A named prompt asset. System seeds use ``is_system=True``;
    user / project / org overrides use ``is_system=False`` and point at
    a ``source_template_id`` (the seed they cloned from, when any).

    Soft delete: ``deleted_at`` is set instead of dropping the row so
    the audit history (``prompt_usage_audit``) can still resolve
    historical template names.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index("ix_prompt_template_category", "category"),
        Index("ix_prompt_template_system", "is_system"),
        Index("ix_prompt_template_source", "source_template_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    # Free-form short string -- e.g. "test_case_drafter", "script_builder",
    # "quick_robot", "stepwise_planner", "healer", "recording_translator",
    # plus future: "defect_analysis", "regression_suite", etc.
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Human display name. Unique per (category, is_system, owner_user_id)
    # would be nice but we don't enforce it -- two clones with the same
    # name are valid and disambiguated by id.
    name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bookkeeping. is_system means "shipped with the product"; users
    # cannot edit these directly (they clone them first via /api/prompts
    # POST with source_template_id).
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # json_array | markdown_table | robot_script | freeform.
    # Drives prompt_output_parser dispatch.
    output_format: Mapped[str] = mapped_column(
        String(32), nullable=False, default="json_array",
    )
    # Optional model preference baked into the template
    # (e.g. "anthropic:claude-3.7-sonnet"). When set, the resolver
    # forwards it as a hint -- ai_bridge still respects the global
    # failover chain when the hinted model is unavailable.
    model_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Declared placeholder names -- the compiler rejects bodies that
    # use undeclared names at save time, so typos surface at edit time
    # instead of mid-generation.
    placeholders_declared: Mapped[list] = mapped_column(
        JSONColumn(), nullable=False, default=list,
    )

    # Ownership / clone chain. owner_user_id is null for system seeds
    # and for project/org templates (where the project/org owns it).
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    source_template_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Content hash of the seed body (only set on is_system rows). Lets
    # the seeder detect "the .md file on disk changed since boot" and
    # append a new system version transparently across releases.
    seed_content_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def to_dict(self, *, include_active_version: dict | None = None) -> dict:
        out = {
            "id": self.id,
            "category": self.category,
            "name": self.name,
            "description": self.description,
            "is_system": bool(self.is_system),
            "is_active": bool(self.is_active),
            "output_format": self.output_format,
            "model_hint": self.model_hint,
            "placeholders_declared": list(self.placeholders_declared or []),
            "owner_user_id": self.owner_user_id,
            "source_template_id": self.source_template_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
        if include_active_version is not None:
            out["active_version"] = include_active_version
        return out


class PromptVersion(Base):
    """Immutable body history. Every "save" appends a new row with
    ``version_number = max + 1`` for that template_id. We never UPDATE
    a version row, so rollback is just "activate version N" again.

    body is TEXT (SQLite unlimited; Postgres TEXT also unlimited). The
    router enforces a configurable cap (PROMPT_MAX_BODY_BYTES, default
    200 KB) so a runaway client can't blow up the DB.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_prompt_version_number"),
        Index("ix_prompt_version_template", "template_id"),
        Index("ix_prompt_version_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    body_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "version_number": int(self.version_number or 0),
            "change_note": self.change_note,
            "body_sha256": self.body_sha256,
            "body_bytes": int(self.body_bytes or 0),
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PromptOverride(Base):
    """Sparse "which template + version is active for which scope".

    Scope semantics:
      * ``user``    -- ``scope_id = user.id``
      * ``project`` -- ``scope_id = project_id (UUID string)``
      * ``org``     -- ``scope_id = null`` (single org per deployment
                       for now; multi-tenant adds a real org id later)

    A row exists ONLY when a non-system scope has chosen something
    other than the seeded default for that category. Resolution walks
    user -> project -> org and falls back to the system seed when no
    row is found at any level -- so we never duplicate defaults into
    every user's table.
    """

    __tablename__ = "prompt_overrides"
    __table_args__ = (
        UniqueConstraint(
            "scope", "scope_id", "category",
            name="uq_prompt_override_scope_category",
        ),
        Index("ix_prompt_override_scope", "scope", "scope_id"),
        Index("ix_prompt_override_category", "category"),
        Index("ix_prompt_override_template", "template_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    # Null for org-scope; string-UUID for user/project. We deliberately
    # store as String rather than UUID so the unique constraint works
    # uniformly across SQLite + Postgres without dialect-specific NULL
    # handling.
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    template_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    active_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "category": self.category,
            "template_id": self.template_id,
            "active_version_id": self.active_version_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by_user_id": self.updated_by_user_id,
        }


class PromptUsageAudit(Base):
    """One row per LLM generation call. Lets an operator answer
    questions like "which prompt version generated this dodgy test
    case?" or "how many tokens has this template burned this month?"
    without spelunking through logs.
    """

    __tablename__ = "prompt_usage_audit"
    __table_args__ = (
        Index("ix_prompt_usage_version", "template_version_id"),
        Index("ix_prompt_usage_user", "user_id", "created_at"),
        Index("ix_prompt_usage_target", "target_type", "target_id"),
        Index("ix_prompt_usage_category", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()),
    )
    template_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qa_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-form target. test_case_id / script_path / plan_id are all
    # legitimate. None when the call isn't tied to a persisted entity
    # (e.g. /preview).
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template_version_id": self.template_version_id,
            "category": self.category,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "model": self.model,
            "provider": self.provider,
            "qa_mode": self.qa_mode,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PromptMeta(Base):
    """Single-row table whose only job is to publish a monotonic
    ``cache_epoch`` integer. The resolver maintains an in-process LRU
    cache keyed on (category, scope_tuple); it checks this counter on
    every resolve and invalidates when it advances. Cheap one-row
    SELECT (~50us on SQLite, single hash hit on a connection-pool
    Postgres) buys us multi-worker cache coherence with no external
    pub/sub.
    """

    __tablename__ = "prompt_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cache_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
