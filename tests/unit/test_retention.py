"""Phase 10: the revocation model, W, and the LTV sequencer's conservatism.

Three of Phase 10's exit criteria live here, plus the resolution of
FINDING-P8-01 — which is the reason ADR-037 and ADR-040 both deferred to this
phase.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from prayas.measure import harness
from prayas.measure.survival import kaplan_meier, log_rank
from prayas.retention.continuation import continuation_value_paise, value_multiple
from prayas.retention.revocation import (
    MIN_CELL_OBSERVATIONS,
    RevocationError,
    RevocationFeatures,
    RevocationModel,
    at_risk_mandates,
    expected_calibration_error,
    fit,
)
from prayas.sequencer.dp import solve
from prayas.sequencer.economics import attempt_cost_matrix, health_multiplier
from prayas.sim.config import SimConfig
from prayas.sim.lifecycle import MandateLifecycle, flatten, generate_lifecycles

CYCLES_PER_MANDATE = 12
AMOUNT = 49_900


@pytest.fixture(scope="module")
def train() -> list[MandateLifecycle]:
    return generate_lifecycles(
        SimConfig(cycles_per_mandate=CYCLES_PER_MANDATE),
        seed=20260829,
        tenant_id="t_ret_train",
        mandates=4000,
    )


@pytest.fixture(scope="module")
def holdout() -> list[MandateLifecycle]:
    return generate_lifecycles(
        SimConfig(cycles_per_mandate=CYCLES_PER_MANDATE),
        seed=777,
        tenant_id="t_ret_test",
        mandates=2500,
    )


@pytest.fixture(scope="module")
def model(train: list[MandateLifecycle]) -> RevocationModel:
    return fit(train)


def _features(failures: int, unpaid: float, successes: int = 3) -> RevocationFeatures:
    return RevocationFeatures(
        consecutive_failures=failures,
        days_since_success=unpaid,
        successful_cycles=successes,
        rail="upi_autopay",
    )


# ── The simulator can now produce what §22 needs ────────────────────────────


def test_mandates_now_live_and_die(train: list[MandateLifecycle]) -> None:
    """ADR-060: `revocation_beta` was declared since Phase 3 and never read.

    Without deaths there is nothing to calibrate against and no time axis to
    draw survival curves over — three Phase 10 criteria were unachievable.
    """
    died = sum(1 for lifecycle in train if lifecycle.died)
    assert 0 < died < len(train), "every mandate died or none did; neither is a population"
    assert any(lifecycle.observed_cycles > 1 for lifecycle in train), "no mandate billed twice"
    assert any(not lifecycle.died for lifecycle in train), "no censored observations"


# ── Exit criterion: revocation model calibrated on simulated deaths ─────────


def test_the_revocation_model_is_calibrated_on_held_out_mandates(
    model: RevocationModel, holdout: list[MandateLifecycle]
) -> None:
    """Appendix C alerts above an ECE of 0.05; §22 needs a *price*, not a rank."""
    ece = expected_calibration_error(model, holdout)
    assert ece < 0.05, f"ECE {ece:.4f} exceeds Appendix C's alert threshold"


def test_a_deliberately_miscalibrated_model_is_detected(
    holdout: list[MandateLifecycle],
) -> None:
    """The metric must be able to fail, or passing it means nothing."""
    broken = RevocationModel(cell_hazard={}, cell_counts={}, global_hazard=0.85)
    assert expected_calibration_error(broken, holdout) > 0.05


def test_hazard_rises_with_every_feature_section_22_names(model: RevocationModel) -> None:
    """§22's directions: failures ↑, days unpaid ↑, tenure ↓."""
    assert model.monthly_hazard(_features(3, 90.0)) > model.monthly_hazard(_features(0, 0.0))
    assert model.monthly_hazard(_features(2, 60.0)) > model.monthly_hazard(_features(1, 30.0))


def test_a_thin_cell_falls_back_to_the_global_rate(model: RevocationModel) -> None:
    """§27's k-anonymity floor: never report a hazard fitted on a handful."""
    exotic = RevocationFeatures(
        consecutive_failures=4, days_since_success=90.0, successful_cycles=99, rail="enach"
    )
    if model.cell_counts.get(exotic.cell, 0) < MIN_CELL_OBSERVATIONS:
        assert model.monthly_hazard(exotic) == model.global_hazard


