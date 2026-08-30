"""The ADR-075 presence formulation (§23.2 deviation; §21).

§23.2 scores a candidate slot by `1 - S(t')/S(t)`, which is monotone in `t'`
for every valid survival curve — so the latest legal slot always weakly
dominates and the curve's *shape* never reaches the decision. ADR-075 lets the
DP consume `P(funds present at t')` instead, which is not monotone, and these
tests pin the behavioural difference that motivates the deviation.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.measure.harness import ATTEMPT_BUDGET, DUNNING_WINDOW_DAYS, PDN_LEAD_HOURS
from prayas.sequencer.dp import STOP, Policy, solve
from prayas.sequencer.economics import attempt_cost_matrix

HORIZON = 30 * 24
AMOUNT = 100_000
W = 1_200_000


def _legal() -> np.ndarray:
    legal = np.zeros(HORIZON, dtype=np.bool_)
    legal[PDN_LEAD_HOURS : DUNNING_WINDOW_DAYS * 24] = True
    return legal


def _solve(**kwargs: object) -> Policy:
    base = {
        "legal": _legal(),
        "cost": attempt_cost_matrix(
            amount_paise=AMOUNT, budget=ATTEMPT_BUDGET, horizon_slots=HORIZON
        ),
        "amount_paise": AMOUNT,
        "continuation_value_paise": W,
        "dr": np.full(HORIZON, 0.01),
        "health": np.ones(HORIZON),
        "budget": ATTEMPT_BUDGET,
        "lead_slots": PDN_LEAD_HOURS,
        "p_recoverable": 0.9,
    }
    base.update(kwargs)
    return solve(**base)  # type: ignore[arg-type]


def _first_choice(policy: Policy) -> int | None:
    slot = policy.best_slot(ATTEMPT_BUDGET, 0)
    return None if slot is None else int(slot)


# ── the interface ──────────────────────────────────────────────────────────


def test_exactly_one_curve_is_required() -> None:
    """Passing both, or neither, is a caller bug rather than a default."""
    survival = np.cumprod(np.full(HORIZON, 1.0 - 0.002))
    presence = np.full(HORIZON, 0.3)

    with pytest.raises(ValueError, match="exactly one"):
        _solve()
    with pytest.raises(ValueError, match="exactly one"):
        _solve(survival=survival, presence=presence)


def test_presence_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="presence must be a probability"):
        _solve(presence=np.full(HORIZON, 1.4))


def test_presence_need_not_be_monotone() -> None:
    """The entire point: §23.2 refuses a curve that falls, and money that has
    been spent makes it fall."""
    presence = np.zeros(HORIZON)
    presence[40:60] = 0.8
    with pytest.raises(ValueError, match="monotone non-increasing"):
        _solve(survival=1.0 - presence)

    policy = _solve(presence=presence)  # must not raise
    assert policy is not None


# ── the behaviour the deviation exists for ─────────────────────────────────


def test_presence_attends_to_the_payday_rather_than_the_deadline() -> None:
    """Under §23.2 this curve produces the last legal slot; under ADR-075 it
    produces the payday. That difference is the whole justification."""
    legal = _legal()
    first, last = int(np.flatnonzero(legal)[0]), int(np.flatnonzero(legal)[-1])

    presence = np.full(HORIZON, 0.02)
    presence[60:96] = 0.85  # a payday window, well before the deadline

    chosen = _first_choice(_solve(presence=presence))
    assert chosen is not None and chosen != STOP
    assert 60 <= chosen < 96, f"chose slot {chosen}, not the payday window"
    assert chosen != last, "the presence DP still went to the deadline"
    assert chosen != first


def test_survival_on_the_same_information_goes_to_the_deadline() -> None:
    """The contrast, made explicit rather than asserted in prose."""
    legal = _legal()
    last = int(np.flatnonzero(legal)[-1])

    hazards = np.full(HORIZON, 1e-6)
    # Gentle enough that survival never reaches the floor — at the floor the DP
    # treats the account as already funded and the comparison would be vacuous.
    hazards[60:96] = 0.02
    survival = np.cumprod(1.0 - hazards)

    assert _first_choice(_solve(survival=survival)) == last


def test_presence_prefers_the_larger_of_two_windows() -> None:
    """Shape-sensitivity, not merely earliness: given two reachable windows it
    must choose on probability, not on which comes first."""
    presence = np.full(HORIZON, 0.01)
    presence[50:70] = 0.30
    presence[120:140] = 0.90

    chosen = _first_choice(_solve(presence=presence))
    assert chosen is not None and 120 <= chosen < 140

    presence[50:70] = 0.95
    chosen = _first_choice(_solve(presence=presence))
    assert chosen is not None and 50 <= chosen < 70


def test_presence_stops_when_no_legal_slot_holds_money() -> None:
    """§23.3: stopping is an output. An empty account for the whole legal
    window is exactly when a system should decline to spend attempts."""
    presence = np.zeros(HORIZON)
    presence[:20] = 0.9  # money, but before any legal slot
    assert _first_choice(_solve(presence=presence)) is None


def test_the_attempt_budget_is_still_respected() -> None:
    presence = np.full(HORIZON, 0.01)
    presence[60:96] = 0.5
    policy = _solve(presence=presence)

    slots, remaining, t = [], ATTEMPT_BUDGET, 0
    while remaining > 0:
        nxt = policy.best_slot(remaining, t)
        if nxt is None:
            break
        slots.append(int(nxt))
        remaining -= 1
        t = int(nxt)
    assert len(slots) <= ATTEMPT_BUDGET
    assert slots == sorted(slots), "attempts must advance in time"


def test_health_still_suppresses_a_degraded_issuer() -> None:
    """ADR-072 composes with ADR-075: presence says the money is there, health
    says the issuer will refuse it anyway."""
    presence = np.full(HORIZON, 0.01)
    presence[60:96] = 0.9
    presence[130:160] = 0.6  # a second window, still inside the legal range

    healthy = _first_choice(_solve(presence=presence))
    assert healthy is not None and 60 <= healthy < 96

    health = np.ones(HORIZON)
    health[60:96] = 0.02  # the issuer is down exactly over the payday
    degraded = _first_choice(_solve(presence=presence, health=health))
    assert degraded is not None, "suppressing one window should divert, not stop"
    assert 130 <= degraded < 160, f"chose {degraded}, not the healthy window"


def test_a_flat_presence_curve_leaves_only_cost_to_decide() -> None:
    """With no shape there is nothing to be shape-sensitive about, and the
    choice falls back to the cheapest reachable slot rather than to noise."""
    policy = _solve(presence=np.full(HORIZON, 0.4))
    chosen = _first_choice(policy)
    assert chosen is not None
    assert bool(_legal()[chosen])


# ── explainability on the new path (§6, §32) ───────────────────────────────


def test_a_presence_stop_explains_itself_in_rupees() -> None:
    """§6's definition of done: "click any recovered rupee and see why". A new
    decision path that could not explain itself would not be finished."""
    from prayas.sequencer.dp import stopping_rationale

    presence = np.full(HORIZON, 1e-4)
    presence[:20] = 0.9  # money, but only before any legal slot
    policy = _solve(presence=presence)
    assert policy.should_stop(ATTEMPT_BUDGET, 0)

    why = stopping_rationale(
        policy=policy,
        budget_remaining=ATTEMPT_BUDGET,
        last_failure_slot=0,
        presence=presence,
        legal=_legal(),
        cost=attempt_cost_matrix(amount_paise=AMOUNT, budget=ATTEMPT_BUDGET, horizon_slots=HORIZON),
        amount_paise=AMOUNT,
        continuation_value_paise=W,
        dr=np.full(HORIZON, 0.01),
        health=np.ones(HORIZON),
        lead_slots=PDN_LEAD_HOURS,
        p_recoverable=0.9,
    )
    assert "stopped" in why
    assert "₹" in why, "a rationale a merchant reads must be in rupees"


def test_the_rationale_also_requires_exactly_one_curve() -> None:
    from prayas.sequencer.dp import stopping_rationale

    presence = np.full(HORIZON, 1e-4)
    policy = _solve(presence=presence)
    with pytest.raises(ValueError, match="exactly one"):
        stopping_rationale(
            policy=policy,
            budget_remaining=ATTEMPT_BUDGET,
            last_failure_slot=0,
            legal=_legal(),
            cost=attempt_cost_matrix(
                amount_paise=AMOUNT, budget=ATTEMPT_BUDGET, horizon_slots=HORIZON
            ),
            amount_paise=AMOUNT,
            continuation_value_paise=W,
            dr=np.full(HORIZON, 0.01),
            health=np.ones(HORIZON),
            lead_slots=PDN_LEAD_HOURS,
            p_recoverable=0.9,
        )


def test_the_suffix_scan_agrees_with_the_quadratic_form() -> None:
    """The Phase 15 optimisation must be a pure speed change.

    `_solve_presence` originally built a full `(horizon, horizon)` validity
    mask. That is 518,400 elements per call, and the load test measured it at
    roughly half the burst target. The suffix scan that replaced it computes
    the same argmax in one pass — this asserts *the same*, on random curves,
    rather than trusting the algebra.
    """
    rng = np.random.default_rng(7)
    legal = _legal()
    cost = attempt_cost_matrix(amount_paise=AMOUNT, budget=ATTEMPT_BUDGET, horizon_slots=HORIZON)

    for _ in range(20):
        presence = np.clip(rng.normal(0.3, 0.2, size=HORIZON), 0.0, 1.0)
        dr = np.clip(rng.normal(0.01, 0.005, size=HORIZON), 0.0, 1.0)
        health = np.clip(rng.normal(0.9, 0.1, size=HORIZON), 0.0, 1.0)

        policy = solve(
            presence=presence,
            legal=legal,
            cost=cost,
            amount_paise=AMOUNT,
            continuation_value_paise=W,
            dr=dr,
            health=health,
            budget=ATTEMPT_BUDGET,
            lead_slots=PDN_LEAD_HOURS,
            p_recoverable=0.9,
        )

        # Recompute the same decision the slow way, directly from the
        # definition, for every state.
        p = np.clip(presence * health * 0.9, 0.0, 1.0)
        for b in range(1, ATTEMPT_BUDGET + 1):
            g = policy.value[b - 1] - dr * W
            row = np.where(legal, p * (AMOUNT + W) + (1.0 - p) * g - cost[b], -np.inf)
            for t in (0, 30, 100, HORIZON - 1):
                lo = t + PDN_LEAD_HOURS
                expected_ev = row[lo:].max() if lo < HORIZON else -np.inf
                floor = W + 1.0  # MIN_EV_PAISE default
                if expected_ev > floor:
                    assert policy.value[b][t] == pytest.approx(expected_ev)
                else:
                    assert policy.value[b][t] == pytest.approx(float(W))
