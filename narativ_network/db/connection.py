"""SQLite connection + migration runner.

Single source of truth for the schema is `schema.sql`. Migrations
beyond initial schema live in `migrations/NNNN_name.sql` (created later
when we need them); they're applied in lexical order and tracked in
the `migrations` table.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import Config, absolute_path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(cfg: Config) -> sqlite3.Connection:
    db_file = absolute_path(cfg, cfg.db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def migrate(cfg: Config) -> list[str]:
    """Apply schema.sql then any incremental migrations. Idempotent.

    Returns the list of migration names newly applied.
    """
    conn = connect(cfg)
    conn.executescript(SCHEMA_PATH.read_text())

    applied: list[str] = []
    if MIGRATIONS_DIR.exists():
        already = {row["name"] for row in conn.execute("SELECT name FROM migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already:
                continue
            with transaction(conn):
                conn.executescript(path.read_text())
                conn.execute("INSERT INTO migrations(name) VALUES (?)", (path.name,))
            applied.append(path.name)
    conn.close()
    return applied
