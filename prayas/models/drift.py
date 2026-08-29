"""Drift monitoring: PSI on features, ECE on probabilities (§21, §43; ADR-070).

Two questions, and they fail in different ways:

**Has the input distribution moved?** Population Stability Index, per feature.
PSI catches a world that has changed — a new merchant vertical, a rail mix
shift, a festival month — before the output has visibly degraded. It needs no
labels, which is what makes it usable the day the drift starts rather than
weeks later when outcomes have matured.

**Are the probabilities still true?** Expected calibration error, reused from
`prayas.inference.calibration` rather than reimplemented. §21 is explicit that
"calibration is the property that matters, not AUC", because the sequencer
"consumes these probabilities as expected rupees" — so a model that still ranks
correctly while its probabilities drift is quietly computing the wrong money and
stopping at the wrong time. §43 alerts above 0.05.

**PSI cannot see a bad model, and ECE cannot see it coming.** A model can be
perfectly stable on inputs and badly calibrated, or drifting hard on inputs
while still calibrated because the drift is in a feature it ignores. Both are
reported; neither is derived from the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.inference.calibration import expected_calibration_error

#: §43's calibration alert threshold.
ECE_ALERT: Final = 0.05

#: Conventional PSI bands. Below 0.10 the shift is not actionable; above 0.25 it
#: is large enough that a refit is the expected response rather than an option.
PSI_MODERATE: Final = 0.10
PSI_SIGNIFICANT: Final = 0.25

#: Floor applied to a bin's share before taking a logarithm. A bin that is empty
#: in one population and populated in the other would otherwise give infinite
#: PSI, which is not "infinitely drifted" — it is a sample-size artefact.
_MIN_SHARE: Final = 1e-6

#: Share of the reference at a single value above which that value is given its
#: own bin rather than being merged into a quantile bucket.
_SPIKE_SHARE: Final = 0.10

#: Quantile bins. Ten is the convention, and it keeps each reference bin at 10%
#: of mass so a bin cannot be empty in the reference by construction.
DEFAULT_BINS: Final = 10


def population_stability_index(
    reference: npt.ArrayLike, current: npt.ArrayLike, *, bins: int = DEFAULT_BINS
) -> float:
    """PSI between a reference and a current sample of one feature.

        PSI = sum over bins of (current% - reference%) * ln(current% / reference%)

    Bins are the *reference* distribution's quantiles, not equal-width ones: a
    feature like `amount_ratio` is heavily skewed, and equal-width bins would
    put almost all mass in one bucket where no drift could ever be visible.
    """
    ref = np.asarray(reference, dtype=np.float64).ravel()
    cur = np.asarray(current, dtype=np.float64).ravel()
    if ref.size == 0 or cur.size == 0:
        raise ValueError("both samples must be non-empty")
    if bins < 2:
        raise ValueError("bins must be at least 2")

    # Interior cut points only, then open tails. Replacing the outermost
    # quantiles with the tails (rather than adding to them) collapses a
    # low-cardinality feature to a single bin covering everything, and a single
    # bin can never show drift — a silent monitor is worse than none.
    quantiles = np.unique(np.quantile(ref, np.linspace(0.0, 1.0, bins + 1)))
    interior = quantiles[1:-1]
    if interior.size == 0:
        # A reference concentrated on one value has no shape to drift. What it
        # *can* do is stop being concentrated, so the comparison is over the
        # mass below, at, and above that value. Declaring such a feature
        # maximally drifted on sight would alert on every sparse feature
        # forever, and an alert that always fires is not a monitor.
        return _point_mass_psi(ref, cur, float(quantiles[0]))

    # **Give a heavy point mass its own bin.** Quantile edges deduplicate, so a
    # feature that is 70% one value — which every cold-start default is —
    # collapses to two or three bins, and PSI then saturates well below the
    # alert threshold no matter how far the rest of the mass moves. Splitting
    # the spike out restores the resolution where the feature actually lives.
    values, counts = np.unique(ref, return_counts=True)
    if counts.max() / ref.size >= _SPIKE_SHARE:
        spike = float(values[counts.argmax()])
        interior = np.unique(
            np.concatenate((interior, [spike, float(np.nextafter(spike, np.inf))]))
        )

    edges = np.concatenate(([-np.inf], interior, [np.inf]))
    ref_share = np.histogram(ref, bins=edges)[0] / ref.size
    cur_share = np.histogram(cur, bins=edges)[0] / cur.size

    ref_share = np.maximum(ref_share, _MIN_SHARE)
    cur_share = np.maximum(cur_share, _MIN_SHARE)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def _point_mass_psi(
    reference: npt.NDArray[np.float64], current: npt.NDArray[np.float64], value: float
) -> float:
    """PSI over `{< value, == value, > value}` for a concentrated feature."""
    ref_share = np.array(
        [
            np.mean(reference < value),
            np.mean(reference == value),
            np.mean(reference > value),
        ]
    )
    cur_share = np.array(
        [np.mean(current < value), np.mean(current == value), np.mean(current > value)]
    )
    ref_share = np.maximum(ref_share, _MIN_SHARE)
    cur_share = np.maximum(cur_share, _MIN_SHARE)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


@dataclass(frozen=True, slots=True)
class FeatureDrift:
    """One feature's PSI and what it means."""

    name: str
    psi: float

    @property
    def severity(self) -> str:
        if self.psi >= PSI_SIGNIFICANT:
            return "significant"
        if self.psi >= PSI_MODERATE:
            return "moderate"
        return "stable"

    @property
    def alerting(self) -> bool:
        return self.psi >= PSI_SIGNIFICANT


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Everything monitoring needs to decide whether to refit."""

    features: tuple[FeatureDrift, ...]
    ece: float | None

    @property
    def calibration_alerting(self) -> bool:
        return self.ece is not None and self.ece > ECE_ALERT

    @property
    def drifting(self) -> tuple[FeatureDrift, ...]:
        return tuple(f for f in self.features if f.alerting)

    @property
    def alerting(self) -> bool:
        return self.calibration_alerting or bool(self.drifting)

    @property
    def worst(self) -> FeatureDrift | None:
        return max(self.features, key=lambda f: f.psi, default=None)

    def render(self) -> str:
        width = max((len(f.name) for f in self.features), default=8) + 2
        lines = [f"{'feature':<{width}}{'PSI':>8}  severity"]
        for feature in sorted(self.features, key=lambda f: -f.psi):
            lines.append(f"{feature.name:<{width}}{feature.psi:>8.4f}  {feature.severity}")
        if self.ece is not None:
            verdict = "ALERT" if self.calibration_alerting else "ok"
            lines.append(f"\n{'ECE':<{width}}{self.ece:>8.4f}  {verdict} (threshold {ECE_ALERT})")
        return "\n".join(lines)


def drift_report(
    reference: npt.NDArray[np.float64],
    current: npt.NDArray[np.float64],
    feature_names: tuple[str, ...],
    *,
    y_true: npt.ArrayLike | None = None,
    y_prob: npt.ArrayLike | None = None,
    bins: int = DEFAULT_BINS,
) -> DriftReport:
    """PSI per feature, plus ECE when labels are available.

    Labels are optional on purpose. Input drift is observable immediately;
    calibration is not observable until outcomes mature, which for a 30-day
    funding horizon is a month. A monitor that could only report once labels
    arrived would be a month late every time.
    """
    if reference.ndim != 2 or current.ndim != 2:
        raise ValueError("reference and current must be 2-D feature matrices")
    if reference.shape[1] != current.shape[1]:
        raise ValueError("reference and current must have the same number of features")
    if len(feature_names) != reference.shape[1]:
        raise ValueError(f"expected {reference.shape[1]} feature names, got {len(feature_names)}")

    features = tuple(
        FeatureDrift(
            name=name,
            psi=population_stability_index(reference[:, i], current[:, i], bins=bins),
        )
        for i, name in enumerate(feature_names)
    )

    ece = None
    if y_true is not None and y_prob is not None:
        ece = expected_calibration_error(y_true, y_prob)

    return DriftReport(features=features, ece=ece)
