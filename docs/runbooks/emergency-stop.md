# Emergency stop

**Trigger.** A compliance or legal instruction to cease collection, or any
Sev-1 where continuing to debit is worse than stopping.

**Budget.** Under sixty seconds, one person (§45). Measured on every CI run by
`test_the_emergency_stop_completes_inside_its_budget`.

## Action

Pick the narrowest scope that covers the instruction. Stopping more than
necessary is recoverable; stopping less is not.

```sql
-- Platform: everything, every tenant. Takes no tenant argument on purpose —
-- an emergency stop that required enumerating tenants could be half-applied
-- while you are being paged.
INSERT INTO tenants (tenant_id, name, config)
VALUES ('__platform__', 'platform kill switch',
        jsonb_build_object('stopped', true, 'reason', 'REASON', 'stopped_at', now()::text))
ON CONFLICT (tenant_id) DO UPDATE
SET config = tenants.config || jsonb_build_object('stopped', true,
             'reason', 'REASON', 'stopped_at', now()::text);
```

```sql
-- One tenant.
UPDATE tenants SET config = config || jsonb_build_object('stopped', true,
       'reason', 'REASON', 'stopped_at', now()::text)
WHERE tenant_id = 'TENANT';
```

```sql
-- One mandate. A single customer dispute does not warrant stopping a tenant.
UPDATE tenants SET config = jsonb_set(config, '{stopped_mandates}',
       COALESCE(config->'stopped_mandates', '[]'::jsonb) || to_jsonb('MANDATE'::text))
WHERE tenant_id = 'TENANT';
```

Or from Python, which is what the drill exercises:

```python
from prayas.executor.killswitch import stop, PLATFORM
await stop(conn, scope=PLATFORM, reason="regulator instruction")
```

## Verification

```sql
SELECT tenant_id, config->>'stopped', config->>'reason', config->>'stopped_at'
FROM tenants WHERE config->>'stopped' = 'true';
```

Then confirm nothing is firing: `attempts` should gain no rows with
`fired_at > ` the stop time.

```sql
SELECT count(*) FROM attempts WHERE fired_at > 'STOP_TIME';  -- expect 0
```

## Notes

**The switch fails closed.** If its state cannot be read, `is_stopped` returns
true and nothing fires. That is deliberate: you reach for this during an
incident, which is exactly when the database is least healthy.

**Restarting is not symmetric** (ADR-084). There is no `resume_all`. Lift the
switch per scope, naming what you are restarting:

```python
from prayas.executor.killswitch import resume, TENANT
await resume(conn, scope=TENANT, tenant_id="TENANT")
```

An accidental un-stop should not cost as little as the stop did.
