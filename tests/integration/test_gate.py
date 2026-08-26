"""Compliance gate behaviour (Master Spec §30.2; Invariants 1 and 2).

Exit criterion: "gate returns DENY when the rule store is unreachable."

The tests that matter most here assert an *absence*: no ALLOW-on-failure branch,
and no short-circuit. Both are properties a passing happy-path test would never
notice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction
from prayas.gate.engine import (
    ALLOW,
    DENY,
    RuleStoreUnavailableError,
    evaluate,
    load_active_rules,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TODAY = date(2026, 8, 25)


def _ctx(**overrides: Any) -> dict[str, Any]:
    """A context in which every rule passes, so a test can fail one at a time."""
    base: dict[str, Any] = {
        "hour_ist": 9.0,
        "pdn_sent_at": datetime.now(tz=UTC) - timedelta(hours=30),
        "is_next_day_debit": False,
        "amount_paise": 49_900,
        "mcc": "5812",
        "consent_ref": "consent_1",
        "consent_withdrawn": False,
        "dlt_template_id": "T1",
        "header_series": "160",
        "dnd_registered": False,
        "messages_30d": 0,
        "tenant_fatigue_cap": 3,
    }
    base.update(overrides)
    return base


# ── the exit criterion ──────────────────────────────────────────────────────


async def test_gate_denies_when_the_rule_store_is_unreachable(app_engine: AsyncEngine) -> None:
    """Invariant 2. A gate that fails open is worse than no gate."""

    class BrokenConn:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    result = await evaluate(
        BrokenConn(),  # type: ignore[arg-type]
        action_type="debit_attempt",
        rail="upi_autopay",
        ctx=_ctx(),
        as_of=TODAY,
    )

    assert result.verdict == DENY
    assert result.degraded is True, (
        "a degraded denial must be distinguishable from a considered one"
    )
    assert result.checks, "even a degraded denial records why"


async def test_load_active_rules_raises_rather_than_returning_empty(
    app_engine: AsyncEngine,
) -> None:
    """An empty list would be indistinguishable from 'no rules apply'."""

    class BrokenConn:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("SELECT 1", {}, Exception("down"))

    with pytest.raises(RuleStoreUnavailableError):
        await load_active_rules(BrokenConn(), "debit_attempt", "upi_autopay", TODAY)  # type: ignore[arg-type]


async def test_unknown_action_type_denies_rather_than_allows(app_engine: AsyncEngine) -> None:
    """No applicable rule is not the same as permitted (Invariant 1)."""
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="something_nobody_reviewed",
            rail=None,
            ctx=_ctx(),
            as_of=TODAY,
        )

    assert result.verdict == DENY
    assert result.degraded is True


# ── the rule pack, loaded and applied ───────────────────────────────────────


async def test_a_fully_compliant_debit_is_allowed(app_engine: AsyncEngine) -> None:
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn, action_type="debit_attempt", rail="upi_autopay", ctx=_ctx(), as_of=TODAY
        )

    assert result.verdict == ALLOW, f"denied by {result.denied_rules()}"
    assert result.allowed is True


@pytest.mark.parametrize(
    ("label", "overrides", "expected_rule"),
    [
        ("outside NPCI window", {"hour_ist": 11.0}, "NPCI-AUTOPAY-WINDOW"),
        ("no PDN", {"pdn_sent_at": None}, "RBI-EMANDATE-PDN-24H"),
        ("PDN too recent", {"pdn_sent_at": datetime.now(tz=UTC)}, "RBI-EMANDATE-PDN-24H"),
        ("consent withdrawn", {"consent_withdrawn": True}, "DPDP-CONSENT-VALID"),
        ("over AFA cap", {"amount_paise": 1_500_100}, "RBI-EMANDATE-AFA-CAP"),
    ],
)
async def test_each_violation_is_caught_and_named(
    app_engine: AsyncEngine, label: str, overrides: dict[str, Any], expected_rule: str
) -> None:
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="debit_attempt",
            rail="upi_autopay",
            ctx=_ctx(**overrides),
            as_of=TODAY,
        )

    assert result.verdict != ALLOW, f"{label} was allowed"
    assert expected_rule in result.denied_rules()


async def test_all_rules_are_evaluated_even_after_a_deny(app_engine: AsyncEngine) -> None:
    """§30.2 — no short-circuiting.

    "A compliance reviewer asking 'was the DND check performed?' needs a
    positive answer regardless of what else failed first."
    """
    async with system_transaction(app_engine) as conn:
        all_pass = await evaluate(
            conn, action_type="debit_attempt", rail="upi_autopay", ctx=_ctx(), as_of=TODAY
        )
        # Violate the *first* rule and one much later in the ordering.
        many_fail = await evaluate(
            conn,
            action_type="debit_attempt",
            rail="upi_autopay",
            ctx=_ctx(hour_ist=11.0, consent_withdrawn=True, pdn_sent_at=None),
            as_of=TODAY,
        )

    assert len(many_fail.checks) == len(all_pass.checks), (
        "fewer rules were recorded once one denied — the gate short-circuited"
    )
    assert len(many_fail.denied_rules()) >= 3


async def test_every_check_carries_its_citation_and_version(app_engine: AsyncEngine) -> None:
    """Invariant 10 — this is what a reviewer reads under §32 replay."""
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn, action_type="debit_attempt", rail="upi_autopay", ctx=_ctx(), as_of=TODAY
        )

    for check in result.checks:
        assert check["rule_id"]
        assert check["citation"]
        assert check["as_of"]
        assert check["regulator"]
        assert isinstance(check["version"], int)


async def test_rail_scoping_excludes_inapplicable_rules(app_engine: AsyncEngine) -> None:
    """NPCI-AUTOPAY-WINDOW is upi_autopay only; a card debit must not be judged by it."""
    async with system_transaction(app_engine) as conn:
        card = await evaluate(
            conn,
            action_type="debit_attempt",
            rail="card_emandate",
            ctx=_ctx(hour_ist=11.0),  # outside the UPI window
            as_of=TODAY,
        )

    assert "NPCI-AUTOPAY-WINDOW" not in {c["rule_id"] for c in card.checks}


async def test_as_of_selects_the_versions_in_force_then(app_engine: AsyncEngine) -> None:
    """§30.1 — the ledger records which version governed each past decision."""
    async with system_transaction(app_engine) as conn:
        recent = await load_active_rules(conn, "debit_attempt", "upi_autopay", TODAY)
        historic = await load_active_rules(conn, "debit_attempt", "upi_autopay", date(2023, 1, 1))

    assert len(recent) > len(historic), "as_of did not restrict the rule set"


# Pure evaluation logic (verdict precedence, fail-closed paths, AFA ceilings)
# lives in tests/unit/test_gate_logic.py so mutmut, which is scoped to
# tests/unit/, can actually exercise it (ADR-023).
