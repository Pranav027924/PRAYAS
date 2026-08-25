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


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


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
mutants_x_load_active_rules__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_load_active_rules__mutmut)
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


async def x_load_active_rules__mutmut_orig(
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


async def x_load_active_rules__mutmut_1(
    conn: AsyncConnection, action_type: str, rail: str | None, as_of: date
) -> list[RuleRecord]:
    """Rules in force for this action at `as_of`, newest version of each rule_id.

    `as_of` is the decision instant, so a replay of a past decision selects the
    rule versions that governed *then* — the property §30.1 says actually matters
    under review.
    """
    try:
        result = None
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


async def x_load_active_rules__mutmut_2(
    conn: AsyncConnection, action_type: str, rail: str | None, as_of: date
) -> list[RuleRecord]:
    """Rules in force for this action at `as_of`, newest version of each rule_id.

    `as_of` is the decision instant, so a replay of a past decision selects the
    rule versions that governed *then* — the property §30.1 says actually matters
    under review.
    """
    try:
        result = await conn.execute(
            None,
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


async def x_load_active_rules__mutmut_3(
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
            None,
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


async def x_load_active_rules__mutmut_4(
    conn: AsyncConnection, action_type: str, rail: str | None, as_of: date
) -> list[RuleRecord]:
    """Rules in force for this action at `as_of`, newest version of each rule_id.

    `as_of` is the decision instant, so a replay of a past decision selects the
    rule versions that governed *then* — the property §30.1 says actually matters
    under review.
    """
    try:
        result = await conn.execute(
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


async def x_load_active_rules__mutmut_5(
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


async def x_load_active_rules__mutmut_6(
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
                None
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


async def x_load_active_rules__mutmut_7(
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
                "XXSELECT DISTINCT ON (rule_id)XX"
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


async def x_load_active_rules__mutmut_8(
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
                "select distinct on (rule_id)"
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


async def x_load_active_rules__mutmut_9(
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
                "SELECT DISTINCT ON (RULE_ID)"
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


async def x_load_active_rules__mutmut_10(
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
                "XX   rule_id, version, regulator, citation, as_of, predicate, on_failXX"
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


async def x_load_active_rules__mutmut_11(
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
                "   RULE_ID, VERSION, REGULATOR, CITATION, AS_OF, PREDICATE, ON_FAIL"
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


async def x_load_active_rules__mutmut_12(
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
                "XX FROM compliance_rulesXX"
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


async def x_load_active_rules__mutmut_13(
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
                " from compliance_rules"
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


async def x_load_active_rules__mutmut_14(
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
                " FROM COMPLIANCE_RULES"
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


async def x_load_active_rules__mutmut_15(
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
                "XX WHERE activeXX"
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


async def x_load_active_rules__mutmut_16(
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
                " where active"
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


async def x_load_active_rules__mutmut_17(
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
                " WHERE ACTIVE"
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


async def x_load_active_rules__mutmut_18(
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
                "XX   AND :action_type = ANY(applies_to)XX"
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


async def x_load_active_rules__mutmut_19(
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
                "   and :action_type = any(applies_to)"
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


async def x_load_active_rules__mutmut_20(
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
                "   AND :ACTION_TYPE = ANY(APPLIES_TO)"
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


async def x_load_active_rules__mutmut_21(
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
                "XX   AND as_of <= :as_ofXX"
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


async def x_load_active_rules__mutmut_22(
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
                "   and as_of <= :as_of"
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


async def x_load_active_rules__mutmut_23(
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
                "   AND AS_OF <= :AS_OF"
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


async def x_load_active_rules__mutmut_24(
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
                "XX   AND (rails IS NULL OR :rail = ANY(rails))XX"
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


async def x_load_active_rules__mutmut_25(
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
                "   and (rails is null or :rail = any(rails))"
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


async def x_load_active_rules__mutmut_26(
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
                "   AND (RAILS IS NULL OR :RAIL = ANY(RAILS))"
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


async def x_load_active_rules__mutmut_27(
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
                "XX ORDER BY rule_id, as_of DESC, version DESCXX"
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


async def x_load_active_rules__mutmut_28(
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
                " order by rule_id, as_of desc, version desc"
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


async def x_load_active_rules__mutmut_29(
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
                " ORDER BY RULE_ID, AS_OF DESC, VERSION DESC"
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


async def x_load_active_rules__mutmut_30(
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
            {"XXaction_typeXX": action_type, "rail": rail, "as_of": as_of},
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


async def x_load_active_rules__mutmut_31(
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
            {"ACTION_TYPE": action_type, "rail": rail, "as_of": as_of},
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


async def x_load_active_rules__mutmut_32(
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
            {"action_type": action_type, "XXrailXX": rail, "as_of": as_of},
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


async def x_load_active_rules__mutmut_33(
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
            {"action_type": action_type, "RAIL": rail, "as_of": as_of},
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


async def x_load_active_rules__mutmut_34(
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
            {"action_type": action_type, "rail": rail, "XXas_ofXX": as_of},
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


async def x_load_active_rules__mutmut_35(
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
            {"action_type": action_type, "rail": rail, "AS_OF": as_of},
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


async def x_load_active_rules__mutmut_36(
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
        raise RuleStoreUnavailableError(None) from exc

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


async def x_load_active_rules__mutmut_37(
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
        raise RuleStoreUnavailableError(str(None)) from exc

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


async def x_load_active_rules__mutmut_38(
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
            rule_id=None,
            version=row.version,
            regulator=row.regulator,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_39(
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
            version=None,
            regulator=row.regulator,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_40(
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
            regulator=None,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_41(
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
            citation=None,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_42(
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
            as_of=None,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_43(
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
            predicate=None,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_44(
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
            on_fail=None,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_45(
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
            version=row.version,
            regulator=row.regulator,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_46(
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
            regulator=row.regulator,
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_47(
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
            citation=row.citation,
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_48(
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
            as_of=row.as_of,
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_49(
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
            predicate=row.predicate,
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_50(
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
            on_fail=row.on_fail,
        )
        for row in result
    ]


async def x_load_active_rules__mutmut_51(
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
            )
        for row in result
    ]

mutants_x_load_active_rules__mutmut['_mutmut_orig'] = x_load_active_rules__mutmut_orig # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_1'] = x_load_active_rules__mutmut_1 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_2'] = x_load_active_rules__mutmut_2 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_3'] = x_load_active_rules__mutmut_3 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_4'] = x_load_active_rules__mutmut_4 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_5'] = x_load_active_rules__mutmut_5 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_6'] = x_load_active_rules__mutmut_6 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_7'] = x_load_active_rules__mutmut_7 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_8'] = x_load_active_rules__mutmut_8 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_9'] = x_load_active_rules__mutmut_9 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_10'] = x_load_active_rules__mutmut_10 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_11'] = x_load_active_rules__mutmut_11 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_12'] = x_load_active_rules__mutmut_12 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_13'] = x_load_active_rules__mutmut_13 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_14'] = x_load_active_rules__mutmut_14 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_15'] = x_load_active_rules__mutmut_15 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_16'] = x_load_active_rules__mutmut_16 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_17'] = x_load_active_rules__mutmut_17 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_18'] = x_load_active_rules__mutmut_18 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_19'] = x_load_active_rules__mutmut_19 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_20'] = x_load_active_rules__mutmut_20 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_21'] = x_load_active_rules__mutmut_21 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_22'] = x_load_active_rules__mutmut_22 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_23'] = x_load_active_rules__mutmut_23 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_24'] = x_load_active_rules__mutmut_24 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_25'] = x_load_active_rules__mutmut_25 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_26'] = x_load_active_rules__mutmut_26 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_27'] = x_load_active_rules__mutmut_27 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_28'] = x_load_active_rules__mutmut_28 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_29'] = x_load_active_rules__mutmut_29 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_30'] = x_load_active_rules__mutmut_30 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_31'] = x_load_active_rules__mutmut_31 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_32'] = x_load_active_rules__mutmut_32 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_33'] = x_load_active_rules__mutmut_33 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_34'] = x_load_active_rules__mutmut_34 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_35'] = x_load_active_rules__mutmut_35 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_36'] = x_load_active_rules__mutmut_36 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_37'] = x_load_active_rules__mutmut_37 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_38'] = x_load_active_rules__mutmut_38 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_39'] = x_load_active_rules__mutmut_39 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_40'] = x_load_active_rules__mutmut_40 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_41'] = x_load_active_rules__mutmut_41 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_42'] = x_load_active_rules__mutmut_42 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_43'] = x_load_active_rules__mutmut_43 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_44'] = x_load_active_rules__mutmut_44 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_45'] = x_load_active_rules__mutmut_45 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_46'] = x_load_active_rules__mutmut_46 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_47'] = x_load_active_rules__mutmut_47 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_48'] = x_load_active_rules__mutmut_48 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_49'] = x_load_active_rules__mutmut_49 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_50'] = x_load_active_rules__mutmut_50 # type: ignore # mutmut generated
mutants_x_load_active_rules__mutmut['x_load_active_rules__mutmut_51'] = x_load_active_rules__mutmut_51 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_load_afa_caps__mutmut)
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


async def x_load_afa_caps__mutmut_orig(conn: AsyncConnection, as_of: date) -> dict[str, int]:
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


async def x_load_afa_caps__mutmut_1(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = None
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_2(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            None,
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_3(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            None,
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_4(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_5(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_6(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                None
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_7(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "XXSELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_referenceXX"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_8(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "select distinct on (mcc) mcc, cap_paise from regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_9(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (MCC) MCC, CAP_PAISE FROM REGULATORY_REFERENCE"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_10(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                "XX WHERE as_of <= :as_of ORDER BY mcc, as_of DESCXX"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_11(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " where as_of <= :as_of order by mcc, as_of desc"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_12(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE AS_OF <= :AS_OF ORDER BY MCC, AS_OF DESC"
            ),
            {"as_of": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_13(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"XXas_ofXX": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_14(conn: AsyncConnection, as_of: date) -> dict[str, int]:
    """AFA-free ceilings in force at `as_of` (ADR-021)."""
    try:
        result = await conn.execute(
            text(
                "SELECT DISTINCT ON (mcc) mcc, cap_paise FROM regulatory_reference"
                " WHERE as_of <= :as_of ORDER BY mcc, as_of DESC"
            ),
            {"AS_OF": as_of},
        )
    except SQLAlchemyError as exc:
        raise RuleStoreUnavailableError(str(exc)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_15(conn: AsyncConnection, as_of: date) -> dict[str, int]:
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
        raise RuleStoreUnavailableError(None) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_16(conn: AsyncConnection, as_of: date) -> dict[str, int]:
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
        raise RuleStoreUnavailableError(str(None)) from exc

    return {row.mcc: int(row.cap_paise) for row in result}


async def x_load_afa_caps__mutmut_17(conn: AsyncConnection, as_of: date) -> dict[str, int]:
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

    return {row.mcc: int(None) for row in result}

mutants_x_load_afa_caps__mutmut['_mutmut_orig'] = x_load_afa_caps__mutmut_orig # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_1'] = x_load_afa_caps__mutmut_1 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_2'] = x_load_afa_caps__mutmut_2 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_3'] = x_load_afa_caps__mutmut_3 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_4'] = x_load_afa_caps__mutmut_4 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_5'] = x_load_afa_caps__mutmut_5 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_6'] = x_load_afa_caps__mutmut_6 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_7'] = x_load_afa_caps__mutmut_7 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_8'] = x_load_afa_caps__mutmut_8 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_9'] = x_load_afa_caps__mutmut_9 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_10'] = x_load_afa_caps__mutmut_10 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_11'] = x_load_afa_caps__mutmut_11 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_12'] = x_load_afa_caps__mutmut_12 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_13'] = x_load_afa_caps__mutmut_13 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_14'] = x_load_afa_caps__mutmut_14 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_15'] = x_load_afa_caps__mutmut_15 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_16'] = x_load_afa_caps__mutmut_16 # type: ignore # mutmut generated
mutants_x_load_afa_caps__mutmut['x_load_afa_caps__mutmut_17'] = x_load_afa_caps__mutmut_17 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_make_afa_free_cap__mutmut)
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


def x_make_afa_free_cap__mutmut_orig(caps: dict[str, int]) -> Any:
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


def x_make_afa_free_cap__mutmut_1(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None or mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_2(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_3(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc not in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_4(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC in caps:
            raise PredicateError("no AFA-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_5(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError(None)
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_6(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("XXno AFA-free ceiling configuredXX")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_7(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("no afa-free ceiling configured")
        return caps[DEFAULT_MCC]

    return afa_free_cap


def x_make_afa_free_cap__mutmut_8(caps: dict[str, int]) -> Any:
    """Bind `afa_free_cap(mcc)` for one evaluation.

    An unknown MCC falls back to the *default* ceiling, never to an unlimited
    one. A missing default means no ceiling can be established, so the predicate
    errors and the rule denies rather than silently permitting any amount.
    """

    def afa_free_cap(mcc: str | None) -> int:
        if mcc is not None and mcc in caps:
            return caps[mcc]
        if DEFAULT_MCC not in caps:
            raise PredicateError("NO AFA-FREE CEILING CONFIGURED")
        return caps[DEFAULT_MCC]

    return afa_free_cap

mutants_x_make_afa_free_cap__mutmut['_mutmut_orig'] = x_make_afa_free_cap__mutmut_orig # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_1'] = x_make_afa_free_cap__mutmut_1 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_2'] = x_make_afa_free_cap__mutmut_2 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_3'] = x_make_afa_free_cap__mutmut_3 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_4'] = x_make_afa_free_cap__mutmut_4 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_5'] = x_make_afa_free_cap__mutmut_5 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_6'] = x_make_afa_free_cap__mutmut_6 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_7'] = x_make_afa_free_cap__mutmut_7 # type: ignore # mutmut generated
mutants_x_make_afa_free_cap__mutmut['x_make_afa_free_cap__mutmut_8'] = x_make_afa_free_cap__mutmut_8 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_evaluate_rules__mutmut)
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


def x_evaluate_rules__mutmut_orig(
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


def x_evaluate_rules__mutmut_1(
    rules: list[RuleRecord], ctx: dict[str, Any], funcs: dict[str, Any] | None = None
) -> GateResult:
    """Evaluate every rule. Pure, so it is directly testable and mutation-tested.

    Note the loop has no `break` and no `return` inside it. That absence is the
    §30.2 requirement, and it is what the mutation tests defend.
    """
    checks: list[dict[str, Any]] = None
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


def x_evaluate_rules__mutmut_2(
    rules: list[RuleRecord], ctx: dict[str, Any], funcs: dict[str, Any] | None = None
) -> GateResult:
    """Evaluate every rule. Pure, so it is directly testable and mutation-tested.

    Note the loop has no `break` and no `return` inside it. That absence is the
    §30.2 requirement, and it is what the mutation tests defend.
    """
    checks: list[dict[str, Any]] = []
    worst = None

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


def x_evaluate_rules__mutmut_3(
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
            passed = None
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


def x_evaluate_rules__mutmut_4(
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
            passed = safe_eval(None, ctx, funcs)
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


def x_evaluate_rules__mutmut_5(
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
            passed = safe_eval(rule.predicate, None, funcs)
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


def x_evaluate_rules__mutmut_6(
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
            passed = safe_eval(rule.predicate, ctx, None)
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


def x_evaluate_rules__mutmut_7(
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
            passed = safe_eval(ctx, funcs)
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


def x_evaluate_rules__mutmut_8(
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
            passed = safe_eval(rule.predicate, funcs)
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


def x_evaluate_rules__mutmut_9(
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
            passed = safe_eval(rule.predicate, ctx, )
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


def x_evaluate_rules__mutmut_10(
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
            passed = None  # fail closed
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


def x_evaluate_rules__mutmut_11(
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
            passed = True  # fail closed
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


def x_evaluate_rules__mutmut_12(
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
            metrics.increment(None, rule_id=rule.rule_id, error=str(exc))
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


def x_evaluate_rules__mutmut_13(
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
            metrics.increment("predicate_error", rule_id=None, error=str(exc))
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


def x_evaluate_rules__mutmut_14(
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
            metrics.increment("predicate_error", rule_id=rule.rule_id, error=None)
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


def x_evaluate_rules__mutmut_15(
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
            metrics.increment(rule_id=rule.rule_id, error=str(exc))
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


def x_evaluate_rules__mutmut_16(
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
            metrics.increment("predicate_error", error=str(exc))
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


def x_evaluate_rules__mutmut_17(
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
            metrics.increment("predicate_error", rule_id=rule.rule_id, )
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


def x_evaluate_rules__mutmut_18(
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
            metrics.increment("XXpredicate_errorXX", rule_id=rule.rule_id, error=str(exc))
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


def x_evaluate_rules__mutmut_19(
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
            metrics.increment("PREDICATE_ERROR", rule_id=rule.rule_id, error=str(exc))
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


def x_evaluate_rules__mutmut_20(
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
            metrics.increment("predicate_error", rule_id=rule.rule_id, error=str(None))
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


def x_evaluate_rules__mutmut_21(
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
                None,
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


def x_evaluate_rules__mutmut_22(
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
                extra=None,
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


def x_evaluate_rules__mutmut_23(
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


def x_evaluate_rules__mutmut_24(
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


def x_evaluate_rules__mutmut_25(
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
                "XXgate.predicate_errorXX",
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


def x_evaluate_rules__mutmut_26(
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
                "GATE.PREDICATE_ERROR",
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


def x_evaluate_rules__mutmut_27(
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
                extra={"XXrule_idXX": rule.rule_id, "version": rule.version, "error": str(exc)},
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


def x_evaluate_rules__mutmut_28(
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
                extra={"RULE_ID": rule.rule_id, "version": rule.version, "error": str(exc)},
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


def x_evaluate_rules__mutmut_29(
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
                extra={"rule_id": rule.rule_id, "XXversionXX": rule.version, "error": str(exc)},
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


def x_evaluate_rules__mutmut_30(
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
                extra={"rule_id": rule.rule_id, "VERSION": rule.version, "error": str(exc)},
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


def x_evaluate_rules__mutmut_31(
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
                extra={"rule_id": rule.rule_id, "version": rule.version, "XXerrorXX": str(exc)},
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


def x_evaluate_rules__mutmut_32(
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
                extra={"rule_id": rule.rule_id, "version": rule.version, "ERROR": str(exc)},
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


def x_evaluate_rules__mutmut_33(
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
                extra={"rule_id": rule.rule_id, "version": rule.version, "error": str(None)},
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


def x_evaluate_rules__mutmut_34(
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

        verdict = None
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


def x_evaluate_rules__mutmut_35(
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

        verdict = ALLOW if passed else _coerce_on_fail(None)
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


def x_evaluate_rules__mutmut_36(
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
            None
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_37(
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
                "XXrule_idXX": rule.rule_id,
                "version": rule.version,
                "regulator": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_38(
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
                "RULE_ID": rule.rule_id,
                "version": rule.version,
                "regulator": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_39(
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
                "XXversionXX": rule.version,
                "regulator": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_40(
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
                "VERSION": rule.version,
                "regulator": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_41(
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
                "XXregulatorXX": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_42(
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
                "REGULATOR": rule.regulator,
                "as_of": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_43(
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
                "XXas_ofXX": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_44(
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
                "AS_OF": str(rule.as_of),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_45(
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
                "as_of": str(None),
                "citation": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_46(
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
                "XXcitationXX": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_47(
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
                "CITATION": rule.citation,
                "verdict": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_48(
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
                "XXverdictXX": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_49(
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
                "VERDICT": verdict,
            }
        )
        worst = max(worst, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_50(
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
        worst = None

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_51(
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
        worst = max(None, verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_52(
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
        worst = max(worst, None, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_53(
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
        worst = max(worst, verdict, key=None)

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_54(
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
        worst = max(verdict, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_55(
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
        worst = max(worst, key=lambda v: VERDICT_PRECEDENCE[v])

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_56(
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
        worst = max(worst, verdict, )

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_57(
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
        worst = max(worst, verdict, key=lambda v: None)

    return GateResult(verdict=worst, checks=checks)


def x_evaluate_rules__mutmut_58(
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

    return GateResult(verdict=None, checks=checks)


def x_evaluate_rules__mutmut_59(
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

    return GateResult(verdict=worst, checks=None)


def x_evaluate_rules__mutmut_60(
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

    return GateResult(checks=checks)


def x_evaluate_rules__mutmut_61(
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

    return GateResult(verdict=worst, )

mutants_x_evaluate_rules__mutmut['_mutmut_orig'] = x_evaluate_rules__mutmut_orig # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_1'] = x_evaluate_rules__mutmut_1 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_2'] = x_evaluate_rules__mutmut_2 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_3'] = x_evaluate_rules__mutmut_3 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_4'] = x_evaluate_rules__mutmut_4 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_5'] = x_evaluate_rules__mutmut_5 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_6'] = x_evaluate_rules__mutmut_6 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_7'] = x_evaluate_rules__mutmut_7 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_8'] = x_evaluate_rules__mutmut_8 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_9'] = x_evaluate_rules__mutmut_9 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_10'] = x_evaluate_rules__mutmut_10 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_11'] = x_evaluate_rules__mutmut_11 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_12'] = x_evaluate_rules__mutmut_12 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_13'] = x_evaluate_rules__mutmut_13 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_14'] = x_evaluate_rules__mutmut_14 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_15'] = x_evaluate_rules__mutmut_15 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_16'] = x_evaluate_rules__mutmut_16 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_17'] = x_evaluate_rules__mutmut_17 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_18'] = x_evaluate_rules__mutmut_18 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_19'] = x_evaluate_rules__mutmut_19 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_20'] = x_evaluate_rules__mutmut_20 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_21'] = x_evaluate_rules__mutmut_21 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_22'] = x_evaluate_rules__mutmut_22 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_23'] = x_evaluate_rules__mutmut_23 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_24'] = x_evaluate_rules__mutmut_24 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_25'] = x_evaluate_rules__mutmut_25 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_26'] = x_evaluate_rules__mutmut_26 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_27'] = x_evaluate_rules__mutmut_27 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_28'] = x_evaluate_rules__mutmut_28 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_29'] = x_evaluate_rules__mutmut_29 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_30'] = x_evaluate_rules__mutmut_30 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_31'] = x_evaluate_rules__mutmut_31 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_32'] = x_evaluate_rules__mutmut_32 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_33'] = x_evaluate_rules__mutmut_33 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_34'] = x_evaluate_rules__mutmut_34 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_35'] = x_evaluate_rules__mutmut_35 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_36'] = x_evaluate_rules__mutmut_36 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_37'] = x_evaluate_rules__mutmut_37 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_38'] = x_evaluate_rules__mutmut_38 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_39'] = x_evaluate_rules__mutmut_39 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_40'] = x_evaluate_rules__mutmut_40 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_41'] = x_evaluate_rules__mutmut_41 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_42'] = x_evaluate_rules__mutmut_42 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_43'] = x_evaluate_rules__mutmut_43 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_44'] = x_evaluate_rules__mutmut_44 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_45'] = x_evaluate_rules__mutmut_45 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_46'] = x_evaluate_rules__mutmut_46 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_47'] = x_evaluate_rules__mutmut_47 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_48'] = x_evaluate_rules__mutmut_48 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_49'] = x_evaluate_rules__mutmut_49 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_50'] = x_evaluate_rules__mutmut_50 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_51'] = x_evaluate_rules__mutmut_51 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_52'] = x_evaluate_rules__mutmut_52 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_53'] = x_evaluate_rules__mutmut_53 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_54'] = x_evaluate_rules__mutmut_54 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_55'] = x_evaluate_rules__mutmut_55 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_56'] = x_evaluate_rules__mutmut_56 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_57'] = x_evaluate_rules__mutmut_57 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_58'] = x_evaluate_rules__mutmut_58 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_59'] = x_evaluate_rules__mutmut_59 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_60'] = x_evaluate_rules__mutmut_60 # type: ignore # mutmut generated
mutants_x_evaluate_rules__mutmut['x_evaluate_rules__mutmut_61'] = x_evaluate_rules__mutmut_61 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__coerce_on_fail__mutmut)
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


def x__coerce_on_fail__mutmut_orig(rule: RuleRecord) -> str:
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


def x__coerce_on_fail__mutmut_1(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail not in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_2(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment(None, rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_3(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=None, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_4(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=None)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_5(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment(rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_6(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_7(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, )
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_8(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("XXunknown_on_failXX", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_9(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("UNKNOWN_ON_FAIL", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "gate.unknown_on_fail",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_10(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        None,
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_11(rule: RuleRecord) -> str:
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
        extra=None,
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_12(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_13(rule: RuleRecord) -> str:
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
        )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_14(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "XXgate.unknown_on_failXX",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_15(rule: RuleRecord) -> str:
    """An unrecognised `on_fail` denies rather than being treated as ALLOW.

    The database CHECK constrains this column, but the gate must not depend on a
    constraint it does not itself enforce — Invariant 2 covers "unknown rule
    versions" for exactly this reason.
    """
    if rule.on_fail in VERDICT_PRECEDENCE:
        return rule.on_fail
    metrics.increment("unknown_on_fail", rule_id=rule.rule_id, on_fail=rule.on_fail)
    log.error(
        "GATE.UNKNOWN_ON_FAIL",
        extra={"rule_id": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_16(rule: RuleRecord) -> str:
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
        extra={"XXrule_idXX": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_17(rule: RuleRecord) -> str:
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
        extra={"RULE_ID": rule.rule_id, "on_fail": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_18(rule: RuleRecord) -> str:
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
        extra={"rule_id": rule.rule_id, "XXon_failXX": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT


def x__coerce_on_fail__mutmut_19(rule: RuleRecord) -> str:
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
        extra={"rule_id": rule.rule_id, "ON_FAIL": rule.on_fail},
    )
    return FAIL_CLOSED_VERDICT

mutants_x__coerce_on_fail__mutmut['_mutmut_orig'] = x__coerce_on_fail__mutmut_orig # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_1'] = x__coerce_on_fail__mutmut_1 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_2'] = x__coerce_on_fail__mutmut_2 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_3'] = x__coerce_on_fail__mutmut_3 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_4'] = x__coerce_on_fail__mutmut_4 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_5'] = x__coerce_on_fail__mutmut_5 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_6'] = x__coerce_on_fail__mutmut_6 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_7'] = x__coerce_on_fail__mutmut_7 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_8'] = x__coerce_on_fail__mutmut_8 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_9'] = x__coerce_on_fail__mutmut_9 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_10'] = x__coerce_on_fail__mutmut_10 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_11'] = x__coerce_on_fail__mutmut_11 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_12'] = x__coerce_on_fail__mutmut_12 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_13'] = x__coerce_on_fail__mutmut_13 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_14'] = x__coerce_on_fail__mutmut_14 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_15'] = x__coerce_on_fail__mutmut_15 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_16'] = x__coerce_on_fail__mutmut_16 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_17'] = x__coerce_on_fail__mutmut_17 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_18'] = x__coerce_on_fail__mutmut_18 # type: ignore # mutmut generated
mutants_x__coerce_on_fail__mutmut['x__coerce_on_fail__mutmut_19'] = x__coerce_on_fail__mutmut_19 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_evaluate__mutmut)
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


async def x_evaluate__mutmut_orig(
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


async def x_evaluate__mutmut_1(
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
        rules = None
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


async def x_evaluate__mutmut_2(
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
        rules = await load_active_rules(None, action_type, rail, as_of)
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


async def x_evaluate__mutmut_3(
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
        rules = await load_active_rules(conn, None, rail, as_of)
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


async def x_evaluate__mutmut_4(
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
        rules = await load_active_rules(conn, action_type, None, as_of)
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


async def x_evaluate__mutmut_5(
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
        rules = await load_active_rules(conn, action_type, rail, None)
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


async def x_evaluate__mutmut_6(
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
        rules = await load_active_rules(action_type, rail, as_of)
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


async def x_evaluate__mutmut_7(
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
        rules = await load_active_rules(conn, rail, as_of)
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


async def x_evaluate__mutmut_8(
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
        rules = await load_active_rules(conn, action_type, as_of)
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


async def x_evaluate__mutmut_9(
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
        rules = await load_active_rules(conn, action_type, rail, )
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


async def x_evaluate__mutmut_10(
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
        caps = None
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


async def x_evaluate__mutmut_11(
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
        caps = await load_afa_caps(None, as_of)
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


async def x_evaluate__mutmut_12(
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
        caps = await load_afa_caps(conn, None)
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


async def x_evaluate__mutmut_13(
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
        caps = await load_afa_caps(as_of)
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


async def x_evaluate__mutmut_14(
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
        caps = await load_afa_caps(conn, )
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


async def x_evaluate__mutmut_15(
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
        metrics.increment(None, action_type=action_type, reason="rule_store")
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


async def x_evaluate__mutmut_16(
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
        metrics.increment("gate_degraded", action_type=None, reason="rule_store")
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


async def x_evaluate__mutmut_17(
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
        metrics.increment("gate_degraded", action_type=action_type, reason=None)
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


async def x_evaluate__mutmut_18(
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
        metrics.increment(action_type=action_type, reason="rule_store")
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


async def x_evaluate__mutmut_19(
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
        metrics.increment("gate_degraded", reason="rule_store")
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


async def x_evaluate__mutmut_20(
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
        metrics.increment("gate_degraded", action_type=action_type, )
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


async def x_evaluate__mutmut_21(
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
        metrics.increment("XXgate_degradedXX", action_type=action_type, reason="rule_store")
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


async def x_evaluate__mutmut_22(
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
        metrics.increment("GATE_DEGRADED", action_type=action_type, reason="rule_store")
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


async def x_evaluate__mutmut_23(
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
        metrics.increment("gate_degraded", action_type=action_type, reason="XXrule_storeXX")
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


async def x_evaluate__mutmut_24(
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
        metrics.increment("gate_degraded", action_type=action_type, reason="RULE_STORE")
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


async def x_evaluate__mutmut_25(
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
        log.error(None, extra={"error": str(exc)})
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


async def x_evaluate__mutmut_26(
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
        log.error("gate.rule_store_unavailable", extra=None)
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


async def x_evaluate__mutmut_27(
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
        log.error(extra={"error": str(exc)})
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


async def x_evaluate__mutmut_28(
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
        log.error("gate.rule_store_unavailable", )
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


async def x_evaluate__mutmut_29(
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
        log.error("XXgate.rule_store_unavailableXX", extra={"error": str(exc)})
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


async def x_evaluate__mutmut_30(
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
        log.error("GATE.RULE_STORE_UNAVAILABLE", extra={"error": str(exc)})
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


async def x_evaluate__mutmut_31(
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
        log.error("gate.rule_store_unavailable", extra={"XXerrorXX": str(exc)})
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


async def x_evaluate__mutmut_32(
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
        log.error("gate.rule_store_unavailable", extra={"ERROR": str(exc)})
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


async def x_evaluate__mutmut_33(
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
        log.error("gate.rule_store_unavailable", extra={"error": str(None)})
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


async def x_evaluate__mutmut_34(
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
            verdict=None,
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


async def x_evaluate__mutmut_35(
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
            checks=None,
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


async def x_evaluate__mutmut_36(
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
            degraded=None,
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


async def x_evaluate__mutmut_37(
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


async def x_evaluate__mutmut_38(
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


async def x_evaluate__mutmut_39(
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


async def x_evaluate__mutmut_40(
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
                    "XXrule_idXX": "__RULE_STORE__",
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


async def x_evaluate__mutmut_41(
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
                    "RULE_ID": "__RULE_STORE__",
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


async def x_evaluate__mutmut_42(
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
                    "rule_id": "XX__RULE_STORE__XX",
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


async def x_evaluate__mutmut_43(
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
                    "rule_id": "__rule_store__",
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


async def x_evaluate__mutmut_44(
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
                    "XXversionXX": 0,
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


async def x_evaluate__mutmut_45(
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
                    "VERSION": 0,
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


async def x_evaluate__mutmut_46(
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
                    "version": 1,
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


async def x_evaluate__mutmut_47(
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
                    "XXregulatorXX": "PRAYAS",
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


async def x_evaluate__mutmut_48(
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
                    "REGULATOR": "PRAYAS",
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


async def x_evaluate__mutmut_49(
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
                    "regulator": "XXPRAYASXX",
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


async def x_evaluate__mutmut_50(
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
                    "regulator": "prayas",
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


async def x_evaluate__mutmut_51(
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
                    "XXas_ofXX": str(as_of),
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


async def x_evaluate__mutmut_52(
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
                    "AS_OF": str(as_of),
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


async def x_evaluate__mutmut_53(
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
                    "as_of": str(None),
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


async def x_evaluate__mutmut_54(
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
                    "XXcitationXX": "Invariant 2 — the gate fails closed",
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


async def x_evaluate__mutmut_55(
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
                    "CITATION": "Invariant 2 — the gate fails closed",
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


async def x_evaluate__mutmut_56(
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
                    "citation": "XXInvariant 2 — the gate fails closedXX",
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


async def x_evaluate__mutmut_57(
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
                    "citation": "invariant 2 — the gate fails closed",
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


async def x_evaluate__mutmut_58(
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
                    "citation": "INVARIANT 2 — THE GATE FAILS CLOSED",
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


async def x_evaluate__mutmut_59(
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
                    "XXverdictXX": FAIL_CLOSED_VERDICT,
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


async def x_evaluate__mutmut_60(
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
                    "VERDICT": FAIL_CLOSED_VERDICT,
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


async def x_evaluate__mutmut_61(
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
            degraded=False,
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


async def x_evaluate__mutmut_62(
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

    if rules:
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


async def x_evaluate__mutmut_63(
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
        metrics.increment(None, action_type=action_type)
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


async def x_evaluate__mutmut_64(
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
        metrics.increment("gate_no_rules", action_type=None)
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


async def x_evaluate__mutmut_65(
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
        metrics.increment(action_type=action_type)
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


async def x_evaluate__mutmut_66(
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
        metrics.increment("gate_no_rules", )
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


async def x_evaluate__mutmut_67(
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
        metrics.increment("XXgate_no_rulesXX", action_type=action_type)
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


async def x_evaluate__mutmut_68(
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
        metrics.increment("GATE_NO_RULES", action_type=action_type)
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


async def x_evaluate__mutmut_69(
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
        log.error(None, extra={"action_type": action_type})
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


async def x_evaluate__mutmut_70(
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
        log.error("gate.no_applicable_rules", extra=None)
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


async def x_evaluate__mutmut_71(
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
        log.error(extra={"action_type": action_type})
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


async def x_evaluate__mutmut_72(
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
        log.error("gate.no_applicable_rules", )
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


async def x_evaluate__mutmut_73(
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
        log.error("XXgate.no_applicable_rulesXX", extra={"action_type": action_type})
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


async def x_evaluate__mutmut_74(
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
        log.error("GATE.NO_APPLICABLE_RULES", extra={"action_type": action_type})
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


async def x_evaluate__mutmut_75(
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
        log.error("gate.no_applicable_rules", extra={"XXaction_typeXX": action_type})
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


async def x_evaluate__mutmut_76(
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
        log.error("gate.no_applicable_rules", extra={"ACTION_TYPE": action_type})
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


async def x_evaluate__mutmut_77(
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
            verdict=None,
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


async def x_evaluate__mutmut_78(
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
            checks=None,
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_79(
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
            degraded=None,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_80(
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


async def x_evaluate__mutmut_81(
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
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_82(
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
            )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_83(
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
                    "XXrule_idXX": "__NO_RULES__",
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


async def x_evaluate__mutmut_84(
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
                    "RULE_ID": "__NO_RULES__",
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


async def x_evaluate__mutmut_85(
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
                    "rule_id": "XX__NO_RULES__XX",
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


async def x_evaluate__mutmut_86(
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
                    "rule_id": "__no_rules__",
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


async def x_evaluate__mutmut_87(
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
                    "XXversionXX": 0,
                    "regulator": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_88(
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
                    "VERSION": 0,
                    "regulator": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_89(
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
                    "version": 1,
                    "regulator": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_90(
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
                    "XXregulatorXX": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_91(
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
                    "REGULATOR": "PRAYAS",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_92(
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
                    "regulator": "XXPRAYASXX",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_93(
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
                    "regulator": "prayas",
                    "as_of": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_94(
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
                    "XXas_ofXX": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_95(
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
                    "AS_OF": str(as_of),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_96(
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
                    "as_of": str(None),
                    "citation": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_97(
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
                    "XXcitationXX": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_98(
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
                    "CITATION": "Invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_99(
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
                    "citation": "XXInvariant 1 — no code path acts without passing the gateXX",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_100(
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
                    "citation": "invariant 1 — no code path acts without passing the gate",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_101(
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
                    "citation": "INVARIANT 1 — NO CODE PATH ACTS WITHOUT PASSING THE GATE",
                    "verdict": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_102(
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
                    "XXverdictXX": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_103(
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
                    "VERDICT": FAIL_CLOSED_VERDICT,
                }
            ],
            degraded=True,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_104(
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
            degraded=False,
        )

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_105(
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

    return evaluate_rules(None, ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_106(
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

    return evaluate_rules(rules, None, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_107(
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

    return evaluate_rules(rules, ctx, None)


async def x_evaluate__mutmut_108(
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

    return evaluate_rules(ctx, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_109(
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

    return evaluate_rules(rules, {"afa_free_cap": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_110(
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

    return evaluate_rules(rules, ctx, )


async def x_evaluate__mutmut_111(
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

    return evaluate_rules(rules, ctx, {"XXafa_free_capXX": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_112(
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

    return evaluate_rules(rules, ctx, {"AFA_FREE_CAP": make_afa_free_cap(caps)})


async def x_evaluate__mutmut_113(
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

    return evaluate_rules(rules, ctx, {"afa_free_cap": make_afa_free_cap(None)})

mutants_x_evaluate__mutmut['_mutmut_orig'] = x_evaluate__mutmut_orig # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_1'] = x_evaluate__mutmut_1 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_2'] = x_evaluate__mutmut_2 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_3'] = x_evaluate__mutmut_3 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_4'] = x_evaluate__mutmut_4 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_5'] = x_evaluate__mutmut_5 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_6'] = x_evaluate__mutmut_6 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_7'] = x_evaluate__mutmut_7 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_8'] = x_evaluate__mutmut_8 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_9'] = x_evaluate__mutmut_9 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_10'] = x_evaluate__mutmut_10 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_11'] = x_evaluate__mutmut_11 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_12'] = x_evaluate__mutmut_12 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_13'] = x_evaluate__mutmut_13 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_14'] = x_evaluate__mutmut_14 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_15'] = x_evaluate__mutmut_15 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_16'] = x_evaluate__mutmut_16 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_17'] = x_evaluate__mutmut_17 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_18'] = x_evaluate__mutmut_18 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_19'] = x_evaluate__mutmut_19 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_20'] = x_evaluate__mutmut_20 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_21'] = x_evaluate__mutmut_21 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_22'] = x_evaluate__mutmut_22 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_23'] = x_evaluate__mutmut_23 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_24'] = x_evaluate__mutmut_24 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_25'] = x_evaluate__mutmut_25 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_26'] = x_evaluate__mutmut_26 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_27'] = x_evaluate__mutmut_27 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_28'] = x_evaluate__mutmut_28 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_29'] = x_evaluate__mutmut_29 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_30'] = x_evaluate__mutmut_30 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_31'] = x_evaluate__mutmut_31 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_32'] = x_evaluate__mutmut_32 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_33'] = x_evaluate__mutmut_33 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_34'] = x_evaluate__mutmut_34 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_35'] = x_evaluate__mutmut_35 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_36'] = x_evaluate__mutmut_36 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_37'] = x_evaluate__mutmut_37 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_38'] = x_evaluate__mutmut_38 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_39'] = x_evaluate__mutmut_39 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_40'] = x_evaluate__mutmut_40 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_41'] = x_evaluate__mutmut_41 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_42'] = x_evaluate__mutmut_42 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_43'] = x_evaluate__mutmut_43 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_44'] = x_evaluate__mutmut_44 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_45'] = x_evaluate__mutmut_45 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_46'] = x_evaluate__mutmut_46 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_47'] = x_evaluate__mutmut_47 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_48'] = x_evaluate__mutmut_48 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_49'] = x_evaluate__mutmut_49 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_50'] = x_evaluate__mutmut_50 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_51'] = x_evaluate__mutmut_51 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_52'] = x_evaluate__mutmut_52 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_53'] = x_evaluate__mutmut_53 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_54'] = x_evaluate__mutmut_54 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_55'] = x_evaluate__mutmut_55 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_56'] = x_evaluate__mutmut_56 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_57'] = x_evaluate__mutmut_57 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_58'] = x_evaluate__mutmut_58 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_59'] = x_evaluate__mutmut_59 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_60'] = x_evaluate__mutmut_60 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_61'] = x_evaluate__mutmut_61 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_62'] = x_evaluate__mutmut_62 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_63'] = x_evaluate__mutmut_63 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_64'] = x_evaluate__mutmut_64 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_65'] = x_evaluate__mutmut_65 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_66'] = x_evaluate__mutmut_66 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_67'] = x_evaluate__mutmut_67 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_68'] = x_evaluate__mutmut_68 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_69'] = x_evaluate__mutmut_69 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_70'] = x_evaluate__mutmut_70 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_71'] = x_evaluate__mutmut_71 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_72'] = x_evaluate__mutmut_72 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_73'] = x_evaluate__mutmut_73 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_74'] = x_evaluate__mutmut_74 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_75'] = x_evaluate__mutmut_75 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_76'] = x_evaluate__mutmut_76 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_77'] = x_evaluate__mutmut_77 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_78'] = x_evaluate__mutmut_78 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_79'] = x_evaluate__mutmut_79 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_80'] = x_evaluate__mutmut_80 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_81'] = x_evaluate__mutmut_81 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_82'] = x_evaluate__mutmut_82 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_83'] = x_evaluate__mutmut_83 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_84'] = x_evaluate__mutmut_84 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_85'] = x_evaluate__mutmut_85 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_86'] = x_evaluate__mutmut_86 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_87'] = x_evaluate__mutmut_87 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_88'] = x_evaluate__mutmut_88 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_89'] = x_evaluate__mutmut_89 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_90'] = x_evaluate__mutmut_90 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_91'] = x_evaluate__mutmut_91 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_92'] = x_evaluate__mutmut_92 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_93'] = x_evaluate__mutmut_93 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_94'] = x_evaluate__mutmut_94 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_95'] = x_evaluate__mutmut_95 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_96'] = x_evaluate__mutmut_96 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_97'] = x_evaluate__mutmut_97 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_98'] = x_evaluate__mutmut_98 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_99'] = x_evaluate__mutmut_99 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_100'] = x_evaluate__mutmut_100 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_101'] = x_evaluate__mutmut_101 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_102'] = x_evaluate__mutmut_102 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_103'] = x_evaluate__mutmut_103 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_104'] = x_evaluate__mutmut_104 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_105'] = x_evaluate__mutmut_105 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_106'] = x_evaluate__mutmut_106 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_107'] = x_evaluate__mutmut_107 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_108'] = x_evaluate__mutmut_108 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_109'] = x_evaluate__mutmut_109 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_110'] = x_evaluate__mutmut_110 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_111'] = x_evaluate__mutmut_111 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_112'] = x_evaluate__mutmut_112 # type: ignore # mutmut generated
mutants_x_evaluate__mutmut['x_evaluate__mutmut_113'] = x_evaluate__mutmut_113 # type: ignore # mutmut generated
