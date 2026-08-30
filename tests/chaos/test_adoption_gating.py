"""Adoption stages on the money path (Master Spec §44; ADR-089).

§44 makes each stage a *behaviour*: Observe ingests only, Shadow decides and
**fires nothing**. Those are claims about what the executor does, so they are
asserted against the executor rather than against a configuration flag.

The check sits at fire time, not only at scheduling time. An action scheduled
before a stage change would otherwise still fire, and "fire nothing" that
permits already-scheduled actions is not what §44 says — it is the same
reasoning as Invariant 3's fire-time revalidation.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.adoption.stages import Evidence, Stage, may_fire
from prayas.adoption.store import current_stage, promote, set_stage
from prayas.db.tenancy import system_transaction, tenant_transaction
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


async def _clear_stage(conn: object, tenant: str) -> None:
    """Remove any recorded stage.

    The shared fixture seeds tenants at FULL so that suites written before the
    adoption ramp still fire (ADR-089). Tests *about* the missing-stage default
    must therefore establish the absence rather than assume it — otherwise they
    would be asserting the fixture's value and passing for the wrong reason.
    """
    await conn.execute(  # type: ignore[attr-defined]
        text("UPDATE tenants SET config = config - 'adoption_stage' WHERE tenant_id = :t"),
        {"t": tenant},
    )


async def _seed_cycle(conn: object, tenant: str, now: object) -> None:
    """A mandate and a cycle for the executor to act on."""
    params = {"t": tenant, "now": now, "deadline": now}
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
            " max_amount_paise, state, consent_ref, created_at)"
            " VALUES (:t || '_stage_m', :t, 'cust_stage', 'upi_autopay', 500000,"
            " 'active', 'c1', :now) ON CONFLICT DO NOTHING"
        ),
        params,
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
            " due_at, deadline_at, attempt_budget, state)"
            " VALUES (:t || '_stage_c', :t, :t || '_stage_m', 1, 149900, :now,"
            " :deadline, 3, 'scheduled') ON CONFLICT DO NOTHING"
        ),
        params,
    )


COMPLETE = Evidence(
    event_completeness=0.9995,
    projection_matches_days=7,
    shadow_decisions=10_000,
    gate_errors=0,
    calibration_ece=0.004,
    audit_report_produced=True,
    canary_days=14,
    double_debits=0,
    compliance_violations=0,
    revocation_rate_above_baseline=False,
    recovery_ci_excludes_zero=True,
    survival_ci_negative=False,
    guardrails_green_days=30,
)


# ── failing closed ─────────────────────────────────────────────────────────


async def test_a_tenant_with_no_recorded_stage_is_observe(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**The load-bearing default.** A missing stage is a tenant nobody has
    onboarded, and firing at their customers would be the worst possible
    reading of silence."""
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await _clear_stage(conn, tenant)
        assert await current_stage(conn, tenant) is Stage.OBSERVE
        assert not may_fire(await current_stage(conn, tenant))


async def test_an_unknown_tenant_is_observe(owner_engine: AsyncEngine) -> None:
    async with system_transaction(owner_engine) as conn:
        assert await current_stage(conn, "never_onboarded") is Stage.OBSERVE


