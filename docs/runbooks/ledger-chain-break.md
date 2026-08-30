# Ledger chain break

**Trigger.** The verifier reports a `ChainBreak` — a sequence gap or a hash
mismatch.

**Severity.** Sev-1. §32's whole claim is that the ledger has not been altered;
a break means that claim is currently unsupportable.

## Diagnosis

```python
from prayas.ledger.chain import verify_chain
breaks = await verify_chain(conn, tenant_id)
```

Each `ChainBreak` names `chain_seq`, `decision_id`, `expected` and `found`.

- **`sequence gap`** — a decision was never written, or was deleted. Deletion
  should be impossible: the app role holds `SELECT, INSERT` only.
- **`hash mismatch`** — a row's content no longer hashes to what the next row
  recorded. Either the row was edited, or the canonicalisation changed.

The second cause is the likelier one and the less alarming: if
`prayas/ledger/canonical.py` changed, every historical hash recomputes
differently. Check `git log` on that file before assuming tampering.

## Action

1. **Do not repair the chain.** A "fixed" chain is worthless as evidence.
2. Establish the cause: canonicalisation change, or genuine mutation.
3. If canonicalisation changed, revert it. The old form is what the stored
   hashes were computed under.
4. If a row was mutated, this is a security incident. Preserve everything,
   check `GRANT`s on `decisions`, and treat the database credentials as
   compromised until shown otherwise.

## Verification

`verify_chain` returns an empty list for every tenant, and the grants are:

```sql
SELECT privilege_type FROM information_schema.role_table_grants
WHERE grantee = 'prayas_app' AND table_name = 'decisions';
-- expect exactly SELECT and INSERT
```
