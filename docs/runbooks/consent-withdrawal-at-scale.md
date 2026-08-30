# Consent withdrawal at scale

**Trigger.** A bulk DPDP erasure request.

## Action

The cascade is `prayas.memory.forget.forget`, and it is idempotent — a run that
was interrupted can simply be run again.

```python
from prayas.memory.forget import forget
for customer_id in customer_ids:
    async with tenant_transaction(engine, tenant_id) as conn:
        await forget(conn, tenant_id, customer_id)
```

**One transaction per customer**, not one for the batch. A single transaction
over thousands of customers holds locks for minutes and an interruption loses
all of it; per-customer, an interruption loses one.

## What it does, and what it deliberately does not

- Deletes the profile.
- Blanks `inbound_replies.raw_text` and pseudonymises the reference — the fact
  of a reply is an operational record, the customer's words are not.
- Pseudonymises `mandates.customer_id`, which severs the person-link the ledger
  joins through.
- Writes a contact suppression keyed on the **pseudonym**, so it survives the
  deletion that triggered it.
- **Does not touch `decisions`.** §28 is explicit: financial records carry
  statutory retention, and "an audit trail with holes is not an audit trail."
  Invariant 5 holds and the hash chain still verifies.

## Prerequisites

`PRAYAS_PSEUDONYM_PEPPER` must be set. `forget()` refuses without it — a
pseudonym derived from identifiers alone is reversible by anyone holding a
customer list.

**Rotating the pepper breaks every pseudonym already written.** Erased
customers would stop matching their own suppression rows. Treat it as permanent
per environment.

## Verification

```sql
SELECT count(*) FROM customer_profiles WHERE customer_id = ANY(:ids);   -- 0
SELECT count(*) FROM mandates WHERE customer_id = ANY(:ids);            -- 0
SELECT count(*) FROM contact_suppressions WHERE tenant_id = :t;         -- grew
```

And `verify_chain` returns the same result as before the run.