async def test_an_unrecognised_stage_value_does_not_guess_upward(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """A corrupted value is not a licence to assume the most permissive stage."""
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await conn.execute(
            text(
                "UPDATE tenants SET config = config || "
                "jsonb_build_object('adoption_stage', 'full') WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
        assert await current_stage(conn, tenant) is Stage.OBSERVE


async def test_an_unreadable_config_fails_closed() -> None:
    """Same direction as the kill switch and the compliance gate: when the
    system cannot establish what it may do, it does less."""
    from sqlalchemy.exc import SQLAlchemyError

    class Broken:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise SQLAlchemyError("connection lost")

    assert await current_stage(Broken(), "t_alpha") is Stage.OBSERVE  # type: ignore[arg-type]


# ── promotion is gated by evidence ─────────────────────────────────────────


async def test_promotion_requires_the_evidence(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    from prayas.adoption.stages import AdoptionError

    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await _clear_stage(conn, tenant)
        with pytest.raises(AdoptionError, match="event completeness"):
            await promote(conn, tenant, Evidence())

        assert await current_stage(conn, tenant) is Stage.OBSERVE, (
            "a refused promotion still moved the stage"
        )


async def test_a_full_ramp_is_persisted_stage_by_stage(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await _clear_stage(conn, tenant)
        for expected in (Stage.SHADOW, Stage.CANARY, Stage.RAMP, Stage.FULL):
            assert await promote(conn, tenant, COMPLETE) is expected
            assert await current_stage(conn, tenant) is expected


async def test_set_stage_is_the_deliberate_override(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Rollback is a person's decision and does not need evidence. It is a
    different function from `promote` precisely so that advancing cannot
    borrow its permissiveness."""
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await set_stage(conn, tenant, Stage.FULL)
        assert await current_stage(conn, tenant) is Stage.FULL

        await set_stage(conn, tenant, Stage.OBSERVE)
        assert await current_stage(conn, tenant) is Stage.OBSERVE


# ── the executor honours the stage ─────────────────────────────────────────


@pytest.mark.parametrize("stage", [Stage.OBSERVE, Stage.SHADOW])
async def test_the_executor_refuses_to_fire_below_canary(
    app_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    two_tenants: tuple[str, str],
    stage: Stage,
) -> None:
    """**§44's behaviour column, asserted by driving the executor.**

    Not by reading `may_fire` — by handing `fire_action` a claimed action and
    observing that it refuses. A flag that says "fires nothing" while the
    executor fires is exactly the gap this closes.
    """
    from datetime import UTC, datetime

    from prayas.executor.claiming import ClaimedAction
    from prayas.executor.firing import fire_action

    tenant, _ = two_tenants
    now = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)

    async with system_transaction(owner_engine) as conn:
        await set_stage(conn, tenant, stage)

    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed_cycle(conn, tenant, now)
        action = ClaimedAction(
            action_id=f"{tenant}_stage_act",
            tenant_id=tenant,
            cycle_id=f"{tenant}_stage_c",
            mandate_id=f"{tenant}_stage_m",
            action_type="debit_attempt",
            fire_at=now,
            payload={},
        )
        outcome = await fire_action(conn, action, now=now)

    assert not outcome.fired
    assert outcome.reason == "stage_forbids_firing", outcome.reason
    assert stage.name in (outcome.detail or "")


async def test_the_executor_would_fire_at_canary(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """The other direction. Without this, a stage check that refused
    *everything* would satisfy every assertion above."""
    from datetime import UTC, datetime

    from prayas.executor.claiming import ClaimedAction
    from prayas.executor.firing import fire_action

    tenant, _ = two_tenants
    now = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)

    async with system_transaction(owner_engine) as conn:
        await set_stage(conn, tenant, Stage.CANARY)

    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed_cycle(conn, tenant, now)
        outcome = await fire_action(
            conn,
            ClaimedAction(
                action_id=f"{tenant}_canary_act",
                tenant_id=tenant,
                cycle_id=f"{tenant}_stage_c",
                mandate_id=f"{tenant}_stage_m",
                action_type="debit_attempt",
                fire_at=now,
                payload={},
            ),
            now=now,
        )

    assert outcome.reason != "stage_forbids_firing", (
        "the stage check refuses at CANARY, so it would refuse everywhere"
    )


async def test_the_firing_path_consults_the_stage() -> None:
    """Structural: the check is in `fire_action`, not only at scheduling.

    An action scheduled before a stage change would otherwise still fire, and
    §44's "fire nothing" does not carve out actions already in flight.
    """
    import inspect

    from prayas.executor import firing

    source = inspect.getsource(firing.fire_action)
    assert "current_stage" in source
    assert "may_fire" in source
