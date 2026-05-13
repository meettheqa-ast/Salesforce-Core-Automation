"""One-shot: copy data from the legacy SQLite ``users.db`` into Postgres.

Usage::

    # 1. Provision Postgres with the pgvector extension installed:
    docker compose up -d postgres

    # 2. Run Alembic so the target DB has every table:
    DATABASE_URL='postgresql+psycopg://portal:portal@localhost:5432/portal' \\
        python -m alembic -c ai_qa_portal/alembic.ini upgrade head

    # 3. Run this script. It reads from the SQLite path implied by your
    #    existing DATA_DIR and writes to the Postgres URL in DATABASE_URL:
    DATABASE_URL='postgresql+psycopg://portal:portal@localhost:5432/portal' \\
        python scripts/migrate_sqlite_to_postgres.py

The script is idempotent: each row is inserted with ``ON CONFLICT DO NOTHING``
keyed by primary key, so re-running after a partial migration is safe.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make `ai_qa_portal.*` importable regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from ai_qa_portal.backend.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate")

# Tables to migrate in dependency order (users first, then anything FK'd to it).
ORDERED_TABLES = (
    "users",
    "project_memberships",
    "project_invitations",
    "runs",
    "audit_log",
    "notifications",
)


def _sqlite_url(data_dir: str) -> str:
    return f"sqlite:///{(Path(data_dir) / 'users.db').as_posix()}"


def _copy_table(src_engine, dst_engine, table: str) -> tuple[int, int]:
    """Returns (read, written) counts."""
    inspector = inspect(src_engine)
    if table not in inspector.get_table_names():
        log.info("  %s: not present in source, skipping", table)
        return (0, 0)
    cols = [c["name"] for c in inspector.get_columns(table)]
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    src_rows = list(src_engine.connect().execute(text(f"SELECT {col_list} FROM {table}")))

    if not src_rows:
        log.info("  %s: 0 rows", table)
        return (0, 0)

    written = 0
    insert_sql = text(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )
    with dst_engine.begin() as conn:
        for row in src_rows:
            payload = dict(zip(cols, row, strict=True))
            result = conn.execute(insert_sql, payload)
            written += result.rowcount or 0

    log.info("  %s: read=%d written=%d", table, len(src_rows), written)
    return (len(src_rows), written)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="Explicit SQLite DB path. Defaults to {DATA_DIR}/users.db.",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Override DATABASE_URL for the destination Postgres.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read source rows, report counts, but do not write.",
    )
    args = parser.parse_args()

    target_url = args.target_url or settings.database_url
    if not target_url or not target_url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
        log.error(
            "Target DATABASE_URL must be a Postgres URL (got %r). "
            "Pass --target-url=... or set DATABASE_URL in .env.",
            target_url,
        )
        return 2

    if args.sqlite_path:
        src_url = f"sqlite:///{Path(args.sqlite_path).expanduser().as_posix()}"
    else:
        src_url = _sqlite_url(settings.data_dir)
    src_path = src_url.replace("sqlite:///", "")
    if not Path(src_path).exists():
        log.error("Source SQLite DB not found at %s", src_path)
        return 3

    log.info("Source : %s", src_url)
    log.info("Target : %s", target_url)
    if args.dry_run:
        log.info("(dry run -- no writes)")

    src_engine = create_engine(src_url, future=True, connect_args={"check_same_thread": False})
    dst_engine = create_engine(target_url, future=True, pool_pre_ping=True)

    # Sanity check: the destination must already have all tables (Alembic
    # upgrade must have been run first).
    dst_tables = set(inspect(dst_engine).get_table_names())
    missing = [t for t in ORDERED_TABLES if t not in dst_tables]
    if missing:
        log.error(
            "Destination is missing tables: %s. Run "
            "`alembic -c ai_qa_portal/alembic.ini upgrade head` first.",
            ", ".join(missing),
        )
        return 4

    total_read = total_written = 0
    for table in ORDERED_TABLES:
        if args.dry_run:
            inspector = inspect(src_engine)
            if table not in inspector.get_table_names():
                log.info("  %s: not present in source, skipping", table)
                continue
            cnt = src_engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            log.info("  %s: %d rows (would copy)", table, cnt)
            total_read += cnt
            continue
        r, w = _copy_table(src_engine, dst_engine, table)
        total_read += r
        total_written += w

    log.info("Done. read=%d written=%d", total_read, total_written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
