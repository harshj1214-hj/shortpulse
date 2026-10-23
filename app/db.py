"""SQLite access. One connection per request (flask.g) plus one for the click
worker. Connections run in autocommit mode; multi-statement writes use
`transaction()` which issues BEGIN IMMEDIATE (takes the write lock up front,
so two writers can't deadlock trying to upgrade read locks)."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


# Columns added after the first release. CREATE TABLE IF NOT EXISTS doesn't
# alter existing tables, so older databases get them here.
MIGRATIONS = {
    "users": [("status", "TEXT NOT NULL DEFAULT 'active'"), ("last_login_at", "INTEGER")],
    "rules": [("open_in_app", "INTEGER NOT NULL DEFAULT 1"), ("track_engagement", "INTEGER NOT NULL DEFAULT 0")],
    "click_events": [("click_uid", "TEXT"), ("ip_address", "TEXT"), ("ip_masked", "TEXT NOT NULL DEFAULT ''"),
                     ("app_switch", "INTEGER NOT NULL DEFAULT 0"), ("engaged_ms", "INTEGER"), ("scroll_pct", "INTEGER"),
                     ("engaged_at", "INTEGER"), ("landing", "INTEGER NOT NULL DEFAULT 0"), ("outbound", "TEXT")],
}
POST_MIGRATION_SQL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_clicks_uid ON click_events(click_uid) WHERE click_uid IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_clicks_ip ON click_events(ts) WHERE ip_address IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_clicks_live ON click_events(engaged_at) WHERE engaged_at IS NOT NULL",
]


def init_schema(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
        conn.executescript(SCHEMA.read_text())
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        for sql in POST_MIGRATION_SQL:
            conn.execute(sql)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
