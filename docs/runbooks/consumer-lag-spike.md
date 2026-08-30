# Consumer lag spike

**Trigger.** Projector lag alert (§43: lag > 60s means actions are not reaching
providers).

## Diagnosis

```sql
SELECT count(*) FROM events_raw WHERE projected_at IS NULL;
SELECT min(received_at) FROM events_raw WHERE projected_at IS NULL;
```

```sql
SELECT count(*) FROM outbox WHERE state = 'pending';
```

Lag has two shapes and they need opposite responses:

- **Ingest lag** — webhooks landing faster than the projector drains. The
  system is behind but correct.
- **Relay lag** — the outbox is not draining. Actions are decided but not
  reaching the provider, so timers may fire late and the fire-time gate check
  will deny them. That is safe but it is lost revenue.

## Action

1. Check whether the database is the bottleneck before scaling workers — more
   workers against a saturated database makes lag worse.
2. `relay_once` is per-tenant and claims with `FOR UPDATE SKIP LOCKED`, so
   additional relay workers are safe to add.
3. Do **not** widen batch sizes to catch up. A larger batch holds locks longer
   and the provider call is outside the transaction for a reason.

## Verification

- Pending `events_raw` returns to its baseline.
- Oldest unprojected event is under the SLO.
- No attempts denied for out-of-window firing caused by the delay.
