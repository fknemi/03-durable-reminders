# Durable Reminders and Follow-Ups

A small service for reminders and scheduled conversational follow-ups that
remains correct across process restarts, worker crashes, temporary
destination failures, duplicate execution, and edit/cancel races.

## Setup

    python -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"

## Run the tests

    python -m pytest -v

25 tests, deterministic, no sleeps, no external services.

## Run the verification benchmark

    python -m benchmark.run_benchmark

This is the reproducible correctness command. It exits non-zero if any
invariant is violated.

## Run the service

    durable-reminders
    # or
    uvicorn durable_reminders.main:app --reload

Environment variables:

- `REMINDERS_DB` — SQLite path (default `reminders.db`)
- `REMINDERS_CLOCK` — `system` (default) or `fake`
- `REMINDERS_INITIAL_TIME` — ISO datetime, required when clock is `fake`
- `REMINDERS_WORKER_INTERVAL` — seconds between worker ticks (default `1.0`)

## API

- `POST   /reminders`
- `GET    /reminders`
- `GET    /reminders/{id}`
- `PATCH  /reminders/{id}`
- `DELETE /reminders/{id}`
- `GET    /health`
- `GET    /metrics`
- `POST   /_test/clock/advance` — advance the fake clock (test mode)
- `POST   /_test/clock/set` — set the fake clock (test mode)
- `POST   /_test/tick` — run one worker cycle synchronously (test mode)

## Architecture

See `docs/architecture.md`. The short version:

    api.py ──▶ service.py ──▶ db.py
                  │             ▲
                  ▼             │
              worker.py ────────┘
                  │
                  ▼
            destination.py

`clock.py` is injected everywhere. `destination.py` is the idempotency
ledger. `worker.py` owns every state transition out of `scheduled`.

## The eight decisions

See `docs/decisions.md`. Summary:

1. Local time → UTC via `zoneinfo`; ambiguous picks first occurrence;
   nonexistent shifts forward.
2. Due work claimed atomically inside `BEGIN IMMEDIATE` with a 30s lease.
3. Temporary failures retry; permanent failures go terminal.
4. Max 5 attempts, backoff 1s/5s/30s/2m/10m with jitter.
5. Unique occurrence is `(reminder_id, version)`.
6. Idempotency at the destination via `delivery_key` in a durable ledger.
7. Edit and cancel win over in-flight execution; the worker re-checks the
   version inside its commit transaction.
8. Multi-worker safe for claim/execute; SQLite serializes writes. Postgres
   `FOR UPDATE SKIP LOCKED` is the production upgrade.

## Known limitations

- SQLite single-writer caps throughput. Correct, not fast.
- The destination ledger is in-DB, which collapses the dual-write race for
  this exercise. A real external provider needs a transactional outbox.
- Idempotency prevents duplicates, not regrets: if the previous version
  already reached the destination before an edit, it cannot be un-sent.
- Recurring schedules and natural-language parsing are out of scope.
