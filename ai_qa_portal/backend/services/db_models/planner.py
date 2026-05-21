"""Planner-brain persistence: verified recipes and per-keyword scoring.

Two tables:

* ``VerifiedRecipe`` -- every Stepwise run that converges (no failed
  steps after replan) is captured here. Future runs with a similar
  prompt can short-circuit the LLM by replaying a known-good plan.
  Per-project AND global-fallback rows let new projects benefit from
  generic patterns while preferring same-project memory.

* ``KeywordOutcome`` -- per (project, keyword, success) counter. The
  planner uses these to bias the catalog presented to the LLM so
  keywords that have historically failed in this org show up annotated
  ("**unreliable in this org**: prefer X") rather than be dropped from
  the catalog entirely.

These models are intentionally tiny -- the system is more useful with
many small rows than with one big JSON blob, since query latency on the
recipe lookup is on the user's critical path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_qa_portal.backend.services.db import Base

from .types import JSONColumn


class VerifiedRecipe(Base):
    """A successful Stepwise plan we want to remember for future reuse.

    The same prompt-shape (similar wording, same project) can be looked
    up cheaply via the prompt's normalised hash + a fuzzy similarity
    pass; on a hit we replay the recipe instead of calling the LLM.

    ``project_slug`` is nullable: rows with NULL apply to every project
    (cross-project memory), rows with a value are scoped to that
    project. Lookup logic prefers project-scoped rows over global ones.
    """

    __tablename__ = "verified_recipes"
    __table_args__ = (
        Index("ix_verified_recipe_project_norm", "project_slug", "prompt_normalized"),
        Index("ix_verified_recipe_score", "success_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_normalized: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    plan: Mapped[list] = mapped_column(JSONColumn(), nullable=False, default=list)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC),
    )


class KeywordOutcome(Base):
    """Per (project, keyword) success/failure counters.

    A row is upserted on every Stepwise execute_step result. The
    planner reads the top-N most-failed keywords for a project and
    annotates them in the catalog presented to the LLM so the model
    knows to prefer alternatives when they exist.
    """

    __tablename__ = "keyword_outcomes"
    __table_args__ = (
        UniqueConstraint("project_slug", "keyword", name="uq_keyword_outcome_project_keyword"),
        Index("ix_keyword_outcome_project", "project_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_slug: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
