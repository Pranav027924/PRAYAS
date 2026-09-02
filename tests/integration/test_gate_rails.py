"""Rail-specific rules apply to their rail and only to their rail (§9, §30.1).

**Phase 14 exit criterion.** The gate has filtered on `rails` since Phase 2 —
`WHERE rails IS NULL OR :rail = ANY(rails)` — but nothing asserted it. A filter
nobody tests is a filter that works until someone edits the query.

The consequential direction is *over*-application: a UPI window rule applied to
a card debit would deny lawful collection continuously, and because the denial
is compliant-looking it would be attributed to the rail rather than to a bug.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction
from prayas.gate.engine import ALLOW, DENY, evaluate, load_active_rules
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

#: After RBI-EMANDATE-PDN-24H v3's effective date (2026-08-30). Evaluating at
#: an earlier instant selects v2, which is the replay property §30.1 requires
#: and which `test_the_earlier_version_still_governs_an_earlier_decision`
#: asserts directly.
TODAY = date(2026, 8, 31)

#: The reviewable source of truth (ADR-020).
RULEPACK_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "prayas-rulepack"
    / "src"
    / "prayas_rulepack"
    / "rulepack.yaml"
)

#: 11:00 IST — inside NPCI's morning peak, so unlawful for UPI Autopay and
#: unremarkable for every other rail.
INSIDE_UPI_PEAK = 11.0


def _ctx(**overrides: Any) -> dict[str, Any]:
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


# ── the criterion: only to their rail ──────────────────────────────────────


async def test_the_upi_window_rule_denies_a_upi_debit_in_the_peak(
    app_engine: AsyncEngine,
) -> None:
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="debit_attempt",
            rail="upi_autopay",
            ctx=_ctx(hour_ist=INSIDE_UPI_PEAK),
            as_of=TODAY,
        )

    assert result.verdict == DENY
    assert any(c["rule_id"] == "NPCI-AUTOPAY-WINDOW" for c in result.checks)


@pytest.mark.parametrize("rail", ["card_emandate", "enach"])
async def test_the_upi_window_rule_does_not_touch_other_rails(
    app_engine: AsyncEngine, rail: str
) -> None:
    """The consequential direction. Over-application would deny lawful
    collection continuously, and look compliant while doing it."""
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="debit_attempt",
            rail=rail,
            ctx=_ctx(hour_ist=INSIDE_UPI_PEAK),
            as_of=TODAY,
        )

    assert result.verdict == ALLOW, f"a UPI-only rule reached {rail}"
    assert not any(c["rule_id"] == "NPCI-AUTOPAY-WINDOW" for c in result.checks)


@pytest.mark.parametrize("rail", ["upi_autopay", "card_emandate", "enach"])
async def test_an_all_rail_rule_applies_everywhere(app_engine: AsyncEngine, rail: str) -> None:
    """`RBI-EMANDATE-PDN-24H` names all three rails. A notice that never went
    out must deny on every one of them — the rulepack is the single source of
    truth for this rule (ADR-020), and the adapters deliberately do not
    re-implement it."""
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="debit_attempt",
            rail=rail,
            ctx=_ctx(pdn_sent_at=None),
            as_of=TODAY,
        )

    assert result.verdict == DENY
    assert any(c["rule_id"] == "RBI-EMANDATE-PDN-24H" for c in result.checks)


@pytest.mark.parametrize("rail", ["upi_autopay", "card_emandate", "enach"])
async def test_a_notice_inside_the_lead_denies_on_every_rail(
    app_engine: AsyncEngine, rail: str
) -> None:
    """The case `test_rails_multi.py` deliberately does not assert at the
    adapter. It is enforced here, once, where the citation lives."""
    async with system_transaction(app_engine) as conn:
        result = await evaluate(
            conn,
            action_type="debit_attempt",
            rail=rail,
            ctx=_ctx(pdn_sent_at=datetime.now(tz=UTC) - timedelta(hours=1)),
            as_of=TODAY,
        )

    assert result.verdict == DENY
    assert any(c["rule_id"] == "RBI-EMANDATE-PDN-24H" for c in result.checks)


# ── what the loader actually returns ───────────────────────────────────────


async def test_the_loader_selects_by_rail(app_engine: AsyncEngine) -> None:
    """Asserted at the query, not only through a verdict, so a broken filter is
    located rather than merely detected."""
    async with system_transaction(app_engine) as conn:
        upi = await load_active_rules(conn, "debit_attempt", "upi_autopay", TODAY)
        card = await load_active_rules(conn, "debit_attempt", "card_emandate", TODAY)

    upi_ids = {r.rule_id for r in upi}
    card_ids = {r.rule_id for r in card}

    assert "NPCI-AUTOPAY-WINDOW" in upi_ids
    assert "NPCI-AUTOPAY-WINDOW" not in card_ids
    assert "RBI-EMANDATE-PDN-24H" in upi_ids & card_ids
    assert card_ids < upi_ids, "card should see a strict subset of UPI's rules"


async def test_an_unknown_rail_now_gets_the_notice_rule(app_engine: AsyncEngine) -> None:
    """**FINDING-P14-01, fixed.** Version 2 enumerated the three rails that
    existed, so a rail added later would have inherited the universal rules and
    silently escaped the 24-hour notice requirement — a debit that looked
    lawful. Version 3 carries `rails: null`.

    Fail-safe direction: a rail nobody has built must not be a way around a
    rule that applies to everything (Invariant 1).
    """
    async with system_transaction(app_engine) as conn:
        rules = await load_active_rules(conn, "debit_attempt", "carrier_billing", TODAY)

    by_id = {r.rule_id: r for r in rules}
    assert "RBI-EMANDATE-PDN-24H" in by_id
    assert by_id["RBI-EMANDATE-PDN-24H"].version == 3
    assert "DPDP-CONSENT-VALID" in by_id
    assert "NPCI-AUTOPAY-WINDOW" not in by_id, "a UPI-only rule must not leak"


async def test_the_earlier_version_still_governs_an_earlier_decision(
    app_engine: AsyncEngine,
) -> None:
    """§30.1: "a replay of a past decision selects the rule versions that
    governed *then*."

    Version 3 is dated the day the encoding was corrected, not back-dated to
    the regulation's own date. So a decision replayed from before that day
    still selects version 2 — which is what actually ran. Back-dating would
    have rewritten history in the one place §30.1 says must not be rewritten.
    """
    before = date(2026, 8, 1)
    async with system_transaction(app_engine) as conn:
        then = await load_active_rules(conn, "debit_attempt", "upi_autopay", before)
        now = await load_active_rules(conn, "debit_attempt", "upi_autopay", TODAY)

    assert {r.rule_id: r.version for r in then}["RBI-EMANDATE-PDN-24H"] == 2
    assert {r.rule_id: r.version for r in now}["RBI-EMANDATE-PDN-24H"] == 3


async def test_the_citation_survived_the_version_bump(app_engine: AsyncEngine) -> None:
    """Invariant 10 — every rule carries a citation. The regulation did not
    change; only our encoding of which rails it covers did."""
    async with system_transaction(app_engine) as conn:
        rules = await load_active_rules(conn, "debit_attempt", "enach", TODAY)

    rule = {r.rule_id: r for r in rules}["RBI-EMANDATE-PDN-24H"]
    assert "E-mandate Framework" in rule.citation
    assert rule.regulator == "RBI"


async def test_a_null_rail_sees_only_universal_rules(app_engine: AsyncEngine) -> None:
    """`rail=None` means "not rail-specific", not "every rule"."""
    async with system_transaction(app_engine) as conn:
        rules = await load_active_rules(conn, "debit_attempt", None, TODAY)

    assert "NPCI-AUTOPAY-WINDOW" not in {r.rule_id for r in rules}


def test_the_superseded_notice_rule_survives_in_the_rulepack() -> None:
    """**Pins the mistake that produced FINDING-P14-01's fix.**

    Version 3 was first written by *editing* version 2's entry in the YAML
    rather than adding a new one. The version number changed, so it looked
    correct — but on a fresh database the loader would then have inserted only
    v3, v2 would never have existed, and every decision made before v3's
    `as_of` would have replayed against a rule that did not govern it. §30.1's
    replay property would have been destroyed by the change that claimed to
    preserve it.

    **A test that reads the database cannot catch this**, because a development
    database still holds the old row from an earlier migration — which is why
    the five tests that failed only did so on a clean build. This reads the
    *source of truth* instead.

    Scoped to the rule this repo actually bumped. Other rules legitimately
    begin above version 1: they were transcribed from the spec at whatever
    version the regulator had reached, and those earlier versions never existed
    here, so their absence is correct rather than a gap.
    """
    import yaml

    pack = yaml.safe_load(RULEPACK_PATH.read_text(encoding="utf-8"))
    by_version = {r["version"]: r for r in pack["rules"] if r["rule_id"] == "RBI-EMANDATE-PDN-24H"}

    assert set(by_version) == {2, 3}, (
        "the superseded version was removed rather than kept alongside — a fresh "
        "database would lose the version that governed earlier decisions (ADR-020)"
    )
    assert by_version[2]["rails"] == ["upi_autopay", "card_emandate", "enach"]
    assert by_version[3]["rails"] is None
    assert by_version[2]["as_of"] < by_version[3]["as_of"], (
        "the newer version must take effect later, or it would silently govern "
        "decisions made before it existed"
    )
