# Gate misconfiguration

**Trigger.** Deny-rate anomaly — a sharp rise, or a suspicious fall.

**A fall is the more dangerous direction.** A rise costs revenue and is loud.
A fall means attempts are passing that previously did not, and Invariant 1 says
no code path debits without passing the gate.

## Diagnosis

```sql
-- Which rules are actually active right now, for a rail.
SELECT DISTINCT ON (rule_id) rule_id, version, as_of, rails, on_fail
FROM compliance_rules
WHERE active AND 'debit_attempt' = ANY(applies_to) AND as_of <= current_date
  AND (rails IS NULL OR 'upi_autopay' = ANY(rails))
ORDER BY rule_id, as_of DESC, version DESC;
```

```sql
-- Denials by rule, last 24h.
SELECT c->>'rule_id' AS rule, c->>'verdict' AS verdict, count(*)
FROM decisions, jsonb_array_elements(compliance_checks) c
WHERE ts > now() - interval '24 hours'
GROUP BY 1, 2 ORDER BY 3 DESC;
```

```sql
-- Degraded decisions: the gate could not establish the rules and denied.
SELECT count(*) FROM decisions WHERE degraded AND ts > now() - interval '1 hour';
```

## Action

- **Deny rate up, `degraded` true** — the rule store is unreachable. The gate
  is failing closed as designed (Invariant 2). Fix connectivity; do not disable
  the check.
- **Deny rate up, `degraded` false** — a rule version activated. Check `as_of`
  dates; a rule dated today starts governing today.
- **Deny rate down** — a rule stopped applying. Check `rails` on the rule: an
  enumerated list that omits a rail silently exempts it (this was
  FINDING-P14-01). Check `active` and `as_of` too.

Never edit a rule version in place to fix this. Add a new version — the ledger
records which version governed each past decision.

## Verification

Deny rate returns to its prior band, and `SELECT count(*) FROM decisions WHERE
degraded` stops growing.