def test_daily_hazard_compounds_rather_than_dividing(model: RevocationModel) -> None:
    """`1 - (1-m)^(1/30)`, not `m/30`.

    Each day's hazard applies to the previous day's survivors, so the daily
    rate needed to reach a given monthly one is slightly *higher* than the
    division suggests. The decisive check is the round trip: compounding the
    daily rate over 30 days must return the monthly rate exactly.
    """
    features = _features(3, 90.0)
    monthly = model.monthly_hazard(features)
    daily = model.daily_hazard(features)

    assert daily > monthly / 30.0, "the linear form understates; this should exceed it"
    assert model.survival(features, days=30) == pytest.approx(1.0 - monthly, abs=1e-9)


def test_the_at_risk_sweep_flags_the_worst_mandates(
    model: RevocationModel, holdout: list[MandateLifecycle]
) -> None:
    """Appendix C's `revocation_hazard.at_risk_threshold: 0.15`."""
    flagged = set(at_risk_mandates(model, holdout, threshold=0.15))
    assert 0 < len(flagged) < len(holdout), "the sweep flagged everything or nothing"
    with pytest.raises(RevocationError, match="threshold"):
        at_risk_mandates(model, holdout, threshold=1.5)


# ── Continuation value W (ADR-063) ─────────────────────────────────────────


def test_w_falls_as_the_mandate_deteriorates(model: RevocationModel) -> None:
    """§23.1: the asset being managed is the mandate.

    A mandate likelier to die before billing again is worth less. ADR-036's
    flat `12 * A` valued a nearly-dead mandate the same as a healthy one, which
    made the DP refuse attempts on exactly the mandates where attempting risks
    least.
    """
    healthy = value_multiple(model, _features(0, 0.0, successes=6), collect_probability=0.44)
    failing = value_multiple(model, _features(4, 90.0, successes=1), collect_probability=0.44)

    assert healthy > failing, "W does not fall as the mandate deteriorates"
    assert failing < 12.0, "the fitted W is no cheaper than ADR-036's placeholder"


def test_w_is_integer_paise_and_rejects_bad_inputs(model: RevocationModel) -> None:
    features = _features(1, 30.0)
    value = continuation_value_paise(model, features, amount_paise=AMOUNT, collect_probability=0.44)
    assert isinstance(value, int)

    # Typed as Any so the runtime guard is exercised; mypy would otherwise
    # reject the call statically and the check would never run.
    not_an_int: Any = 1.5
    with pytest.raises(TypeError, match="integer paise"):
        continuation_value_paise(model, features, amount_paise=not_an_int, collect_probability=0.44)
    with pytest.raises(ValueError, match="probability"):
        continuation_value_paise(model, features, amount_paise=AMOUNT, collect_probability=1.4)


# ── Exit criterion: survival curves per arm with a log-rank test ────────────


def test_survival_curves_per_arm_with_log_rank(
    train: list[MandateLifecycle], holdout: list[MandateLifecycle]
) -> None:
    """§34's retention comparison, on real simulated deaths with censoring."""

    def arrays(lifecycles: list[MandateLifecycle]) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.array([lc.duration_days for lc in lifecycles], dtype=np.float64),
            np.array([lc.died for lc in lifecycles], dtype=bool),
        )

    a_dur, a_obs = arrays(train)
    b_dur, b_obs = arrays(holdout)

    curve = kaplan_meier(a_dur, a_obs)
    assert curve.times.size > 1, "no event times, so no curve"
    assert curve.at(30.0) > curve.at(360.0), "survival must be non-increasing"
    assert 0.0 <= curve.at(360.0) <= 1.0

    # Two draws from the same process should not differ significantly.
    result = log_rank(a_dur, a_obs, b_dur, b_obs)
    assert result.p_value > 0.01, f"two samples of one process differ at p={result.p_value:.4f}"


def test_censoring_is_not_counted_as_survival(train: list[MandateLifecycle]) -> None:
    """The correction §34 exists for: a live mandate is censored, not immortal."""
    durations = np.array([lc.duration_days for lc in train], dtype=np.float64)
    observed = np.array([lc.died for lc in train], dtype=bool)

    honest = kaplan_meier(durations, observed)
    as_if_all_died = kaplan_meier(durations, np.ones_like(observed))
    assert honest.at(360.0) > as_if_all_died.at(360.0)


# ── Exit criterion: the LTV sequencer is measurably more conservative ───────


def _solve_with(*, w: int, dr: np.ndarray, hazards: np.ndarray) -> object:
    horizon, budget = harness.HORIZON_SLOTS, harness.ATTEMPT_BUDGET
    legal = np.zeros(horizon, dtype=np.bool_)
    legal[harness.PDN_LEAD_HOURS : harness.DUNNING_WINDOW_DAYS * 24] = True
    return solve(
        survival=harness.survival_from(hazards),
        legal=legal,
        cost=attempt_cost_matrix(amount_paise=AMOUNT, budget=budget, horizon_slots=horizon),
        amount_paise=AMOUNT,
        continuation_value_paise=w,
        dr=dr,
        health=health_multiplier(horizon),
        budget=budget,
        lead_slots=harness.PDN_LEAD_HOURS,
        p_recoverable=0.8,
    )


