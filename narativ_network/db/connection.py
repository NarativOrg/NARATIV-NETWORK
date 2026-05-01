"""SQLite connection + migration runner.

`schema.sql` is the canonical CURRENT state of the schema (everything
has CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS), so a
fresh install gets the full schema in one shot.

Migrations in `migrations/NNNN_name.sql` are for upgrading EXISTING
databases that pre-date a schema change. On a fresh install where
schema.sql already covers what a migration would add, the migration
becomes a no-op — we detect that via OperationalError ('duplicate
column name' / 'already exists') and silently mark the migration as
applied.
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
    """Manual BEGIN/COMMIT/ROLLBACK. NOTE: don't use this around
    `executescript` — that runs its own implicit commits and can
    leave the connection without an open transaction by the time
    you try to ROLLBACK.
    """
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


_NOOP_MIGRATION_FRAGMENTS = ("duplicate column name", "already exists")


def _is_already_applied_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(frag in msg for frag in _NOOP_MIGRATION_FRAGMENTS)


def migrate(cfg: Config) -> list[str]:
    conn = connect(cfg)
    conn.executescript(SCHEMA_PATH.read_text())
    applied: list[str] = []
    if MIGRATIONS_DIR.exists():
        already = {row["name"] for row in conn.execute("SELECT name FROM migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in already:
                continue
            try:
                conn.executescript(path.read_text())
                conn.execute("INSERT INTO migrations(name) VALUES (?)", (path.name,))
                applied.append(path.name)
            except sqlite3.OperationalError as e:
                if _is_already_applied_error(e):
                    conn.execute("INSERT OR IGNORE INTO migrations(name) VALUES (?)",
                                 (path.name,))
                else:
                    raise
    conn.close()
    return applied
