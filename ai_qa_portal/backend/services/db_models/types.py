"""Dialect-aware column types shared across the new db_models.

The portal runs on Postgres (production) and SQLite (local dev fallback).
Two column kinds need special handling to keep both dialects working:

* **JSON / JSONB**: we want JSONB on Postgres for index + operator support
  and the standard JSON type on SQLite.
* **Vector**: pgvector's ``Vector`` type only exists on Postgres. On SQLite
  we fall back to a JSON column so the schema still creates and so tests
  that run on SQLite can at least round-trip the embedding payload (real
  similarity search is Postgres-only).

Both columns are implemented as ``TypeDecorator`` subclasses so callers can
write ``mapped_column(JSONColumn())`` or ``mapped_column(VectorColumn(1536))``
without branching on the dialect themselves.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class JSONColumn(TypeDecorator):
    """JSONB on Postgres, JSON on every other dialect."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001 -- SQLAlchemy hook signature
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class VectorColumn(TypeDecorator):
    """pgvector ``Vector(dim)`` on Postgres; JSON list fallback elsewhere.

    Callers should pass ``dim`` explicitly so the Postgres column is
    constrained at the database level. Dimension mismatches are detected
    at insert time by pgvector itself.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int, *args: Any, **kwargs: Any) -> None:
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        if dialect.name == "postgresql":
            # Imported lazily so non-Postgres dev environments don't pay the
            # import cost (and so a bad install of the pgvector wheel never
            # crashes SQLite-based unit tests).
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())
