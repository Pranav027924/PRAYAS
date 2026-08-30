"""Every ledger record carries an arm and a propensity (Playbook Phase 7).

This criterion has teeth: Phase 6's fire path wrote `None` for both columns, so
the assertion below fails against the code as it stood one commit ago.

§34: off-policy evaluation is "valid only because propensity was logged at
decision time". A propensity reconstructed afterwards is not evidence about how
the action came to be chosen — which is why this is asserted on the persisted
record rather than on a return value.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.executor.claiming import claim_due_actions
from prayas.executor.firing import fire_action
from prayas.measure.assignment import CONTROL, TREATMENT
from prayas.measure.experiment import ExperimentError, active_experiment, freeze
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_measure"
FIRE_AT = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)  # 09:00 IST, a legal window
CONTROL_PCT = 0.20

_WIPE = (
    "TRUNCATE decisions, outbox, scheduled_actions, attempts, cycles,"
    " mandates, experiment_config, tenants RESTART IDENTITY CASCADE"
)


async def _seed(conn: object, n: int) -> None:
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tenants (tenant_id, name, config) VALUES"
            " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
        ),
        {"t": TENANT},
    )
    for i in range(n):
        m, c, a = f"m{i}", f"c{i}", f"a{i}"
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO mandates (mandate_id,tenant_id,customer_id,rail,"
                "max_amount_paise,state,consent_ref,created_at,mcc)"
                " VALUES (:m,:t,:cu,'upi_autopay',5000000,'active','c1',:n,'5411')"
            ),
            {"m": m, "t": TENANT, "cu": f"cust_{i}", "n": FIRE_AT},
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO cycles (cycle_id,tenant_id,mandate_id,seq_no,amount_paise,"
                "due_at,deadline_at,attempt_budget,attempts_used,state,pdn_sent_at)"
                " VALUES (:c,:t,:m,1,49900,:n,:d,4,0,'executing',:p)"
            ),
            {
                "c": c,
                "t": TENANT,
                "m": m,
                "n": FIRE_AT,
                "d": FIRE_AT + timedelta(days=20),
                "p": FIRE_AT - timedelta(hours=30),
            },
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
                "action_type,fire_at,state,payload)"
                " VALUES (:a,:t,:c,:m,'debit_attempt',:f,'pending','{}'::jsonb)"
            ),
            {"a": a, "t": TENANT, "c": c, "m": m, "f": FIRE_AT - timedelta(minutes=1)},
        )


@pytest.fixture
async def batch(owner_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
        await _seed(conn, 40)
    yield owner_engine
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))


async def _fire_all(app_engine: AsyncEngine) -> None:
    async with tenant_transaction(app_engine, TENANT) as conn:
        claimed = await claim_due_actions(conn, TENANT, batch=100)
    for action in claimed:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await fire_action(conn, action, now=FIRE_AT)


async def test_every_decision_carries_an_arm_and_a_propensity(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """The exit criterion, asserted on persisted rows."""
    async with batch.begin() as conn:  # owner role: freezing is an operator act
        await freeze(
            conn,
            experiment_id="exp_p7",
            seed="committed_seed_p7",
            control_pct=CONTROL_PCT,
            git_commit="deadbeef",
            tenant_id=TENANT,
        )

    await _fire_all(app_engine)

    async with tenant_transaction(app_engine, TENANT) as conn:
        total = await conn.scalar(text("SELECT count(*) FROM decisions"))
        missing_arm = await conn.scalar(
            text("SELECT count(*) FROM decisions WHERE holdout_arm IS NULL")
        )
        missing_prop = await conn.scalar(
            text("SELECT count(*) FROM decisions WHERE propensity IS NULL")
        )
        arms = [
            r.holdout_arm
            for r in await conn.execute(text("SELECT DISTINCT holdout_arm FROM decisions"))
        ]

    assert total == 40, f"expected 40 decision records, got {total}"
    assert missing_arm == 0, f"{missing_arm} decisions have no arm"
    assert missing_prop == 0, f"{missing_prop} decisions have no propensity"
    assert set(arms) <= {CONTROL, TREATMENT}
    assert len(arms) == 2, f"only one arm appeared: {arms} — assignment is not splitting"


async def test_recorded_propensity_matches_the_recorded_arm(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """ADR-048: the logged probability must be the one that arm actually had.

    A propensity that did not correspond to its arm would silently reweight
    every IPS estimate built on it.
    """
    async with batch.begin() as conn:  # owner role: freezing is an operator act
        await freeze(
            conn,
            experiment_id="exp_p7",
            seed="committed_seed_p7",
            control_pct=CONTROL_PCT,
            git_commit="deadbeef",
            tenant_id=TENANT,
        )

    await _fire_all(app_engine)

    async with tenant_transaction(app_engine, TENANT) as conn:
        rows = list(await conn.execute(text("SELECT holdout_arm, propensity FROM decisions")))

    assert rows
    for row in rows:
        expected = CONTROL_PCT if row.holdout_arm == CONTROL else 1.0 - CONTROL_PCT
        assert float(row.propensity) == pytest.approx(expected, abs=1e-6), (
            f"arm {row.holdout_arm} logged propensity {row.propensity}, expected {expected}"
        )


async def test_refusals_also_carry_an_arm(app_engine: AsyncEngine, batch: AsyncEngine) -> None:
    """A denied action is a decision too (§32).

    Excluding refusals from the experiment record would bias every estimate
    toward the cycles that happened to succeed at the gate.
    """
    async with batch.begin() as conn:  # owner role: freezing is an operator act
        await freeze(
            conn,
            experiment_id="exp_p7",
            seed="committed_seed_p7",
            control_pct=CONTROL_PCT,
            git_commit="deadbeef",
            tenant_id=TENANT,
        )
        # Make every cycle stale, so every fire refuses.
        await conn.execute(
            text("UPDATE cycles SET state = 'succeeded' WHERE tenant_id = :t"), {"t": TENANT}
        )

    await _fire_all(app_engine)

    async with tenant_transaction(app_engine, TENANT) as conn:
        refusals = await conn.scalar(text("SELECT count(*) FROM decisions WHERE verdict = 'STALE'"))
        missing = await conn.scalar(
            text(
                "SELECT count(*) FROM decisions"
                " WHERE verdict = 'STALE' AND (holdout_arm IS NULL OR propensity IS NULL)"
            )
        )

    assert refusals == 40, f"expected 40 refusals, got {refusals}"
    assert missing == 0, f"{missing} refusals were recorded without an arm"


async def test_without_a_frozen_experiment_the_arm_is_null_not_invented(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """Most of the system's life is spent outside an experiment.

    Recording a fabricated arm then would be worse than recording none — it
    would look like evidence. NULL is the honest value.
    """
    await _fire_all(app_engine)

    async with tenant_transaction(app_engine, TENANT) as conn:
        total = await conn.scalar(text("SELECT count(*) FROM decisions"))
        with_arm = await conn.scalar(
            text("SELECT count(*) FROM decisions WHERE holdout_arm IS NOT NULL")
        )

    assert total == 40
    assert with_arm == 0, "an arm was recorded with no experiment running"


async def test_an_unfrozen_experiment_cannot_assign(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """§33: pre-registration means the seed is fixed before the run."""
    async with batch.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO experiment_config"
                " (experiment_id, tenant_id, seed, control_pct, git_commit,"
                "  committed_at, frozen)"
                " VALUES ('draft', :t, 's', 0.2, 'abc', now(), false)"
            ),
            {"t": TENANT},
        )
    # `active_experiment` only ever returns frozen rows.
    async with tenant_transaction(app_engine, TENANT) as conn:
        assert await active_experiment(conn) is None


async def test_a_frozen_experiment_cannot_be_re_registered(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """Re-seeding after seeing results is the failure freezing prevents."""
    async with batch.begin() as conn:
        await freeze(
            conn,
            experiment_id="exp_locked",
            seed="first",
            control_pct=0.2,
            git_commit="abc",
            tenant_id=TENANT,
        )

    with pytest.raises(ExperimentError, match="already frozen"):
        async with batch.begin() as conn:
            await freeze(
                conn,
                experiment_id="exp_locked",
                seed="second_and_more_flattering",
                control_pct=0.2,
                git_commit="def",
                tenant_id=TENANT,
            )


async def test_the_app_role_cannot_write_experiment_config(
    app_engine: AsyncEngine, batch: AsyncEngine
) -> None:
    """Pre-registration is enforced by privilege, not just by a flag.

    If the running application could rewrite `experiment_config`, the seed
    could be changed after results were seen and `frozen` would be advisory.
    The app role holds SELECT and nothing else — the same construction that
    makes the ledger append-only under Invariant 5.
    """
    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await conn.execute(
                text(
                    "INSERT INTO experiment_config"
                    " (experiment_id, tenant_id, seed, control_pct, git_commit,"
                    "  committed_at, frozen)"
                    " VALUES ('smuggled', :t, 's', 0.2, 'abc', now(), true)"
                ),
                {"t": TENANT},
            )
    assert "permission denied" in str(exc.value).lower()
