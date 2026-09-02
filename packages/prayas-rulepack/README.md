# prayas-rulepack

Indian recurring-payments compliance rules, with citations, as versioned data.

Every rule carries a **regulator, a citation, and an `as_of` date**. A rule
without them is an opinion, not a rule — so the loader refuses to load one.

One dependency (PyYAML), deliberately. Anyone deciding whether to trust these
rules should be able to read the whole tree in an afternoon.

## Install

```bash
pip install prayas-rulepack        # from a checkout: pip install packages/prayas-rulepack
```

## The audit

The reason this package exists standalone. Run it:

```bash
prayas-audit
```

```
The default recurring-retry behaviour widely deployed today — retries on
days 1, 3, 5 at 10:00 IST — produces 30,000 debit attempts outside NPCI
execution windows and 30,000 debits without valid 24-hour pre-debit notice,
per 10,000 cycles.
```

**What that measures.** A *documented schedule* — day 1/3/5 at 10:00 IST, the
default in most dunning tools — evaluated against these rules. NPCI's execution
windows are fixed clock hours, so an attempt at 10:00 IST falls outside them
whoever it debits. The per-10,000 figure is arithmetic on the schedule, not an
extrapolation from a sample.

**What it does not measure.** How often those attempts succeed. Any real
merchant's traffic. Whether any particular product ships this exact schedule.

## Using the rules

```python
from datetime import date
from prayas_rulepack import load_rules, evaluate

rules = load_rules(action_type="debit_attempt", rail="upi_autopay")

verdict = evaluate(
    rules,
    {
        "hour_ist": 11.0,  # inside the NPCI peak
        "pdn_sent_at": None,  # no pre-debit notice
        "is_next_day_debit": False,
        "amount_paise": 49_900,
        "mcc": "5812",
        "consent_ref": "c_1",
        "consent_withdrawn": False,
        "dlt_template_id": "T1",
        "header_series": "160",
        "dnd_registered": False,
        "messages_30d": 0,
        "tenant_fatigue_cap": 3,
    },
)

print(verdict.verdict)  # DENY
print(verdict.explain())  # denied by NPCI-AUTOPAY-WINDOW v3 (NPCI, ...); ...
```

## Two design choices worth knowing about

**Versions are added, never edited.** `load_rules(as_of=...)` returns the
versions in force *then*, so a decision made last quarter can be reviewed
against the rule that actually applied to it. Editing a version in place would
retroactively change what the past was judged by.

```python
load_rules(action_type="debit_attempt", rail="upi_autopay", as_of=date(2026, 5, 1))
```

**`rails: null` means every rail, including ones that do not exist yet.** A rule
that enumerates the rails it knows about silently exempts any rail added later,
and the resulting debit looks lawful. The pack that this was extracted from hit
exactly that, and it is why the notice rule is `null` rather than a list.

## Evaluation is fail-closed

A predicate that raises — a missing context key, an uncomparable type — denies.
The alternative is a system that permits a debit because it could not work out
whether the debit was lawful, which is the worst outcome for the person whose
account it comes from.

Every rule is evaluated even after one denies, so a report says a debit broke
three rules rather than the first one found.

Verdicts are ordered `DENY > ESCALATE_HUMAN > DEFER > ALLOW`; the most severe
wins. **Only a clean `ALLOW` permits an action** — `DEFER` is "not yet" and
`ESCALATE_HUMAN` is "a person decides", and treating either as permission would
turn a held action into a debit.

## The predicate sandbox

Predicates are expressions evaluated against an AST whitelist: no attribute
access, no subscripting, no imports, no comprehensions, and no calls except to
a fixed set of helpers. `__builtins__` is emptied. See `predicate.py` — it is
about 150 lines and worth reading before you trust it.

## Contributing a regulatory change

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: **add a new version,
never edit an existing one**, and every rule needs a test on both sides of its
boundary before it ships.

## Licence

Apache-2.0.
