"""Incremental estimators, CUPED and the SRM check (Master Spec §34).

ADR-047: hand-rolled, no new dependency. Both comparisons here are two-arm, so
the only distribution needed is chi-square with **one** degree of freedom,
whose survival function is exactly `erfc(sqrt(x/2))` — an identity, not an
approximation. §40.2 already sets the precedent of validating statistics
against reference implementations rather than importing them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]

#: §34's `incremental()` uses 1.96, i.e. a 95% normal interval.
Z_95: Final = 1.96

#: §34: "propensities clipped at 0.01 and the clipping rate reported, since an
#: unreported clip is a silent bias."
PROPENSITY_FLOOR: Final = 0.01

#: An SRM below this blocks the report (§34: "must block the report rather
#: than footnote it").
SRM_ALPHA: Final = 0.001


class EstimatorError(ValueError):
    """The inputs do not support the estimate being asked for."""


@dataclass(frozen=True, slots=True)
class ArmSummary:
    """One arm's outcome counts."""

    n: int
    recovered: int

    def __post_init__(self) -> None:
        if self.n < 0 or self.recovered < 0:
            raise EstimatorError("counts must be non-negative")
        if self.recovered > self.n:
            raise EstimatorError(f"recovered {self.recovered} exceeds n {self.n}")

    @property
    def rate(self) -> float:
        return self.recovered / self.n if self.n else 0.0


@dataclass(frozen=True, slots=True)
class Interval:
    point: float
    low: float
    high: float

    @property
    def excludes_zero(self) -> bool:
        """§6's target: "Positive, CI excluding zero"."""
        return self.low > 0.0 or self.high < 0.0

    @property
    def contains_zero(self) -> bool:
        return self.low <= 0.0 <= self.high


def incremental(treat: ArmSummary, ctrl: ArmSummary) -> Interval:
    """§34's primary estimator, transcribed.

    lift = p1 - p0
    se   = sqrt(p1(1-p1)/n1 + p0(1-p0)/n0)
    return lift, (lift - 1.96 se, lift + 1.96 se)
    """
    if treat.n == 0 or ctrl.n == 0:
        raise EstimatorError("both arms need at least one unit")

    p1, p0 = treat.rate, ctrl.rate
    lift = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / treat.n + p0 * (1 - p0) / ctrl.n)
    return Interval(lift, lift - Z_95 * se, lift + Z_95 * se)


def cuped(y: FloatArray, x: FloatArray) -> FloatArray:
    """§34's variance reduction, transcribed.

        theta = cov(y, x) / var(x)
        return y - theta * (x - x.mean())

    The adjusted outcome has the same expectation as `y` — subtracting a
    mean-zero term cannot move the point estimate — while its variance falls
    with the strength of the covariate. That property is asserted in the tests:
    a "variance reduction" that also shifted the estimate would be a bias.

    `x` must be measured **before** assignment (ADR-049), or the adjustment
    absorbs part of the treatment effect it exists to sharpen.
    """
    if y.shape != x.shape:
        raise EstimatorError(f"y and x differ in shape: {y.shape} vs {x.shape}")
    if y.size < 2:
        raise EstimatorError("CUPED needs at least two observations")

    var_x = float(np.var(x))
    if var_x <= 0.0:
        # A constant covariate carries no information; theta is undefined and
        # the honest adjustment is none at all.
        return y.astype(np.float64, copy=True)

    theta = float(np.cov(y, x)[0, 1] / var_x)
    return (y - theta * (x - float(np.mean(x)))).astype(np.float64)


def cuped_theta(y: FloatArray, x: FloatArray) -> float:
    """The fitted coefficient, exposed so a report can state it."""
    var_x = float(np.var(x))
    return 0.0 if var_x <= 0.0 else float(np.cov(y, x)[0, 1] / var_x)


def chi2_sf_1df(statistic: float) -> float:
    """P(X > statistic) for chi-square with one degree of freedom.

    Exact: for 1 df the survival function is `erfc(sqrt(x/2))`. This is why
    ADR-047 could decline scipy — every test in this phase is two-arm, hence
    one degree of freedom.
    """
    if statistic < 0.0:
        raise EstimatorError(f"chi-square statistic cannot be negative: {statistic}")
    return math.erfc(math.sqrt(statistic / 2.0))


@dataclass(frozen=True, slots=True)
class SrmResult:
    """§34's sample ratio mismatch check."""

    observed_control: int
    observed_treatment: int
    expected_control: float
    expected_treatment: float
    statistic: float
    p_value: float

    @property
    def passed(self) -> bool:
        """False means the experiment is invalid and the report must not ship."""
        return self.p_value >= SRM_ALPHA


def srm_check(observed_control: int, observed_treatment: int, control_pct: float) -> SrmResult:
    """chi-square on arm sizes against the intended split.

    §34: "An SRM failure invalidates the experiment and must block the report
    rather than footnote it." A mismatch means assignment did not do what the
    pre-registration said, so every downstream number is about a different
    experiment than the one that was registered.
    """
    total = observed_control + observed_treatment
    if total == 0:
        raise EstimatorError("no units to check")
    if not 0.0 < control_pct < 1.0:
        raise EstimatorError(f"control_pct must be strictly between 0 and 1, got {control_pct}")

    expected_control = total * control_pct
    expected_treatment = total * (1.0 - control_pct)

    statistic = (observed_control - expected_control) ** 2 / expected_control + (
        observed_treatment - expected_treatment
    ) ** 2 / expected_treatment

    return SrmResult(
        observed_control=observed_control,
        observed_treatment=observed_treatment,
        expected_control=expected_control,
        expected_treatment=expected_treatment,
        statistic=statistic,
        p_value=chi2_sf_1df(statistic),
    )


@dataclass(frozen=True, slots=True)
class IpsResult:
    estimate: float
    clipped_fraction: float


def ips(
    outcomes: FloatArray, propensities: FloatArray, *, floor: float = PROPENSITY_FLOOR
) -> IpsResult:
    """Inverse-propensity-score estimate, with the clipping rate reported.

    §34: "propensities clipped at 0.01 and the clipping rate reported, since an
    unreported clip is a silent bias." The clipped fraction is returned rather
    than logged, so a caller cannot present the estimate without it.

    ADR-048: propensities here are arm-assignment probabilities, so this is
    valid at the arm level. Action-level OPE needs an exploring policy.
    """
    if outcomes.shape != propensities.shape:
        raise EstimatorError("outcomes and propensities differ in shape")
    if outcomes.size == 0:
        raise EstimatorError("no observations")
    if np.any(propensities <= 0.0) or np.any(propensities > 1.0):
        raise EstimatorError("propensities must lie in (0, 1]")

    clipped = np.maximum(propensities, floor)
    clipped_fraction = float(np.mean(propensities < floor))
    return IpsResult(
        estimate=float(np.mean(outcomes / clipped)),
        clipped_fraction=clipped_fraction,
    )
