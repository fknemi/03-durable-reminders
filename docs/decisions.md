# Design Decisions

This document records the eight decisions the brief requires. Each decision
states the policy, the rationale, and the residual risk.

## 1. How local time becomes an execution instant

**Policy.** Input is a naive local datetime string plus an IANA timezone name.
We resolve it to a UTC instant using `zoneinfo` (stdlib). We store both the
original local request and the resolved UTC instant.

For ambiguous local times (DST fall-back, the wall clock repeats):
we choose the **first occurrence** (pre-transition, DST offset).

For nonexistent local times (DST spring-forward, the wall clock skips):
we **shift forward by the gap** (02:30 becomes 03:30).

**Rationale.** Both are deterministic, explainable, and testable. Choosing the
first occurrence for ambiguity matches "the earliest moment the user's stated
wall clock could have meant." Shifting forward for a gap preserves the user's
intent to be woken *after* the transition rather than silently dropping the
reminder.

**Residual.** A user who meant the second occurrence of an ambiguous time is
not represented; the stored `local_requested` string preserves the input so
the choice is auditable.

## 2. How due work is discovered and claimed

**Policy.** A single atomic SQL statement: under `BEGIN IMMEDIATE`, select due
rows and update them in one shot to `status='running'`, setting
`claimed_version=version`, `lease_until=now+30s`, `attempt_count+=1`.

**Rationale.** Atomicity is the whole point. Two workers running the same
statement cannot both claim the same row. `BEGIN IMMEDIATE` acquires the
write lock before the select, avoiding the classic SQLite read-then-write
race.

**Residual.** SQLite serializes writers. Correct, not high-throughput.

## 3. Which failures are retryable and why

**Policy.** The destination returns a typed outcome. `temporary_failure`
(timeout, connection reset, 5xx) is retryable. `permanent_failure`
(4xx, malformed payload) is not.

**Rationale.** Retrying a 400 wastes attempts and delays the terminal state.
Retrying a 503 is the only way to recover.

**Residual.** A destination that returns 500 for a permanently bad payload
will be retried until exhaustion. Acceptable; documented as a destination
contract.

## 4. Retry limit and delay policy

**Policy.** Maximum 5 attempts. Backoff: 1s, 5s, 30s, 2m, 10m with ±20%
jitter. All delays expressed in simulated seconds.

**Rationale.** Bounded, monotonic, and fast enough that the benchmark can
settle in seconds of simulated time. Jitter prevents synchronized retry storms
across workers.

## 5. What creates a unique scheduled occurrence

**Policy.** `(reminder_id, version)`. An edit bumps the version and produces a
new occurrence. `delivery_key = f"{reminder_id}:v{version}"`.

**Rationale.** Treating a reminder as a series of occurrences rather than one
mutable row is what makes edit-racing-execution decidable.

## 6. How idempotency is enforced at the delivery boundary

**Policy.** The destination is a durable ledger keyed by `delivery_key`
(PRIMARY KEY). Insert is `INSERT OR IGNORE`. A second insert is a no-op and is
recorded as an idempotent success.

**Rationale.** Sends are at-least-once; the destination makes repeats
harmless. One logical notification per occurrence.

**Residual.** Idempotency prevents duplicates, not regrets. If the old
occurrence already reached the destination before an edit, it cannot be
un-sent.

## 7. Edit/cancellation race policy

**Policy.** Edit and cancel win. The worker re-reads `version` and `status`
inside its commit transaction. If either changed, it records `superseded`,
does not deliver, and exits. Edit and cancel both bump the version and clear
the lease.

**Rationale.** The user's latest intent is authoritative. A worker that has
merely *claimed* the old version has not yet committed an irreversible effect
(in this design), so it can safely lose the race.

**Residual.** Same as decision 6.

## 8. What changes with multiple workers

**Policy.** Multiple workers are supported for claim/execute. The atomic claim
plus lease guarantees at-most-one active lease per reminder. A reaper resets
expired leases.

**Rationale.** Correctness does not depend on a single worker. SQLite's
single-writer model serializes claims but does not break them.

**Production upgrade.** Postgres with `SELECT ... FOR UPDATE SKIP LOCKED`
replaces the SQLite claim. The transactional outbox replaces the in-DB
destination ledger.
