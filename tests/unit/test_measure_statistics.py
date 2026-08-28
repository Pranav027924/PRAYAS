"""Hand-rolled statistics against published reference values (ADR-047).

ADR-047 declined `lifelines` and `scipy`, so the burden is to show these agree
with established implementations rather than merely with themselves. §40.2 sets
the same expectation for the Wilson bound and CUSUM.

The survival reference is Freireich et al. (1963), 6-mercaptopurine versus
placebo in leukemia remission — the canonical worked example for Kaplan-Meier
and the log-rank test, reproduced in essentially every survival-analysis text.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from prayas.measure.estimators import (
    ArmSummary,
    EstimatorError,
    chi2_sf_1df,
    cuped,
    cuped_theta,
    incremental,
    ips,
    srm_check,
)
from prayas.measure.survival import kaplan_meier, log_rank

# ── Freireich et al. (1963) ─────────────────────────────────────────────────

MP_DURATIONS = np.array(
    [6, 6, 6, 6, 7, 9, 10, 10, 11, 13, 16, 17, 19, 20, 22, 23, 25, 32, 32, 34, 35],
    dtype=np.float64,
)
MP_OBSERVED = np.array([1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0], dtype=bool)
PLACEBO_DURATIONS = np.array(
    [1, 1, 2, 2, 3, 4, 4, 5, 5, 8, 8, 8, 8, 11, 11, 12, 12, 15, 17, 22, 23],
    dtype=np.float64,
)
PLACEBO_OBSERVED = np.ones(21, dtype=bool)


@pytest.mark.parametrize(
    ("t", "published"),
    [(6, 0.857), (7, 0.807), (10, 0.753), (13, 0.690), (16, 0.627), (22, 0.538), (23, 0.448)],
)
def test_kaplan_meier_matches_published_freireich_values(t: int, published: float) -> None:
    """Every published S(t) for the 6-MP arm, to three decimals."""
    km = kaplan_meier(MP_DURATIONS, MP_OBSERVED)
    assert km.at(t) == pytest.approx(published, abs=0.001)


def test_kaplan_meier_median_matches_the_published_placebo_median() -> None:
    assert kaplan_meier(PLACEBO_DURATIONS, PLACEBO_OBSERVED).median == 8.0


def test_log_rank_matches_the_published_freireich_statistic() -> None:
    """chi2 = 16.79, O_A = 9, E_A = 19.25 — the figures the texts report."""
    result = log_rank(MP_DURATIONS, MP_OBSERVED, PLACEBO_DURATIONS, PLACEBO_OBSERVED)

    assert result.statistic == pytest.approx(16.79, abs=0.01)
    assert result.observed_a == 9
    assert result.expected_a == pytest.approx(19.25, abs=0.01)
    assert result.p_value < 0.0001
    assert result.significant


def test_censoring_is_not_treated_as_survival() -> None:
    """The correction §34 exists for.

    Two cohorts with identical event times, differing only in whether the
    remaining units are censored early or observed to fail, must not produce the
    same curve — a raw rate would conflate them.
    """
    events_only = kaplan_meier(np.array([1.0, 2.0, 3.0, 4.0]), np.array([True, True, True, True]))
    with_censoring = kaplan_meier(
        np.array([1.0, 2.0, 3.0, 4.0]), np.array([True, True, False, False])
    )
    assert events_only.at(4.0) < with_censoring.at(4.0)
    assert events_only.at(4.0) == pytest.approx(0.0)
    assert with_censoring.at(4.0) == pytest.approx(0.5)


def test_a_cohort_with_no_events_survives_throughout() -> None:
    km = kaplan_meier(np.array([5.0, 9.0, 12.0]), np.array([False, False, False]))
    assert km.at(0.0) == 1.0
    assert km.at(100.0) == 1.0
    assert km.median is None


def test_identical_arms_produce_no_log_rank_signal() -> None:
    """An A/A on survival must not manufacture a difference."""
    durations = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    observed = np.array([True, True, True, False, True])
    result = log_rank(durations, observed, durations.copy(), observed.copy())
    assert result.statistic == pytest.approx(0.0, abs=1e-9)
    assert result.p_value == pytest.approx(1.0, abs=1e-9)
    assert not result.significant


# ── chi-square, one degree of freedom ───────────────────────────────────────


@pytest.mark.parametrize(
    ("statistic", "published_p"),
    [(2.706, 0.10), (3.841, 0.05), (5.024, 0.025), (6.635, 0.01), (10.828, 0.001)],
)
def test_chi2_1df_matches_published_critical_values(statistic: float, published_p: float) -> None:
    """Standard chi-square table, 1 df. `erfc(sqrt(x/2))` is exact here."""
    assert chi2_sf_1df(statistic) == pytest.approx(published_p, abs=0.0005)


def test_chi2_sf_is_monotone_and_bounded() -> None:
    values = [chi2_sf_1df(x) for x in (0.0, 0.5, 1.0, 4.0, 16.0, 64.0)]
    assert values[0] == pytest.approx(1.0)
    assert all(b <= a for a, b in itertools.pairwise(values))
    assert all(0.0 <= v <= 1.0 for v in values)


def test_a_negative_statistic_is_rejected() -> None:
    with pytest.raises(EstimatorError, match="cannot be negative"):
        chi2_sf_1df(-1.0)


# ── §34's primary estimator ─────────────────────────────────────────────────


def test_incremental_matches_a_hand_computed_interval() -> None:
    """Worked by hand from §34's formula, not read back from the code."""
    treat = ArmSummary(n=1000, recovered=300)
    ctrl = ArmSummary(n=1000, recovered=250)

    p1, p0 = 0.30, 0.25
    se = math.sqrt(p1 * (1 - p1) / 1000 + p0 * (1 - p0) / 1000)

    result = incremental(treat, ctrl)
    assert result.point == pytest.approx(0.05)
    assert result.low == pytest.approx(0.05 - 1.96 * se)
    assert result.high == pytest.approx(0.05 + 1.96 * se)
    assert result.excludes_zero


