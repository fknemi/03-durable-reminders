"""Shared fixtures: fresh DB, FakeClock, seeded initial time."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from durable_reminders.clock import FakeClock, InMemoryClockStore
from durable_reminders.db import init_schema, connect

BASE_TIME = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def clock() -> FakeClock:
    store = InMemoryClockStore(initial=BASE_TIME)
    return FakeClock(store=store)
