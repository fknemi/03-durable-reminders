"""REST surface. Thin: parses requests, calls service, returns responses.

Test-only routes let the benchmark advance the fake clock without going
through a wall-clock sleep.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from .errors import AlreadyTerminal, InvalidTimezone, NotFound
from .models import (
    CreateReminderRequest,
    EditReminderRequest,
    ReminderOut,
)
from .service import (
    cancel_reminder,
    create_reminder,
    edit_reminder,
    get_reminder,
    list_reminders,
)

router = APIRouter()


def _ctx(request: Request):
    return request.app.state.conn, request.app.state.clock


@router.post("/reminders", response_model=ReminderOut, status_code=201)
def create(payload: CreateReminderRequest, ctx=Depends(_ctx)):
    conn, clock = ctx
    try:
        return create_reminder(
            conn, clock,
            content=payload.content,
            fire_at_local=payload.fire_at_local,
            tz_name=payload.timezone,
        )
    except InvalidTimezone as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/reminders", response_model=list[ReminderOut])
def list_all(ctx=Depends(_ctx)):
    conn, _ = ctx
    return list_reminders(conn)


@router.get("/reminders/{rid}", response_model=ReminderOut)
def get(rid: str, ctx=Depends(_ctx)):
    conn, _ = ctx
    try:
        return get_reminder(conn, rid)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/reminders/{rid}", response_model=ReminderOut)
def edit(rid: str, payload: EditReminderRequest, ctx=Depends(_ctx)):
    conn, clock = ctx
    try:
        return edit_reminder(
            conn, clock, rid,
            content=payload.content,
            fire_at_local=payload.fire_at_local,
            tz_name=payload.timezone,
        )
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AlreadyTerminal as e:
        raise HTTPException(status_code=409, detail=str(e))
    except InvalidTimezone as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/reminders/{rid}", response_model=ReminderOut)
def cancel(rid: str, ctx=Depends(_ctx)):
    conn, clock = ctx
    try:
        return cancel_reminder(conn, clock, rid)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AlreadyTerminal as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/health")
def health(request: Request):
    conn = request.app.state.conn
    conn.execute("SELECT 1")
    return {"status": "ok"}


@router.get("/metrics")
def metrics(request: Request):
    conn = request.app.state.conn
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM reminders GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in rows}
    deliveries = conn.execute(
        "SELECT COUNT(*) AS n FROM deliveries"
    ).fetchone()["n"]
    return {
        "reminders_by_status": by_status,
        "logical_notifications": deliveries,
    }


# -------- Test-only clock control --------

@router.post("/_test/clock/advance")
def advance_clock(payload: dict, request: Request):
    seconds = float(payload.get("seconds", 0))
    request.app.state.clock.advance(seconds=seconds)
    return {"now": request.app.state.clock.now().isoformat()}


@router.post("/_test/clock/set")
def set_clock(payload: dict, request: Request):
    dt = datetime.fromisoformat(payload["now"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    request.app.state.clock.set(dt)
    return {"now": request.app.state.clock.now().isoformat()}


@router.post("/_test/tick")
def manual_tick(payload: dict, request: Request):
    from .worker import tick
    limit = int(payload.get("limit", 100))
    result = tick(
        request.app.state.conn,
        request.app.state.clock,
        request.app.state.destination,
        limit=limit,
    )
    return result.__dict__
