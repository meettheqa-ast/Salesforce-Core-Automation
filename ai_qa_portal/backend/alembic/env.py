"""Alembic env: drives migrations off the live ``settings.database_url``.

Why a custom env instead of the stock template:

* We must register every SQLAlchemy model on ``Base.metadata`` BEFORE
  Alembic introspects it, otherwise autogenerate emits half a schema.
  That's why we import ``services.db`` first (gets the original tables)
  and then ``services.db_models`` (gets the new ones).
* We read the DB URL from the same ``settings`` object the app uses so
  ``alembic upgrade head`` always targets the same database as the
  running FastAPI process.
"""

from __future__ import annotations

import logging.config
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---- Make sure the repo root is on sys.path so `ai_qa_portal.*` imports work
# whether alembic is invoked from the root or from the package directory.
import sys

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_qa_portal.backend.config import settings  # noqa: E402
from ai_qa_portal.backend.services.db import Base  # noqa: E402
from ai_qa_portal.backend.services import db_models  # noqa: E402,F401 -- registers extension tables

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # noqa: BLE001 -- logging config is best-effort
        logging.basicConfig(level=logging.INFO)

target_metadata = Base.metadata


def _resolved_url() -> str:
    """Prefer the alembic.ini URL (lets CI override via -x), else use the
    runtime settings URL, else fall back to the local SQLite dev DB.
    """
    url = config.get_main_option("sqlalchemy.url") or ""
    if url:
        return url
    if settings.database_url:
        return settings.database_url
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'users.db').as_posix()}"


def run_migrations_offline() -> None:
    """Emit SQL to stdout without binding to a real engine."""
    url = _resolved_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Open a connection and run migrations against the live database."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolved_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
