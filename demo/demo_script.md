# Demo Script

Target: 4–6 minutes. Five scenarios, in order, ending on the benchmark.

Before recording:

    cd 03-durable-reminders
    source .venv/bin/activate
    python -m pytest -v                     # show all 25 passing on camera
    rm -f demo.db demo.db-wal demo.db-shm   # clean slate for the demo

Set the environment for fake-clock mode so time is controllable:

    export REMINDERS_DB=demo.db
    export REMINDERS_CLOCK=fake
    export REMINDERS_INITIAL_TIME=2024-01-01T12:00:00+00:00

Start the service in one terminal:

    uvicorn durable_reminders.main:app --port 8000

Keep a second terminal for curl.

---

## Scenario 1 — Scheduled delivery (AC1)

Create a reminder for 30 simulated seconds from now, in Kolkata.

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"call mom","fire_at_local":"2024-01-01T12:00:30","timezone":"Asia/Kolkata"}' \
      | python -m json.tool

Point out: status `scheduled`, version `1`, `delivery_key` ends in `:v1`,
`tz_policy` is `unambiguous`.

Not due yet — run a tick:

    curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'
    # {"claimed":0,...}

Advance the clock 60 simulated seconds and tick again:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":60}'
    curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'
    # {"claimed":1,"delivered":1,...}

Inspect the reminder:

    curl -s localhost:8000/reminders | python -m json.tool

Status `delivered`, one attempt with outcome `success`, `delivered_at` set.

    curl -s localhost:8000/metrics | python -m json.tool
    # logical_notifications: 1

---

## Scenario 2 — Restart recovery (AC2)

Create a reminder 60 simulated seconds out, then stop the service before it
fires.

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"survive-restart","fire_at_local":"2024-01-01T12:01:00","timezone":"Asia/Kolkata"}'

Ctrl-C the uvicorn process.

Now "time passes while the service is down." Restart uvicorn against the same
`demo.db`:

    uvicorn durable_reminders.main:app --port 8000

Advance the clock past the due instant and tick:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":120}'
    curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'

The overdue reminder is discovered and delivered. Point out: nothing was
lost across the restart because the schedule lives in SQLite, not in memory.

---

## Scenario 3 — Edit racing execution (AC5)

Create a reminder due in 30 seconds.

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"v1 content","fire_at_local":"2024-01-01T12:00:30","timezone":"Asia/Kolkata"}' \
      | tee /tmp/r.json | python -m json.tool

Advance past due, then claim without delivering by hand:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":60}'

Now, before the tick, edit the reminder:

    RID=$(python -c "import json;print(json.load(open('/tmp/r.json'))['id'])")
    curl -s -X PATCH localhost:8000/reminders/$RID \
      -H 'content-type: application/json' \
      -d '{"content":"v2 content"}' | python -m json.tool

Version is now 2, `delivery_key` ends in `:v2`.

Run a tick. The worker claims v2 (the current version) and delivers it.
There is no way for v1 to fire later because v1 was never claimed; if it
had been claimed, the commit-time version check would have recorded it as
`superseded`. State this in the narration — it's decision 7.

    curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'
    curl -s localhost:8000/reminders/$RID | python -m json.tool

Point out: one attempt, `success`, content `v2 content`.

(If you want to show the superseded path explicitly, keep the process running
and use the test suite — `test_edit_before_execution_old_version_superseded`
walks it step by step.)

---

## Scenario 4 — Duplicate execution, one logical notification (AC4)

Create a reminder, advance past due, tick once — delivered.

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"dedupe-me","fire_at_local":"2024-01-01T12:00:30","timezone":"Asia/Kolkata"}'
    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":60}'
    curl -s -X POST localhost:8000/_test/tick -d '{}' -H 'content-type: application/json'

Metrics:

    curl -s localhost:8000/metrics | python -m json.tool

Now demonstrate the idempotency ledger directly with SQLite:

    sqlite3 demo.db "SELECT delivery_key FROM deliveries;"

Point out: one row per successful occurrence, keyed by `(reminder_id, version)`.
Even if the worker retried or a second worker raced, `INSERT OR IGNORE` would
make the second attempt a no-op, and the attempt log would record a
`success` outcome from the duplicate detection. The user receives one
logical notification.

The strongest version of this scenario is the test
`test_crash_after_destination_before_commit` — mention it and, if time
allows, run it on camera:

    python -m pytest tests/test_idempotency.py -v

---

## Scenario 5 — Benchmark, architecture, one trade-off (close)

Run the benchmark live:

    python -m benchmark.run_benchmark

Read the output aloud:

    steps advanced:       2
    reminders by status:  {'cancelled': 3, 'delivered': 19, 'failed': 2}
    logical notifications: 19
    RESULT: PASS

Narrate what just happened:

- 24 reminders across `Asia/Kolkata` and `America/New_York`.
- 3 edited, 3 cancelled, 5 temporary failures, 2 permanent failures,
  1 duplicate-executed.
- The service was stopped and restarted before processing.
- An injected clock advanced until everything settled.
- The invariant: `logical_notifications == delivered`.

Show `docs/architecture.md` for ~20 seconds and name the three seams:

- `clock.py` — every read of "now" is injectable.
- `destination.py` — the idempotency ledger.
- `worker.py` — claim, deliver, commit-with-version-check, reap.

Close on one trade-off, stated plainly:

> "The destination ledger is in the same SQLite database. That collapses the
> dual-write problem — the send and the state update happen in one
> transaction — which is the honest simplification for a six-hour exercise.
> A real external provider would need a transactional outbox: write the
> intent to send in the same transaction as the state change, and let a
> separate relay deliver it with the idempotency key. I documented that as
> the production upgrade in `docs/decisions.md`."

That last sentence — naming what would change and why — is what earns the
"Communication and trade-offs" credit and separates "I know what this code
does" from "I know what it can't do."

---

## Recording tips

- 1080p, one terminal font size large enough to read at 720p.
- Show `python -m pytest -v` at the start so the reviewer knows the tests
  pass before the demo starts.
- Keep the benchmark output visible for a full 5 seconds at the end.
- Do not narrate the code; narrate the behavior and the decisions.
