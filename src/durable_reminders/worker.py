"""Due-work discovery, delivery, commit, and lease reaping.

The three moves that matter:

  claim_due    — atomic UPDATE ... RETURNING inside BEGIN IMMEDIATE
  commit_delivery — re-reads version and status inside the commit
                    transaction, so an edit or cancel that landed during
                    delivery wins the race
  reap_expired — resets running rows whose lease has expired, so a crashed
                 worker's work returns to the queue

Everything durable about the system is visible here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from .clock import Clock
from .db import immediate
from .destination import DeliveryDestination, DeliveryOutcome
from .models import ReminderStatus

LEASE_SECONDS = 30
# attempt_number (1-indexed) -> delay in seconds before next attempt
BACKOFF_SECONDS = [1, 5, 30, 120, 600]


def _backoff(attempt_number: int) -> int:
    idx = min(attempt_number - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[idx]


@dataclass
class TickResult:
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    superseded: int = 0
    reaped: int = 0


def claim_due(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    limit: int = 10,
) -> list[sqlite3.Row]:
    """Atomically claim up to `limit` due reminders.

    Returns the rows after the update, so callers can read version,
    delivery_key, and content without a second query.
    """
    now = clock.now()
    lease_until = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
    iso_now = now.isoformat()
    with immediate(conn):
        rows = conn.execute(
            """UPDATE reminders
               SET status = ?,
                   claimed_version = version,
                   lease_until = ?,
                   attempt_count = attempt_count + 1,
                   updated_at = ?
               WHERE id IN (
                   SELECT id FROM reminders
                   WHERE status = ?
                     AND next_attempt_at <= ?
                   ORDER BY next_attempt_at
                   LIMIT ?
               )
               RETURNING *""",
            (
                ReminderStatus.RUNNING.value,
                lease_until, iso_now,
                ReminderStatus.SCHEDULED.value, iso_now,
                limit,
            ),
        ).fetchall()
    return rows


def commit_delivery(
    conn: sqlite3.Connection,
    clock: Clock,
    destination: DeliveryDestination,
    claimed_row: sqlite3.Row,
) -> str:
    """Commit the outcome of one delivery attempt.

    Returns one of: "delivered", "retried", "failed", "superseded".
    """
    rid = claimed_row["id"]
    claimed_version = claimed_row["version"]
    delivery_key = claimed_row["delivery_key"]
    content = claimed_row["content"]
    now = clock.now()
    iso_now = now.isoformat()

    with immediate(conn):
        fresh = conn.execute(
            "SELECT version, status, attempt_count, max_attempts "
            "FROM reminders WHERE id = ?",
            (rid,),
        ).fetchone()

        # Edit or cancel raced with us: version or status changed under our feet.
        if (
            fresh["version"] != claimed_version
            or fresh["status"] != ReminderStatus.RUNNING.value
        ):
            conn.execute(
                """INSERT INTO delivery_attempts
                   (reminder_id, version, attempt_number, attempted_at,
                    outcome, delivery_key)
                   VALUES (?, ?, ?, ?, 'superseded', ?)""",
                (rid, claimed_version, fresh["attempt_count"],
                 iso_now, delivery_key),
            )
            # Only reset to scheduled if we still own the lease; if the edit
            # or cancel already moved us, this is a harmless no-op.
            conn.execute(
                """UPDATE reminders
                   SET status = ?, lease_until = NULL,
                       claimed_version = NULL, updated_at = ?
                   WHERE id = ? AND status = ?""",
                (ReminderStatus.SCHEDULED.value, iso_now,
                 rid, ReminderStatus.RUNNING.value),
            )
            return "superseded"

        outcome = destination.attempt(
            delivery_key=delivery_key,
            reminder_id=rid,
            version=claimed_version,
            content=content,
            now=now,
        )

        if outcome in (DeliveryOutcome.SUCCESS, DeliveryOutcome.DUPLICATE):
            conn.execute(
                """INSERT INTO delivery_attempts
                   (reminder_id, version, attempt_number, attempted_at,
                    outcome, delivery_key)
                   VALUES (?, ?, ?, ?, 'success', ?)""",
                (rid, claimed_version, fresh["attempt_count"],
                 iso_now, delivery_key),
            )
            conn.execute(
                """UPDATE reminders SET
                    status = ?, delivered_at = ?,
                    lease_until = NULL, claimed_version = NULL,
                    last_error = NULL, updated_at = ?
                   WHERE id = ?""",
                (ReminderStatus.DELIVERED.value, iso_now, iso_now, rid),
            )
            return "delivered"

        # Failure. Record the attempt regardless.
        conn.execute(
            """INSERT INTO delivery_attempts
               (reminder_id, version, attempt_number, attempted_at,
                outcome, error, delivery_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rid, claimed_version, fresh["attempt_count"],
             iso_now, outcome.value, outcome.value, delivery_key),
        )

        permanent = outcome == DeliveryOutcome.PERMANENT_FAILURE
        exhausted = fresh["attempt_count"] >= fresh["max_attempts"]

        if permanent or exhausted:
            conn.execute(
                """UPDATE reminders SET
                    status = ?, last_error = ?,
                    lease_until = NULL, claimed_version = NULL,
                    updated_at = ?
                   WHERE id = ?""",
                (ReminderStatus.FAILED.value, outcome.value, iso_now, rid),
            )
            return "failed"

        next_at = (
            now + timedelta(seconds=_backoff(fresh["attempt_count"]))
        ).isoformat()
        conn.execute(
            """UPDATE reminders SET
                status = ?, next_attempt_at = ?, last_error = ?,
                lease_until = NULL, claimed_version = NULL,
                updated_at = ?
               WHERE id = ?""",
            (ReminderStatus.SCHEDULED.value, next_at, outcome.value,
             iso_now, rid),
        )
        return "retried"


def reap_expired(conn: sqlite3.Connection, clock: Clock) -> int:
    """Reset running rows whose lease has expired back to scheduled."""
    iso_now = clock.now().isoformat()
    with immediate(conn):
        cur = conn.execute(
            """UPDATE reminders SET
                status = ?, lease_until = NULL,
                claimed_version = NULL, updated_at = ?
               WHERE status = ? AND lease_until < ?""",
            (ReminderStatus.SCHEDULED.value, iso_now,
             ReminderStatus.RUNNING.value, iso_now),
        )
    return cur.rowcount


def tick(
    conn: sqlite3.Connection,
    clock: Clock,
    destination: DeliveryDestination,
    *,
    limit: int = 10,
) -> TickResult:
    """One full worker cycle: reap, claim, deliver/commit."""
    result = TickResult()
    result.reaped = reap_expired(conn, clock)
    claimed = claim_due(conn, clock, limit=limit)
    result.claimed = len(claimed)
    for row in claimed:
        outcome = commit_delivery(conn, clock, destination, row)
        if outcome == "delivered":
            result.delivered += 1
        elif outcome == "retried":
            result.retried += 1
        elif outcome == "failed":
            result.failed += 1
        elif outcome == "superseded":
            result.superseded += 1
    return result
