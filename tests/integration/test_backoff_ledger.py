"""Back-off decisions reach the ledger with a rationale (§24.6, §32).

"Do nothing, and record that doing nothing was chosen and why. **The ledger
entry for a back-off decision is as important as one for a debit.**"

So the test is not that `select` returns BACK_OFF — that is covered in the unit
suite — but that the decision is durable, replayable, and carries a reason a
reviewer can read.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.ledger.chain import append
from prayas.measure.replay import replay_decision
from prayas.retention.interventions import Decision, Intervention, select
from prayas.retention.revocation import RevocationFeatures, RevocationModel
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_backoff"
TS = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)
AMOUNT, W = 49_900, 49_900 * 5
_WIPE = "TRUNCATE decisions, tenants RESTART IDENTITY CASCADE"


@pytest.fixture
async def tenant(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config) VALUES"
                " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
            ),
            {"t": TENANT},
        )
    yield
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))


def _back_off_decision() -> Decision:
    from prayas.retention.interventions import DayOfMonthHazard

    return select(
        hard_stop_reasons=[],
        debit_day=5,
        hazard=DayOfMonthHazard(p_by_day={5: 0.6}, observations={5: 200}),
        consecutive_high_risk_cycles=0,
        revocation=RevocationModel(cell_hazard={}, cell_counts={}, global_hazard=0.22),
        features=RevocationFeatures(
            consecutive_failures=3,
            days_since_success=90.0,
            successful_cycles=1,
            rail="upi_autopay",
        ),
        best_attempt_ev_paise=float(W) - 8_000.0,
        continuation_value_paise=W,
        messages_30d=3,
        fatigue_cap=3,
    )


async def _record(app_engine: AsyncEngine, decision: Decision, decision_id: str) -> None:
    async with tenant_transaction(app_engine, TENANT) as conn:
        await append(
            conn,
            {
                "decision_id": decision_id,
                "ts": TS,
                "trigger_event_id": None,
                "mandate_id": "sub_backoff",
                "cycle_id": "cyc_backoff",
                "action_type": str(decision.intervention),
                "verdict": "ALLOW",
                "feature_snapshot_ref": "snap_backoff",
                "model_versions": json.dumps({"revocation": "v1", "hazard": "v0"}),
                "cause_posterior": None,
                "liquidity_curve_ref": "curve_backoff",
                "revocation_hazard": 0.22,
                "continuation_value": W,
                # §32: every candidate, including those not chosen — a back-off
                # is only reviewable against what it was chosen over.
                "candidate_actions": json.dumps(
                    [
                        {"action": "retry", "ev_paise": W - 8_000},
                        {"action": "back_off", "ev_paise": W},
                    ]
                ),
                "chosen_action": json.dumps({"action": "back_off"}),
                "rationale": decision.rationale,
                "compliance_checks": json.dumps([]),
                "holdout_arm": "treatment",
                "propensity": 0.85,
                "degraded": False,
                "outcome": None,
                "outcome_ts": None,
                "recovered_paise": None,
            },
            TENANT,
        )


async def test_a_back_off_is_durable_and_replayable(app_engine: AsyncEngine, tenant: None) -> None:
    """The criterion: recorded in the ledger, with rationale, and replayable."""
    decision = _back_off_decision()
    assert decision.intervention is Intervention.BACK_OFF

    await _record(app_engine, decision, "dec_backoff_1")

    async with tenant_transaction(app_engine, TENANT) as conn:
        replay = await replay_decision(conn, "dec_backoff_1")

    assert replay.verified, "the back-off record does not hash-verify"
    assert replay.action_type == "back_off"
    assert replay.rationale is not None and "backed off" in replay.rationale
    # Rupee-denominated, so a merchant and an auditor can both read it.
    assert "Rs" in replay.rationale
    assert replay.shows_rejected_candidates, "no record of what silence was chosen over"


async def test_the_recorded_rationale_names_why_silence_was_chosen(
    app_engine: AsyncEngine, tenant: None
) -> None:
    """§24.6 names fatigue and rising revocation hazard as the drivers."""
    await _record(app_engine, _back_off_decision(), "dec_backoff_2")

    async with tenant_transaction(app_engine, TENANT) as conn:
        replay = await replay_decision(conn, "dec_backoff_2")

    assert replay.rationale is not None
    assert "revocation hazard" in replay.rationale
    assert "fatigue" in replay.rationale


async def test_a_back_off_carries_its_arm_like_any_other_decision(
    app_engine: AsyncEngine, tenant: None
) -> None:
    """Excluding silence from the experiment would bias every estimate.

    A treatment arm that backs off more is doing something measurable; if those
    decisions were unarmed the effect would vanish from the comparison.
    """
    await _record(app_engine, _back_off_decision(), "dec_backoff_3")

    async with tenant_transaction(app_engine, TENANT) as conn:
        replay = await replay_decision(conn, "dec_backoff_3")

    assert replay.holdout_arm == "treatment"
    assert replay.propensity is not None
