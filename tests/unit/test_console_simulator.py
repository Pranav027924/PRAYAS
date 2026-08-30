"""The policy simulator (Phase 16; ADR-088).

**Phase 16 exit criterion:** "Simulator recomputes 1,000 cycles in under 2
seconds" — measured, not asserted.

And the guarantee the criterion does not mention: the simulator **cannot act**.
It recomputes what a policy would do; enacting that is a different operation on
a system that moves money, and the difference should be visible in the import
list rather than promised in a docstring.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import numpy as np
import pytest

from prayas.console import simulator
from prayas.console.simulator import (
    SIMULATION_BUDGET_SECONDS,
    PolicyKnobs,
    SimulatorError,
    simulate,
)
from prayas.measure.harness import HORIZON_SLOTS, legal_mask

DUE_AT = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
AMOUNT = 149_900
W = 1_200_000
BUDGET = 3


def _curves(n: int, seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [np.clip(rng.normal(0.3, 0.12, size=HORIZON_SLOTS), 0.0, 1.0) for _ in range(n)]


def _run(curves: list[np.ndarray], knobs: PolicyKnobs) -> simulator.SimulationResult:
    return simulate(
        curves,
        legal_mask(DUE_AT),
        knobs=knobs,
        amount_paise=AMOUNT,
        continuation_value_paise=W,
        revocation_delta=np.full(HORIZON_SLOTS, 0.01),
        health=np.ones(HORIZON_SLOTS),
        budget=BUDGET,
        lead_slots=25,
    )


# ── the criterion ──────────────────────────────────────────────────────────


@pytest.mark.slow
def test_a_thousand_cycles_recompute_inside_the_budget() -> None:
    """**Phase 16 exit criterion.** The Playbook budgeted for an 8 ms DP;
    Phase 15's suffix scan brought it to well under one, so the screen is more
    responsive than the phase was designed around."""
    result = _run(_curves(1000), PolicyKnobs())

    assert result.cycles == 1000
    assert result.elapsed_seconds < SIMULATION_BUDGET_SECONDS, (
        f"1,000 cycles took {result.elapsed_seconds:.2f}s"
    )
    assert result.acted > 0, "a simulation that decided nothing would be fast and useless"


# ── the knobs actually move the policy ─────────────────────────────────────


def test_raising_the_attempt_cost_pushes_attempts_later() -> None:
    """A slider that changes nothing is a slider that lies about what the
    system is weighing.

    The first observable effect of dearer attempts is not *fewer* of them but
    *later* ones: a costlier attempt has to clear a higher bar, so only slots
    where funding is likelier justify it. Measured: mean slot 57 -> 97.
    """
    curves = _curves(120)
    cheap = _run(curves, PolicyKnobs(attempt_cost_multiplier=0.25))
    dear = _run(curves, PolicyKnobs(attempt_cost_multiplier=40.0))

    assert cheap.mean_slot is not None and dear.mean_slot is not None
    assert dear.mean_slot > cheap.mean_slot + 10, (
        f"cost had no effect on timing: {cheap.mean_slot:.0f} -> {dear.mean_slot:.0f}"
    )


def test_a_high_enough_attempt_cost_stops_the_policy_entirely() -> None:
    """And eventually it does stop — but the multiplier required says something
    worth knowing about the calibration.

    An attempt costs 300 to 10,193 paise against a prize of `amount + W` =
    1,349,900. Cost only dominates around a thousand-fold multiplier, which is
    the sequencer being correctly calibrated rather than insensitive: ADR-063
    fitted `W/A` between 7.45 and 1.17, so a mandate is worth many attempts.
    """
    curves = _curves(60)
    assert _run(curves, PolicyKnobs(attempt_cost_multiplier=200.0)).stop_rate == 0.0
    assert _run(curves, PolicyKnobs(attempt_cost_multiplier=1000.0)).stop_rate == 1.0


def test_pricing_revocation_harder_makes_the_policy_more_cautious() -> None:
    """§23.1's μ. Raising it should not make the system attempt *more*."""
    curves = _curves(120)
    lenient = _run(curves, PolicyKnobs(mu_revocation=0.1))
    strict = _run(curves, PolicyKnobs(mu_revocation=50.0))

    assert strict.acted <= lenient.acted


def test_the_default_knobs_leave_the_policy_alone() -> None:
    """Multipliers of 1.0 must be the identity, or the screen's starting
    position would already be a change nobody made."""
    curves = _curves(60)
    result = _run(curves, PolicyKnobs())
    assert result.acted > 0
    assert result.distinct_slots >= 1


def test_the_simulation_is_deterministic() -> None:
    """A slider's effect is only attributable if everything else is fixed."""
    curves = _curves(50)
    first, second = (
        _run(curves, PolicyKnobs(lambda_annoyance=2.0)),
        _run(curves, PolicyKnobs(lambda_annoyance=2.0)),
    )
    assert (first.acted, first.mean_slot, first.distinct_slots) == (
        second.acted,
        second.mean_slot,
        second.distinct_slots,
    )


@pytest.mark.parametrize(
    "knobs",
    [
        {"attempt_cost_multiplier": -1.0},
        {"lambda_annoyance": float("nan")},
        {"mu_revocation": float("inf")},
    ],
)
def test_incoherent_knobs_are_refused(knobs: dict[str, float]) -> None:
    with pytest.raises(SimulatorError, match="finite and non-negative"):
        PolicyKnobs(**knobs)


def test_an_empty_simulation_is_refused() -> None:
    with pytest.raises(SimulatorError, match="nothing to simulate"):
        _run([], PolicyKnobs())


# ── the guarantee: it cannot act (ADR-088) ─────────────────────────────────


def test_the_simulator_imports_nothing_that_can_act() -> None:
    """**Asserted as absence.** Recomputing what a policy would do is a
    different act from doing it, and on a system that moves money the
    difference belongs in the import list."""
    source = inspect.getsource(simulator)

    for forbidden in (
        "prayas.executor",
        "prayas.ledger",
        "prayas.notify",
        "AsyncConnection",
        "AsyncEngine",
    ):
        assert forbidden not in source, f"the simulator reaches {forbidden}"


def test_no_function_here_takes_a_connection() -> None:
    """A module that cannot be handed a database cannot write to one."""
    for name, obj in vars(simulator).items():
        if not callable(obj) or name.startswith("_"):
            continue
        try:
            signature = inspect.signature(obj)
        except (TypeError, ValueError):
            continue
        for parameter in signature.parameters.values():
            annotation = str(parameter.annotation)
            assert "Conn" not in annotation and "Engine" not in annotation, (
                f"{name} accepts {parameter.name}: {annotation}"
            )


def test_the_simulator_exposes_no_write_shaped_entry_point() -> None:
    for forbidden in ("apply", "commit", "schedule", "fire", "enact", "save", "persist"):
        assert not hasattr(simulator, forbidden), f"simulator exposes {forbidden}()"
