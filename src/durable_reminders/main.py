"""Application wiring: DB, clock, destination, worker thread, FastAPI app.

The worker runs as a background thread when the app is started via uvicorn.
In tests and the benchmark, the worker is driven manually via /_test/tick so
the injected clock fully controls execution.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .api import router
from .clock import Clock, FakeClock, SystemClock
from .db import SQLiteClockStore, connect, init_schema
from .destination import FakeDestination
from .worker import tick

DB_PATH = os.environ.get("REMINDERS_DB", "reminders.db")
CLOCK_MODE = os.environ.get("REMINDERS_CLOCK", "system")   # "system" | "fake"
INITIAL_TIME = os.environ.get("REMINDERS_INITIAL_TIME")    # ISO, for fake mode
WORKER_INTERVAL = float(os.environ.get("REMINDERS_WORKER_INTERVAL", "1.0"))


def _build_clock(conn) -> Clock:
    if CLOCK_MODE == "fake":
        store = SQLiteClockStore(conn)
        if store.get() is None:
            if not INITIAL_TIME:
                raise RuntimeError(
                    "REMINDERS_INITIAL_TIME required when clock mode is fake"
                )
            from datetime import datetime
            store.set(datetime.fromisoformat(INITIAL_TIME))
        return FakeClock(store=store)
    return SystemClock()


def _worker_loop(app: FastAPI, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            tick(app.state.conn, app.state.clock, app.state.destination, limit=50)
        except Exception as e:  # noqa: BLE001
            # Worker must not die on transient errors; log and continue.
            print(f"[worker] tick error: {e!r}")
        stop.wait(WORKER_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = connect(DB_PATH)
    init_schema(conn)
    app.state.conn = conn
    app.state.clock = _build_clock(conn)
    app.state.destination = FakeDestination(conn)

    # Only run the background worker when using the system clock. In fake
    # clock mode, the caller advances time explicitly via /_test/tick.
    stop = threading.Event()
    app.state.stop = stop
    app.state.worker = None
    if CLOCK_MODE != "fake":
        t = threading.Thread(target=_worker_loop, args=(app, stop), daemon=True)
        t.start()
        app.state.worker = t

    yield

    stop.set()
    if app.state.worker:
        app.state.worker.join(timeout=2)
    conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Durable Reminders", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


def cli() -> None:
    import uvicorn
    uvicorn.run(
        "durable_reminders.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    cli()
