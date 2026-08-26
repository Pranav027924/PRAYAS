"""Phase 4 exit criteria, against simulator ground truth (§21, §38).

Three claims, each measured rather than asserted:

1. V0 hazard beats a uniform prior by log-loss.
2. ECE below 0.05 on held-out data.
3. **The conditional `p(t|t_last)` matches empirical ground truth.**

The third is the one that matters. §21 calls the `t_last` conditioning "the
subtlety most implementations miss" and the modelling rules calls getting
it wrong "the most likely error in the whole project". Phase 3 built ground truth
precisely so this could be checked against reality instead of against itself.

Uses the simulator's in-memory objects rather than the database: the claim is
about the mathematics, and `sim_ground_truth` is owner-only by design (ADR-030).
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.inference.calibration import (
    ECE_ALERT_THRESHOLD,
    expected_calibration_error,
    log_loss,
)
from prayas.inference.hazard import (
    conditional_probability,
    marginal_probability,
    survival,
)
from prayas.sim.config import SALARIED_1ST, SimConfig
from prayas.sim.generate import SimulatedCycle, generate

HORIZON = 30
TENANT = "t_alpha"


def _funding_day(cycle: SimulatedCycle) -> int | None:
    """Day index on which the account was actually funded, from ground truth."""
    truth = cycle.truth
    if truth.true_funding_time is None:
        return None
    delta = truth.true_funding_time - cycle.due_at
    day = delta.days
    return day if 0 <= day < HORIZON else None


def _is_funded_by(cycle: SimulatedCycle, t: int) -> bool:
    """True if ground truth says the account was funded on or before slot t."""
    day = _funding_day(cycle)
    return day is not None and day <= t


def _empirical_hazards(cycles: list[SimulatedCycle], horizon: int = HORIZON) -> np.ndarray:
    """h(t) = funded at t / still unfunded entering t — the definition in §21."""
    at_risk = np.zeros(horizon, dtype=float)
    events = np.zeros(horizon, dtype=float)

    for cycle in cycles:
        day = _funding_day(cycle)
        last = day if day is not None else horizon
        for t in range(min(last + 1, horizon)):
            at_risk[t] += 1
        if day is not None:
            events[day] += 1

    with np.errstate(divide="ignore", invalid="ignore"):
        hazards = np.where(at_risk > 0, events / at_risk, 0.0)
    return np.clip(hazards, 1e-6, 1 - 1e-6)


def _split(
    cycles: list[SimulatedCycle],
) -> tuple[list[SimulatedCycle], list[SimulatedCycle]]:
    """Deterministic held-out split. Index-based, so it does not consume RNG."""
    midpoint = len(cycles) // 2
    return cycles[:midpoint], cycles[midpoint:]


@pytest.fixture(scope="module")
def population() -> list[SimulatedCycle]:
    return generate(SimConfig(), seed=4004, tenant_id=TENANT, cycles=6000)


# ── exit criterion 1: V0 beats a uniform prior by log-loss ─────────────────


def test_v0_hazard_beats_a_uniform_prior(population: list[SimulatedCycle]) -> None:
    """§21 — a calendar "encodes no payday information at all"."""
    train, held_out = _split(population)
    hazards = _empirical_hazards(train)

    # A uniform prior is the calendar: the same hazard in every slot.
    base_rate = float(np.mean([_funding_day(c) is not None for c in train]))
    uniform = np.full(HORIZON, np.clip(base_rate / HORIZON, 1e-6, 1 - 1e-6))

    labels: list[int] = []
    v0_probs: list[float] = []
    uniform_probs: list[float] = []

    for cycle in held_out:
        day = _funding_day(cycle)
        for t in range(HORIZON):
            labels.append(1 if day == t else 0)
            v0_probs.append(float(hazards[t]))
            uniform_probs.append(float(uniform[t]))

    v0_loss = log_loss(labels, v0_probs)
    uniform_loss = log_loss(labels, uniform_probs)

    assert v0_loss < uniform_loss, (
        f"V0 log-loss {v0_loss:.5f} did not beat uniform {uniform_loss:.5f}"
    )
    # Pin the margin: a regression that stays merely "better" must still fail.
    assert uniform_loss - v0_loss > 0.001, f"margin collapsed to {uniform_loss - v0_loss:.6f}"


def test_payday_concentration_is_what_creates_the_edge(population: list[SimulatedCycle]) -> None:
    """The artifact: a hazard curve that visibly peaks on payday.

    Salaried-1st customers are funded on day 0 of the cycle, so their hazard
    must spike there rather than spreading flat across the horizon.
    """
    salaried = generate(
        SimConfig(payday_mix={SALARIED_1ST: 1.0}, outage_lambda=0.0),
        seed=4005,
        tenant_id=TENANT,
        cycles=1500,
    )
    hazards = _empirical_hazards(salaried)

    assert hazards[0] > 0.5, f"payday hazard did not peak: h(0)={hazards[0]:.3f}"
    assert hazards[0] > hazards[5:].max() * 5, "the peak is not distinctive"


# ── exit criterion 2: ECE below 0.05 on held-out data ──────────────────────


def test_ece_is_below_the_threshold_on_held_out_data(population: list[SimulatedCycle]) -> None:
    train, held_out = _split(population)
    hazards = _empirical_hazards(train)

    labels: list[int] = []
    probs: list[float] = []
    for cycle in held_out:
        day = _funding_day(cycle)
        for t in range(HORIZON):
            labels.append(1 if day == t else 0)
            probs.append(float(hazards[t]))

    ece = expected_calibration_error(labels, probs)
    assert ece < ECE_ALERT_THRESHOLD, f"ECE {ece:.4f} exceeds {ECE_ALERT_THRESHOLD}"


def test_a_deliberately_miscalibrated_model_is_caught(population: list[SimulatedCycle]) -> None:
    """Guards the metric itself.

    Without this, `expected_calibration_error` could be returning something near
    zero for every input and the criterion above would pass vacuously.
    """
    _, held_out = _split(population)

    labels: list[int] = []
    inflated: list[float] = []
    for cycle in held_out[:500]:
        day = _funding_day(cycle)
        for t in range(HORIZON):
            labels.append(1 if day == t else 0)
            inflated.append(0.9)  # claim 90% every slot

    ece = expected_calibration_error(labels, inflated)
    assert ece > ECE_ALERT_THRESHOLD, "a wildly miscalibrated model was not detected"


# ── exit criterion 3: the conditional, against ground truth ────────────────


@pytest.mark.parametrize("t_last", [0, 1, 3, 6])
def test_conditional_matches_empirical_ground_truth(
    population: list[SimulatedCycle], t_last: int
) -> None:
    """p(t|t_last) against the observed frequency among survivors.

    Among cycles genuinely still unfunded after slot `t_last`, the fraction
    funded by slot `t` is directly computable from `true_funding_time`. That is
    the quantity `conditional_probability` claims to predict.
    """
    train, held_out = _split(population)
    hazards = _empirical_hazards(train)

    # The cohort the conditional is about: not funded on or before t_last.
    survivors = [c for c in held_out if not _is_funded_by(c, t_last)]
    assert len(survivors) > 200, f"cohort too small at t_last={t_last}"

    for t in (t_last + 1, t_last + 3, min(t_last + 10, HORIZON - 1)):
        if t >= HORIZON:
            continue
        observed = np.mean([1.0 if _is_funded_by(c, t) else 0.0 for c in survivors])
        predicted = conditional_probability(hazards, t, t_last)

        assert predicted == pytest.approx(observed, abs=0.06), (
            f"t_last={t_last} t={t}: predicted {predicted:.4f}, observed {observed:.4f}"
        )


def test_using_the_marginal_instead_would_be_measurably_wrong(
    population: list[SimulatedCycle],
) -> None:
    """The failure §21 warns about, demonstrated against ground truth.

    "Using the marginal probability instead of the conditional produces
    systematically wrong expected values and therefore systematically wrong
    stopping points." This asserts the marginal is not merely different but
    *further from reality* — otherwise the distinction would be academic.
    """
    train, held_out = _split(population)
    hazards = _empirical_hazards(train)

    t_last, t = 3, 10
    survivors = [c for c in held_out if not _is_funded_by(c, t_last)]
    observed = float(np.mean([1.0 if _is_funded_by(c, t) else 0.0 for c in survivors]))

    conditional_error = abs(conditional_probability(hazards, t, t_last) - observed)
    marginal_error = abs(marginal_probability(hazards, t) - observed)

    assert conditional_error < marginal_error, (
        f"the marginal was not worse: conditional off by {conditional_error:.4f}, "
        f"marginal off by {marginal_error:.4f}"
    )
    assert marginal_error > 0.05, (
        "the marginal is close enough to be harmless here — the test population "
        "is not discriminating and would not catch the real error"
    )


def test_survival_matches_empirical_survival(population: list[SimulatedCycle]) -> None:
    """S(t) is what the conditional renormalises by, so it is checked directly."""
    train, held_out = _split(population)
    hazards = _empirical_hazards(train)
    s = survival(hazards)

    for t in (0, 2, 5, 12, 25):
        observed = np.mean([0.0 if _is_funded_by(c, t) else 1.0 for c in held_out])
        assert s[t] == pytest.approx(observed, abs=0.06), (
            f"S({t}): predicted {s[t]:.4f}, observed {observed:.4f}"
        )
