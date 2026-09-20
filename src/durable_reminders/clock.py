"""Injectable clock.

Every read of "now" in the system goes through a Clock. Production uses
SystemClock. Tests use FakeClock, whose state is stored in a ClockStore so
that a process restart does not silently reset simulated time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """Source of the current time. Always returns timezone-aware UTC."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        raise NotImplementedError


class ClockStore(Protocol):
    """Persistence seam for FakeClock.

    Tests use InMemoryClockStore. Restart tests and the benchmark use
    SQLiteClockStore so simulated time survives a process restart.
    """

    def get(self) -> datetime | None:
        """Return the stored time, or None if none has been set."""
        raise NotImplementedError

    def set(self, dt: datetime) -> None:
        """Persist a timezone-aware datetime."""
        raise NotImplementedError

class SystemClock:
    """Real wall clock. Always returns UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class InMemoryClockStore:
    """ClockStore for tests that do not need restart persistence."""

    def __init__(self, initial: datetime | None = None) -> None:
        self._value = initial

    def get(self) -> datetime | None:
        return self._value

    def set(self, dt: datetime) -> None:
        self._value = dt


@dataclass
class FakeClock:
    """Deterministic clock for tests and the benchmark.

    Time only moves when ``advance`` or ``set`` is called. State lives in the
    supplied store so it can survive a simulated restart.
    """

    store: ClockStore

    def now(self) -> datetime:
        value = self.store.get()
        if value is None:
            raise RuntimeError("FakeClock has no time set; call set() first")
        return value

    def set(self, dt: datetime) -> None:
        if dt.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self.store.set(dt.astimezone(timezone.utc))

    def advance(
        self,
        *,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> datetime:
        delta = timedelta(seconds=seconds, minutes=minutes,
                          hours=hours, days=days)
        new = self.now() + delta
        self.store.set(new)
        return new
