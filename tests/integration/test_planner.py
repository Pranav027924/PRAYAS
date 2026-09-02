"""The decision service (ADR-096; Master Spec §17.2, §23.3, §44).

These pin the guards rather than the arithmetic. The DP's choice is already
covered by the sequencer's own tests; what is untested elsewhere is whether the
planner *may* act at all — the stage, the cohort, and the duplicate check — and
those are the ones whose failure would queue debits that should not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.adoption.stages import Stage
from prayas.models.live import PriorTable
from prayas.planner.worker import plan_tenant

TENANT = "t_planner"
MANDATE = "sub_planner_1"
CYCLE = "inv_planner_1"
AMOUNT = 250_000

#: Presence high enough that §23.3's expected-value test passes. At the
#: cold-start rate the DP correctly declines every cycle (FINDING-P17-07), so a
#: thin prior here would make every assertion below pass vacuously.
RICH_PRIORS = PriorTable(cells={}, global_hazard=0.42)
COLD_PRIORS = PriorTable(cells={}, global_hazard=0.02)


async def _seed(engine: AsyncEngine, *, stage: Stage) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM scheduled_actions WHERE tenant_id = :t"), {"t": TENANT}
        )
        await conn.execute(text("DELETE FROM cycles WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(text("DELETE FROM mandates WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config)"
                " VALUES (:t, :t, jsonb_build_object('adoption_stage', CAST(:s AS int)))"
            ),
            {"t": TENANT, "s": int(stage)},
        )
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, 'cust_1', 'upi_autopay', 1500000, 'active', 'c1', :now)"
            ),
            {"m": MANDATE, "t": TENANT, "now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                " due_at, deadline_at, attempt_budget, attempts_used, state)"
                " VALUES (:c, :t, :m, 1, :amt, :due, :dl, 4, 1, 'executing')"
            ),
            {
                "c": CYCLE,
                "t": TENANT,
                "m": MANDATE,
                "amt": AMOUNT,
                "due": now,
                "dl": now + timedelta(days=20),
            },
        )


async def _actions(engine: AsyncEngine) -> list[tuple[str, str]]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT action_id, action_type FROM scheduled_actions"
                " WHERE tenant_id = :t ORDER BY fire_at"
            ),
            {"t": TENANT},
        )
        return [(r.action_id, r.action_type) for r in rows]


@pytest.mark.asyncio
async def test_observe_schedules_nothing(owner_engine: AsyncEngine) -> None:
    """§44: OBSERVE ingests only. A tenant nobody has onboarded is not debited."""
    await _seed(owner_engine, stage=Stage.OBSERVE)
    result = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
    assert result.scheduled == 0
    assert await _actions(owner_engine) == []


@pytest.mark.asyncio
async def test_shadow_decides_but_queues_nothing(owner_engine: AsyncEngine) -> None:
    """§44: 'Decide and log. Fire nothing.'

    A queued action the executor would only cancel makes the shadow arm
    indistinguishable from a broken treatment arm.
    """
    await _seed(owner_engine, stage=Stage.SHADOW)
    result = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
    assert result.shadowed == 1
    assert result.scheduled == 0
    assert await _actions(owner_engine) == []


@pytest.mark.asyncio
async def test_a_scheduled_debit_is_always_paired_with_its_notice(
    owner_engine: AsyncEngine,
) -> None:
    """ADR-100. A debit without a notice is denied at fire time, every time.

    The planner must therefore emit the pair or neither — scheduling a lone
    debit would queue an action guaranteed to be refused, which is how
    FINDING-P17-11 looked from the outside.
    """
    await _seed(owner_engine, stage=Stage.FULL)
    result = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
    # The mandate may land in FULL's 15% holdout; either way one cycle is
    # considered and nothing is queued for a holdout.
    assert result.scheduled + result.stopped <= 1

    actions = await _actions(owner_engine)
    assert len(actions) == result.scheduled * 2, "a debit was queued without its notice"
    if result.scheduled:
        kinds = [kind for _, kind in actions]
        assert kinds == ["pdn_notice", "debit_attempt"], "the notice must precede the debit"


@pytest.mark.asyncio
async def test_a_second_tick_does_not_queue_a_second_debit(owner_engine: AsyncEngine) -> None:
    """The duplicate guard is what stops the planner queueing an attempt a tick.

    Both halves matter: the `NOT EXISTS` check skips the cycle, and the
    deterministic `action_id` collides if the check is ever wrong.
    """
    await _seed(owner_engine, stage=Stage.FULL)
    first = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
    before = await _actions(owner_engine)

    for _ in range(3):
        again = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
        assert again.scheduled == 0

    assert await _actions(owner_engine) == before
    assert len(before) == first.scheduled * 2


@pytest.mark.asyncio
async def test_the_revocation_model_is_what_lets_the_planner_act(
    owner_engine: AsyncEngine,
) -> None:
    """ADR-062, and the bug this test exists for.

    `revocation_delta()` without a model returns ADR-037's placeholder `0.04`
    at *every* slot, so an attempt costs `0.04 x 12A = 0.48A` in expected
    continuation value whenever it fires. No realistic liquidity prior clears
    that, and the planner declines everything — which is what it did until §22's
    model was wired in.

    §22's `marginal_delta` is instead the risk accrued by *waiting*: ~0 for an
    early slot, rising with delay. Asserting both directions is the point; a
    test that only checked "it schedules" would pass with the placebo restored
    as long as some other input were generous enough.
    """
    from prayas.planner.worker import _bootstrap_revocation_model, _revocation_features
    from prayas.sequencer.economics import revocation_delta

    placeholder = revocation_delta(720)
    modelled = revocation_delta(
        720,
        model=_bootstrap_revocation_model(),
        features=_revocation_features(
            rail="upi_autopay", attempts_used=1, days_since_success=2.0, successful_cycles=3
        ),
    )

    assert placeholder.min() == placeholder.max(), "the placeholder is flat by construction"
    assert modelled[0] < placeholder[0] / 100, "waiting no time should cost ~nothing"
    assert modelled[-1] > modelled[0], "risk must accrue with delay"

    # And end to end: with the model, a cycle at bootstrap liquidity is planned.
    await _seed(owner_engine, stage=Stage.FULL)
    result = await plan_tenant(owner_engine, TENANT, RICH_PRIORS)
    assert result.scheduled + result.stopped == 1
