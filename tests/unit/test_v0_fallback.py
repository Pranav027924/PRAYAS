"""V0 fallback under a killed V1 (Phase 11 exit criterion).

"Kill V1, system degrades without firing anything illegal."

Two claims, and the second is the one that matters. *Degrades* means the system
keeps deciding — a model outage must not become a payments outage. *Without
firing anything illegal* means the compliance gate is not downstream of the
model at all: Invariants 1 and 2 hold whichever model is serving, because the
model chooses among slots the gate has already permitted rather than choosing
whether a slot is permitted.

That separation is what makes a rollback a routine operation instead of an
incident, so it is asserted rather than assumed.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.measure.harness import (
    HORIZON_SLOTS,
    legal_mask,
    population_hazard_provider,
    recovery_population,
    run_batch,
)
from prayas.models.serving import fallback_provider, v0_provider, v1_provider
from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate

TENANT = "t_fallback"


@pytest.fixture(scope="module")
def population() -> list[SimulatedCycle]:
    return generate(SimConfig(), seed=808, tenant_id=TENANT, cycles=3000)


def test_killing_v1_mid_run_keeps_the_system_deciding(
    population: list[SimulatedCycle],
) -> None:
    """The switch is consulted per cycle, not once at fit time.

    Fitting the fallback lazily would mean discovering, during an incident, that
    the replacement has to train first.
    """
    alive = {"v1": True}
    provider = fallback_provider(v1_provider(), v0_provider(), enabled=lambda: alive["v1"])

    eligible = recovery_population(population)
    split = int(len(eligible) * 0.3)
    curve_for = provider(eligible[:split])

    cycle = eligible[split]
    with_v1 = curve_for(cycle)
    alive["v1"] = False
    with_v0 = curve_for(cycle)

    assert with_v1.shape == with_v0.shape == (HORIZON_SLOTS,)
    assert not np.array_equal(with_v1, with_v0), "the fallback served the same curve"
    assert np.isfinite(with_v0).all()


def test_the_fallback_needs_no_training_at_the_moment_it_is_needed(
    population: list[SimulatedCycle],
) -> None:
    """Both models are fitted up front, so the switch costs nothing."""
    eligible = recovery_population(population)
    split = int(len(eligible) * 0.3)

    dead = {"v1": False}
    provider = fallback_provider(v1_provider(), v0_provider(), enabled=lambda: dead["v1"])
    curve_for = provider(eligible[:split])
    assert np.isfinite(curve_for(eligible[split])).all()


@pytest.mark.parametrize(
    ("provider", "as_presence"),
    [
        pytest.param(population_hazard_provider, False, id="population"),
        pytest.param(v0_provider(), False, id="v0"),
        pytest.param(v1_provider(), False, id="v1"),
        pytest.param(None, True, id="v1-presence"),
    ],
)
def test_no_model_can_cause_an_illegal_attempt(
    population: list[SimulatedCycle], provider: object, as_presence: bool
) -> None:
    """**The criterion that matters.** Whichever model serves, every fired slot
    is one the gate permits — because the model ranks legal slots, it never
    decides legality. This is Invariant 1 expressed as a test over the whole
    population rather than as a claim about the code path.
    """
    if as_presence:
        # ADR-075's path deviates from §23.2, so it needs this guarantee most:
        # the gate is not downstream of the model on it either.
        from prayas.measure.harness import funds_present
        from prayas.models.serving import presence_provider

        result = run_batch(
            population,
            seed="fallback",
            control_pct=0.15,
            presence_provider=presence_provider(
                "v1", funds_present, lambda c: legal_mask(c.due_at)
            ),
        )
    else:
        result = run_batch(
            population,
            seed="fallback",
            control_pct=0.15,
            hazard_provider=provider,  # type: ignore[arg-type]
        )
    assert result.cycles

    by_cycle = {c.cycle_id: c for c in population}
    illegal = 0
    for scored in result.cycles:
        legal = legal_mask(by_cycle[scored.cycle_id].due_at)
        illegal += sum(1 for slot in scored.decision_slots if not legal[slot])
    assert illegal == 0, f"{illegal} attempts fired into slots the gate would deny"


def test_a_degraded_system_still_recovers_money(population: list[SimulatedCycle]) -> None:
    """Degrading is not stopping. A model outage must not become a payments
    outage — the fallback has to keep earning, not merely keep running."""
    result = run_batch(population, seed="fallback", control_pct=0.15, hazard_provider=v0_provider())
    treatment = result.arm("treatment")
    assert treatment.recovery_rate > 0.0
    assert sum(c.recovered_paise for c in result.cycles if c.arm == "treatment") > 0


def test_every_arm_respects_the_attempt_budget(population: list[SimulatedCycle]) -> None:
    """§1 gives one execution and three retries; the execution already failed."""
    from prayas.measure.harness import ATTEMPT_BUDGET

    for provider in (v0_provider(), v1_provider()):
        result = run_batch(
            population,
            seed="fallback",
            control_pct=0.15,
            hazard_provider=provider,
        )
        assert max(c.attempts_fired for c in result.cycles) <= ATTEMPT_BUDGET
