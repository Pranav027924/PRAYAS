"""Compliance gate evaluation (Master Spec §30.2; Invariants 1 and 2).

Every side effect in the system crosses this one chokepoint (D6). Two properties
govern everything here:

**It fails closed.** There is no ALLOW-on-failure branch anywhere in this module.
A predicate error, an unreachable rule store, an unknown `on_fail` value, a rule
version we cannot interpret — all deny. Invariant 2 is not a preference; a gate
that fails open is worse than no gate, because it looks like protection.

**It never short-circuits.** All rules are evaluated even after a DENY, because
§30.2 is explicit: "A compliance reviewer asking 'was the DND check performed?'
needs a positive answer regardless of what else failed first." The audit record
must show every rule that was checked, not every rule up to the first failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.gate.predicate import PredicateError, safe_eval
from prayas.observability import metrics

log = logging.getLogger(__name__)

#: §30.2. Higher wins when combining verdicts.
VERDICT_PRECEDENCE: Final[dict[str, int]] = {
    "DENY": 3,
    "ESCALATE_HUMAN": 2,
    "DEFER": 1,
    "ALLOW": 0,
}

ALLOW: Final = "ALLOW"
DENY: Final = "DENY"

#: The verdict used whenever anything at all goes wrong.
FAIL_CLOSED_VERDICT: Final = DENY

#: Default AFA-free ceiling key in `regulatory_reference`.
DEFAULT_MCC: Final = "*"


@dataclass(frozen=True, slots=True)
class RuleRecord:
    rule_id: str
    version: int
    regulator: str
    citation: str
    as_of: date
    predicate: str
    on_fail: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """A verdict plus the complete evidence for it.

    `checks` goes verbatim into `decisions.compliance_checks`, which is what a
    reviewer reads under §32 replay.
    """

    verdict: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW

    def denied_rules(self) -> list[str]:
        return [c["rule_id"] for c in self.checks if c["verdict"] != ALLOW]


class RuleStoreUnavailableError(RuntimeError):
    """The rule store could not be read. Always produces DENY."""


async def load_active_rules(
    conn: AsyncConnection, action_type: str, rail: str | None, as_of: date
) -> list[RuleRecord]:
    """Rules in force for this action at `as_of`, newest version of each rule_id.

    `as_of` is the decision instant, so a replay of a past decision selects the
    rule versions that governed *then* — the property §30.1 says actually matters
    under review.
    """
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (rule_id)"
                "   rule_id, version, regulator, citation, as_of, predicate, on_fail"
                " FROM compliance_rules"
                " WHERE active"
                "   AND :action_type = ANY(applies_to)"
                "   AND as_of <= :as_of"
                "   AND (rails IS NULL OR :rail = ANY(rails))"
                " ORDER BY rule_id, as_of DESC, version DESC"
            ),
            {"action_type": action_type, "rail": rail, "as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return [
        RuleRecord(
            rule_id=row.rule_id,
            version=row.version,
            regulator=row.regulator,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def load_afa_caps(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


def make_afa_free_cap(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def evaluate_rules(
    rules: list[RuleRecord], ctx: dict[str, Any], funcs: dict[str, Any] | None = None
) -> GateResult:
    """Evaluate every rule. Pure, so it is directly testable and mutation-tested.

    Note the loop has no `break` and no `return` inside it. That absence is the
    §30.2 requirement, and it is what the mutation tests defend.
    """
    checks: list[dict[str, Any]] = []
    worst = ALLOW

    for rule in rules:
        try:
            passed = safe_eval(rule.predicate, ctx, funcs)
        except PredicateError as exc:
            passed = False  # fail closed
            metrics.increment("predicate_error", rule_id=rule.rule_id, error=str(exc))
            log.warning(
                "gate.predicate_error",
                extra={"rule_id": rule.rule_id, "version": rule.version, "error": str(exc)},
            )

        verdict = ALLOW if passed else _coerce_on_fail(rule)
        checks.append(
            {
                "rule_id": rule.rule_id,
                "version": rule.version,
                "regulator": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def _coerce_on_fail(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


async def evaluate(
    conn: AsyncConnection,
    *,
    action_type: str,
    rail: str | None,
    ctx: dict[str, Any],
    as_of: date,
) -> GateResult:
    """The chokepoint. Returns a verdict and the evidence behind it.

    Any failure to establish what the rules *are* produces DENY with
    `degraded=True`, which is recorded in the ledger so a reviewer can tell a
    considered denial from a degraded one.
    """
    try:
        rules = await load_active_rules(conn, action_type, rail, as_of)
        caps = await load_afa_caps(conn, as_of)
    except RuleStoreUnavailableError as exc:
        metrics.increment("gate_degraded", action_type=action_type, reason="rule_store")
        log.error("gate.rule_store_unavailable", extra={"error": str(exc)})
        return GateResult(
            verdict=FAIL_CLOSED_VERDICT,
            checks=[
                {
                    "rule_id": "__RULE_STORE__",
                    "version": 0,
                    "regulator": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 2 — the gate fails closed",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    if not rules:
        # No applicable rule is not the same as "permitted". An action type the
        # rule pack does not cover has not been reviewed, so it does not proceed.
        metrics.increment("gate_no_rules", action_type=action_type)
        log.error("gate.no_applicable_rules", extra={"action_type": action_type})
        return GateResult(
            verdict=FAIL_CLOSED_VERDICT,
            checks=[
                {
                    "rule_id": "__NO_RULES__",
                    "version": 0,
                    "regulator": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})
