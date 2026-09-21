# Demo Script

Target: 4–6 minutes. Five scenarios, ending on the benchmark.
Uses the fake clock so every step is deterministic and instant.

Time convention: the clock starts at 2024-01-01T06:30:00Z, which is
12:00:00 in Asia/Kolkata. Reminder times below are expressed in local
Kolkata time; the service converts them to UTC internally.

## Setup (do before recording)

Two terminals side by side, large font (14–16pt), dark background.

Right terminal — start the server:

    cd ~/Downloads/03-durable-reminders
    source .venv/bin/activate
    rm -f demo.db demo.db-wal demo.db-shm

    REMINDERS_DB=demo.db \
    REMINDERS_CLOCK=fake \
    REMINDERS_INITIAL_TIME=2024-01-01T06:30:00+00:00 \
    uvicorn durable_reminders.main:app --port 8000

Left terminal — prove the tests pass before the demo:

    python -m pytest -v

Leave the passing output on screen for 5 seconds.

---

## Scenario 1 — Scheduled delivery (AC1)

Create a reminder 30 simulated seconds in the future, Kolkata time:

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"call mom","fire_at_local":"2024-01-01T12:00:30","timezone":"Asia/Kolkata"}' \
      | python -m json.tool

Point out: status `scheduled`, version `1`, `delivery_key` ends in `:v1`,
`tz_policy` is `unambiguous`, `scheduled_at_utc` is `06:30:30Z`.

Tick without advancing — not due yet:

    curl -s -X POST localhost:8000/_test/tick \
      -H 'content-type: application/json' -d '{}'

Returns `{"claimed":0,...}`.

Advance the clock 60 simulated seconds and tick again:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":60}'

    curl -s -X POST localhost:8000/_test/tick \
      -H 'content-type: application/json' -d '{}'

Returns `{"claimed":1,"delivered":1,...}`.

Inspect the reminder:

    curl -s localhost:8000/reminders | python -m json.tool

Status is `delivered`, one attempt with outcome `success`.

    curl -s localhost:8000/metrics | python -m json.tool

Shows `logical_notifications: 1`.

Narrate: "One notification, one logical occurrence. No wall-clock waiting —
the injected clock controlled the timing."

Clock is now at 06:31:00 UTC (12:01 IST).

---

## Scenario 2 — Restart recovery (AC2)

Create a reminder 60 simulated seconds in the future:

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"survive-restart","fire_at_local":"2024-01-01T12:02:00","timezone":"Asia/Kolkata"}'

Due at 06:32:00 UTC. Clock is at 06:31:00 UTC, so it is not yet due.

Ctrl-C uvicorn. Say: "The service is now stopped. Time will advance while
it's down. The reminder is still sitting in the database, still scheduled,
waiting for a worker that isn't running."

Restart with the same env vars:

    REMINDERS_DB=demo.db \
    REMINDERS_CLOCK=fake \
    REMINDERS_INITIAL_TIME=2024-01-01T06:30:00+00:00 \
    uvicorn durable_reminders.main:app --port 8000

Optionally, show the reminder is still scheduled after restart:

    curl -s localhost:8000/reminders | python -m json.tool

Advance past due and tick:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":120}'

    curl -s -X POST localhost:8000/_test/tick \
      -H 'content-type: application/json' -d '{}'

    curl -s localhost:8000/reminders | python -m json.tool

The overdue reminder is discovered and delivered.

Narrate: "The schedule lives in SQLite. The restart lost nothing. The
recovered worker found the overdue reminder on its first tick."

Clock is now at 06:33:00 UTC (12:03 IST).

---

## Scenario 3 — Edit before execution (AC5, AC6)

Create a reminder due in 120 simulated seconds:

    curl -s -X POST localhost:8000/reminders \
      -H 'content-type: application/json' \
      -d '{"content":"v1 content","fire_at_local":"2024-01-01T12:05:00","timezone":"Asia/Kolkata"}' \
      | tee /tmp/r.json | python -m json.tool

Due at 06:35:00 UTC. Clock is at 06:33:00 UTC.