def test_identical_arms_give_an_interval_containing_zero() -> None:
    same = ArmSummary(n=500, recovered=125)
    result = incremental(same, ArmSummary(n=500, recovered=125))
    assert result.point == 0.0
    assert result.contains_zero
    assert not result.excludes_zero


def test_recovered_cannot_exceed_n() -> None:
    with pytest.raises(EstimatorError, match="exceeds n"):
        ArmSummary(n=10, recovered=11)


# ── CUPED ───────────────────────────────────────────────────────────────────


def test_cuped_preserves_the_mean_exactly() -> None:
    """Subtracting a mean-zero term cannot move the point estimate.

    A "variance reduction" that also shifted the mean would be a bias, not an
    improvement — this is the property that makes CUPED safe to apply.
    """
    rng = np.random.default_rng(11)
    x = rng.normal(size=500)
    y = 0.7 * x + rng.normal(size=500)

    adjusted = cuped(y, x)
    assert float(np.mean(adjusted)) == pytest.approx(float(np.mean(y)), abs=1e-9)


def test_cuped_reduces_variance_in_proportion_to_correlation() -> None:
    """Stronger covariates should buy more reduction, weaker ones less."""
    rng = np.random.default_rng(12)
    x = rng.normal(size=4000)

    strong = cuped(0.9 * x + rng.normal(size=4000) * 0.4, x)
    weak = cuped(0.1 * x + rng.normal(size=4000), x)

    strong_ratio = float(np.var(strong)) / float(np.var(0.9 * x + rng.normal(size=4000) * 0.4))
    assert float(np.var(strong)) < float(np.var(weak))
    assert strong_ratio < 1.0


def test_cuped_with_a_constant_covariate_is_a_no_op() -> None:
    """theta is undefined when var(x) = 0; the honest adjustment is none."""
    y = np.array([1.0, 2.0, 3.0, 4.0])
    np.testing.assert_allclose(cuped(y, np.full(4, 5.0)), y)
    assert cuped_theta(y, np.full(4, 5.0)) == 0.0


# ── SRM ─────────────────────────────────────────────────────────────────────


def test_srm_passes_on_a_balanced_split() -> None:
    result = srm_check(observed_control=1000, observed_treatment=9000, control_pct=0.10)
    assert result.passed
    assert result.statistic == pytest.approx(0.0, abs=1e-9)


def test_srm_fails_on_a_deliberately_skewed_split() -> None:
    """The check must be able to say no, or passing means nothing."""
    result = srm_check(observed_control=3000, observed_treatment=7000, control_pct=0.10)
    assert not result.passed
    assert result.p_value < 1e-10


def test_srm_statistic_matches_a_hand_computed_chi_square() -> None:
    """chi2 = sum (O-E)^2/E, worked by hand."""
    result = srm_check(observed_control=520, observed_treatment=480, control_pct=0.5)
    expected = (520 - 500) ** 2 / 500 + (480 - 500) ** 2 / 500
    assert result.statistic == pytest.approx(expected)
    assert result.passed  # 1.6 on 1 df is unremarkable


# ── IPS ─────────────────────────────────────────────────────────────────────


def test_ips_reports_the_clipping_rate() -> None:
    """§34: an unreported clip is a silent bias."""
    outcomes = np.array([1.0, 1.0, 1.0, 1.0])
    propensities = np.array([0.5, 0.5, 0.001, 0.002])

    result = ips(outcomes, propensities)
    assert result.clipped_fraction == pytest.approx(0.5)
    assert result.estimate > 0


def test_ips_rejects_impossible_propensities() -> None:
    with pytest.raises(EstimatorError, match="propensities must lie"):
        ips(np.array([1.0]), np.array([0.0]))
