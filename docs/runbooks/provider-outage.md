# Provider outage

**Trigger.** Elevated ambiguous responses, or the issuer nowcast reporting a
rail down.

## Diagnosis

```sql
SELECT state, count(*) FROM outbox
WHERE tenant_id = 'TENANT' GROUP BY state;
```

```python
from prayas.inference.nowcast import IssuerNowcast
nowcast.degraded_issuers()   # issuer -> since when
```

## Action

**Do not infer failure from silence.** An ambiguous outcome means the debit
*may* have happened. §31's rule is: retry with the same key, then reconcile by
key — never re-submit to discover an outcome.

```python
from prayas.executor.outbox import reconcile_ambiguous
resolved = await reconcile_ambiguous(engine, tenant_id, provider)
```

On eNACH specifically, an outcome that has not arrived is **not** a failure.
The rail settles T+1 on working days, and `retry_is_blocked` holds retries for
both awaiting and overdue attempts — presenting again into the next clearing
cycle would debit the customer twice on a rail where reversal is slow and
manual.

If the outage is prolonged, stop the affected tenant rather than accumulating
ambiguous rows: see [emergency-stop](emergency-stop.md).

## Verification

- `outbox` has no rows stuck in `ambiguous` older than the reconcile interval.
- No cycle has two attempts with a `provider_ref`.
- The nowcast reports the issuer healthy for at least two consecutive windows.
