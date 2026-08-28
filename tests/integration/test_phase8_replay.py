"""Every decision replayable (Playbook Phase 8; Master Spec §32).

The criterion is *every*, so the check is exhaustive rather than sampled — a
sample would not detect the one record that fails.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.ledger.chain import append
from prayas.measure.replay import ReplayError, replay_all, replay_decision
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_p8replay"
DECISIONS = 250
TS = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)

_WIPE = "TRUNCATE decisions, tenants RESTART IDENTITY CASCADE"


@pytest.fixture
async def ledger(owner_engine: AsyncEngine, app_engine: AsyncEngine) -> AsyncIterator[None]:
    """A tenant with a realistic run's worth of decisions on its chain."""
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
        await conn.execute(
            text("INSERT INTO tenants (tenant_id, name) VALUES (:t,:t)"), {"t": TENANT}
        )

    async with tenant_transaction(app_engine, TENANT) as conn:
        for i in range(DECISIONS):
            await append(
                conn,
                {
                    "decision_id": f"dec_{i:06d}",
                    "ts": TS + timedelta(minutes=i),
                    "trigger_event_id": f"evt_{i:06d}",
                    "mandate_id": f"mnd_{i:06d}",
                    "cycle_id": f"cyc_{i:06d}",
                    "action_type": "debit_attempt",
                    "verdict": "ALLOW" if i % 5 else "DENY",
                    "feature_snapshot_ref": f"snap_{i:06d}",
                    "model_versions": json.dumps({"hazard": "v0", "cause": "v0"}),
                    "cause_posterior": json.dumps({"no_funds": 0.71, "fraud_hold": 0.29}),
                    "liquidity_curve_ref": f"curve_{i:06d}",
                    "revocation_hazard": 0.04,
                    "continuation_value": 598_800,
                    # §32: every candidate, including those not chosen.
                    "candidate_actions": json.dumps(
                        [
                            {"slot": 49, "ev_paise": 310_000},
                            {"slot": 73, "ev_paise": 305_100},
                            {"slot": 97, "ev_paise": 298_400},
                        ]
                    ),
                    "chosen_action": json.dumps({"slot": 49, "ev_paise": 310_000}),
                    "rationale": f"fired attempt {i % 3 + 1} of 3",
                    "compliance_checks": json.dumps(
                        [
                            {
                                "rule_id": "NPCI-AUTOPAY-WINDOW",
                                "version": 3,
                                "as_of": "2026-08-01",
                                "citation": "UPI Autopay non-peak execution windows",
                                "verdict": "ALLOW",
                            }
                        ]
                    ),
                    "holdout_arm": "treatment" if i % 7 else "control",
                    "propensity": 0.85 if i % 7 else 0.15,
                    "degraded": False,
                    "outcome": None,
                    "outcome_ts": None,
                    "recovered_paise": None,
                },
                TENANT,
            )
    yield
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))


async def test_every_decision_replays_and_verifies(app_engine: AsyncEngine, ledger: None) -> None:
    """The criterion, exhaustively."""
    async with tenant_transaction(app_engine, TENANT) as conn:
        replays = await replay_all(conn, TENANT)

    assert len(replays) == DECISIONS, f"replayed {len(replays)} of {DECISIONS}"

    unverified = [r.decision_id for r in replays if not r.verified]
    assert unverified == [], f"{len(unverified)} records failed hash verification"

    # §32 requires the rejected candidates, not just the winner.
    without_candidates = [r.decision_id for r in replays if not r.shows_rejected_candidates]
    assert without_candidates == [], (
        f"{len(without_candidates)} decisions recorded no candidate set"
    )


async def test_replay_carries_arm_propensity_and_citations(
    app_engine: AsyncEngine, ledger: None
) -> None:
    """What a reviewer actually needs to answer for the action."""
    async with tenant_transaction(app_engine, TENANT) as conn:
        replay = await replay_decision(conn, "dec_000001")

    assert replay.holdout_arm in {"control", "treatment"}
    assert replay.propensity is not None
    assert replay.chosen_action is not None
    assert replay.compliance_checks
    check = replay.compliance_checks[0]
    # Invariant 10: every rule carries a citation and an as_of date.
    assert check["citation"] and check["as_of"] and check["version"]
    assert replay.verified


async def test_denials_replay_as_readily_as_allowals(app_engine: AsyncEngine, ledger: None) -> None:
    """§32: "a denial is the proof the gate works"."""
    async with tenant_transaction(app_engine, TENANT) as conn:
        replays = await replay_all(conn, TENANT)

    denials = [r for r in replays if r.verdict == "DENY"]
    assert denials, "no denials in the fixture"
    assert all(r.verified and r.shows_rejected_candidates for r in denials)


async def test_a_tampered_record_fails_replay_verification(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, ledger: None
) -> None:
    """Replay must be able to say no.

    The app role cannot UPDATE `decisions` (Invariant 5), so the tamper is
    applied as the owner — which is precisely the insider case the hash chain
    exists to detect.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE decisions SET recovered_paise = 999999 WHERE decision_id = 'dec_000010'")
        )

    async with tenant_transaction(app_engine, TENANT) as conn:
        tampered = await replay_decision(conn, "dec_000010")
        intact = await replay_decision(conn, "dec_000011")

    assert not tampered.verified, "a tampered record replayed as verified"
    assert intact.verified, "tampering one record invalidated an untouched one"


async def test_replaying_an_unknown_decision_is_an_error(
    app_engine: AsyncEngine, ledger: None
) -> None:
    with pytest.raises(ReplayError, match="no decision"):
        async with tenant_transaction(app_engine, TENANT) as conn:
            await replay_decision(conn, "dec_does_not_exist")
