"""The LTV dynamic program (Master Spec §23.2, §23.3).

State is `(attempts remaining b, last-failure time t)`. `t` is in the state
because **it conditions the hazard** — the DP consumes `p(t'|t) = 1 - S(t')/S(t)`,
the *conditional*, never the marginal. Using the marginal here is the single
most likely error in the project (the modelling rules), so the conditional
is computed in exactly one place and asserted against brute force.

Stopping is `policy[b][t] == -1`, which happens precisely when no legal
continuation is worth more than keeping the mandate and collecting nothing
(§23.3). There is no attempt-count rule anywhere in this module.

**STOP is worth `W`, not zero (ADR-061).** §23.2 writes `max{0, ...}` while
§23.1 pays `A + W` on success — incompatible baselines, which ADR-040 showed
make §40.3's W-monotonicity false below `p ~ 3.85%`. Stopping does not destroy
the mandate; it keeps it and collects nothing this cycle. With `V(0, .) = W`
the attempt condition becomes "expected collection exceeds expected revocation
damage", and monotonicity in `W` holds unconditionally.

**Money stays integer paise.** Amounts are never cast to float; expected value
is a probability-weighted statistic, not a money amount, and numpy promotes the
integer amounts during that arithmetic without the amounts themselves becoming
floats.

**Two implementations.** `solve_reference` is a literal transcription of
§23.2, kept because it is the spec. `solve` is an algebraically identical
vectorisation, used in production. §23.2's loop issues roughly `B·H` separate
numpy calls (~2,900 at B=4, H=720) where per-call overhead dominates and the
15 ms exit criterion is at risk. Tests assert the two agree exactly.

The vectorisation rests on one rearrangement. Writing `q = health·p_recoverable`,
`G = V[b-1] - Δr·W`, `D = (A + W) - G`:

    ev(t, t') = p(t'|t)·D + G - cost
              = q·(1 - S[t']/S[t])·D + G - cost
              = (q·D + G - cost)  -  (q·D·S[t']) / S[t]
              =        U[t']      -     Z[t']  ·  (1/S[t])

`U` and `Z` depend only on `t'`, so a whole layer becomes one outer product
instead of `H` separate reductions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from prayas.sequencer.economics import MIN_EV_PAISE

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]

#: Below this, the account is treated as already funded and the conditional
#: hazard is undefined (§23.2 uses the same guard).
SURVIVAL_FLOOR: Final = 1e-9

#: `policy[b][t] == -1` is STOP (§23.3).
STOP: Final = -1


@dataclass(frozen=True, slots=True)
class Policy:
    """The solved DP: value and optimal action for every state."""

    value: FloatArray  # V[b][t], expected paise
    action: IntArray  # policy[b][t]: slot index, or STOP

    def should_stop(self, budget_remaining: int, last_failure_slot: int) -> bool:
        """§23.3 — stopping is the absence of a positive-value continuation."""
        return bool(self.action[budget_remaining][last_failure_slot] == STOP)

    def best_slot(self, budget_remaining: int, last_failure_slot: int) -> int | None:
        slot = int(self.action[budget_remaining][last_failure_slot])
        return None if slot == STOP else slot

    def expected_value_paise(self, budget_remaining: int, last_failure_slot: int) -> float:
        return float(self.value[budget_remaining][last_failure_slot])


def _validate(
    survival: FloatArray,
    legal: BoolArray,
    cost: IntArray,
    dr: FloatArray,
    health: FloatArray,
    budget: int,
    lead_slots: int,
    p_recoverable: float,
    *,
    monotone: bool = True,
) -> int:
    horizon = int(survival.shape[0])
    if horizon == 0:
        raise ValueError("survival curve is empty")
    for name, arr in (("legal", legal), ("dr", dr), ("health", health)):
        if arr.shape[0] != horizon:
            raise ValueError(f"{name} has length {arr.shape[0]}, expected {horizon}")
    if cost.shape != (budget + 1, horizon):
        raise ValueError(f"cost has shape {cost.shape}, expected {(budget + 1, horizon)}")
    if budget < 0:
        raise ValueError(f"budget must be non-negative, got {budget}")
    if lead_slots < 0:
        raise ValueError(f"lead_slots must be non-negative, got {lead_slots}")
    if not 0.0 <= p_recoverable <= 1.0:
        raise ValueError(f"p_recoverable must be a probability, got {p_recoverable}")
    if monotone and np.any(np.diff(survival) > 1e-12):
        raise ValueError("survival must be monotone non-increasing (§23.2)")
    if not monotone and (np.any(survival < 0.0) or np.any(survival > 1.0)):
        raise ValueError("presence must be a probability in [0, 1] (ADR-075)")
    return horizon


def solve(
    *,
    survival: FloatArray | None = None,
    presence: FloatArray | None = None,
    legal: BoolArray,
    cost: IntArray,
    amount_paise: int,
    continuation_value_paise: int,
    dr: FloatArray,
    health: FloatArray,
    budget: int,
    lead_slots: int,
    p_recoverable: float,
    min_ev_paise: float = MIN_EV_PAISE,
) -> Policy:
    """Solve §23.2's recursion. Vectorised per layer; see the module docstring.

    `cost` is `cost[b][t]` (ADR-038), indexed by attempts *remaining*, matching
    the DP's own state.

    **Exactly one of `survival` or `presence` (ADR-075).**

    `survival` is §23.2 as written: funding is absorbing, and the success
    probability of attempting at `t'` after failing at `t` is
    `1 - S(t')/S(t)`. That expression is monotone in `t'` for every valid `S`,
    so the latest legal slot always weakly dominates and the curve's *shape*
    never enters the decision.

    `presence` is `P(funds present at t')` — the ADR-075 deviation. §21 states
    that "money arrives and is spent", and under those dynamics the absorbing
    ordering is measurably reversed: firing at the first legal slot beats the
    last (34.68% against 31.86%), and the ceiling is 76.04% against the 31.86%
    patience achieves. Presence is not monotone, which is precisely why it can
    express "attend to *this* customer's payday" — and precisely why §23.2's
    formulation could not.
    """
    if (survival is None) == (presence is None):
        raise ValueError("pass exactly one of survival or presence (ADR-075)")

    if presence is not None:
        return _solve_presence(
            presence=presence,
            legal=legal,
            cost=cost,
            amount_paise=amount_paise,
            continuation_value_paise=continuation_value_paise,
            dr=dr,
            health=health,
            budget=budget,
            lead_slots=lead_slots,
            p_recoverable=p_recoverable,
            min_ev_paise=min_ev_paise,
        )

    assert survival is not None
    horizon = _validate(survival, legal, cost, dr, health, budget, lead_slots, p_recoverable)

    # ADR-061: the base case is the mandate's own value, not zero. A cycle
    # with no attempts left still holds a live mandate.
    value = np.full((budget + 1, horizon), float(continuation_value_paise), dtype=np.float64)
    action = np.full((budget + 1, horizon), STOP, dtype=np.int64)

    # Integer paise; numpy promotes during the probability arithmetic below.
    total = amount_paise + continuation_value_paise
    q = health * p_recoverable

    slots = np.arange(horizon)
    # valid[t, t'] — t' is legal and respects the notice lead from t.
    reachable = slots[None, :] >= (slots[:, None] + lead_slots)
    valid = reachable & legal[None, :]

    funded = survival <= SURVIVAL_FLOOR
    inv_survival = np.where(funded, 0.0, 1.0 / np.maximum(survival, SURVIVAL_FLOOR))

    for b in range(1, budget + 1):
        g = value[b - 1] - dr * continuation_value_paise
        d = total - g

        u = q * d + g - cost[b]
        z = q * d * survival

        # ev[t, t'] = U[t'] - Z[t'] / S[t]
        ev = u[None, :] - z[None, :] * inv_survival[:, None]
        ev = np.where(valid, ev, -np.inf)
        # A state whose account is already funded has no decision to make.
        ev[funded, :] = -np.inf

        best = np.argmax(ev, axis=1)
        best_ev = ev[slots, best]

        # ADR-061: continue only if it beats *keeping the mandate*, not zero.
        floor = continuation_value_paise + min_ev_paise
        take = best_ev > floor
        value[b] = np.where(take, best_ev, float(continuation_value_paise))
        action[b] = np.where(take, best, STOP)

    return Policy(value=value, action=action)


def _solve_presence(
    *,
    presence: FloatArray,
    legal: BoolArray,
    cost: IntArray,
    amount_paise: int,
    continuation_value_paise: int,
    dr: FloatArray,
    health: FloatArray,
    budget: int,
    lead_slots: int,
    p_recoverable: float,
    min_ev_paise: float,
) -> Policy:
    """§23.2's recursion over `P(funds present at t')` instead of survival.

    **The whole matrix collapses to a row.** Under §23.2 the success
    probability depends on both the failure slot and the candidate, so `ev` is
    two-dimensional. Presence depends only on the candidate, so every failure
    slot scores the candidates identically and the reachability mask is the
    only thing that differs between rows. That is not a shortcut — it is what
    "the observation at `t` no longer renormalises the future" means.

    The conditioning that §23.2 carried is genuinely dropped: a failure at `t`
    said the account was empty then, and under absorbing funding that shifted
    the entire remaining curve. Under non-absorbing dynamics it says much less,
    because the next payday is a fresh event. Slots close to `t` are the
    exception — see `correlation_slots` in the harness, which damps them.
    """
    horizon = _validate(
        presence, legal, cost, dr, health, budget, lead_slots, p_recoverable, monotone=False
    )

    value = np.full((budget + 1, horizon), float(continuation_value_paise), dtype=np.float64)
    action = np.full((budget + 1, horizon), STOP, dtype=np.int64)
    total = amount_paise + continuation_value_paise
    p = np.clip(presence * health * p_recoverable, 0.0, 1.0)

    slots = np.arange(horizon)
    valid = (slots[None, :] >= (slots[:, None] + lead_slots)) & legal[None, :]

    for b in range(1, budget + 1):
        g = value[b - 1] - dr * continuation_value_paise
        row = p * total + (1.0 - p) * g - cost[b]

        ev = np.where(valid, row[None, :], -np.inf)
        best = np.argmax(ev, axis=1)
        best_ev = ev[slots, best]

        # ADR-061: continue only if it beats *keeping the mandate*, not zero.
        floor = continuation_value_paise + min_ev_paise
        take = best_ev > floor
        value[b] = np.where(take, best_ev, float(continuation_value_paise))
        action[b] = np.where(take, best, STOP)

    return Policy(value=value, action=action)


def solve_reference(
    *,
    survival: FloatArray,
    legal: BoolArray,
    cost: IntArray,
    amount_paise: int,
    continuation_value_paise: int,
    dr: FloatArray,
    health: FloatArray,
    budget: int,
    lead_slots: int,
    p_recoverable: float,
    min_ev_paise: float = MIN_EV_PAISE,
) -> Policy:
    """Literal transcription of §23.2's loop. Kept because it is the spec.

    Not used in production — `solve` is. This exists so the vectorisation has
    something independent to be checked against, and so a reader can compare
    the shipped code to the specification without reconstructing the algebra.
    """
    horizon = _validate(survival, legal, cost, dr, health, budget, lead_slots, p_recoverable)

    value = np.full((budget + 1, horizon), float(continuation_value_paise), dtype=np.float64)
    action = np.full((budget + 1, horizon), STOP, dtype=np.int64)
    total = amount_paise + continuation_value_paise

    for b in range(1, budget + 1):
        for t in range(horizon - 1, -1, -1):
            if survival[t] <= SURVIVAL_FLOOR:
                continue
            lo = t + lead_slots
            if lo >= horizon:
                continue
            candidates = np.arange(lo, horizon)[legal[lo:horizon]]
            if candidates.size == 0:
                continue

            p = (1.0 - survival[candidates] / survival[t]) * health[candidates] * p_recoverable
            ev = (
                p * total
                + (1.0 - p) * (value[b - 1][candidates] - dr[candidates] * continuation_value_paise)
                - cost[b][candidates]
            )

            j = int(np.argmax(ev))
            if ev[j] > continuation_value_paise + min_ev_paise:  # ADR-061
                value[b][t] = ev[j]
                action[b][t] = int(candidates[j])

    return Policy(value=value, action=action)


def stopping_rationale(
    *,
    policy: Policy,
    budget_remaining: int,
    last_failure_slot: int,
    survival: FloatArray | None = None,
    presence: FloatArray | None = None,
    legal: BoolArray,
    cost: IntArray,
    amount_paise: int,
    continuation_value_paise: int,
    dr: FloatArray,
    health: FloatArray,
    lead_slots: int,
    p_recoverable: float,
) -> str:
    """§23.3 — why the loop stopped, in rupees.

    "Compare `if retries > 3: stop`. This version tells a merchant, an auditor,
    and a regulator *why*, in rupees."

    Reports the best *rejected* option, because "we stopped" is only meaningful
    alongside what stopping was chosen over. Rupee formatting appears here and
    only here — this string is read by humans, not by the money path.
    """
    if not policy.should_stop(budget_remaining, last_failure_slot):
        raise ValueError("not a stopping state — policy has a chosen action")
    if (survival is None) == (presence is None):
        raise ValueError("pass exactly one of survival or presence (ADR-075)")

    curve = survival if survival is not None else presence
    assert curve is not None
    horizon = curve.shape[0]
    lo = last_failure_slot + lead_slots
    w_rupees = continuation_value_paise / 100

    # On the presence path there is no "already funded" state to short-circuit:
    # money can be present now and gone later, so the only reason to stop early
    # is that nothing legal remains.
    exhausted = survival is not None and survival[last_failure_slot] <= SURVIVAL_FLOOR
    if lo >= horizon or exhausted:
        return (
            f"stopped: no legal slot remains within the horizon; "
            f"attempts left {budget_remaining}; "
            f"continuation value ₹{w_rupees:,.2f}"
        )

    candidates = np.arange(lo, horizon)[legal[lo:horizon]]
    if candidates.size == 0:
        return (
            f"stopped: every remaining slot is outside the legal execution window; "
            f"attempts left {budget_remaining}; "
            f"continuation value ₹{w_rupees:,.2f}"
        )

    total = amount_paise + continuation_value_paise
    if survival is not None:
        p = (
            (1.0 - survival[candidates] / survival[last_failure_slot])
            * health[candidates]
            * p_recoverable
        )
    else:
        assert presence is not None
        p = np.clip(presence[candidates] * health[candidates] * p_recoverable, 0.0, 1.0)
    ev = (
        p * total
        + (1.0 - p)
        * (
            policy.value[budget_remaining - 1][candidates]
            - dr[candidates] * continuation_value_paise
        )
        - cost[budget_remaining][candidates]
    )

    j = int(np.argmax(ev))
    best_slot = int(candidates[j])

    return (
        f"stopped: best remaining EV ₹{ev[j] / 100:,.2f} at slot {best_slot} "
        f"is below the ₹{cost[budget_remaining][best_slot] / 100:,.2f} cost of firing; "
        f"attempts left {budget_remaining}; "
        f"revocation hazard {dr[best_slot]:.3f} against "
        f"continuation value ₹{w_rupees:,.2f}"
    )
