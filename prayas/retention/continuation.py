"""Continuation value `W` from the fitted revocation model (§23.1; ADR-063).

    W = Σ_{k=1..K} δ^k · P(alive at cycle k) · A_k · P(collect | alive)

"`W` is computed once per decision and cached."

This replaces ADR-036's placeholder of `12 * A`, which was derived from
Appendix C's δ and K under an *assumed* survival decay because §22's model did
not exist. Now it does, so `P(alive at cycle k)` is read from it rather than
guessed — which matters, because W is the price the sequencer puts on the
mandate and therefore sets how readily it declines to attempt.

The placeholder was materially too generous: it assumed a mandate survives long
enough to bill most of a 24-cycle horizon, whereas the fitted hazard has a
failing mandate dying far sooner. Over-valuing `W` makes the DP refuse
attempts it should make, which is the opposite error to the one §23.1 is
guarding against and just as costly.
"""

from __future__ import annotations

from typing import Final

from prayas.retention.revocation import RevocationFeatures, RevocationModel

#: Appendix C: `sequencer.discount_factor: 0.98`, `ltv_horizon_cycles: 24`.
DISCOUNT_FACTOR: Final = 0.98
LTV_HORIZON_CYCLES: Final = 24


def continuation_value_paise(
    model: RevocationModel,
    features: RevocationFeatures,
    *,
    amount_paise: int,
    collect_probability: float,
    horizon_cycles: int = LTV_HORIZON_CYCLES,
    discount: float = DISCOUNT_FACTOR,
) -> int:
    """§23.1's `W`, in integer paise.

    `P(alive at k)` compounds the fitted monthly hazard forward. A mandate
    already carrying failures is worth less precisely because it is likelier to
    die before billing again — which is the asymmetry that makes "the asset
    being managed is the mandate, not the cycle" operational rather than a
    slogan.

    Survival is evaluated under the mandate's *current* state. A mandate that
    recovers will do better than this; one that keeps failing, worse. Holding
    state fixed is the conservative reading at the moment of decision.
    """
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int - money is integer paise")
    if amount_paise < 0:
        raise ValueError(f"amount_paise must be non-negative, got {amount_paise}")
    if not 0.0 <= collect_probability <= 1.0:
        raise ValueError(f"collect_probability must be a probability, got {collect_probability}")
    if horizon_cycles < 0:
        raise ValueError(f"horizon_cycles must be non-negative, got {horizon_cycles}")
    if not 0.0 < discount <= 1.0:
        raise ValueError(f"discount must be in (0, 1], got {discount}")

    monthly_survival = 1.0 - model.monthly_hazard(features)
    total = 0.0
    alive = 1.0
    for k in range(1, horizon_cycles + 1):
        alive *= monthly_survival
        total += (discount**k) * alive * amount_paise * collect_probability

    return round(total)


def value_multiple(
    model: RevocationModel,
    features: RevocationFeatures,
    *,
    collect_probability: float,
) -> float:
    """`W / A` — how many cycles' worth the mandate is currently valued at.

    Exposed because the multiple is the number a reviewer can sanity-check
    against ADR-036's placeholder of 12, and because it makes the effect of a
    mandate's failure history immediately legible.
    """
    reference = 1_000_000
    return (
        continuation_value_paise(
            model, features, amount_paise=reference, collect_probability=collect_probability
        )
        / reference
    )
