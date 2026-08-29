"""V1 liquidity hazard: gradient-boosted, isotonically calibrated (§21; ADR-064, ADR-066).

§21: "V0 is a per-segment empirical hazard lookup... V1 is a gradient-boosted
discrete-time hazard."

**Calibration is the property that matters, not AUC.** §21 is unusually blunt
about why: "The sequencer consumes these probabilities as expected rupees. A
model that ranks well but is miscalibrated does not merely order badly — it
computes the wrong money and stops at the wrong time." So the model is not the
GBM; the model is the GBM *plus* an isotonic stage, and the stage is fitted on a
fold the GBM never saw. Calibrating on training predictions would produce a
flattering reliability curve and a sequencer that still stops in the wrong place.

**V0 lives here too**, fitted from the same rows. Phase 11's exit criterion is
"V1 beats V0 on held-out log-loss", and that is only a claim about the model if
both were shown identical data. A baseline fitted from a different population,
or evaluated on a different grid, would make the comparison an artefact of the
harness rather than a fact about the models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from prayas.inference.hazard import DEFAULT_KAPPA
from prayas.models.features import (
    FEATURE_NAMES,
    SLOTS_PER_DAY,
    Dataset,
    subset,
)

#: Index of `day_offset` and `hour_band` in `FEATURE_NAMES`, for the V0 lookup.
_DAY_OFFSET: Final = FEATURE_NAMES.index("day_offset")
_HOUR_BAND: Final = FEATURE_NAMES.index("hour_band")
_DAY_OF_MONTH: Final = FEATURE_NAMES.index("day_of_month")
_TICKET_BAND: Final = FEATURE_NAMES.index("ticket_band")

#: §27's k-anonymity floor. A cell thinner than this is not stored at all; the
#: lookup falls back to the global prior rather than publishing a rate derived
#: from a handful of customers.
MIN_CELL_OBSERVATIONS: Final = 50


class HazardV0:
    """Per-segment empirical lookup, fitted from discrete-time rows.

    Keyed on `(day_of_month, hour_band, ticket_band)` — §36's `segment_priors`
    grid minus the dimensions a single simulated tenant cannot populate. Cells
    below §27's floor fall back to the global rate, shrunk by §21's
    `w = n/(n+kappa)` so the transition is smooth rather than a cliff.
    """

    def __init__(self, kappa: float = DEFAULT_KAPPA) -> None:
        self._cells: dict[tuple[int, int, int], tuple[float, int]] = {}
        self._global: float = 0.0
        self._kappa = kappa

    @staticmethod
    def _keys(x: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
        return np.stack(
            [
                x[:, _DAY_OF_MONTH].astype(np.int64),
                x[:, _HOUR_BAND].astype(np.int64),
                x[:, _TICKET_BAND].astype(np.int64),
            ],
            axis=1,
        )

    def fit(self, dataset: Dataset) -> HazardV0:
        keys = self._keys(dataset.x)
        self._global = float(dataset.y.mean())

        seen: dict[tuple[int, int, int], list[int]] = {}
        for key, label in zip(map(tuple, keys.tolist()), dataset.y.tolist(), strict=True):
            seen.setdefault(key, []).append(label)

        self._cells = {
            key: (float(np.mean(labels)), len(labels))
            for key, labels in seen.items()
            if len(labels) >= MIN_CELL_OBSERVATIONS
        }
        return self

    def predict(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, key in enumerate(map(tuple, self._keys(x).tolist())):
            cell = self._cells.get(key)
            if cell is None:
                out[i] = self._global
            else:
                rate, n = cell
                weight = n / (n + self._kappa)
                out[i] = weight * rate + (1.0 - weight) * self._global
        return np.clip(out, 1e-6, 1.0 - 1e-6)


@dataclass(frozen=True, slots=True)
class HazardV1Params:
    """Hyperparameters, pinned so a refit is reproducible (§43's registry).

    Deliberately conservative. The event rate is under 2%, so a deep unregulated
    ensemble will happily memorise individual customers' paydays and post a
    training log-loss that means nothing; the depth cap and the leaf minimum are
    what keep the held-out number honest.
    """

    max_depth: int = 5
    max_leaf_nodes: int = 31
    learning_rate: float = 0.06
    max_iter: int = 300
    min_samples_leaf: int = 60
    l2_regularization: float = 1.0
    early_stopping: bool = True
    validation_fraction: float = 0.15
    random_state: int = 0


class HazardV1:
    """§21's gradient-boosted discrete-time hazard, isotonically calibrated.

    Use `fit` to train both stages at once. The isotonic stage is fitted on a
    held-out slice of the *training* customers — never on the evaluation
    holdout, which would leak the number the exit criterion is measured on.
    """

    def __init__(self, params: HazardV1Params | None = None) -> None:
        self.params = params or HazardV1Params()
        self._gbm: HistGradientBoostingClassifier | None = None
        self._isotonic: IsotonicRegression | None = None

    @property
    def is_fitted(self) -> bool:
        return self._gbm is not None

    def fit(self, dataset: Dataset, *, calibration_fraction: float = 0.25) -> HazardV1:
        """Train the GBM, then fit isotonic on customers it never saw."""
        if not 0.0 < calibration_fraction < 1.0:
            raise ValueError("calibration_fraction must be in (0, 1)")
        if dataset.y.sum() == 0:
            raise ValueError("no positive labels — nothing to learn")

        customers = np.unique(dataset.groups)
        rng = np.random.default_rng(self.params.random_state)
        shuffled = rng.permutation(customers)
        n_cal = max(1, round(len(shuffled) * calibration_fraction))
        cal_customers = set(shuffled[:n_cal].tolist())

        is_cal = np.asarray([g in cal_customers for g in dataset.groups], dtype=np.bool_)
        core, calibration = subset(dataset, ~is_cal), subset(dataset, is_cal)

        p = self.params
        self._gbm = HistGradientBoostingClassifier(
            max_depth=p.max_depth,
            max_leaf_nodes=p.max_leaf_nodes,
            learning_rate=p.learning_rate,
            max_iter=p.max_iter,
            min_samples_leaf=p.min_samples_leaf,
            l2_regularization=p.l2_regularization,
            early_stopping=p.early_stopping,
            validation_fraction=p.validation_fraction,
            random_state=p.random_state,
        )
        self._gbm.fit(core.x, core.y)
        self.refit_calibration(calibration)
        return self

    def refit_calibration(self, dataset: Dataset) -> HazardV1:
        """§21's "isotonic recalibration weekly on a held-out fold".

        Separated from `fit` because that is how the spec wants it operated: the
        expensive stage is refitted rarely, the calibration stage on a schedule,
        against recent data. Drift shows up in calibration long before it shows
        up in ranking.
        """
        if self._gbm is None:
            raise RuntimeError("fit the model before recalibrating it")
        if len(dataset) == 0:
            raise ValueError("calibration fold is empty")

        raw = self._raw(dataset.x)
        self._isotonic = IsotonicRegression(
            y_min=0.0, y_max=1.0, out_of_bounds="clip", increasing=True
        ).fit(raw, dataset.y)
        return self

    def _raw(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        if self._gbm is None:
            raise RuntimeError("model is not fitted")
        proba: npt.NDArray[np.float64] = self._gbm.predict_proba(x)[:, 1]
        return proba

    def predict(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        """Calibrated hazards, clamped strictly inside (0, 1).

        The clamp is not cosmetic: a hazard of exactly 1 makes `S(t)` zero and
        every later conditional a division by zero, which is the arithmetic
        `prayas.inference.hazard` was written to keep out of the money path.
        """
        raw = self._raw(x)
        if self._isotonic is None:
            return np.clip(raw, 1e-6, 1.0 - 1e-6)
        calibrated: npt.NDArray[np.float64] = self._isotonic.predict(raw)
        return np.clip(calibrated, 1e-6, 1.0 - 1e-6)

    def hazard_curve(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        """Hazards for one cycle's slots, in slot order.

        The output feeds `prayas.inference.hazard.conditional_probability`
        unchanged — V1 replaces how the numbers are produced, not what they
        mean, so §21's conditioning on `t_last` is untouched.
        """
        if x.ndim != 2 or x.shape[1] != len(FEATURE_NAMES):
            raise ValueError(f"expected a (slots, {len(FEATURE_NAMES)}) feature matrix")
        return self.predict(x)


def fit_pair(train: Dataset, *, params: HazardV1Params | None = None) -> tuple[HazardV0, HazardV1]:
    """Both models, on identical rows. The only fair way to compare them."""
    return HazardV0().fit(train), HazardV1(params).fit(train)


def hazards_by_slot(
    hazards: npt.NDArray[np.float64], slots: npt.NDArray[np.int64]
) -> dict[int, float]:
    """Mean hazard per slot — the shape a reliability plot is read from."""
    out: dict[int, float] = {}
    for slot in np.unique(slots):
        out[int(slot)] = float(hazards[slots == slot].mean())
    return out


def daily_hazard(
    hazards: npt.NDArray[np.float64], slots: npt.NDArray[np.int64]
) -> dict[int, float]:
    """Mean hazard per day offset, for reading a payday off the curve."""
    days = slots // SLOTS_PER_DAY
    return {int(d): float(hazards[days == d].mean()) for d in np.unique(days)}
