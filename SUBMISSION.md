# Product Engineering Challenge Submission

## Candidate

- **Name:** Vihaan Singh
- **Email:** vihaan.s0b@gmail.com
- **GitHub:** https://github.com/fknemi
- **Selected problem:** Problem 3 — Durable Reminders and Follow-Ups
- **Demo video:** https://youtu.be/KO0TWopETtw

## Run the project

**Prerequisites:** Python 3.11 or higher.

```text
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Start the service:

```text
uvicorn durable_reminders.main:app --port 8000
```

The service listens on `http://localhost:8000`. The database file is
`reminders.db` in the current directory by default, configurable via the
`REMINDERS_DB` environment variable. No secrets or credentials are required.

**Triggering the successful scenario (AC1):**

```text
curl -s -X POST localhost:8000/reminders \
  -H 'content-type: application/json' \
  -d '{"content":"call mom","fire_at_local":"2030-01-01T09:00:00","timezone":"Asia/Kolkata"}'

curl -s localhost:8000/reminders
curl -s localhost:8000/metrics
```

The worker thread delivers it when the wall clock reaches the scheduled
instant. To force the scenario without waiting, run the service in fake-clock
mode and drive it via the test-only routes:

```text
REMINDERS_DB=demo.db \
REMINDERS_CLOCK=fake \
REMINDERS_INITIAL_TIME=2030-01-01T00:00:00+00:00 \
uvicorn durable_reminders.main:app --port 8000

curl -s -X POST localhost:8000/reminders \
  -H 'content-type: application/json' \
  -d '{"content":"call mom","fire_at_local":"2030-01-01T09:00:30","timezone":"Asia/Kolkata"}'

curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'

curl -s -X POST localhost:8000/_test/clock/advance \
  -H 'content-type: application/json' -d '{"seconds":60}'

curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'
```

**Triggering the recovery scenario (AC2):**

1. Create a reminder due in 60 simulated seconds (fake-clock mode).
2. Stop the service with `Ctrl-C`.
3. Restart with the same environment variables. The persisted fake clock
   resumes.
4. Advance the clock past due and tick once. The overdue reminder is
   discovered and delivered.

The full sequence with exact commands and expected output is in
`demo/demo_script.md`.

## Run the tests

```text
python -m pytest -v
```

Expected: **25 passed**. No sleeps, no external services, deterministic.

## Acceptance scenarios and verification

All seven acceptance criteria from the problem brief are implemented and
covered by tests:

| AC | Test |
| --- | --- |
| AC1 Scheduled delivery | `tests/test_worker.py::test_due_discovery_delivers_once` |
| AC2 Restart recovery | `tests/test_recovery.py::test_overdue_work_is_picked_up_after_restart` |
| AC3 Temporary failure | `tests/test_worker.py::test_temporary_failure_retries_then_succeeds` and `::test_retry_exhaustion_reaches_failed` |
| AC4 Duplicate execution | `tests/test_idempotency.py::test_crash_after_destination_before_commit` |
| AC5 Edit before execution | `tests/test_races.py::test_edit_before_execution_old_version_superseded` |
| AC6 Cancellation | `tests/test_races.py::test_cancel_during_execution_superseded` |
| AC7 Time-zone boundary | `tests/test_timezones.py::test_dst_fall_back_ambiguous_chooses_first_occurrence` and `::test_dst_spring_forward_gap_shifts_forward` |

Nothing in the acceptance set is incomplete. No requirements were
reinterpreted.

**Verification benchmark:**

```text
python -m benchmark.run_benchmark
```

Observed result:

```text
--- benchmark results ---
steps advanced:       2
reminders by status:  {'cancelled': 3, 'delivered': 19, 'failed': 2}
logical notifications: 19
RESULT: PASS
```

The benchmark seeds 24 reminders across `Asia/Kolkata` and
`America/New_York`, applies 3 edits and 3 cancels, configures 5 temporary
failures and 2 permanent failures, stops and restarts the service before
processing, simulates duplicate execution on one occurrence, advances an
injected clock until processing settles, and asserts that every successful
occurrence produced exactly one logical notification. Exit code is non-zero
on any invariant violation.

**Failure or recovery scenario demonstrated in the video:**

The video shows restart recovery: a reminder becomes due while the service
is stopped, and on restart the overdue work is discovered and delivered
without loss. It also shows duplicate execution without duplicate
notification — the test `test_crash_after_destination_before_commit`
demonstrates that when the destination accepted a send but the worker died
before committing, the replay is detected by the idempotency ledger and
returns `DUPLICATE`, so the user receives one logical notification.

## Architecture and data flow

