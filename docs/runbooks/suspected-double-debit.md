# Suspected double debit

**Trigger.** A duplicate `provider_ref`, or a customer reporting two charges.

**Severity.** Sev-1 always. §42 gives this SLI a zero error budget: "any
occurrence is a Sev-1." It is a correctness property, not a reliability target.

## Diagnosis

```sql
-- Two attempts on one cycle that both reached the provider.
SELECT cycle_id, count(*), array_agg(attempt_id), array_agg(provider_ref)
FROM attempts
WHERE tenant_id = 'TENANT' AND provider_ref IS NOT NULL
GROUP BY cycle_id HAVING count(*) > 1;
```

```sql
-- The same idempotency key submitted under two different keys is the real
-- failure. Invariant 4 makes the key deterministic in
-- (cycle_id, attempt_seq, action_type, amount_paise), so two keys for one
-- logical attempt means the key derivation changed or an input drifted.
SELECT idem_key, count(*) FROM outbox
WHERE tenant_id = 'TENANT' GROUP BY idem_key HAVING count(*) > 1;
```

Check whether it was an ambiguous outcome resolved wrongly:

```sql
SELECT * FROM outbox WHERE tenant_id = 'TENANT' AND state = 'ambiguous';
```

## Action

1. **Stop the affected mandate first**, before investigating further — see
   [emergency-stop](emergency-stop.md). Diagnosis can wait; a third debit
   cannot.
2. Establish whether money actually moved twice, by querying the provider with
   `fetch_by_key`. **Never re-submit to find out**: re-submission is
   indistinguishable from trying again.
3. If two charges landed, initiate a refund through the rail's own path.
4. Preserve the ledger. Do not delete or amend `decisions` — it is append-only
   (Invariant 5) and it is the evidence.

## Verification

- `verify_chain` returns no breaks for the tenant.
- No further attempts on the affected cycle.
- The provider's record for the key shows exactly the charges you expect.
