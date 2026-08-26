"""Calibration metrics (ADR-034; Master Spec §21, §43).

§21: "Calibration is the property that matters, not AUC. The sequencer consumes
these probabilities as expected rupees. A model that ranks well but is
miscalibrated does not merely order badly — it computes the wrong money and
stops at the wrong time."

Implemented here rather than imported so the definitions are auditable. Binning
choice is not cosmetic: equal-width and equal-frequency binning give materially
different ECE for the same model, so the choice is stated and fixed.

§43 alerts when ECE exceeds 0.05 over 7 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

#: §43's paging threshold.
ECE_ALERT_THRESHOLD: Final = 0.05

#: Probabilities are clipped before taking logs. Without this a single confident
#: mistake yields infinite loss and one outlier destroys the metric.
_EPS: Final = 1e-15

DEFAULT_BINS: Final = 10


def log_loss(y_true: npt.ArrayLike, y_prob: npt.ArrayLike) -> float:
    """Mean binary cross-entropy, in nats.

    Lower is better. A model that always predicts the base rate scores the
    entropy of the base rate, which is the bar a hazard model must beat.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(y_prob, dtype=float), _EPS, 1.0 - _EPS)

    if y.shape != p.shape:
        raise ValueError(f"shape mismatch: y_true {y.shape}, y_prob {p.shape}")
    if y.size == 0:
        raise ValueError("log_loss needs at least one observation")
    if not np.all((y == 0) | (y == 1)):
        raise ValueError("y_true must be binary")

    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


@dataclass(frozen=True, slots=True)
class ReliabilityCurve:
    """Per-bin predicted vs observed frequency — the picture behind ECE."""

    bin_lower: npt.NDArray[np.float64]
    bin_upper: npt.NDArray[np.float64]
    mean_predicted: npt.NDArray[np.float64]
    observed_frequency: npt.NDArray[np.float64]
    count: npt.NDArray[np.int64]

    @property
    def populated(self) -> npt.NDArray[np.bool_]:
        return self.count > 0


def reliability_curve(
    y_true: npt.ArrayLike, y_prob: npt.ArrayLike, *, bins: int = DEFAULT_BINS
) -> ReliabilityCurve:
    """Equal-width bins over [0, 1].

    Equal-width rather than equal-frequency: the question is whether "0.7 means
    70%" holds across the probability range, and equal-frequency bins would hide
    a badly calibrated region simply because few predictions land there.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)

    if y.shape != p.shape:
        raise ValueError(f"shape mismatch: y_true {y.shape}, y_prob {p.shape}")
    if bins < 1:
        raise ValueError("bins must be positive")

    edges = np.linspace(0.0, 1.0, bins + 1)
    # Right-closed on the final bin so p == 1.0 is not pushed out of range.
    index = np.clip(np.digitize(p, edges[1:-1], right=False), 0, bins - 1)

    mean_predicted = np.zeros(bins, dtype=float)
    observed = np.zeros(bins, dtype=float)
    count = np.zeros(bins, dtype=np.int64)

    for b in range(bins):
        mask = index == b
        n = int(np.count_nonzero(mask))
        count[b] = n
        if n:
            mean_predicted[b] = float(np.mean(p[mask]))
            observed[b] = float(np.mean(y[mask]))

    return ReliabilityCurve(
        bin_lower=edges[:-1],
        bin_upper=edges[1:],
        mean_predicted=mean_predicted,
        observed_frequency=observed,
        count=count,
    )


def expected_calibration_error(
    y_true: npt.ArrayLike, y_prob: npt.ArrayLike, *, bins: int = DEFAULT_BINS
) -> float:
    """Count-weighted mean |predicted - observed| across populated bins.

    Weighting by count matters: an unweighted mean lets a bin holding three
    predictions carry the same influence as one holding thirty thousand.
    """
    curve = reliability_curve(y_true, y_prob, bins=bins)
    populated = curve.populated
    if not np.any(populated):
        raise ValueError("no populated bins — ECE is undefined")

    gaps = np.abs(curve.mean_predicted[populated] - curve.observed_frequency[populated])
    weights = curve.count[populated].astype(float)
    return float(np.sum(gaps * weights) / np.sum(weights))


def is_calibrated(
    y_true: npt.ArrayLike, y_prob: npt.ArrayLike, *, bins: int = DEFAULT_BINS
) -> bool:
    """§43's threshold, as a predicate."""
    return expected_calibration_error(y_true, y_prob, bins=bins) < ECE_ALERT_THRESHOLD


def brier_score(y_true: npt.ArrayLike, y_prob: npt.ArrayLike) -> float:
    """Mean squared error on probabilities. Reported alongside log-loss because
    it is bounded, so a single confident error cannot dominate the summary."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    if y.shape != p.shape:
        raise ValueError(f"shape mismatch: y_true {y.shape}, y_prob {p.shape}")
    if y.size == 0:
        raise ValueError("brier_score needs at least one observation")
    return float(np.mean((p - y) ** 2))
