# Benchmark

An end-to-end correctness benchmark for `durable_reminders`. It seeds a
realistic mix of reminder scenarios, simulates a service restart and duplicate
delivery, then asserts that every observable invariant holds.

## What it tests

The benchmark seeds **24 reminders** across two IANA time zones
(`Asia/Kolkata` and `America/New_York`) and exercises five distinct lifecycle
paths:

| Group | Count | Expected final status |
|---|---|---|
| Plain delivery | 10 | `delivered` |
| Edited before fire | 3 | `delivered` (with updated content/time) |
| Cancelled before fire | 3 | `cancelled` |
| Temporary failure → success | 5 | `delivered` (after retries) |
| Permanent failure | 2 | `failed` |
| Duplicate delivery | 1 | `delivered` (idempotent) |

It then runs through five phases:

1. **Seed** — create all 24 reminders; apply edits and cancellations before
   any worker tick runs.
2. **Restart** — close the database connection, advance the fake clock by two
   hours (past all due times), and reopen the database. This simulates a
   service restart after downtime.
3. **Duplicate simulation** — manually call `FakeDestination.attempt` twice
   on the same reminder/version. The first call must return `SUCCESS`; the
   second must return `DUPLICATE`.
4. **Settle** — run `reap_expired` + `tick` in a loop (up to 200 steps, 30 s
   per step) until no reminders remain in `SCHEDULED` or `RUNNING` state.
5. **Assert** — check every invariant listed below and print a `PASS`/`FAIL`
   summary.

## Invariants checked

- Exactly **19 reminders** reach `delivered` status (10 plain + 3 edited + 5
  temp-fail + 1 duplicate).
- Exactly **3 reminders** are `cancelled`.
- Exactly **2 reminders** are `failed`.
- The `deliveries` table has exactly **19 rows** — one logical notification per
  successfully delivered reminder.
- No cancelled reminder produced a row in `deliveries`.
- Every `failed` reminder followed exactly one of two valid attempt sequences:
  - *Permanent path*: one attempt with outcome `permanent_failure`.
  - *Exhaustion path*: exactly `max_attempts` attempts, all `temporary_failure`.
- Every `delivered` reminder has exactly **1 row** in `deliveries` and at least
  **1 attempt** with outcome `success`.

## Running

```bash
# From the repository root:
python -m benchmark.run_benchmark

# Keep the database file for post-run inspection:
python -m benchmark.run_benchmark --db /tmp/reminders_bench.db
```

The script exits with code **0** on `PASS` and **1** on `FAIL`.

## Output

```
--- benchmark results ---
steps advanced:       4
reminders by status:  {'delivered': 19, 'cancelled': 3, 'failed': 2}
logical notifications: 19
RESULT: PASS
```

`steps advanced` is the number of 30-second clock increments the settle loop
needed. A large number (approaching 200) suggests the worker is not making
progress and warrants investigation.

## Dependencies

The benchmark uses only the internal `durable_reminders` package and the
Python standard library. No additional installation is required beyond the
normal project dependencies:

```bash
pip install -e ".[dev]"
```

## Design notes

- **Fake clock** (`FakeClock` / `InMemoryClockStore`) — all time is
  controlled deterministically; no wall-clock dependency.
- **Fake destination** (`FakeDestination`) — accepts a `failure_script` dict
  that maps `delivery_key → [DeliveryOutcome, …]`. Each call pops the next
  outcome; once the script is exhausted the destination returns `SUCCESS`.
- **Restart fidelity** — closing and reopening the SQLite connection before
  processing verifies that all reminder state survives across restarts with no
  in-memory crutches.
