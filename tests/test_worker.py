"""AC1 and AC3: due discovery, temporary failure, retry exhaustion."""

from __future__ import annotations

from durable_reminders.destination import DeliveryOutcome, FakeDestination
from durable_reminders.models import ReminderStatus
from durable_reminders.service import create_reminder, get_reminder
from durable_reminders.worker import tick


def test_due_discovery_delivers_once(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    assert r.status == ReminderStatus.SCHEDULED

    result = tick(conn, clock, FakeDestination(conn))
    assert result.claimed == 0  # not due yet

    clock.advance(seconds=10)
    result = tick(conn, clock, FakeDestination(conn))
    assert result.claimed == 1
    assert result.delivered == 1

    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.DELIVERED
    assert got.delivered_at is not None
    assert len(got.attempts) == 1
    assert got.attempts[0].outcome == "success"

    # Running a tick again must not deliver a second time.
    result = tick(conn, clock, FakeDestination(conn))
    assert result.claimed == 0
    dcount = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert dcount == 1


def test_temporary_failure_retries_then_succeeds(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)

    dest = FakeDestination(conn, failure_script={
        r.delivery_key: [
            DeliveryOutcome.TEMPORARY_FAILURE,
            DeliveryOutcome.TEMPORARY_FAILURE,
        ],
    })

    result = tick(conn, clock, dest)
    assert result.retried == 1
    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.SCHEDULED
    assert got.attempt_count == 1

    clock.advance(seconds=2)  # past 1s backoff
    result = tick(conn, clock, dest)
    assert result.retried == 1
    assert get_reminder(conn, r.id).attempt_count == 2

    clock.advance(seconds=6)  # past 5s backoff
    result = tick(conn, clock, dest)
    assert result.delivered == 1

    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.DELIVERED
    outcomes = [a.outcome for a in got.attempts]
    assert outcomes == ["temporary_failure", "temporary_failure", "success"]


def test_retry_exhaustion_reaches_failed(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
        max_attempts=3,
    )
    clock.advance(seconds=10)

    dest = FakeDestination(conn, failure_script={
        r.delivery_key: [DeliveryOutcome.TEMPORARY_FAILURE] * 3,
    })

    tick(conn, clock, dest)
    clock.advance(seconds=2)
    tick(conn, clock, dest)
    clock.advance(seconds=6)
    result = tick(conn, clock, dest)

    assert result.failed == 1
    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.FAILED
    assert len(got.attempts) == 3
    assert all(a.outcome == "temporary_failure" for a in got.attempts)


def test_permanent_failure_goes_terminal_immediately(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    dest = FakeDestination(conn, failure_script={
        r.delivery_key: [DeliveryOutcome.PERMANENT_FAILURE],
    })
    result = tick(conn, clock, dest)
    assert result.failed == 1
    assert get_reminder(conn, r.id).status == ReminderStatus.FAILED
