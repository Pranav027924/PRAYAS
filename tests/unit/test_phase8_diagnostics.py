"""What the Phase 8 lift is, and what it is not.

The Playbook: "If the lift is not there, stop and find out why before building
further." The lift *is* there and survives every perturbation — but it is not
attributable to liquidity forecasting, and these tests pin exactly why.

Three genuine bugs were found and fixed while establishing this:

1. **The recovery population included cycles that never failed.** Roughly 40%
   of generated cycles succeed on the first debit and are not recovery
   opportunities at all; scoring both arms on them added free wins.
2. **Funding landed only on whole-day boundaries**, making the hazard curve a
   comb — 5 of 143 legal slots carried any mass — so `S(t)` was flat between
   spikes and `p(t'|t)` could not separate adjacent slots.
3. **The attempt budget was 4 rather than 3.** §1 permits "one execution plus
   up to three retries"; the execution is the debit that already failed, so
   budgeting 4 handed the sequencer an attempt the regulator does not allow.

All three are fixed. **The lift is unchanged when the fitted hazard is replaced
by an uninformative prior anyway** — which is the finding these tests exist to
record, now isolated from the bugs that used to confound it.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.measure import harness
from prayas.measure.assignment import CONTROL, TREATMENT, arm
from prayas.sequencer.dp import solve
from prayas.sequencer.economics import (
    attempt_cost_matrix,
    continuation_value_paise,
    health_multiplier,
    revocation_delta,
)
from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate

SEED = 20260829
CYCLES = 4000
ASSIGN_SEED = "p8_seed"
CONTROL_PCT = 0.15


@pytest.fixture(scope="module")
def population() -> list[SimulatedCycle]:
    return generate(SimConfig(), seed=SEED, tenant_id="t_p8diag", cycles=CYCLES)


@pytest.fixture(scope="module")
def eligible(population: list[SimulatedCycle]) -> list[SimulatedCycle]:
    return harness.recovery_population(population)


def _run(eligible: list[SimulatedCycle], hazards: np.ndarray) -> harness.RunResult:
    split = int(len(eligible) * 0.3)
    return harness.RunResult(
        seed=ASSIGN_SEED,
        control_pct=CONTROL_PCT,
        cycles=[
            harness.run_arm(
                c,
                arm(c.customer_id, ASSIGN_SEED, CONTROL_PCT),
                hazards=hazards,
                control_pct=CONTROL_PCT,
            )
            for c in eligible[split:]
        ],
    )


# ── the three fixed bugs, pinned so they cannot return ──────────────────────


def test_only_failed_cycles_enter_the_recovery_population(
    population: list[SimulatedCycle], eligible: list[SimulatedCycle]
) -> None:
    """A cycle whose first debit succeeded has nothing to recover."""
    assert 0 < len(eligible) < len(population), "population filter is a no-op"
    assert all(harness.initial_debit_failed(c) for c in eligible)
    assert any(not harness.initial_debit_failed(c) for c in population), (
        "the fixture contains no successful cycles, so the filter is untested"
    )


def test_the_hazard_curve_now_has_usable_structure(eligible: list[SimulatedCycle]) -> None:
    """Funding is dispersed within the day, so `h(t)` is no longer a comb.

    Before the fix, 5 of 143 legal slots carried mass and `S(t)` was flat
    between them. This asserts the *fixed* state, and is deliberately the
    inverse of what this test once claimed.
    """
    split = int(len(eligible) * 0.3)
    hazards = harness.empirical_hazards(eligible[:split])
    legal_region = hazards[harness.PDN_LEAD_HOURS : harness.DUNNING_WINDOW_DAYS * 24]

    with_mass = int((legal_region > 1e-5).sum())
    assert with_mass > 50, (
        f"only {with_mass} of {legal_region.size} legal slots carry mass — the comb has returned"
    )


def test_the_attempt_budget_is_three_retries_not_four() -> None:
    """§1: one execution plus up to three retries. The execution is spent."""
    assert harness.ATTEMPT_BUDGET == 3


# ── the finding that survives all three fixes ───────────────────────────────


def test_an_uninformative_prior_produces_the_same_lift(eligible: list[SimulatedCycle]) -> None:
    """The decisive diagnostic, with every known bug fixed.

    The hazard curve now has real structure, the population is correct, and the
    budget is right — and replacing the fitted curve with a flat prior *still*
    reproduces the lift exactly. The liquidity model is not what produces it.
    """
    split = int(len(eligible) * 0.3)
    fitted = harness.empirical_hazards(eligible[:split])
    flat = np.full(harness.HORIZON_SLOTS, float(fitted.mean()))

    with_model = _run(eligible, fitted)
    without_model = _run(eligible, flat)

    lift_with = with_model.arm(TREATMENT).recovery_rate - with_model.arm(CONTROL).recovery_rate
    lift_without = (
        without_model.arm(TREATMENT).recovery_rate - without_model.arm(CONTROL).recovery_rate
    )

    assert lift_with == pytest.approx(lift_without, abs=1e-9), (
        f"fitted {lift_with:+.4f} vs uninformative {lift_without:+.4f}"
    )


def test_the_dp_itself_is_not_degenerate() -> None:
    """The sequencer responds to shape when the shape has somewhere to bite.

    This separates "the DP is broken" from "the objective has no delay term".
    It is the latter: given a curve whose mass *stops*, the DP acts early;
    given one that keeps accruing, later always weakly dominates because §23.1
    charges for attempts but never for waiting.
    """
    horizon, budget, amount = harness.HORIZON_SLOTS, harness.ATTEMPT_BUDGET, 49_900
    legal = np.zeros(horizon, dtype=np.bool_)
    legal[harness.PDN_LEAD_HOURS : harness.DUNNING_WINDOW_DAYS * 24] = True

    def chosen(hazards: np.ndarray) -> int | None:
        policy = solve(
            survival=harness.survival_from(hazards),
            legal=legal,
            cost=attempt_cost_matrix(amount_paise=amount, budget=budget, horizon_slots=horizon),
            amount_paise=amount,
            continuation_value_paise=continuation_value_paise(amount),
            dr=revocation_delta(horizon),
            health=health_multiplier(horizon),
            budget=budget,
            lead_slots=harness.PDN_LEAD_HOURS,
            p_recoverable=0.8,
        )
        return policy.best_slot(budget, 0)

    flat = np.full(horizon, 0.01)
    early = np.full(horizon, 1e-6)
    early[25:72] = 0.08  # mass early, nothing after day 3

    flat_choice, early_choice = chosen(flat), chosen(early)
    assert flat_choice is not None and early_choice is not None
    assert early_choice < flat_choice, (
        "a curve whose funding mass stops early was not acted on earlier"
    )


def test_the_measured_lift_is_attempt_economy_not_timing(eligible: list[SimulatedCycle]) -> None:
    """What the number actually demonstrates.

    Treatment spends far fewer attempts per recovery than control, clearing
    §6's "target below 2.0". That result is real and survives the critique —
    but it comes from spending fewer attempts, later, inside the legal windows,
    not from predicting when money arrives.
    """
    split = int(len(eligible) * 0.3)
    result = _run(eligible, harness.empirical_hazards(eligible[:split]))
    control, treatment = result.arm(CONTROL), result.arm(TREATMENT)

    assert treatment.attempts_per_recovery < 2.0, "§6's efficiency target not met"
    assert treatment.attempts_per_recovery < control.attempts_per_recovery
    assert treatment.recovery_rate > control.recovery_rate


def test_treatment_fires_nothing_the_gate_would_deny(eligible: list[SimulatedCycle]) -> None:
    """Zero compliance violations in treatment — a Phase 8 exit criterion.

    Every slot the sequencer proposes clears the execution window, the 24-hour
    notice lead and the cycle deadline before it counts as fired.
    """
    split = int(len(eligible) * 0.3)
    result = _run(eligible, harness.empirical_hazards(eligible[:split]))
    treatment = result.arm(TREATMENT)

    assert treatment.gate_denials == 0, (
        f"{treatment.gate_denials} treatment attempts would have been denied"
    )
    assert treatment.attempts > 0, "no attempts fired, so the assertion is vacuous"
