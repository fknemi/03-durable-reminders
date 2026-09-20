"""AC2: restart recovery and lease expiry."""

from __future__ import annotations

from durable_reminders.destination import FakeDestination
from durable_reminders.models import ReminderStatus
from durable_reminders.service import create_reminder, get_reminder
from durable_reminders.worker import claim_due, reap_expired, tick


def test_overdue_work_is_picked_up_after_restart(conn, clock):
    """Service is stopped while a reminder becomes due; on restart it fires."""
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    # No worker running. Time passes past the due instant.
    clock.advance(seconds=3600)
    assert get_reminder(conn, r.id).status == ReminderStatus.SCHEDULED

    # "Restart": a fresh tick discovers the overdue work.
    result = tick(conn, clock, FakeDestination(conn))
    assert result.claimed == 1
    assert result.delivered == 1
    assert get_reminder(conn, r.id).status == ReminderStatus.DELIVERED


def test_lease_expiry_resets_running_row(conn, clock):
    """A worker that claimed and died leaves a running row. The reaper
    returns it to the queue."""
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)

    claimed = claim_due(conn, clock)
    assert len(claimed) == 1
    assert get_reminder(conn, r.id).status == ReminderStatus.RUNNING

    # Simulate death: no commit. Advance past the lease.
    clock.advance(seconds=60)
    reaped = reap_expired(conn, clock)
    assert reaped == 1
    assert get_reminder(conn, r.id).status == ReminderStatus.SCHEDULED

    # Next tick delivers it.
    result = tick(conn, clock, FakeDestination(conn))
    assert result.delivered == 1


def test_reap_ignores_live_lease(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    claim_due(conn, clock)
    # Only 5s passed; lease is 30s.
    clock.advance(seconds=5)
    assert reap_expired(conn, clock) == 0
    assert get_reminder(conn, r.id).status == ReminderStatus.RUNNING