```
Client
  │
  ▼
api.py ──▶ service.py ──▶ db.py
                │           ▲
                ▼           │
            worker.py ──────┤
                │           │
                ▼           │
        destination.py ─────┘
                ▲
                │
            clock.py  (injected everywhere)
```

**Components and responsibilities:**

- **`api.py`** — HTTP surface only. Parses requests, calls the service,
  returns responses. No scheduling or retry logic.
- **`service.py`** — create, get, edit, cancel. Owns version bumping and
  lease invalidation on edit and cancel.
- **`worker.py`** — claim, deliver, commit with a version re-check, and
  reap expired leases. Owns every state transition out of `scheduled`.
- **`db.py`** — SQLite schema, connection helper, transaction context
  manager, and the persisted clock store for the fake clock.
- **`clock.py`** — every read of "now" goes through an injectable clock.
  `SystemClock` in production, `FakeClock` in tests and the benchmark.
- **`destination.py`** — `DeliveryDestination` protocol with a
  `FakeDestination` implementation backed by the `deliveries` table.
  Deduplicates on `delivery_key` via `INSERT OR IGNORE`.
- **`timezones.py`** — resolves a local datetime plus IANA zone to a UTC
  instant, with documented policies for ambiguous and nonexistent local
  times.

**Data flow for a single delivery:**

1. `api.py` receives `POST /reminders` and calls `service.create_reminder`.
2. `service` resolves the local time to UTC via `timezones.resolve_instant`,
   inserts a row with `status='scheduled'`, `version=1`,
   `delivery_key='<id>:v1'`.
3. `worker.claim_due` runs an atomic `UPDATE ... RETURNING` inside
   `BEGIN IMMEDIATE`, moving the row to `running` with a 30-second lease.
4. `worker.commit_delivery` opens its own transaction and re-reads `version`
   and `status`. If either changed since the claim, it records `superseded`
   and does not deliver. Otherwise it calls `destination.attempt`, records
   the outcome in `delivery_attempts`, and either marks `delivered`, sets a
   backoff for retry, or marks `failed`.
5. `worker.reap_expired` resets any `running` rows whose lease has expired
   back to `scheduled`, so a crashed worker's work returns to the queue.

**Storage:** three primary tables plus one auxiliary.

- `reminders` — one row per reminder; carries status, version, lease, and
  the resolved UTC instant.
- `delivery_attempts` — append-only history of every attempt.
- `deliveries` — the destination ledger, keyed by `delivery_key`.
- `clock_state` — persisted current time for the fake clock.

## Technology choices

**Python 3.11 or higher.** The stdlib `zoneinfo` handles IANA time zones
and DST transitions with no third-party dependency, and the `fold`
parameter gives an explicit lever for ambiguous local times. That removed
an entire class of dependency decisions.

**SQLite in WAL mode.** Durable across restarts, zero infrastructure for
the reviewer, and a single writer is sufficient for the atomic
claim-and-lease pattern. The brief explicitly says a distributed queue or
workflow engine is not expected, so adding Postgres would have been
solving a problem not being asked.

**FastAPI.** Minimal REST surface, automatic OpenAPI docs for the reviewer,
and a lifespan hook for starting and stopping the worker thread.

**A hand-written worker loop.** The hard part of this problem is the
scheduling, recovery, and race handling itself. Importing Celery,
APScheduler, or a workflow engine would hide the signal the brief is
testing for.

Alternatives considered and rejected:

- **Postgres with `SELECT ... FOR UPDATE SKIP LOCKED`.** The production-
  grade choice for multi-worker throughput. Documented as the upgrade path
  in `docs/decisions.md` but overkill for a 6–8 hour exercise.
- **A distributed workflow engine (DBOS, Inngest, Temporal).** Would solve
  the hard parts for me rather than demonstrate that I understand them.
- **Node.js or Go.** Both fine; Python won because `zoneinfo` is in the
  stdlib and handles DST ambiguity cleanly, and because the exercise
  rewards clear state machines over throughput.

Trade-offs accepted: SQLite serializes writers, so multi-worker throughput
is capped. The persisted clock store only matters for the fake-clock demo
path; tests use an in-memory store. Both are documented in
`docs/decisions.md`.

## Important decisions

Three decisions shaped the solution more than any others.

**1. At-least-once delivery with an idempotent destination, not
exactly-once.** Exactly-once delivery across an unreliable boundary is
impossible without cooperation from the destination. So sends are
at-least-once and the destination ledger deduplicates on a stable
`delivery_key`. A repeat attempt returns `DUPLICATE` instead of a second
logical notification. This is what makes AC4 hold even when the wire
carries two sends.

