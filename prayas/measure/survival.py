"""Kaplan-Meier and the log-rank test (Master Spec §34; ADR-047).

"Survival comparison for the retention metric — Kaplan-Meier curves per arm
with a log-rank test, since mandate survival is censored by the observation
window and a simple rate would be biased by cohort age."

That last clause is the reason this module exists rather than a ratio. A
mandate enrolled last week has not had the chance to be revoked that one
enrolled a year ago has; comparing raw revocation rates would report the
younger cohort as healthier purely because it is younger. Kaplan-Meier handles
that by conditioning on being at risk at each event time.

Hand-rolled per ADR-047, validated against published reference data in the
tests. The only distribution needed is chi-square with one degree of freedom
(two arms), which `estimators.chi2_sf_1df` computes exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from prayas.measure.estimators import EstimatorError, chi2_sf_1df

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class SurvivalCurve:
    """A Kaplan-Meier estimate.

    `times` are the distinct event times; `survival[i]` is S(t) just after
    `times[i]`. `at_risk` and `events` are retained because a curve without its
    risk set cannot be audited, and §34's comparison needs both.
    """

    times: FloatArray
    survival: FloatArray
    at_risk: IntArray
    events: IntArray

    def at(self, t: float) -> float:
        """S(t): the estimate at time `t`, right-continuous.

        Before the first event S(t) = 1 — nothing has happened yet.
        """
        if self.times.size == 0:
            return 1.0
        idx = int(np.searchsorted(self.times, t, side="right")) - 1
        return 1.0 if idx < 0 else float(self.survival[idx])

    @property
    def median(self) -> float | None:
        """First time at which S(t) <= 0.5, or None if never reached."""
        below = np.flatnonzero(self.survival <= 0.5)
        return None if below.size == 0 else float(self.times[below[0]])


def kaplan_meier(durations: FloatArray, observed: NDArray[np.bool_]) -> SurvivalCurve:
    """Estimate S(t) from right-censored durations.

    `observed[i]` is True when the event (revocation) was seen, and False when
    the unit was still alive at the end of the observation window — censored,
    not survived-forever. Censored units contribute to the risk set up to their
    censoring time and then leave it, which is precisely the correction a raw
    rate fails to make.

        S(t) = prod over event times t_i <= t of (1 - d_i / n_i)
    """
    if durations.shape != observed.shape:
        raise EstimatorError("durations and observed differ in shape")
    if durations.size == 0:
        raise EstimatorError("no observations")
    if np.any(durations < 0):
        raise EstimatorError("durations must be non-negative")

    order = np.argsort(durations, kind="stable")
    sorted_durations = durations[order]
    sorted_observed = observed[order]

    event_times = np.unique(sorted_durations[sorted_observed])
    if event_times.size == 0:
        # Nobody was observed to fail: S(t) = 1 throughout.
        return SurvivalCurve(
            times=np.zeros(0, dtype=np.float64),
            survival=np.zeros(0, dtype=np.float64),
            at_risk=np.zeros(0, dtype=np.int64),
            events=np.zeros(0, dtype=np.int64),
        )

    n = durations.size
    survival: list[float] = []
    at_risk: list[int] = []
    events: list[int] = []
    running = 1.0

    for t in event_times:
        # At risk = still under observation at t, i.e. duration >= t.
        risk = int(np.count_nonzero(sorted_durations >= t))
        died = int(np.count_nonzero((sorted_durations == t) & sorted_observed))
        running *= 1.0 - died / risk
        survival.append(running)
        at_risk.append(risk)
        events.append(died)

    assert n > 0
    return SurvivalCurve(
        times=event_times.astype(np.float64),
        survival=np.asarray(survival, dtype=np.float64),
        at_risk=np.asarray(at_risk, dtype=np.int64),
        events=np.asarray(events, dtype=np.int64),
    )


@dataclass(frozen=True, slots=True)
class LogRankResult:
    statistic: float
    p_value: float
    observed_a: int
    expected_a: float

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05


def log_rank(
    durations_a: FloatArray,
    observed_a: NDArray[np.bool_],
    durations_b: FloatArray,
    observed_b: NDArray[np.bool_],
) -> LogRankResult:
    """Two-sample log-rank test.

    At each distinct event time across both arms, compare the events actually
    seen in arm A against the number expected if both arms shared one hazard,
    weighting by the risk sets. The summed discrepancy, standardised, is
    chi-square with one degree of freedom.

        O_A = sum of d_A
        E_A = sum of n_A * d / n
        V   = sum of n_A n_B d (n - d) / (n^2 (n - 1))
        X^2 = (O_A - E_A)^2 / V

    The `n - 1` denominator makes V vanish when only one unit remains at risk;
    such times carry no information and are skipped rather than special-cased
    into a divide-by-zero.
    """
    for name, arr, obs in (
        ("a", durations_a, observed_a),
        ("b", durations_b, observed_b),
    ):
        if arr.shape != obs.shape:
            raise EstimatorError(f"arm {name}: durations and observed differ in shape")
        if arr.size == 0:
            raise EstimatorError(f"arm {name} is empty")

    all_times = np.unique(np.concatenate([durations_a[observed_a], durations_b[observed_b]]))
    if all_times.size == 0:
        # No events anywhere: the arms are indistinguishable.
        return LogRankResult(statistic=0.0, p_value=1.0, observed_a=0, expected_a=0.0)

    observed_total = 0
    expected_total = 0.0
    variance_total = 0.0

    for t in all_times:
        n_a = int(np.count_nonzero(durations_a >= t))
        n_b = int(np.count_nonzero(durations_b >= t))
        n = n_a + n_b
        if n <= 1:
            continue

        d_a = int(np.count_nonzero((durations_a == t) & observed_a))
        d_b = int(np.count_nonzero((durations_b == t) & observed_b))
        d = d_a + d_b
        if d == 0:
            continue

        observed_total += d_a
        expected_total += n_a * d / n
        variance_total += n_a * n_b * d * (n - d) / (n * n * (n - 1))

    if variance_total <= 0.0:
        return LogRankResult(
            statistic=0.0,
            p_value=1.0,
            observed_a=observed_total,
            expected_a=expected_total,
        )

    statistic = (observed_total - expected_total) ** 2 / variance_total
    return LogRankResult(
        statistic=statistic,
        p_value=chi2_sf_1df(statistic),
        observed_a=observed_total,
        expected_a=expected_total,
    )
