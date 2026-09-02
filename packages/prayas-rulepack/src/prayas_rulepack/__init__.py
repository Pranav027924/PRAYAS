"""Indian recurring-payments compliance rules, as versioned data.

Every rule carries a **regulator, a citation, and an `as_of` date**. A rule
without them is an opinion, not a rule — so the loader refuses to load one.

The pack is deliberately small and dependency-light. Anyone deciding whether to
trust these rules should be able to read the whole tree in an afternoon, and a
compliance artifact that dragged in a web framework would be harder to audit
than the rules it carries.

    from prayas_rulepack import load_rules, evaluate

    rules = load_rules(action_type="debit_attempt", rail="upi_autopay")
    verdict = evaluate(rules, {"hour_ist": 11.0, "pdn_sent_at": None, ...})

**Versions are never edited, only added.** The `as_of` date selects which
version governed a decision at a given moment, which is what makes a past
decision reviewable against the rule that actually applied to it rather than
against today's.
"""

from prayas_rulepack.evaluate import Check, Verdict, evaluate
from prayas_rulepack.loader import (
    Rule,
    RulePackError,
    afa_caps,
    all_rules,
    load_rules,
)
from prayas_rulepack.predicate import PredicateError, safe_eval, validate

__all__ = [
    "Check",
    "PredicateError",
    "Rule",
    "RulePackError",
    "Verdict",
    "afa_caps",
    "all_rules",
    "evaluate",
    "load_rules",
    "safe_eval",
    "validate",
]

__version__ = "0.1.0"
