# Architecture

## Components

    ┌──────────┐
    │  Client  │
    └────┬─────┘
         │ HTTP
         ▼
    ┌──────────┐     ┌───────────────┐
    │  api.py  │────▶│  service.py   │
    └──────────┘     └───────┬───────┘
                             │
                             ▼
    ┌──────────┐     ┌───────────────┐     ┌──────────────┐
    │ clock.py │◀────│    db.py      │◀────│  worker.py   │
    └──────────┘     │  (SQLite WAL) │     └──────┬───────┘
                     └───────┬───────┘            │
                             │                    ▼
                             │            ┌────────────────┐
                             └───────────▶│ destination.py │
                                          └────────────────┘

## Responsibilities

- **api.py** — HTTP surface only. Parses requests, calls service, returns
  responses. No scheduling or retry logic.
- **service.py** — create / get / edit / cancel. Owns version bumping and
  lease invalidation.
- **worker.py** — claim, deliver, commit (with version re-check), reap. Owns
  all state transitions out of `scheduled`.
- **db.py** — schema, connection, transactions. Knows nothing about reminders
  as a domain concept beyond the tables.
- **clock.py** — every read of "now". `SystemClock` in production, `FakeClock`
  in tests. FakeClock state persists in `clock_state` so restart tests are
  honest.
- **destination.py** — `DeliveryDestination` protocol. Production adapter
  would call a real provider; the shipped `FakeDestination` is the `deliveries`
  ledger plus a configurable failure script.

## Dependency direction

Dependencies point inward. `api.py` depends on `service.py`, which depends on
`db.py`. `worker.py` depends on `db.py`, `clock.py`, and `destination.py`.
`timezones.py` depends on nothing from the package. No core module imports
FastAPI.

## The two seams

`clock.py` and `destination.py` are the seams that make the system testable
without sleeping and without external services. Every time-dependent and
every I/O-dependent behavior is reached through one of them.

## Storage

Three primary tables plus one auxiliary:

- `reminders` — one row per reminder; carries status, version, lease, and the
  resolved UTC instant.
- `delivery_attempts` — append-only history, one row per attempt.
- `deliveries` — the destination ledger, keyed by `delivery_key`. This is
  what makes duplicate sends harmless.
- `clock_state` — persisted current time for `FakeClock`, so a restart does
  not silently reset simulated time.

## State machine

See `docs/state-machine.md`. Terminal states are `delivered`, `cancelled`,
`failed`. Everything else is transient.