**2. A reminder is a series of occurrences, not one mutable row.**
`(reminder_id, version)` is the unit. An edit bumps the version and
produces a new occurrence. The worker re-reads `version` and `status`
inside its commit transaction, so an edit or cancel that lands mid-flight
wins the race and the old version is recorded as `superseded`. This is what
makes AC5 and AC6 decidable and is the answer to the follow-up question in
the brief.

**3. The destination ledger lives in the same SQLite database as the
reminders.** This collapses the dual-write problem for this exercise — the
send and the state update happen in one transaction. A real external
provider would need a transactional outbox instead, documented as the
production upgrade.

## Assumptions and limitations

Assumptions:

- Single region, single database. Multi-region scheduling is out of scope
  per the brief.
- The destination accepts a stable idempotency key and deduplicates on it.
  Real email and SMS providers vary; some require implementing the dedupe
  layer in front of them.
- Clock skew between processes is negligible on a single host. Tests use
  the fake clock so this does not affect determinism.
- The reviewer runs the setup in a Python 3.11+ environment with network
  access to PyPI.

Known limitations:

- SQLite single-writer caps throughput. The system is correct under
  multiple workers but not high-throughput. Postgres with
  `FOR UPDATE SKIP LOCKED` replaces the claim statement.
- The in-DB destination ledger is the honest simplification for this
  exercise; production needs a transactional outbox.
- Idempotency prevents duplicates, not regrets. If an earlier version
  already reached the destination before an edit, that send cannot be
  un-sent.
- Recurring schedules and natural-language date parsing are out of scope
  per the brief.

Deliberately unfinished: nothing in the required behaviour set. Optional
extensions (recurring schedules, real providers, auth, dashboards) were
not built because the brief explicitly scopes them out.

## Production and scale

If this prototype needed to operate in production or at significantly
greater scale, I would change three things first.

1. **Replace SQLite with Postgres.** The claim statement becomes
   `SELECT ... FOR UPDATE SKIP LOCKED`, which supports true concurrent
   workers. The rest of the logic — lease, version re-check, idempotency
   ledger — is unchanged, which is why the interface was designed around a
   single atomic claim.
2. **Replace the in-DB destination ledger with a transactional outbox.**
   Write the send intent in the same transaction as the state change, and
   let a separate relay deliver it with the idempotency key. This closes
   the residual where a delivered-then-edited reminder cannot be recalled.
3. **Add observability.** Structured logs for each state transition, a
   counter for lease recoveries per hour, and a dashboard for pending
   versus delivered versus failed counts. The current code already keeps
   the append-only `delivery_attempts` history, so the data is there; only
   the exporter is missing.

What the current implementation does now: single-process SQLite, in-DB
destination ledger, metrics endpoint with counts, no external exporter.
What I am proposing for production: the three items above, each documented
in `docs/decisions.md`.

## AI usage

I used an AI assistant (Claude) as a design collaborator and to help plan
the repository structure and module decomposition. It helped think through
the dual-write problem, the version-based race handling, and the DST
policies.

I reviewed and tested every part of the output. The 25 tests, the
verification benchmark, and the live demo all run against the code as
submitted. The benchmark caught one incorrect invariant I had written (it
demanded `max_attempts` attempts for every failed reminder, but permanent
failures intentionally reach terminal state on the first attempt); the fix
was to the benchmark assertion, not the implementation.

I can walk through any part of the code, the state machine, or the race
handling in detail. The specific trade-offs I chose and their residuals
are recorded in `docs/decisions.md`.

## Credibility note

My closest shipped-system experience is **NutriLens**, a React Native app
that overlays AR nutrition labels on a live camera feed.

**What problem it solved:** real-time food detection and nutrition lookup
on-device, so the app works without a network round-trip and can run
offline.

**My personal contribution:** on a small team, I built the real-time
detection pipeline. `react-native-vision-camera` delivers frames at 60fps,
`react-native-fast-tflite` runs YOLOv8n on-device to produce bounding
boxes, `expo-sqlite` looks up USDA nutrition data from a bundled local
database, and `react-native-skia` draws the labels on the GPU.

**Scale or operational complexity:** the entire pipeline runs on-device
with no server component. That constraint is the hard part — the model,
the data, and the rendering all have to fit inside a phone's compute and
memory budget, and the pipeline has to keep up with the camera or the AR
overlay visibly lags.

**One difficult engineering decision:** the trade-off was model size.
YOLOv8n sustains 60fps but is less accurate than larger variants, so we
accepted a higher false-negative rate on partially-occluded food. The
reasoning was that a missed detection is less jarring to the user than a
wrong label drawn over someone's hand. I would make the same call unless
the target devices had meaningfully more compute headroom.

**Evidence:** https://github.com/fknemi/NutriLens (public repository).
