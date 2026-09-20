"""The verification benchmark.

Seeds 24 reminders across two IANA time zones with a mix of delivered,
edited, cancelled, temporarily failing, and permanently failing items.
Stops and restarts the service (new connection) before processing all due
work. Simulates duplicate execution on one occurrence. Advances the clock
until processing settles. Asserts and prints invariants.

Exit code is non-zero if any invariant is violated.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure src is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from durable_reminders.clock import FakeClock, InMemoryClockStore  # noqa: E402
from durable_reminders.db import connect, init_schema  # noqa: E402
from durable_reminders.destination import (  # noqa: E402
    DeliveryOutcome,
    FakeDestination,
)
from durable_reminders.models import ReminderStatus  # noqa: E402
from durable_reminders.service import (  # noqa: E402
    cancel_reminder,
    create_reminder,
    edit_reminder,
    get_reminder,
    list_reminders,
)
from durable_reminders.worker import reap_expired, tick  # noqa: E402

BASE = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_conn(path: str) -> sqlite3.Connection:
    conn = connect(path)
    init_schema(conn)
    return conn


def run(db_path: str, verbose: bool = True) -> int:
    clock = FakeClock(store=InMemoryClockStore(initial=BASE))

    # ---------- Phase 1: seed 24 reminders, 2 TZs ----------
    conn = make_conn(db_path)
    failures: dict[str, list[DeliveryOutcome]] = {}

    delivered_ids = []
    edited_ids = []
    cancelled_ids = []
    tempfail_ids = []
    permfail_ids = []
    duplicate_ids = []

    # 10 delivered: 5 Kolkata, 5 New York
    for i in range(5):
        r = create_reminder(
            conn, clock, content=f"kolkata-{i}",
            fire_at_local="2024-01-01T13:00:00",
            tz_name="Asia/Kolkata",
        )
        delivered_ids.append(r.id)
    for i in range(5):
        r = create_reminder(
            conn, clock, content=f"newyork-{i}",
            fire_at_local="2024-01-01T08:00:00",
            tz_name="America/New_York",
        )
        delivered_ids.append(r.id)

    # 3 edited
    for i in range(3):
        r = create_reminder(
            conn, clock, content=f"edit-me-{i}",
            fire_at_local="2024-01-01T13:00:00",
            tz_name="Asia/Kolkata",
        )
        edited_ids.append(r.id)

    # 3 cancelled
    for i in range(3):
        r = create_reminder(
            conn, clock, content=f"cancel-me-{i}",
            fire_at_local="2024-01-01T13:00:00",
            tz_name="Asia/Kolkata",
        )
        cancelled_ids.append(r.id)

    # 5 temporary failure then success
    for i in range(5):
        r = create_reminder(
            conn, clock, content=f"tempfail-{i}",
            fire_at_local="2024-01-01T13:00:00",
            tz_name="Asia/Kolkata",
        )
        failures[r.delivery_key] = [DeliveryOutcome.TEMPORARY_FAILURE] * 2
        tempfail_ids.append(r.id)

    # 2 permanent failure
    for i in range(2):
        r = create_reminder(
            conn, clock, content=f"permfail-{i}",
            fire_at_local="2024-01-01T13:00:00",
            tz_name="Asia/Kolkata",
        )
        failures[r.delivery_key] = [DeliveryOutcome.PERMANENT_FAILURE]
        permfail_ids.append(r.id)

    # 1 duplicate: same as delivered but we will replay the destination call
    r = create_reminder(
        conn, clock, content="duplicate-me",
        fire_at_local="2024-01-01T13:00:00",
        tz_name="Asia/Kolkata",
    )
    duplicate_ids.append(r.id)

    assert len(list_reminders(conn)) == 24

    # Apply edits and cancels before any tick.
    for rid in edited_ids:
        edit_reminder(
            conn, clock, rid,
            content="edited", fire_at_local="2024-01-01T13:30:00",
        )
    for rid in cancelled_ids:
        cancel_reminder(conn, clock, rid)

    # ---------- Phase 2: stop the service, advance past due, "restart" ----------
    conn.close()
    clock.advance(hours=2)

    conn = make_conn(db_path)  # restart: new connection, same DB

    # ---------- Phase 3: simulate duplicate execution on one occurrence ----------
    dest = FakeDestination(conn, failure_script=failures)
    dup = get_reminder(conn, duplicate_ids[0])
    first = dest.attempt(
        delivery_key=dup.delivery_key, reminder_id=dup.id,
        version=dup.version, content=dup.content, now=clock.now(),
    )
    second = dest.attempt(
        delivery_key=dup.delivery_key, reminder_id=dup.id,
        version=dup.version, content=dup.content, now=clock.now(),
    )
    assert first == DeliveryOutcome.SUCCESS, first
    assert second == DeliveryOutcome.DUPLICATE, second

    # ---------- Phase 4: advance the clock until processing settles ----------
    steps = 0
    max_steps = 200
    while steps < max_steps:
        reap_expired(conn, clock)
        tick(conn, clock, dest, limit=100)
        active = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE status IN (?, ?)",
            (ReminderStatus.SCHEDULED.value, ReminderStatus.RUNNING.value),
        ).fetchone()[0]
        if active == 0:
            break
        clock.advance(seconds=30)
        steps += 1
    assert steps < max_steps, "processing did not settle"

    # ---------- Phase 5: assert invariants and report ----------
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM reminders GROUP BY status"
    ).fetchall())
    deliveries = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]

    print("\n--- benchmark results ---")
    print(f"steps advanced:       {steps}")
    print(f"reminders by status:  {counts}")
    print(f"logical notifications: {deliveries}")

    ok = True

    # Exactly 10 base delivered + 3 edited + 5 tempfail + 1 duplicate = 19.
    # Permfail = 2 failed; cancelled = 3 cancelled.
    expected_delivered = 10 + 3 + 5 + 1
    if counts.get(ReminderStatus.DELIVERED.value, 0) != expected_delivered:
        print(f"FAIL: delivered count {counts.get('delivered')} != {expected_delivered}")
        ok = False
    if counts.get(ReminderStatus.CANCELLED.value, 0) != 3:
        print("FAIL: cancelled count != 3")
        ok = False
    if counts.get(ReminderStatus.FAILED.value, 0) != 2:
        print("FAIL: failed count != 2")
        ok = False

    if deliveries != expected_delivered:
        print(f"FAIL: logical notifications {deliveries} != {expected_delivered}")
        ok = False

    # No cancelled reminder produced a notification.
    bad = conn.execute(
        "SELECT COUNT(*) FROM deliveries d JOIN reminders r "
        "ON d.reminder_id = r.id WHERE r.status = ?",
        (ReminderStatus.CANCELLED.value,),
    ).fetchone()[0]
    if bad:
        print(f"FAIL: {bad} cancelled reminders produced notifications")
        ok = False

    # Every failed reminder must have either:
    #   - a single permanent_failure attempt (permanent path), or
    #   - exactly max_attempts temporary_failure attempts (exhaustion path).
    for r in conn.execute(
        "SELECT id, max_attempts FROM reminders WHERE status = ?",
        (ReminderStatus.FAILED.value,),
    ).fetchall():
        attempts = conn.execute(
            "SELECT outcome FROM delivery_attempts WHERE reminder_id = ? "
            "ORDER BY attempt_number",
            (r["id"],),
        ).fetchall()
        outcomes = [a["outcome"] for a in attempts]
        n = len(outcomes)

        permanent_path = n == 1 and outcomes == ["permanent_failure"]
        exhaustion_path = (
            n == r["max_attempts"]
            and all(o == "temporary_failure" for o in outcomes)
        )
        if not (permanent_path or exhaustion_path):
            print(
                f"FAIL: failed {r['id']} has {n} attempts "
                f"{outcomes}, expected either one permanent_failure or "
                f"{r['max_attempts']} temporary_failures"
            )
            ok = False
    # Every delivered reminder has >=1 success attempt and exactly 1 delivery row.
    for r in conn.execute(
        "SELECT id FROM reminders WHERE status = ?",
        (ReminderStatus.DELIVERED.value,),
    ).fetchall():
        d = conn.execute(
            "SELECT COUNT(*) FROM deliveries WHERE reminder_id = ?",
            (r["id"],),
        ).fetchone()[0]
        s = conn.execute(
            "SELECT COUNT(*) FROM delivery_attempts "
            "WHERE reminder_id = ? AND outcome = 'success'",
            (r["id"],),
        ).fetchone()[0]
        if d != 1 or s < 1:
            print(f"FAIL: delivered {r['id']} deliveries={d} successes={s}")
            ok = False

    print("RESULT:", "PASS" if ok else "FAIL")
    conn.close()
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    if args.db:
        path = args.db
        tmp = None
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        path = tmp.name

    try:
        code = run(path)
    finally:
        if tmp is not None:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass
    sys.exit(code)


if __name__ == "__main__":
    main()
