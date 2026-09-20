"""AC4: duplicate execution produces one logical notification."""

from __future__ import annotations

from durable_reminders.destination import DeliveryOutcome, FakeDestination
from durable_reminders.models import ReminderStatus
from durable_reminders.service import create_reminder, get_reminder
from durable_reminders.worker import claim_due, reap_expired, tick


def test_crash_after_destination_before_commit(conn, clock):
    """The classic dual-write failure. Destination accepted the send, the
    worker died before committing. On recovery the send is replayed, the
    destination detects the duplicate, and the user sees one notification."""
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    dest = FakeDestination(conn)

    claimed = claim_due(conn, clock)
    assert len(claimed) == 1

    # Destination side effect happens; then we "crash" (no commit).
    first = dest.attempt(
        delivery_key=r.delivery_key, reminder_id=r.id,
        version=1, content="hi", now=clock.now(),
    )
    assert first == DeliveryOutcome.SUCCESS
    assert get_reminder(conn, r.id).status == ReminderStatus.RUNNING

    # Lease expires, reaper returns it to scheduled.
    clock.advance(seconds=60)
    assert reap_expired(conn, clock) == 1

    # Recovery tick: claims again, retries the send.
    result = tick(conn, clock, dest)
    assert result.delivered == 1

    # Exactly one row in the ledger.
    dcount = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert dcount == 1

    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.DELIVERED
    # Two attempts recorded: the crashed one was never recorded (we never
    # committed it), so we see exactly one success from the recovery tick.
    assert len(got.attempts) == 1
    assert got.attempts[0].outcome == "success"


def test_direct_duplicate_returns_duplicate_outcome(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    dest = FakeDestination(conn)

    first = dest.attempt(
        delivery_key=r.delivery_key, reminder_id=r.id,
        version=1, content="hi", now=clock.now(),
    )
    second = dest.attempt(
        delivery_key=r.delivery_key, reminder_id=r.id,
        version=1, content="hi", now=clock.now(),
    )
    assert first == DeliveryOutcome.SUCCESS
    assert second == DeliveryOutcome.DUPLICATE
    dcount = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert dcount == 1
