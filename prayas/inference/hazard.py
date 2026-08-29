"""V0 liquidity hazard (Master Spec §21; ADR-032, ADR-033).

**Question:** for this customer, what is `P(account funded >= A)` as a function
of time?

    h(t) = P(funded >= A at slot t | not funded before t)
    S(t) = prod_{u <= t} (1 - h(u))
    p(t | t_last) = 1 - S(t)/S(t_last)

**The conditional is the whole point, and the easiest thing to get wrong.**
§21: "The conditioning on `t_last` is the subtlety most implementations miss. A
failure at hour `t_last` is evidence the account was unfunded then, which shifts
the entire remaining curve. Using the marginal probability instead of the
conditional produces systematically wrong expected values and therefore
systematically wrong stopping points."

the modelling rules are blunter: "Getting this wrong is the most likely
error in the whole project." So `conditional_probability` is written to make the
distinction structural — it takes `t_last` as a required argument, and there is
no function here that returns a marginal for a post-failure decision.

V0 is a per-segment empirical lookup, shrunk toward the segment prior. §21: it
"ships in a day and already beats a calendar, because a calendar encodes no
payday information at all." V1's gradient-boosted version is Phase 11.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

#: §21 — shrinkage strength. `w = n/(n+kappa)`, kappa ~ 5 cycles.
DEFAULT_KAPPA: Final = 5.0

#: §21 — "a 30-day horizon cap", one of the mitigations for non-absorbing funding.
DEFAULT_HORIZON_DAYS: Final = 30

#: Hazards are clamped away from 0 and 1. A hazard of exactly 1 makes S(t) zero
#: and every later conditional a division by zero; exactly 0 makes a slot
#: contribute nothing and can never be learned away.
_MIN_HAZARD: Final = 1e-6
_MAX_HAZARD: Final = 1.0 - 1e-6


@dataclass(frozen=True, slots=True)
class SegmentKey:
    """The `segment_priors` key from §36."""

    mcc: str
    ticket_band: int
    rail: str
    day_of_month: int
    hour_band: int


def shrink(
    observed_hazard: float, n_observed: int, segment_hazard: float, *, kappa: float = DEFAULT_KAPPA
) -> float:
    """Hierarchical partial pooling (§21).

        h_hat = w * h_observed + (1 - w) * h_segment,   w = n / (n + kappa)

    With no observations the estimate is the segment prior exactly, which is
    what makes cold start behave rather than divide by zero.
    """
    if n_observed < 0:
        raise ValueError("n_observed must be non-negative")
    if kappa <= 0:
        raise ValueError("kappa must be positive")

    weight = n_observed / (n_observed + kappa)
    return clamp_hazard(weight * observed_hazard + (1.0 - weight) * segment_hazard)


def clamp_hazard(h: float) -> float:
    """Keep hazards strictly inside (0, 1). See `_MIN_HAZARD`."""
    return float(min(max(h, _MIN_HAZARD), _MAX_HAZARD))


def survival(hazards: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """S(t) = prod_{u <= t} (1 - h(u)), inclusive of slot t.

    Returns an array the same length as `hazards`, where `S[i]` is the
    probability of *still* being unfunded after slot `i` has passed.
    """
    h = np.asarray(hazards, dtype=float)
    if h.ndim != 1:
        raise ValueError("hazards must be one-dimensional")
    if h.size == 0:
        raise ValueError("hazards must be non-empty")
    if np.any(h < 0.0) or np.any(h > 1.0):
        raise ValueError("hazards must be probabilities")

    return np.cumprod(1.0 - np.clip(h, _MIN_HAZARD, _MAX_HAZARD))


#: §21's leak parameter, per day. Zero keeps the strictly-absorbing behaviour
#: every closed phase was measured under; a positive value is the mitigation
#: §21 names for its own stated limitation.
DEFAULT_LEAK_PER_DAY: Final = 0.0


def leaky_survival(
    hazards: npt.ArrayLike,
    *,
    leak_per_day: float = DEFAULT_LEAK_PER_DAY,
    hours_per_slot: float = 1.0,
) -> npt.NDArray[np.float64]:
    """S(t) with §21's leak — "money arrives and is spent".

    §21 names three mitigations for funding not being absorbing: "a leak
    parameter decaying survival over long gaps, a 30-day horizon cap, and
    validation against simulator configurations with explicitly non-absorbing
    dynamics". This is the first.

    **The leak decays the credit given to distant funding, not the curve's
    monotonicity.** §23.2 requires `S` to be non-increasing, and
    `p(t | t_last) = 1 - S(t)/S(t_last)` is monotone in `t` for *any* such `S` —
    so no transformation here can make a later slot look worse than an earlier
    one outright. What it can do is shrink the *gain* from waiting, and shrink
    it differently for different customers: money forecast to arrive on day two
    keeps almost all its credit, money forecast for day six keeps much less.
    Against a rising `Δr` that is what decides where a mandate stops waiting.

    **This is an approximation, and the direction of its error is known.** The
    true quantity is `P(funds present at t)`, which sums over arrivals `u <= t`
    weighted by how long the money survives the gap `t - u`. That is not
    monotone — it falls once money leaves — so §23.2's interface cannot carry
    it, and `dp._validate` rejects it outright. What is implemented instead
    discounts hazard by *elapsed time from the due date*, not by the gap between
    arrival and attempt. It therefore penalises a late-funding customer even
    when the attempt would land immediately after their money arrives, which is
    precisely the case a liquidity model exists to catch. Expect it to push
    attempts earlier than the true leak would, and expect that error to grow
    with `leak_per_day`.

    With `leak_per_day = 0` this is exactly `survival`, so the default changes
    nothing that has already been measured.
    """
    h = np.asarray(hazards, dtype=float)
    if leak_per_day < 0:
        raise ValueError("leak_per_day must be non-negative")
    if leak_per_day == 0.0:
        return survival(h)

    days = np.arange(h.size, dtype=np.float64) * hours_per_slot / 24.0
    return survival(np.clip(h, _MIN_HAZARD, _MAX_HAZARD) * np.exp(-leak_per_day * days))


def marginal_probability(hazards: npt.ArrayLike, t: int) -> float:
    """P(funded by end of slot t), unconditioned.

    Provided for comparison and testing only. **Do not use this for a decision
    after an observed failure** — that is precisely the error §21 warns about.
    `conditional_probability` is the one the sequencer consumes.
    """
    s = survival(hazards)
    _check_slot(t, s.size, "t")
    return float(1.0 - s[t])


def conditional_probability(hazards: npt.ArrayLike, t: int, t_last: int) -> float:
    """p(t | t_last) = 1 - S(t)/S(t_last) — §21's conditional.

    `t_last` is the slot of the observed failure. Everything up to and including
    it is known to have produced no funding, so the remaining curve is
    renormalised by `S(t_last)` rather than by 1.

    Requires `t > t_last`: asking about a slot at or before the observed failure
    is not a forecast, it is a contradiction of something already observed.
    """
    s = survival(hazards)
    _check_slot(t, s.size, "t")
    _check_slot(t_last, s.size, "t_last")

    if t <= t_last:
        raise ValueError(
            f"t must be strictly after t_last (t={t}, t_last={t_last}): "
            f"a failure at t_last already established no funding by then"
        )

    denominator = s[t_last]
    if denominator <= 0.0:
        # Survival has collapsed to zero, so the customer was certain to be
        # funded by t_last — which contradicts the observed failure. Return
        # zero rather than dividing: no probability mass remains to allocate.
        return 0.0

    return float(1.0 - s[t] / denominator)


def conditional_curve(hazards: npt.ArrayLike, t_last: int) -> npt.NDArray[np.float64]:
    """The whole remaining curve after a failure at `t_last`.

    Slots at or before `t_last` are zero: they are not forecasts.
    """
    s = survival(hazards)
    _check_slot(t_last, s.size, "t_last")

    curve = np.zeros_like(s)
    denominator = s[t_last]
    if denominator > 0.0:
        curve[t_last + 1 :] = 1.0 - s[t_last + 1 :] / denominator
    return curve


def _check_slot(value: int, size: int, name: str) -> None:
    if not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer slot index")
    if not 0 <= value < size:
        raise ValueError(f"{name}={value} out of range for {size} slots")


class HazardTable:
    """Per-segment empirical lookup with shrinkage toward the segment prior.

    §27's `n_obs >= 50` k-anonymity floor means a sparse segment cannot be
    stored at all, so lookups fall back explicitly to the global prior rather
    than silently returning nothing.
    """

    def __init__(
        self,
        segment_hazards: dict[SegmentKey, float],
        *,
        global_prior: float,
        kappa: float = DEFAULT_KAPPA,
    ) -> None:
        if not 0.0 <= global_prior <= 1.0:
            raise ValueError("global_prior must be a probability")
        self._segments = dict(segment_hazards)
        self._global_prior = clamp_hazard(global_prior)
        self._kappa = kappa

    @property
    def global_prior(self) -> float:
        return self._global_prior

    def segment_hazard(self, key: SegmentKey) -> float:
        """The segment estimate, or the global prior when the cell is unpopulated."""
        return clamp_hazard(self._segments.get(key, self._global_prior))

    def estimate(
        self, key: SegmentKey, *, observed_hazard: float | None = None, n_observed: int = 0
    ) -> float:
        """Shrunk hazard for one slot.

        With no customer history this is the segment prior; with plenty it
        approaches the customer's own rate. `w = n/(n+kappa)` makes that
        transition smooth rather than a threshold nobody can defend.
        """
        segment = self.segment_hazard(key)
        if observed_hazard is None or n_observed == 0:
            return segment
        return shrink(observed_hazard, n_observed, segment, kappa=self._kappa)

    def curve(
        self,
        keys: list[SegmentKey],
        *,
        observed: dict[SegmentKey, tuple[float, int]] | None = None,
    ) -> npt.NDArray[np.float64]:
        """Hazards for an ordered sequence of slots — the input to `survival`."""
        observed = observed or {}
        values = []
        for key in keys:
            obs = observed.get(key)
            if obs is None:
                values.append(self.estimate(key))
            else:
                hazard, count = obs
                values.append(self.estimate(key, observed_hazard=hazard, n_observed=count))
        return np.asarray(values, dtype=float)


def uniform_hazards(rate: float, slots: int) -> npt.NDArray[np.float64]:
    """A flat hazard — the uniform prior the V0 model must beat (exit criterion).

    This is what a calendar encodes: no payday information at all (§21).
    """
    if slots <= 0:
        raise ValueError("slots must be positive")
    return np.full(slots, clamp_hazard(rate), dtype=float)
