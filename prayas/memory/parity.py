"""Online/offline feature parity (Master Spec §43; Phase 12).

§43 lists it among the alerts with a threshold of **"any divergence"** and a
one-line reason: *training-serving skew*. It is the failure where a model is
trained on one definition of a feature and served another, so every offline
number is describing a model that never ran.

**Any divergence, not a tolerance.** Floating point makes exact equality the
wrong test, so the comparison is to a tight relative tolerance — but the
threshold is not a knob to widen when the job goes red. A feature that differs
by 1% between training and serving is not "close enough": it is two different
features with one name, and §43 says so.

The job compares the vector a model was *trained* on against the vector the
serving path *builds* for the same entity, and reports per feature. Reporting
per feature matters because the fix differs entirely: a single skewed column is
usually a definition drift in one transform, while every column skewed is
usually the wrong snapshot being loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

#: Relative tolerance below which two computations of the same feature are
#: treated as the same number rather than as skew. Sized for float64
#: accumulation order, not for genuine disagreement.
RELATIVE_TOLERANCE: Final = 1e-9

#: Absolute floor, so a feature that is legitimately zero on one side does not
#: register as infinite relative error.
ABSOLUTE_TOLERANCE: Final = 1e-12


class ParityError(ValueError):
    """The comparison itself is not well formed."""


@dataclass(frozen=True, slots=True)
class FeatureSkew:
    """One feature's divergence between the offline and online paths."""

    name: str
    offline: float
    online: float

    @property
    def absolute(self) -> float:
        return abs(self.offline - self.online)

    @property
    def relative(self) -> float:
        scale = max(abs(self.offline), abs(self.online), ABSOLUTE_TOLERANCE)
        return self.absolute / scale


@dataclass(frozen=True, slots=True)
class ParityReport:
    """What the job found, per feature and overall."""

    rows_compared: int
    skewed: tuple[FeatureSkew, ...]
    feature_names: tuple[str, ...]

    @property
    def alerting(self) -> bool:
        """§43: any divergence at all."""
        return bool(self.skewed)

    @property
    def worst(self) -> FeatureSkew | None:
        return max(self.skewed, key=lambda s: s.relative, default=None)

    @property
    def diagnosis(self) -> str:
        """A first read on *what kind* of skew this is.

        Cheap to compute and worth stating: one column skewed and all columns
        skewed have different causes and different fixes, and an operator woken
        at 03:00 should not have to derive that from a table.
        """
        if not self.skewed:
            return "no divergence"
        if len(self.skewed) == len(self.feature_names):
            return "every feature diverges — likely the wrong snapshot, not a transform"
        if len(self.skewed) == 1:
            return f"one feature diverges ({self.skewed[0].name}) — likely a definition drift"
        return f"{len(self.skewed)} of {len(self.feature_names)} features diverge"

    def render(self) -> str:
        if not self.skewed:
            return f"parity ok across {self.rows_compared:,} rows"
        width = max(len(s.name) for s in self.skewed) + 2
        lines = [
            f"SKEW across {self.rows_compared:,} rows — {self.diagnosis}",
            f"{'feature':<{width}}{'offline':>14}{'online':>14}{'rel':>12}",
        ]
        for skew in sorted(self.skewed, key=lambda s: -s.relative):
            lines.append(
                f"{skew.name:<{width}}{skew.offline:>14.6g}{skew.online:>14.6g}"
                f"{skew.relative:>12.3g}"
            )
        return "\n".join(lines)


def check_parity(
    offline: npt.NDArray[np.float64],
    online: npt.NDArray[np.float64],
    feature_names: tuple[str, ...],
    *,
    relative_tolerance: float = RELATIVE_TOLERANCE,
) -> ParityReport:
    """Compare the same entities' features as built by both paths.

    Rows must be aligned: `offline[i]` and `online[i]` are the same entity. An
    unaligned comparison would report skew everywhere and mean nothing, so the
    shapes are checked rather than broadcast.
    """
    if offline.shape != online.shape:
        raise ParityError(
            f"shapes differ: offline {offline.shape} vs online {online.shape} — "
            f"the two paths must be compared over the same entities"
        )
    if offline.ndim != 2:
        raise ParityError("expected a (rows, features) matrix")
    if len(feature_names) != offline.shape[1]:
        raise ParityError(f"expected {offline.shape[1]} feature names, got {len(feature_names)}")
    if offline.shape[0] == 0:
        raise ParityError("no rows to compare — an empty check is not a passing check")

    skewed: list[FeatureSkew] = []
    for i, name in enumerate(feature_names):
        a, b = offline[:, i], online[:, i]
        scale = np.maximum(np.maximum(np.abs(a), np.abs(b)), ABSOLUTE_TOLERANCE)
        relative = np.abs(a - b) / scale
        worst = int(np.argmax(relative))
        if relative[worst] > relative_tolerance:
            skewed.append(FeatureSkew(name=name, offline=float(a[worst]), online=float(b[worst])))

    return ParityReport(
        rows_compared=int(offline.shape[0]),
        skewed=tuple(skewed),
        feature_names=feature_names,
    )
