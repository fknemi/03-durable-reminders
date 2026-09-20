"""IANA timezone resolution with documented DST policies.

Policy for ambiguous local times (DST fall-back): choose the first
occurrence, i.e. the pre-transition (DST) offset, which is `fold=0`.

Policy for nonexistent local times (DST spring-forward gap): shift forward
by the gap. `zoneinfo` uses the pre-transition offset for a gap time, which
produces exactly the shifted-forward UTC instant.

Both policies are tested against America/New_York on 2024-11-03 (ambiguous)
and 2024-03-10 (nonexistent).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import InvalidTimezone


@dataclass(frozen=True)
class ResolvedInstant:
    utc: datetime
    policy: str  # "unambiguous" | "ambiguous_first_occurrence" | "nonexistent_shift_forward"
    local_requested: str
    timezone: str


def load_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezone(f"unknown IANA timezone: {tz_name!r}") from exc


def resolve_instant(local_naive: datetime, tz_name: str) -> ResolvedInstant:
    """Resolve a naive local datetime in `tz_name` to a UTC instant.

    `local_naive` must be timezone-naive; its tzinfo is ignored.
    """
    if local_naive.tzinfo is not None:
        raise ValueError("resolve_instant expects a naive local datetime")

    tz = load_zone(tz_name)
    local_requested = local_naive.isoformat(timespec="seconds")

    # fold=0 is the pre-transition offset.
    local_0 = local_naive.replace(tzinfo=tz, fold=0)
    utc_0 = local_0.astimezone(timezone.utc)

    # Existence check: round-trip back to local. If the wall clock differs,
    # the input time does not exist (spring-forward gap). utc_0 already
    # represents the shifted-forward instant.
    round_trip = utc_0.astimezone(tz).replace(tzinfo=None)
    if round_trip != local_naive:
        return ResolvedInstant(
            utc=utc_0,
            policy="nonexistent_shift_forward",
            local_requested=local_requested,
            timezone=tz_name,
        )

    # Ambiguity check: fold=1 gives the post-transition offset. If the two
    # instants differ, the wall clock repeats (fall-back).
    local_1 = local_naive.replace(tzinfo=tz, fold=1)
    utc_1 = local_1.astimezone(timezone.utc)
    if utc_0 != utc_1:
        return ResolvedInstant(
            utc=utc_0,
            policy="ambiguous_first_occurrence",
            local_requested=local_requested,
            timezone=tz_name,
        )

    return ResolvedInstant(
        utc=utc_0,
        policy="unambiguous",
        local_requested=local_requested,
        timezone=tz_name,
    )