Advance past due:

    curl -s -X POST localhost:8000/_test/clock/advance \
      -H 'content-type: application/json' -d '{"seconds":180}'

Clock is now at 06:36:00 UTC, so the reminder is overdue but no tick has
run yet.

Edit the reminder before the tick runs:

    RID=$(python -c "import json;print(json.load(open('/tmp/r.json'))['id'])")

    curl -s -X PATCH localhost:8000/reminders/$RID \
      -H 'content-type: application/json' \
      -d '{"content":"v2 content"}' | python -m json.tool

Version is now 2, `delivery_key` ends in `:v2`. v1 never had a chance to
fire because no worker had claimed it yet.

Tick and inspect:

    curl -s -X POST localhost:8000/_test/tick \
      -H 'content-type: application/json' -d '{}'

    curl -s localhost:8000/reminders/$RID | python -m json.tool

v2 delivered, one success attempt. v1 never fired.

Then run the race tests to show the superseded path explicitly:

    python -m pytest tests/test_races.py -v

Narrate: "The live demo showed edit landing before any claim. The test
shows edit landing after the claim but before the commit. Both paths end
the same way — v1 never delivers. That's how edit and cancel always win
over in-flight execution — acceptance criteria five and six."

---

## Scenario 4 — Duplicate execution, one notification (AC4)

Run the idempotency tests:

    python -m pytest tests/test_idempotency.py -v

Narrate: "`test_crash_after_destination_before_commit` is the key case.
The destination was called, the worker 'died' before committing, and
recovery replayed the send. The ledger returned DUPLICATE. One logical
notification."

Show the ledger for the current demo DB:

    sqlite3 demo.db "SELECT delivery_key FROM deliveries;"

One row per successful occurrence.

---

## Scenario 5 — Benchmark, architecture, one trade-off

    python -m benchmark.run_benchmark

Let the output sit for 5 seconds. Narrate:

- 24 reminders across Asia/Kolkata and America/New_York.
- 3 edited, 3 cancelled, 5 temporary failures, 2 permanent failures,
  1 duplicate-executed.
- The service is stopped and restarted before processing all due work.
- The injected clock advances until processing settles.
- Invariant: logical notifications == delivered.

Open `docs/architecture.md` for ~20 seconds. Name the three seams:

- `clock.py` — injectable time.
- `destination.py` — idempotency ledger.
- `worker.py` — claim, deliver, commit-with-version-check, reap.

Close with the trade-off:

> "The destination ledger is in the same SQLite database. That collapses
> the dual-write problem — the send and the state update happen in one
> transaction — which is the honest simplification for a six-hour
> exercise. A real external provider would need a transactional outbox:
> write the send intent in the same transaction as the state change, and
> let a separate relay deliver it with the idempotency key. I documented
> that as the production upgrade in `docs/decisions.md`."

---

## Clock position after each scenario

| After | Clock (UTC) | Clock (IST) |
| --- | --- | --- |
| Setup | 06:30:00 | 12:00 |
| S1 | 06:31:00 | 12:01 |
| S2 | 06:33:00 | 12:03 |
| S3 | 06:36:00 | 12:06 |
| S4, S5 | unchanged | unchanged |

If you re-run any scenario, first re-check the clock position. Do not
re-run S1's creation step after S1 — it will be immediately overdue.

---

## Recording tips

- 1080p, font readable at 720p.
- Show `python -m pytest -v` for 5 seconds at the start.
- Hold the benchmark output on screen for 5 seconds at the end.
- Narrate behavior and decisions, not code.
- If a command outputs something unexpected, leave it in. Real output
  is more convincing than a re-take.

## Upload

Host unlisted on YouTube, Loom, or Drive with "anyone with the link can
view." Then add to `SUBMISSION.md` under `## Demo`:

    Video: <url>

    See `demo/demo_script.md` for the scenarios demonstrated.

Then:

    git add SUBMISSION.md demo/demo_script.md
    git commit -m "docs: correct demo script timings for fake-clock flow"
    git push