def test_the_ltv_objective_is_more_conservative_than_recovery_only(
    model: RevocationModel, train: list[MandateLifecycle]
) -> None:
    """The exit criterion, measured.

    Recovery-only prices the mandate at nothing worth protecting; the LTV
    objective prices it at `W` and charges `Δr·W` for the risk an attempt adds.
    It must therefore fire no more often, and stop sooner.
    """
    eligible = harness.recovery_population(flatten(train))
    hazards = harness.empirical_hazards(eligible[: len(eligible) // 3])
    horizon = harness.HORIZON_SLOTS
    features = _features(2, 60.0)

    w = continuation_value_paise(model, features, amount_paise=AMOUNT, collect_probability=0.44)
    dr = np.array([model.marginal_delta(features, days_ahead=t / 24.0) for t in range(horizon)])

    recovery_only = _solve_with(w=0, dr=np.zeros(horizon), hazards=hazards)
    ltv = _solve_with(w=w, dr=dr, hazards=hazards)

    budget = harness.ATTEMPT_BUDGET
    recovery_fires = int((recovery_only.action[budget] != -1).sum())  # type: ignore[attr-defined]
    ltv_fires = int((ltv.action[budget] != -1).sum())  # type: ignore[attr-defined]

    assert ltv_fires < recovery_fires, (
        f"LTV fired from {ltv_fires} states, recovery-only from {recovery_fires} — "
        "the LTV objective is not more conservative"
    )


# ── FINDING-P8-01: does the liquidity model now change the answer? ──────────


def test_the_liquidity_model_now_changes_the_decision(
    model: RevocationModel, train: list[MandateLifecycle]
) -> None:
    """FINDING-P8-01's resolution, asserted.

    Through Phases 8 and 9 the fitted hazard and an uninformative prior gave
    *identical* answers, because §23.1 charged for attempts but never for
    delay, so the latest legal slot always weakly dominated. With §22's
    time-dependent `Δr` (ADR-062) waiting costs something, and with `W` fitted
    per mandate state (ADR-063) attempting is worthwhile where the curve says
    money is likely. The two must now diverge.
    """
    eligible = harness.recovery_population(flatten(train))
    hazards = harness.empirical_hazards(eligible[: len(eligible) // 3])
    uninformative = np.full(harness.HORIZON_SLOTS, float(hazards.mean()))

    features = _features(2, 60.0)
    w = continuation_value_paise(model, features, amount_paise=AMOUNT, collect_probability=0.44)
    dr = np.array(
        [model.marginal_delta(features, days_ahead=t / 24.0) for t in range(harness.HORIZON_SLOTS)]
    )

    budget = harness.ATTEMPT_BUDGET
    with_model = _solve_with(w=w, dr=dr, hazards=hazards).best_slot(budget, 0)  # type: ignore[attr-defined]
    without = _solve_with(w=w, dr=dr, hazards=uninformative).best_slot(budget, 0)  # type: ignore[attr-defined]

    assert with_model != without, (
        "the fitted hazard and an uninformative prior still agree — FINDING-P8-01 has not resolved"
    )


def test_a_constant_delta_r_reproduces_the_old_degeneracy(
    model: RevocationModel, train: list[MandateLifecycle]
) -> None:
    """Pins *why* it resolved, so a regression is attributable.

    Holding Δr constant — ADR-037's original choice — restores the behaviour
    FINDING-P8-01 recorded: the curve stops mattering. That isolates the
    time-dependence as the cause rather than leaving it asserted.
    """
    eligible = harness.recovery_population(flatten(train))
    hazards = harness.empirical_hazards(eligible[: len(eligible) // 3])
    uninformative = np.full(harness.HORIZON_SLOTS, float(hazards.mean()))
    constant = np.full(harness.HORIZON_SLOTS, 0.04)
    w = continuation_value_paise(
        model, _features(2, 60.0), amount_paise=AMOUNT, collect_probability=0.44
    )

    budget = harness.ATTEMPT_BUDGET
    fitted_choice = _solve_with(w=w, dr=constant, hazards=hazards).best_slot(budget, 0)  # type: ignore[attr-defined]
    flat_choice = _solve_with(w=w, dr=constant, hazards=uninformative).best_slot(budget, 0)  # type: ignore[attr-defined]

    assert fitted_choice == flat_choice, (
        "a constant Δr no longer reproduces the degeneracy; the diagnosis needs revisiting"
    )
