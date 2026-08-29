"""Cost model and the placeholder terms the DP consumes (ADR-036/037/038/039).

Governing spec: Master Spec §23.1, Appendix C. Playbook Phase 5.

Three of the DP's inputs describe models that do not exist yet. Each is a
constant here behind an explicit seam, and each is recorded as an ADR rather
than buried as a magic number:

* `W`      — mandate continuation value. §22's revocation model is Phase 10.
* `Δr`     — marginal revocation hazard per attempt. **Wired in Phase 11**
             (ADR-072) to §22's fitted model; falls back to ADR-037's constant.
* `health` — issuer health forecast. **Wired in Phase 11** (ADR-072) to §20's
             nowcast; falls back to ADR-039's neutral constant.

**Cost is two-dimensional** (`cost[b][t]`), which deviates from §23.2's
`cost[t]`. The Phase 5 build list requires "convex fraud risk", and convexity
lives in attempt count, not clock time — repetition against one mandate is what
resembles card testing. See ADR-038.

Money is integer paise everywhere. Every returned cost is an int.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import numpy as np
from numpy.typing import NDArray

from prayas.inference.nowcast import IssuerNowcast
from prayas.retention.revocation import RevocationFeatures, RevocationModel

# ── Appendix C coefficients ─────────────────────────────────────────────────

#: `sequencer.beta_fraud: 0.4` — convexity coefficient on repeated attempts.
BETA_FRAUD: Final = 0.4
#: `sequencer.lambda_annoyance: 1.0` — policy simulator slider.
LAMBDA_ANNOYANCE: Final = 1.0
#: `sequencer.discount_factor: 0.98` (δ) and `ltv_horizon_cycles: 24` (K).
DISCOUNT_FACTOR: Final = 0.98
LTV_HORIZON_CYCLES: Final = 24
#: `sequencer.min_ev_paise: 0` — the threshold below which STOP dominates.
MIN_EV_PAISE: Final = 0

# ── placeholders, each a marked upgrade point ───────────────────────────────

#: ADR-036. `W = 12 * A`, derived from Appendix C's δ and K under a constant
#: amount, modest per-cycle survival decay and ~0.9 collection rate.
#: **Phase 10 replaces this with §22's revocation model.**
W_MULTIPLE_OF_AMOUNT: Final = 12

#: ADR-037. Matches Phase 3's `SimConfig.revocation_beta`, so the DP optimises
#: against the same revocation process the labelled data exhibits.
#: **Phase 10 replaces this with §22's hazard.**
DELTA_R_PER_ATTEMPT: Final = 0.04

#: ADR-039. The value when the nowcast has no opinion, and the value after a
#: predicted recovery.
HEALTH_NEUTRAL: Final = 1.0

#: ADR-072. Success multiplier while an issuer is believed down. Not zero: an
#: outage raises the failure rate, it does not close the shutter, and a zero
#: would make the DP treat those slots as unreachable rather than poor.
OUTAGE_HEALTH: Final = 0.15

#: Flat per-attempt processing cost, paise. UPI carries no MDR, but an attempt
#: is not free — reconciliation and support load are real.
BASE_FEE_PAISE: Final = 300  # ₹3

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


def continuation_value_paise(amount_paise: int) -> int:
    """`W` — expected value of the mandate surviving (§23.1). ADR-036.

    Proportional to the cycle amount, because a ₹199 and a ₹4,999 subscription
    do not share a continuation value. Phase 10 makes this a model output.
    """
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int — money is integer paise")
    if amount_paise < 0:
        raise ValueError(f"amount_paise must be non-negative, got {amount_paise}")
    return amount_paise * W_MULTIPLE_OF_AMOUNT


def revocation_delta(
    horizon_slots: int,
    *,
    model: RevocationModel | None = None,
    features: RevocationFeatures | None = None,
    hours_per_slot: float = 1.0,
) -> FloatArray:
    """`Δr[t]` — marginal revocation hazard from attempting at slot `t`. ADR-072.

    With a fitted §22 model this is the risk actually accrued by *waiting* until
    `t`, which rises with the delay. Without one it falls back to ADR-037's
    constant, so existing callers are unchanged.

    **The time-dependence is what makes the sequencer's problem non-trivial.**
    Constant `Δr` plus absorbing funding means waiting costs nothing, and the
    optimal policy is "attempt at the last legal slot" for every cycle
    regardless of its liquidity curve — which is exactly what FINDING-P11-01
    measured, and why a better hazard model changed no decisions at all.
    """
    if model is None or features is None:
        return np.full(horizon_slots, DELTA_R_PER_ATTEMPT, dtype=np.float64)

    days = np.arange(horizon_slots, dtype=np.float64) * hours_per_slot / 24.0
    return np.asarray(
        [model.marginal_delta(features, days_ahead=float(d)) for d in days],
        dtype=np.float64,
    )


def health_multiplier(
    horizon_slots: int,
    *,
    nowcast: IssuerNowcast | None = None,
    issuer: str | None = None,
    at: datetime | None = None,
    hours_per_slot: float = 1.0,
    outage_health: float = OUTAGE_HEALTH,
) -> FloatArray:
    """`health[t]` — issuer health forecast, ADR-072 (was ADR-039's constant).

    The DP multiplies the success probability by this, so it answers "if the
    money is there, will the issuer honour the debit at `t`?" — a different
    question from liquidity, and the one §20 answers with "wait for recovery
    ETA".

    A degraded issuer suppresses health until its estimated recovery and then
    returns to neutral. Suppressed rather than zeroed: an outage is a raised
    failure rate, not a closed shutter, and a zero would make the DP treat those
    slots as unreachable rather than merely poor.

    Neutral whenever the nowcast has no opinion. Silence must not suppress an
    attempt any more than it should license one.
    """
    if nowcast is None or issuer is None or at is None:
        return np.full(horizon_slots, HEALTH_NEUTRAL, dtype=np.float64)

    state = nowcast.state(issuer, at)
    if not state.is_degraded or state.recovery_eta is None:
        return np.full(horizon_slots, HEALTH_NEUTRAL, dtype=np.float64)

    recovered_after = (state.recovery_eta - at).total_seconds() / 3600.0 / hours_per_slot
    hours = np.arange(horizon_slots, dtype=np.float64)
    return np.where(hours < recovered_after, outage_health, HEALTH_NEUTRAL)


def attempt_cost_matrix(
    *,
    amount_paise: int,
    budget: int,
    horizon_slots: int,
    base_fee_paise: int = BASE_FEE_PAISE,
    beta_fraud: float = BETA_FRAUD,
    lambda_annoyance: float = LAMBDA_ANNOYANCE,
) -> IntArray:
    """`cost[b][t]` in paise (ADR-038).

    Indexed by **attempts remaining** `b`, matching the DP's own state, so the
    caller never has to convert. With budget `B`, having `b` remaining means
    `k = B - b` attempts already spent.

        cost(b, t) = base_fee
                   + beta_fraud   · k² · (amount / 100)   ← convex fraud risk
                   + lambda_annoyance · k  · (amount / 100)   ← annoyance

    Fraud and annoyance scale with the amount at stake: hammering a ₹4,999
    mandate draws more scrutiny and more irritation than a ₹199 one. Both are
    expressed in hundredths of the amount so the coefficients stay the
    order-one sliders Appendix C describes.

    Constant in `t` today. The dimension is present because the DP indexes it,
    and because time-varying annoyance is a natural Phase 9 extension once the
    notification optimiser knows a customer's attention pattern.
    """
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int — money is integer paise")
    if budget < 0:
        raise ValueError(f"budget must be non-negative, got {budget}")
    if horizon_slots <= 0:
        raise ValueError(f"horizon_slots must be positive, got {horizon_slots}")

    unit = amount_paise / 100.0
    cost = np.zeros((budget + 1, horizon_slots), dtype=np.int64)

    for b in range(budget + 1):
        spent = budget - b
        fraud = beta_fraud * (spent**2) * unit
        annoyance = lambda_annoyance * spent * unit
        cost[b, :] = round(base_fee_paise + fraud + annoyance)

    return cost
