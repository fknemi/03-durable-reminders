"""Basic service behavior: create, get, edit, cancel, terminals."""

from __future__ import annotations

import pytest

from durable_reminders.errors import AlreadyTerminal, NotFound
from durable_reminders.models import ReminderStatus
from durable_reminders.service import (
    cancel_reminder,
    create_reminder,
    edit_reminder,
    get_reminder,
    list_reminders,
)


def test_create_and_get(conn, clock):
    r = create_reminder(
        conn, clock,
        content="call mom",
        fire_at_local="2024-11-03T01:30:00",
        tz_name="America/New_York",
    )
    assert r.version == 1
    assert r.status == ReminderStatus.SCHEDULED
    assert r.delivery_key == f"{r.id}:v1"
    assert r.tz_policy == "ambiguous_first_occurrence"

    got = get_reminder(conn, r.id)
    assert got.id == r.id
    assert got.content == "call mom"


def test_get_missing_raises(conn):
    with pytest.raises(NotFound):
        get_reminder(conn, "nope")


def test_edit_bumps_version_and_reschedules(conn, clock):
    r = create_reminder(
        conn, clock,
        content="v1",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    edited = edit_reminder(conn, clock, r.id, content="v2")
    assert edited.version == 2
    assert edited.content == "v2"
    assert edited.delivery_key == f"{r.id}:v2"
    assert edited.status == ReminderStatus.SCHEDULED


def test_cancel_sets_terminal(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    c = cancel_reminder(conn, clock, r.id)
    assert c.status == ReminderStatus.CANCELLED
    assert c.cancelled_at is not None


def test_edit_after_cancel_rejected(conn, clock):
    r = create_reminder(
        conn, clock,
        content="hi",
        fire_at_local="2024-01-01T00:00:05",
        tz_name="UTC",
    )
    cancel_reminder(conn, clock, r.id)
    with pytest.raises(AlreadyTerminal):
        edit_reminder(conn, clock, r.id, content="nope")


def test_list(conn, clock):
    a = create_reminder(conn, clock, content="a",
                        fire_at_local="2024-01-01T00:00:05", tz_name="UTC")
    b = create_reminder(conn, clock, content="b",
                        fire_at_local="2024-01-01T00:00:05", tz_name="UTC")
    ids = {r.id for r in list_reminders(conn)}
    assert ids == {a.id, b.id}
