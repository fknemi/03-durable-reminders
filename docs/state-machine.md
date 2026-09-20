# State Machine

## States

- `scheduled` — waiting for `next_attempt_at` to arrive
- `running` — claimed by a worker, holding a lease until `lease_until`
- `delivered` — terminal; one logical notification produced
- `cancelled` — terminal; user cancelled before delivery committed
- `failed` — terminal; retries exhausted or permanent failure

## Transitions

    scheduled --claim--> running
    running --success/duplicate--> delivered
    running --temp fail (attempts remain)--> scheduled
    running --temp fail (attempts exhausted)--> failed
    running --perm fail--> failed
    running --lease expired (reaper)--> scheduled
    running --edit--> scheduled (version++)
    running --cancel--> cancelled (version++)
    scheduled --edit--> scheduled (version++)
    scheduled --cancel--> cancelled (version++)

## Guards

- Claim only from `scheduled` where `next_attempt_at <= now`.
- Commit only if `version` and `status` still match the claim.
- Edit and cancel rejected from terminal states.

## Invariants

1. At most one active lease per reminder.
2. Every `delivered` reminder has exactly one row in `deliveries`.
3. Every `cancelled` reminder has zero rows in `deliveries`.
4. Every `failed` reminder has either:
   - one `permanent_failure` attempt (permanent path), or
   - exactly `max_attempts` `temporary_failure` attempts (exhaustion path).
5. `delivery_key` always equals `f"{id}:v{version}"`.

## The three moves in the worker

1. **claim_due** — a single atomic `UPDATE ... RETURNING` inside
   `BEGIN IMMEDIATE`. Sets `status='running'`, `claimed_version=version`,
   `lease_until=now+30s`, `attempt_count+=1`.
2. **commit_delivery** — opens its own transaction, re-reads `version` and
   `status`. If either changed, records `superseded` and exits. Otherwise
   calls the destination and applies one of the terminal or retry
   transitions.
3. **reap_expired** — resets `running` rows whose `lease_until < now` back
   to `scheduled`. Recovers crashed workers.

## Why the version re-check matters

Between claim and commit, a user can edit or cancel the reminder. The
re-check inside the commit transaction is the only place where the race is
decided. If the worker committed without re-reading, an edited or cancelled
reminder could still fire. The re-check is what makes AC5 and AC6 hold.
