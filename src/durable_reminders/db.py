"""SQLite storage: schema, connection, transactions, and the clock store."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS reminders (
    id               TEXT PRIMARY KEY,
    content          TEXT NOT NULL,
    timezone         TEXT NOT NULL,
    local_requested  TEXT NOT NULL,
    scheduled_at_utc TEXT NOT NULL,
    tz_policy        TEXT NOT NULL,
    status           TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    delivery_key     TEXT NOT NULL,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 5,
    next_attempt_at  TEXT,
    lease_until      TEXT,
    claimed_version  INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    cancelled_at     TEXT,
    delivered_at     TEXT,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_reminders_due
    ON reminders(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id    TEXT NOT NULL,
    version        INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    attempted_at   TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    error          TEXT,
    delivery_key   TEXT NOT NULL,
    FOREIGN KEY (reminder_id) REFERENCES reminders(id)
);

CREATE INDEX IF NOT EXISTS idx_attempts_reminder
    ON delivery_attempts(reminder_id, version, attempt_number);

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_key TEXT PRIMARY KEY,
    reminder_id  TEXT NOT NULL,
    version      INTEGER NOT NULL,
    content      TEXT NOT NULL,
    delivered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clock_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    current_time TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE transaction. Acquires the write lock up front,
    which is what makes the claim-and-update in worker.py atomic."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteClockStore:
    """ClockStore backed by the clock_state table.

    Used by FakeClock in restart tests and in the benchmark so that simulated
    time survives a process restart.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self) -> datetime | None:
        row = self._conn.execute(
            "SELECT current_time FROM clock_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["current_time"])

    def set(self, dt: datetime) -> None:
        self._conn.execute(
            "INSERT INTO clock_state(id, current_time) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET current_time = excluded.current_time",
            (dt.isoformat(),),
        )
