"""Evaluate a set of rules against one action's context.

**Fails closed.** A predicate that raises — a missing context key, a type that
cannot be compared — denies. That is not defensive coding: the alternative is a
system that permits a debit because it could not work out whether the debit was
lawful, which is the worst available outcome for the customer whose account it
comes out of.

**Every rule is evaluated, even after one denies.** It would be cheaper to stop
at the first failure, but then the report would say a debit broke one rule when
it broke three, and whoever is fixing it would fix a third of the problem.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from prayas_rulepack.loader import ALLOW, VERDICT_PRECEDENCE, Rule, afa_caps
from prayas_rulepack.predicate import PredicateError, safe_eval

#: The wildcard MCC carrying the default AFA-free ceiling.
DEFAULT_MCC: Final = "*"


def make_afa_free_cap(as_of: date | None = None) -> Callable[[str | None], int]:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    Some rules need a value derived from the pack's own `afa_caps` table rather
    than supplied by the caller. Building it here means a third party can
    evaluate the pack without knowing that — the first version of this package
    shipped rules that could not be evaluated without it, which made the
    published artifact incomplete in a way only a run would reveal.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the
    predicate errors and the rule denies rather than permitting any amount.
    """
    when = as_of or date.today()  # noqa: DTZ011 — a cap's effective date, not a timestamp
    caps: dict[str, int] = {}
    for entry in afa_caps():
        cap_as_of = entry.get("as_of")
        if isinstance(cap_as_of, date) and cap_as_of > when:
            continue
        caps[str(entry["mcc"])] = int(entry["cap_paise"])

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


@dataclass(frozen=True, slots=True)
class Check:
    """One rule's verdict, with everything needed to cite it."""

    rule_id: str
    version: int
    regulator: str
    citation: str
    as_of: str
    verdict: str
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == ALLOW


@dataclass(frozen=True, slots=True)
class Verdict:
    """The overall answer and the evidence behind it."""

    verdict: str
    checks: tuple[Check, ...]

    @property
    def allowed(self) -> bool:
        """Only a clean ALLOW permits the action.

        DEFER and ESCALATE_HUMAN are not permission. They are "not yet" and
        "a person decides", and treating either as allowed would turn a
        held action into a debit.
        """
        return self.verdict == ALLOW

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def explain(self) -> str:
        """Why, in citations. The point of the pack is that this is possible."""
        if self.allowed:
            return f"allowed; {len(self.checks)} rules evaluated, none failed"
        return "denied by " + "; ".join(
            f"{c.rule_id} v{c.version} ({c.regulator}, {c.citation})" for c in self.failures
        )


def evaluate(rules: list[Rule], context: dict[str, Any], *, as_of: date | None = None) -> Verdict:
    """Evaluate every rule against `context`.

    Values the pack can derive from itself — currently the AFA-free ceiling —
    are supplied automatically, so a caller only has to provide facts about the
    *action*. A caller may still override them by passing them in.
    """
    bound: dict[str, Any] = {"afa_free_cap": make_afa_free_cap(as_of), **context}

    checks: list[Check] = []
    worst = ALLOW

    for rule in rules:
        error: str | None = None
        try:
            passed = safe_eval(rule.predicate, bound)
        except PredicateError as exc:
            # Could not establish whether the rule holds. Treat as failed.
            passed, error = False, str(exc)

        verdict = ALLOW if passed else rule.on_fail
        # §30.2: the most severe verdict wins. Not simply "any DENY denies" —
        # an ESCALATE_HUMAN must not be silently downgraded to ALLOW because
        # another rule passed, and it must not be upgraded to DENY either.
        if VERDICT_PRECEDENCE[verdict] > VERDICT_PRECEDENCE[worst]:
            worst = verdict

        checks.append(
            Check(
                rule_id=rule.rule_id,
                version=rule.version,
                regulator=rule.regulator,
                citation=rule.citation,
                as_of=rule.as_of.isoformat(),
                verdict=verdict,
                error=error,
            )
        )

    return Verdict(verdict=worst, checks=tuple(checks))
