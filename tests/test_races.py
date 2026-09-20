"""AC5 and AC6: edit and cancel racing execution."""

from __future__ import annotations

from durable_reminders.destination import DeliveryOutcome, FakeDestination
from durable_reminders.models import ReminderStatus
from durable_reminders.service import (
    cancel_reminder,
    create_reminder,
    edit_reminder,
    get_reminder,
)
from durable_reminders.worker import claim_due, commit_delivery, tick


def test_edit_before_execution_old_version_superseded(conn, clock):
    r = create_reminder(
        conn, clock,
        content="v1",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)

    claimed = claim_due(conn, clock)
    assert len(claimed) == 1

    edit_reminder(conn, clock, r.id, content="v2")

    outcome = commit_delivery(conn, clock, FakeDestination(conn), claimed[0])
    assert outcome == "superseded"

    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.SCHEDULED
    assert got.version == 2
    assert got.content == "v2"

    dcount = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert dcount == 0


def test_cancel_during_execution_superseded(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    claimed = claim_due(conn, clock)
    cancel_reminder(conn, clock, r.id)

    outcome = commit_delivery(conn, clock, FakeDestination(conn), claimed[0])
    assert outcome == "superseded"

    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.CANCELLED
    dcount = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert dcount == 0

    # Later ticks must not resurrect it.
    clock.advance(seconds=120)
    result = tick(conn, clock, FakeDestination(conn))
    assert result.claimed == 0
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


def test_cancel_after_delivery_is_rejected(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    tick(conn, clock, FakeDestination(conn))
    assert get_reminder(conn, r.id).status == ReminderStatus.DELIVERED

    from durable_reminders.errors import AlreadyTerminal
    import pytest
    with pytest.raises(AlreadyTerminal):
        cancel_reminder(conn, clock, r.id)


def test_edit_reschedules_and_delivers_new_version(conn, clock):
    r = create_reminder(
        conn, clock,
        content="v1",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    clock.advance(seconds=10)
    edit_reminder(
        conn, clock, r.id,
        content="v2",
        fire_at_local="2024-01-01T00:01:00",
    )
    # Not due yet at the new time.
    assert tick(conn, clock, FakeDestination(conn)).claimed == 0

    clock.advance(seconds=60)
    result = tick(conn, clock, FakeDestination(conn))
    assert result.delivered == 1
    got = get_reminder(conn, r.id)
    assert got.status == ReminderStatus.DELIVERED
    assert got.content == "v2"
    assert got.version == 2
    # One row in deliveries, for v2 only.
    rows = conn.execute(
        "SELECT delivery_key FROM deliveries"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["delivery_key"] == f"{r.id}:v2"
