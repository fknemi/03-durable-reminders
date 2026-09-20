"""Delivery destinations and the idempotency ledger.

The ledger lives in the `deliveries` table, keyed by delivery_key. Insert is
INSERT OR IGNORE, so a repeat attempt is a no-op that returns DUPLICATE
instead of creating a second row. That is what makes at-least-once sends
produce at most one logical notification.

A production adapter would call a real provider inside a transactional
outbox. The in-DB ledger is the honest simplification for this exercise and
is documented in docs/decisions.md.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class DeliveryOutcome(StrEnum):
    SUCCESS = "success"
    DUPLICATE = "duplicate"
    TEMPORARY_FAILURE = "temporary_failure"
    PERMANENT_FAILURE = "permanent_failure"


class DeliveryDestination(Protocol):
    def attempt(
        self,
        *,
        delivery_key: str,
        reminder_id: str,
        version: int,
        content: str,
        now: datetime,
    ) -> DeliveryOutcome: ...


class FakeDestination:
    """In-DB destination with a configurable failure script.

    `failure_script` maps a delivery_key to a list of outcomes to return on
    successive attempts. Once a key's list is exhausted, attempts succeed.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        failure_script: dict[str, list[DeliveryOutcome]] | None = None,
    ) -> None:
        self._conn = conn
        self._script = {k: list(v) for k, v in (failure_script or {}).items()}

    def attempt(
        self,
        *,
        delivery_key: str,
        reminder_id: str,
        version: int,
        content: str,
        now: datetime,
    ) -> DeliveryOutcome:
        script = self._script.get(delivery_key)
        if script:
            outcome = script.pop(0)
            if outcome in (
                DeliveryOutcome.TEMPORARY_FAILURE,
                DeliveryOutcome.PERMANENT_FAILURE,
            ):
                return outcome

        cur = self._conn.execute(
            """INSERT OR IGNORE INTO deliveries
               (delivery_key, reminder_id, version, content, delivered_at)
               VALUES (?, ?, ?, ?, ?)""",
            (delivery_key, reminder_id, version, content, now.isoformat()),
        )
        if cur.rowcount == 0:
            return DeliveryOutcome.DUPLICATE
        return DeliveryOutcome.SUCCESS
