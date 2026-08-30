# Rule change deployment

**Trigger.** A regulatory update, or a correction to how a rule is encoded.

## The one rule about changing rules

**Add a new version. Never edit an existing one.** ADR-020 and the rulepack's
own header both say it: the ledger records which version governed each past
decision, and rewriting a version retroactively falsifies that record.

This is easy to get wrong in a way that looks right. Bumping `version:` while
*replacing* the old entry in `rulepack.yaml` passes review — but the loader
upserts on `(rule_id, version)` from that file, so a fresh database would hold
only the new version, and every earlier decision would replay against a rule
that never governed it. **Keep both entries.**

## Action

1. Add the new version to `prayas/gate/rules/rulepack.yaml`, leaving the
   previous entry in place.
2. Set `as_of` to the date the new version **takes effect**. If you are
   correcting an encoding rather than tracking a regulatory change, that is
   today — not the regulation's date. Back-dating makes the new version govern
   decisions made before it existed.
3. Keep `citation` accurate. If the regulation did not change, the citation
   does not change either (Invariant 10).
4. Write a migration that inserts the new version. Do not rely on re-running
   the loader.
5. Every rule needs a test covering **both sides** of its boundary before it is
   activated.

## Verification

```sql
SELECT version, as_of, rails FROM compliance_rules
WHERE rule_id = 'RULE' ORDER BY version;
-- every version that ever shipped is still present
```

Then rebuild from zero and confirm both versions load:

```
alembic downgrade base && alembic upgrade head
```

That last step is the one that catches an in-place edit. A development database
still holds the old row, so the mistake is invisible until a clean build.
