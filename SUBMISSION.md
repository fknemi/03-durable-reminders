# Submission

## Problem

Problem 3: Durable Reminders and Follow-Ups.

## Repository layout

    src/durable_reminders/   service code
    tests/                   25 deterministic tests
    benchmark/               verification benchmark
    docs/                    architecture, decisions, state machine
    demo/                    demo script

## Setup

    python -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"

## Run the tests

    python -m pytest -v

Expected: 25 passed. No sleeps, no external services, deterministic.

## Run the verification benchmark

    python -m benchmark.run_benchmark

Expected output:

    --- benchmark results ---
    steps advanced:       2
    reminders by status:  {'cancelled': 3, 'delivered': 19, 'failed': 2}
    logical notifications: 19
    RESULT: PASS

The benchmark seeds 24 reminders across Asia/Kolkata and America/New_York,
applies 3 edits and 3 cancels, configures 5 temporary failures and 2
permanent failures, stops and restarts the service before processing,
simulates duplicate execution on one occurrence, advances an injected clock
until processing settles, and asserts that every successful occurrence
produced exactly one logical notification. Exit code is non-zero on any
invariant violation.

## The eight required decisions

Full rationale in `docs/decisions.md`. Summary:

1. **Local time -> execution instant.** `zoneinfo`. Ambiguous local times
   (DST fall-back) resolve to the first occurrence. Nonexistent local times
   (spring-forward gap) shift forward by the gap. Both policies recorded on
   the reminder as `tz_policy` so the choice is auditable.
2. **Due-work discovery and claim.** A single atomic
   `UPDATE ... RETURNING` inside `BEGIN IMMEDIATE`. Sets
   `claimed_version=version`, `lease_until=now+30s`, `attempt_count+=1`.
3. **Retryable failures.** The destination returns a typed outcome.
   Temporary failures retry; permanent failures go terminal immediately.
4. **Retry limit and delay.** Max 5 attempts. Backoff 1s, 5s, 30s, 2m, 10m
   with jitter.
5. **Unique scheduled occurrence.** `(reminder_id, version)`. An edit
   creates a new version, which is a new occurrence.
6. **Idempotency at the delivery boundary.** A durable ledger keyed by
   `delivery_key = f"{reminder_id}:v{version}"`. `INSERT OR IGNORE`.
   Repeat attempts return `DUPLICATE`, not a second notification.
7. **Edit/cancellation race policy.** Edit and cancel win. The worker
   re-reads `version` and `status` inside its commit transaction. On
   mismatch it records a `superseded` attempt and does not deliver.
8. **Multi-worker guarantees.** Atomic claim plus lease gives at-most-one
   active lease per reminder. Reaper recovers crashed leases. SQLite
   single-writer caps throughput; Postgres `FOR UPDATE SKIP LOCKED` is the
   documented production upgrade.

## Acceptance criteria

All seven are covered by tests:

| AC | Test |
| --- | --- |
| AC1 Scheduled delivery | `tests/test_worker.py::test_due_discovery_delivers_once` |
| AC2 Restart recovery | `tests/test_recovery.py::test_overdue_work_is_picked_up_after_restart` |
| AC3 Temporary failure | `tests/test_worker.py::test_temporary_failure_retries_then_succeeds` and `::test_retry_exhaustion_reaches_failed` |
| AC4 Duplicate execution | `tests/test_idempotency.py::test_crash_after_destination_before_commit` |
| AC5 Edit before execution | `tests/test_races.py::test_edit_before_execution_old_version_superseded` |
| AC6 Cancellation | `tests/test_races.py::test_cancel_during_execution_superseded` |
| AC7 Time-zone boundary | `tests/test_timezones.py::test_dst_fall_back_ambiguous_chooses_first_occurrence` and `::test_dst_spring_forward_gap_shifts_forward` |

## AI usage disclosure

I used an AI assistant as a design collaborator and to help plan the
repository structure and module decomposition. The design decisions,
implementation, test suite, and verification benchmark in this submission
were reviewed and run by me; I can walk through any part of the code, the
state machine, or the race handling in detail. The specific trade-offs I
chose and their residuals are recorded in `docs/decisions.md`.

## Credibility note

My closest shipped-system experience is NutriLens, a React Native app that
overlays AR nutrition labels on a live camera feed. On a small team,
I built the real-time detection pipeline: vision-camera delivers frames at 60fps,
YOLOv8n runs on-device via fast-tflite to produce bounding boxes,
expo-sqlite looks up USDA nutrition data locally, and skia draws the labels on the GPU.
Everything had to run on-device with no server round-trip or the overlay would lag the camera,
which forced the pipeline onto background threads so the JS thread never blocked. 
The trade-off was model size: YOLOv8n sustains 60fps but is less accurate than larger variants, 
so we accepted false negatives on partially-occluded food — a missed detection is less jarring than a wrong label. I'd make the same call without more device headroom.

## Known limitations

- SQLite single-writer caps throughput. Correct, not fast.
- The destination ledger lives in the same database. This collapses the
  dual-write problem for this exercise; a real external provider would need
  a transactional outbox.
- Idempotency prevents duplicates, not regrets. If an earlier version
  already reached the destination before an edit, that send cannot be
  un-sent.
- Recurring schedules and natural-language date parsing are out of scope.

## Follow-up: reschedule during execution

If a user reschedules an item at the same instant a worker has claimed its
previous version, the edit wins. The edit runs inside `BEGIN IMMEDIATE`,
increments `version`, and clears the lease. The worker's `commit_delivery`
opens its own transaction, re-reads `version` and `status`, finds the
mismatch, records a `superseded` attempt, and does not insert into the
`deliveries` ledger. The new version schedules and delivers later. Residual:
if the worker already called the destination before the edit, that send
cannot be recalled. Closing this gap requires a transactional outbox with a
destination-side cancellation tombstone, which is documented as the
production upgrade.

## Demo

See `demo/demo_script.md`.
