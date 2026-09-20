"""Create, inspect, edit, and cancel reminders.

All mutations run inside BEGIN IMMEDIATE transactions. Editing or cancelling
bumps the version, which invalidates any in-flight claim on the old version.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from .clock import Clock
from .db import immediate
from .errors import AlreadyTerminal, NotFound
from .models import (
    DeliveryAttemptOut,
    ReminderOut,
    ReminderStatus,
    TERMINAL_STATUSES,
)
from .timezones import resolve_instant


def _fetch_row(conn: sqlite3.Connection, reminder_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"reminder {reminder_id!r} not found")
    return row


def _to_out(conn: sqlite3.Connection, row: sqlite3.Row) -> ReminderOut:
    attempts = [
        DeliveryAttemptOut(
            attempt_number=a["attempt_number"],
            attempted_at=datetime.fromisoformat(a["attempted_at"]),
            outcome=a["outcome"],
            error=a["error"],
        )
        for a in conn.execute(
            "SELECT attempt_number, attempted_at, outcome, error "
            "FROM delivery_attempts WHERE reminder_id = ? "
            "ORDER BY attempt_number, id",
            (row["id"],),
        ).fetchall()
    ]
    return ReminderOut(
        id=row["id"],
        content=row["content"],
        timezone=row["timezone"],
        local_requested=row["local_requested"],
        scheduled_at_utc=datetime.fromisoformat(row["scheduled_at_utc"]),
        tz_policy=row["tz_policy"],
        status=ReminderStatus(row["status"]),
        version=row["version"],
        delivery_key=row["delivery_key"],
        attempt_count=row["attempt_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        cancelled_at=(
            datetime.fromisoformat(row["cancelled_at"])
            if row["cancelled_at"] else None
        ),
        delivered_at=(
            datetime.fromisoformat(row["delivered_at"])
            if row["delivered_at"] else None
        ),
        last_error=row["last_error"],
        attempts=attempts,
    )


def create_reminder(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    content: str,
    fire_at_local: str,
    tz_name: str,
    max_attempts: int = 5,
) -> ReminderOut:
    local_naive = datetime.fromisoformat(fire_at_local)
    resolved = resolve_instant(local_naive, tz_name)
    now = clock.now()
    rid = uuid.uuid4().hex
    delivery_key = f"{rid}:v1"
    iso_now = now.isoformat()

    with immediate(conn):
        conn.execute(
            """INSERT INTO reminders (
                id, content, timezone, local_requested, scheduled_at_utc,
                tz_policy, status, version, delivery_key, attempt_count,
                max_attempts, next_attempt_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (
                rid, content, tz_name, resolved.local_requested,
                resolved.utc.isoformat(), resolved.policy,
                ReminderStatus.SCHEDULED.value, 1, delivery_key,
                max_attempts, resolved.utc.isoformat(),
                iso_now, iso_now,
            ),
        )
    return get_reminder(conn, rid)


def get_reminder(conn: sqlite3.Connection, reminder_id: str) -> ReminderOut:
    return _to_out(conn, _fetch_row(conn, reminder_id))


def list_reminders(conn: sqlite3.Connection) -> list[ReminderOut]:
    rows = conn.execute(
        "SELECT * FROM reminders ORDER BY created_at"
    ).fetchall()
    return [_to_out(conn, r) for r in rows]


def edit_reminder(
    conn: sqlite3.Connection,
    clock: Clock,
    reminder_id: str,
    *,
    content: str | None = None,
    fire_at_local: str | None = None,
    tz_name: str | None = None,
) -> ReminderOut:
    row = _fetch_row(conn, reminder_id)
    if ReminderStatus(row["status"]) in TERMINAL_STATUSES:
        raise AlreadyTerminal(
            f"cannot edit reminder in terminal state {row['status']!r}"
        )

    new_content = content if content is not None else row["content"]
    if fire_at_local is not None or tz_name is not None:
        local_str = fire_at_local if fire_at_local is not None else row["local_requested"]
        zone = tz_name if tz_name is not None else row["timezone"]
        resolved = resolve_instant(datetime.fromisoformat(local_str), zone)
        new_scheduled = resolved.utc
        new_local = resolved.local_requested
        new_zone = zone
        new_policy = resolved.policy
    else:
        new_scheduled = datetime.fromisoformat(row["scheduled_at_utc"])
        new_local = row["local_requested"]
        new_zone = row["timezone"]
        new_policy = row["tz_policy"]

    new_version = row["version"] + 1
    new_delivery_key = f"{reminder_id}:v{new_version}"
    now_iso = clock.now().isoformat()

    with immediate(conn):
        conn.execute(
            """UPDATE reminders SET
                content = ?, timezone = ?, local_requested = ?,
                scheduled_at_utc = ?, tz_policy = ?,
                status = ?, version = ?, delivery_key = ?,
                attempt_count = 0, max_attempts = max_attempts,
                next_attempt_at = ?,
                lease_until = NULL, claimed_version = NULL,
                last_error = NULL, updated_at = ?
               WHERE id = ?""",
            (
                new_content, new_zone, new_local,
                new_scheduled.isoformat(), new_policy,
                ReminderStatus.SCHEDULED.value, new_version, new_delivery_key,
                new_scheduled.isoformat(), now_iso, reminder_id,
            ),
        )
    return get_reminder(conn, reminder_id)


def cancel_reminder(
    conn: sqlite3.Connection,
    clock: Clock,
    reminder_id: str,
) -> ReminderOut:
    row = _fetch_row(conn, reminder_id)
    if ReminderStatus(row["status"]) in TERMINAL_STATUSES:
        raise AlreadyTerminal(
            f"cannot cancel reminder in terminal state {row['status']!r}"
        )
    new_version = row["version"] + 1
    now_iso = clock.now().isoformat()
    with immediate(conn):
        conn.execute(
            """UPDATE reminders SET
                status = ?, version = ?, cancelled_at = ?,
                lease_until = NULL, claimed_version = NULL,
                updated_at = ?
               WHERE id = ?""",
            (
                ReminderStatus.CANCELLED.value, new_version,
                now_iso, now_iso, reminder_id,
            ),
        )
    return get_reminder(conn, reminder_id)
