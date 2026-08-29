"""The DP against brute force, and the conditional-hazard property (§23.2).

Two of Phase 5's exit criteria live here: agreement with brute-force
enumeration at `B <= 3, H <= 40`, and monotonicity in `B`, `A`, `W`.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from prayas.sequencer.dp import (
    STOP,
    Policy,
    solve,
    solve_reference,
    stopping_rationale,
)
from tests.dp_scenario import FloatArray, Scenario


def _scenario(
    rng: np.random.Generator, horizon: int, budget: int, *, legal_rate: float = 0.4
) -> Scenario:
    """A random but well-formed DP instance."""
    # Monotone non-increasing survival, strictly positive so the conditional
    # hazard is defined everywhere.
    decrements = rng.uniform(0.0, 0.08, size=horizon)
    survival = np.clip(np.cumprod(1.0 - decrements), 1e-6, 1.0).astype(np.float64)

    legal = rng.random(horizon) < legal_rate
    if not legal.any():
        legal[horizon // 2] = True

    amount = int(rng.integers(9_900, 499_900))
    cost = np.tile(rng.integers(200, 4_000, size=horizon).astype(np.int64), (budget + 1, 1))

    return {
        "survival": survival,
        "legal": legal.astype(np.bool_),
        "cost": cost,
        "amount_paise": amount,
        "continuation_value_paise": amount * 12,
        "dr": np.full(horizon, 0.04, dtype=np.float64),
        "health": np.ones(horizon, dtype=np.float64),
        "budget": budget,
        "lead_slots": int(rng.integers(0, 4)),
        "p_recoverable": float(rng.uniform(0.3, 1.0)),
    }


def _brute_force_value(scenario: Scenario, budget: int, t: int) -> float:
    """Expected value by unmemoised enumeration, written from §23.1's equation.

    Deliberately transcribed from the *objective*:

        E[value] = p(t)·[A + W] + (1 - p(t))·[V(b-1, t) - Δr(t)·W] - cost(t)

    rather than from the DP module, so agreement is evidence about the
    implementation rather than the implementation agreeing with itself.

    ADR-061: STOP is worth `W`, not zero — stopping keeps the mandate and
    collects nothing. So the base case and the STOP option are both `W`.
    """
    if budget == 0:
        return float(scenario["continuation_value_paise"])

    survival: FloatArray = scenario["survival"]
    legal = scenario["legal"]
    cost = scenario["cost"]
    dr: FloatArray = scenario["dr"]
    health: FloatArray = scenario["health"]
    amount = scenario["amount_paise"]
    w = scenario["continuation_value_paise"]
    lead = scenario["lead_slots"]
    p_rec = scenario["p_recoverable"]
    horizon = survival.shape[0]

    best = float(w)  # STOP: keep the mandate, collect nothing (ADR-061)
    for nxt in range(t + lead, horizon):
        if not legal[nxt]:
            continue
        p = (1.0 - survival[nxt] / survival[t]) * health[nxt] * p_rec
        continued = _brute_force_value(scenario, budget - 1, nxt)
        ev = p * (amount + w) + (1.0 - p) * (continued - dr[nxt] * w) - cost[budget][nxt]
        best = max(best, float(ev))
    return best


# ── Exit criterion: DP matches brute force for B <= 3, H <= 40 ───────────────


@pytest.mark.parametrize("budget", [1, 2, 3])
@pytest.mark.parametrize("horizon", [10, 25, 40])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_dp_matches_brute_force_enumeration(budget: int, horizon: int, seed: int) -> None:
    """§23.2 solved exactly, checked against enumeration of the objective."""
    rng = np.random.default_rng(seed)
    scenario = _scenario(rng, horizon, budget)
    policy = solve(**scenario)

    for t in range(horizon):
        expected = _brute_force_value(scenario, budget, t)
        actual = policy.expected_value_paise(budget, t)
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-6), (
            f"B={budget} H={horizon} seed={seed} t={t}: DP {actual} != brute force {expected}"
        )


@pytest.mark.parametrize("budget", [1, 2, 3])
@pytest.mark.parametrize("seed", [11, 12])
def test_dp_chooses_the_same_slot_as_brute_force(budget: int, seed: int) -> None:
    """Agreement on value is necessary; agreement on the *action* is the point."""
    rng = np.random.default_rng(seed)
    horizon = 30
    scenario = _scenario(rng, horizon, budget)
    policy = solve(**scenario)

    survival = scenario["survival"]
    cost = scenario["cost"]
    dr = scenario["dr"]
    health = scenario["health"]
    amount = scenario["amount_paise"]
    w = scenario["continuation_value_paise"]
    p_rec = scenario["p_recoverable"]

    for t in range(horizon):
        chosen = policy.best_slot(budget, t)
        if chosen is None:
            continue
        p = (1.0 - survival[chosen] / survival[t]) * health[chosen] * p_rec
        expected_ev = (
            p * (amount + w)
            + (1.0 - p) * (_brute_force_value(scenario, budget - 1, chosen) - dr[chosen] * w)
            - cost[budget][chosen]
        )
        assert float(expected_ev) == pytest.approx(
            policy.expected_value_paise(budget, t), rel=1e-9, abs=1e-6
        ), f"chosen slot {chosen} at t={t} does not realise the reported value"


def test_vectorised_solve_matches_the_literal_spec_transcription() -> None:
    """`solve` is an optimisation of `solve_reference`; they must not diverge."""
    for seed in range(6):
        rng = np.random.default_rng(100 + seed)
        scenario = _scenario(rng, 60, 4)
        fast = solve(**scenario)
        ref = solve_reference(**scenario)

        np.testing.assert_allclose(fast.value, ref.value, rtol=1e-9, atol=1e-6)
        np.testing.assert_array_equal(fast.action, ref.action)


# ── Exit criterion: value monotone non-decreasing in B, A, W ────────────────


@given(
    seed=st.integers(min_value=0, max_value=500),
    horizon=st.integers(min_value=8, max_value=30),
)
@settings(max_examples=60, deadline=None)
def test_value_is_monotone_non_decreasing_in_budget(seed: int, horizon: int) -> None:
    """§40.3 — more attempts remaining can never be worth less."""
    rng = np.random.default_rng(seed)
    scenario = _scenario(rng, horizon, 4)
    policy = solve(**scenario)

    for b in range(1, 5):
        assert np.all(policy.value[b] >= policy.value[b - 1] - 1e-6), (
            f"value decreased going from budget {b - 1} to {b}"
        )


@given(seed=st.integers(min_value=0, max_value=500))
@settings(max_examples=40, deadline=None)
def test_value_is_monotone_non_decreasing_in_amount(seed: int) -> None:
    """More money at stake can never reduce the optimal value."""
    rng = np.random.default_rng(seed)
    base = _scenario(rng, 24, 3)

    richer: Scenario = dict(base)  # type: ignore[assignment]
    richer["amount_paise"] = base["amount_paise"] * 2

    lo = solve(**base)
    hi = solve(**richer)
    assert np.all(hi.value >= lo.value - 1e-6)


@given(seed=st.integers(min_value=0, max_value=500))
@settings(max_examples=40, deadline=None)
def test_total_position_value_is_monotone_in_continuation_value(seed: int) -> None:
    """ADR-040 — `V + W` is non-decreasing in `W`, unconditionally.

    §40.3 states the property as "value monotone non-decreasing in W", but for
    §23.2's `V` alone that is **false**: `dV/dW = p - (1-p)·Δr`, which is
    negative whenever `p < Δr/(1+Δr)` ~ 3.85% at ADR-037's Δr = 0.04. The cause
    is that §23.2 gives STOP a value of 0 while §23.1 pays `A + W` on success,
    so surviving is counted as a gain although the mandate was already held.

    The total position — option value plus the mandate itself — is monotone:
    `d(V+W)/dW = 1 + p - (1-p)·Δr > 0`. That is the economically meaningful
    quantity and the one asserted here.
    """
    rng = np.random.default_rng(seed)
    base = _scenario(rng, 24, 3)

    valuable: Scenario = dict(base)  # type: ignore[assignment]
    valuable["continuation_value_paise"] = base["continuation_value_paise"] * 2

    lo = solve(**base)
    hi = solve(**valuable)

    # ADR-061 makes this hold on `V` itself; the `V + W` restatement ADR-040
    # needed is no longer required, and both are asserted so a regression to
    # the old baseline would fail here rather than silently.
    assert np.all(hi.value >= lo.value - 1e-6)
    lo_total = lo.value + base["continuation_value_paise"]
    hi_total = hi.value + valuable["continuation_value_paise"]
    assert np.all(hi_total >= lo_total - 1e-6)


def test_value_is_now_monotone_in_w_at_every_hazard() -> None:
    """ADR-061 resolves ADR-040: `V` itself is monotone in `W`, unconditionally.

    ADR-040 recorded that §23.2's STOP value of 0 made `dV/dW = p - (1-p)·Δr`,
    negative below `p ~ 3.85%`, and pinned the failure at exactly the regime
    where it bit. With STOP worth `W`, both branches gain at least `W`, so the
    derivative is bounded below by a positive quantity and the anomaly is gone.

    This test replaces the regression that used to pin the defect — the defect
    is fixed, so pinning it would now assert the wrong thing.
    """
    horizon = 6
    survival = np.array([1.0, 0.97, 0.9409, 0.9127, 0.8853, 0.8588], dtype=np.float64)
    legal = np.array([False, True, False, False, False, False], dtype=np.bool_)
    amount = 10_000_000

    def value_at(w: int) -> float:
        policy = solve(
            survival=survival,
            legal=legal,
            cost=np.zeros((2, horizon), dtype=np.int64),
            amount_paise=amount,
            continuation_value_paise=w,
            dr=np.full(horizon, 0.04, dtype=np.float64),
            health=np.ones(horizon, dtype=np.float64),
            budget=1,
            lead_slots=1,
            p_recoverable=1.0,
        )
        return policy.expected_value_paise(1, 0)

    values = [value_at(w) for w in (1_200_000, 2_400_000, 4_800_000, 9_600_000)]
    assert all(b >= a for a, b in itertools.pairwise(values)), (
        f"V still decreases in W under ADR-061: {values}"
    )


# ── Exit criterion: stopping rationale carries real rupee figures ────────────


def _stopping_scenario() -> Scenario:
    """Costs far above anything recoverable, so STOP dominates everywhere."""
    horizon = 24
    survival = np.clip(np.cumprod(np.full(horizon, 0.999)), 1e-6, 1.0).astype(np.float64)
    return {
        "survival": survival,
        "legal": np.ones(horizon, dtype=np.bool_),
        "cost": np.full((3, horizon), 5_000_000, dtype=np.int64),
        "amount_paise": 49_900,
        "continuation_value_paise": 49_900 * 12,
        "dr": np.full(horizon, 0.04, dtype=np.float64),
        "health": np.ones(horizon, dtype=np.float64),
        "budget": 2,
        "lead_slots": 1,
        "p_recoverable": 0.8,
    }


def test_stopping_rationale_states_actual_rupee_figures() -> None:
    """§23.3 — the rationale must be readable by a merchant and an auditor."""
    scenario = _stopping_scenario()
    policy = solve(**scenario)
    assert policy.should_stop(2, 0), "scenario failed to produce a stopping state"

    rationale = stopping_rationale(
        policy=policy,
        budget_remaining=2,
        last_failure_slot=0,
        survival=scenario["survival"],
        legal=scenario["legal"],
        cost=scenario["cost"],
        amount_paise=scenario["amount_paise"],
        continuation_value_paise=scenario["continuation_value_paise"],
        dr=scenario["dr"],
        health=scenario["health"],
        lead_slots=scenario["lead_slots"],
        p_recoverable=scenario["p_recoverable"],
    )

    assert "stopped:" in rationale
    assert "attempts left 2" in rationale
    # The continuation value, in rupees, must actually appear — not a zero
    # placeholder, which is how a templated rationale passes vacuously.
    assert "₹5,988.00" in rationale, rationale
    assert "₹50,000.00" in rationale, rationale
    assert "revocation hazard 0.040" in rationale
    assert "0.00" not in rationale.split("continuation value")[0].split("EV ")[1][:6]


def test_rationale_refuses_a_non_stopping_state() -> None:
    """Asking why we stopped when we did not is a caller bug."""
    rng = np.random.default_rng(7)
    scenario = _scenario(rng, 20, 2, legal_rate=1.0)
    scenario["cost"] = np.zeros((3, 20), dtype=np.int64)
    policy = solve(**scenario)
    assert not policy.should_stop(2, 0)

    with pytest.raises(ValueError, match="not a stopping state"):
        stopping_rationale(
            policy=policy,
            budget_remaining=2,
            last_failure_slot=0,
            survival=scenario["survival"],
            legal=scenario["legal"],
            cost=scenario["cost"],
            amount_paise=scenario["amount_paise"],
            continuation_value_paise=scenario["continuation_value_paise"],
            dr=scenario["dr"],
            health=scenario["health"],
            lead_slots=scenario["lead_slots"],
            p_recoverable=scenario["p_recoverable"],
        )


def test_zero_budget_layer_holds_the_mandate_value() -> None:
    """`V(0, ·) = W` and no action — the recursion's base case under ADR-061.

    A cycle with no attempts left has not lost the mandate; it simply cannot
    try again this cycle. Valuing that state at zero was ADR-040's finding.
    """
    rng = np.random.default_rng(3)
    scenario = _scenario(rng, 15, 2)
    policy: Policy = solve(**scenario)

    assert np.all(policy.value[0] == float(scenario["continuation_value_paise"]))
    assert np.all(policy.action[0] == STOP)
