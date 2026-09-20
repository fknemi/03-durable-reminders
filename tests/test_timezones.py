"""Timezone resolution: unambiguous, ambiguous (DST fall-back), and
nonexistent (DST spring-forward) cases across two IANA zones."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from durable_reminders.errors import InvalidTimezone
from durable_reminders.timezones import resolve_instant


def test_kolkata_is_unambiguous():
    # Asia/Kolkata has no DST.
    local = datetime(2024, 6, 15, 9, 0, 0)
    result = resolve_instant(local, "Asia/Kolkata")
    assert result.policy == "unambiguous"
    # 09:00 IST (UTC+05:30) -> 03:30 UTC
    assert result.utc == datetime(2024, 6, 15, 3, 30, tzinfo=timezone.utc)


def test_new_york_summer_is_unambiguous():
    local = datetime(2024, 7, 4, 9, 0, 0)
    result = resolve_instant(local, "America/New_York")
    assert result.policy == "unambiguous"
    # 09:00 EDT (UTC-04:00) -> 13:00 UTC
    assert result.utc == datetime(2024, 7, 4, 13, 0, tzinfo=timezone.utc)


def test_dst_fall_back_ambiguous_chooses_first_occurrence():
    # 2024-11-03 01:30 America/New_York happens twice:
    #   01:30 EDT (UTC-04:00) = 05:30 UTC  <- first occurrence, our choice
    #   01:30 EST (UTC-05:00) = 06:30 UTC
    local = datetime(2024, 11, 3, 1, 30, 0)
    result = resolve_instant(local, "America/New_York")
    assert result.policy == "ambiguous_first_occurrence"
    assert result.utc == datetime(2024, 11, 3, 5, 30, tzinfo=timezone.utc)


def test_dst_spring_forward_gap_shifts_forward():
    # 2024-03-10 02:30 America/New_York does not exist:
    #   the clock jumps from 02:00 EST to 03:00 EDT.
    # We shift forward by the gap: 02:30 -> 03:30 EDT = 07:30 UTC.
    local = datetime(2024, 3, 10, 2, 30, 0)
    result = resolve_instant(local, "America/New_York")
    assert result.policy == "nonexistent_shift_forward"
    assert result.utc == datetime(2024, 3, 10, 7, 30, tzinfo=timezone.utc)


def test_unknown_timezone_raises():
    with pytest.raises(InvalidTimezone):
        resolve_instant(datetime(2024, 1, 1, 0, 0), "Not/AZone")


def test_naive_input_required():
    aware = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        resolve_instant(aware, "UTC")
