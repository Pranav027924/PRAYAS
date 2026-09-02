"""Pure gate evaluation logic (§30.2) — the mutation-testing target.

These live in `tests/unit/` deliberately. They need no database, and mutmut is
scoped to `tests/unit/` (ADR-023): a pure function whose only tests sit under
`tests/integration/` reports "no tests" and every mutant of it survives
unexamined, which is worse than having no mutation testing at all because it
looks like coverage.

The properties asserted here are absences — no ALLOW-on-failure branch, no
short-circuit — which is exactly what mutation testing is good at defending.
"""

from __future__ import annotations

from datetime import date

import pytest
from prayas_rulepack.predicate import PredicateError

from prayas.gate.engine import (
    ALLOW,
    DENY,
    VERDICT_PRECEDENCE,
    GateResult,
    RuleRecord,
    evaluate_rules,
    make_afa_free_cap,
)

TODAY = date(2026, 8, 25)


def _rule(rule_id: str, predicate: str, on_fail: str) -> RuleRecord:
    return RuleRecord(
        rule_id=rule_id,
        version=1,
        regulator="TEST",
        citation="test citation",
        as_of=TODAY,
        predicate=predicate,
        on_fail=on_fail,
    )


# ── verdict precedence ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("on_fails", "expected"),
    [
        ([], ALLOW),
        (["DEFER"], "DEFER"),
        (["ESCALATE_HUMAN"], "ESCALATE_HUMAN"),
        (["DENY"], DENY),
        (["DEFER", "ESCALATE_HUMAN"], "ESCALATE_HUMAN"),
        (["ESCALATE_HUMAN", "DEFER"], "ESCALATE_HUMAN"),
        (["DEFER", "ESCALATE_HUMAN", "DENY"], DENY),
        (["DENY", "DEFER"], DENY),
        (["DENY", "ESCALATE_HUMAN", "DEFER"], DENY),
    ],
)
def test_worst_verdict_wins_regardless_of_rule_order(on_fails: list[str], expected: str) -> None:
    rules = [_rule(f"R{i}", "False", of) for i, of in enumerate(on_fails)]
    assert evaluate_rules(rules, {}).verdict == expected


def test_precedence_table_is_strictly_ordered() -> None:
    """DENY > ESCALATE_HUMAN > DEFER > ALLOW (§30.2)."""
    assert (
        VERDICT_PRECEDENCE["DENY"]
        > VERDICT_PRECEDENCE["ESCALATE_HUMAN"]
        > VERDICT_PRECEDENCE["DEFER"]
        > VERDICT_PRECEDENCE["ALLOW"]
    )


def test_a_passing_rule_contributes_allow_not_its_on_fail() -> None:
    assert evaluate_rules([_rule("R", "True", DENY)], {}).verdict == ALLOW


# ── no short-circuiting ─────────────────────────────────────────────────────


def test_every_rule_is_recorded_even_after_a_deny() -> None:
    """§30.2 — "was the DND check performed?" needs a positive answer."""
    rules = [
        _rule("FIRST_DENIES", "False", DENY),
        _rule("SECOND", "True", DENY),
        _rule("THIRD", "True", DENY),
    ]
    result = evaluate_rules(rules, {})

    assert len(result.checks) == 3, "evaluation stopped at the first denial"
    assert [c["rule_id"] for c in result.checks] == ["FIRST_DENIES", "SECOND", "THIRD"]


def test_checks_preserve_rule_order() -> None:
    rules = [_rule("A", "True", DENY), _rule("B", "True", DENY)]
    assert [c["rule_id"] for c in evaluate_rules(rules, {}).checks] == ["A", "B"]


def test_each_check_carries_full_provenance() -> None:
    """Invariant 10 — this is what a reviewer reads under §32 replay."""
    (check,) = evaluate_rules([_rule("R", "True", DENY)], {}).checks

    assert check["rule_id"] == "R"
    assert check["version"] == 1
    assert check["regulator"] == "TEST"
    assert check["citation"] == "test citation"
    assert check["as_of"] == str(TODAY)
    assert check["verdict"] == ALLOW


# ── fail closed ─────────────────────────────────────────────────────────────


def test_a_predicate_error_denies_and_is_still_recorded() -> None:
    result = evaluate_rules([_rule("BROKEN", "missing_feature > 1", DENY)], {})

    assert result.verdict == DENY
    assert len(result.checks) == 1
    assert result.checks[0]["verdict"] == DENY


def test_a_predicate_error_uses_the_rules_own_on_fail() -> None:
    """A DEFER rule that errors defers; it does not escalate to DENY."""
    assert evaluate_rules([_rule("E", "boom > 1", "DEFER")], {}).verdict == "DEFER"


def test_an_unrecognised_on_fail_denies() -> None:
    """Invariant 2 — the gate must not trust a database CHECK it cannot see."""
    assert evaluate_rules([_rule("W", "False", "PROBABLY_FINE")], {}).verdict == DENY


def test_a_non_boolean_predicate_denies() -> None:
    """Truthiness must never decide a compliance verdict."""
    assert evaluate_rules([_rule("N", "1 + 1", DENY)], {}).verdict == DENY


def test_empty_rule_set_is_allow_at_the_pure_layer() -> None:
    """`evaluate` applies the fail-closed empty-set policy, not `evaluate_rules`."""
    result = evaluate_rules([], {})
    assert result.verdict == ALLOW
    assert result.checks == []


# ── GateResult helpers ──────────────────────────────────────────────────────


def test_allowed_is_true_only_for_allow() -> None:
    assert GateResult(verdict=ALLOW).allowed is True
    for verdict in ("DENY", "DEFER", "ESCALATE_HUMAN"):
        assert GateResult(verdict=verdict).allowed is False


def test_denied_rules_lists_every_non_allow_check() -> None:
    rules = [
        _rule("PASSES", "True", DENY),
        _rule("DENIES", "False", DENY),
        _rule("DEFERS", "False", "DEFER"),
    ]
    assert evaluate_rules(rules, {}).denied_rules() == ["DENIES", "DEFERS"]


def test_a_result_defaults_to_not_degraded() -> None:
    assert GateResult(verdict=ALLOW).degraded is False


# ── AFA ceiling resolution ──────────────────────────────────────────────────


def test_known_mcc_uses_its_own_ceiling() -> None:
    cap = make_afa_free_cap({"*": 1_500_000, "6300": 10_000_000})
    assert cap("6300") == 10_000_000


def test_unknown_mcc_falls_back_to_the_default_never_to_unlimited() -> None:
    cap = make_afa_free_cap({"*": 1_500_000, "6300": 10_000_000})
    assert cap("9999") == 1_500_000
    assert cap(None) == 1_500_000


def test_a_missing_default_ceiling_denies_rather_than_permitting_anything() -> None:
    """Fail closed: no configured ceiling must not read as an unlimited one."""
    cap = make_afa_free_cap({"6300": 10_000_000})

    assert cap("6300") == 10_000_000  # explicit entries still resolve
    with pytest.raises(PredicateError, match="no AFA-free ceiling"):
        cap("9999")
    with pytest.raises(PredicateError, match="no AFA-free ceiling"):
        cap(None)


def test_an_empty_cap_table_denies_every_lookup() -> None:
    cap = make_afa_free_cap({})
    with pytest.raises(PredicateError):
        cap("6300")
