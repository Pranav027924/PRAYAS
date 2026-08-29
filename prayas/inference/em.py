"""EM-trained cause inference for code 05 (Master Spec §20; ADR-068).

§20: "Fit by EM over the latent variable, initialised from the deterministic
rules." The latent variable is the true cause behind a decline the issuer
refused to explain.

**Why EM and not a classifier.** There is no label. Code 05 means "Do Not
Honor" and nothing else; the issuer will not say whether the account was empty,
the card was over its limit, or a fraud rule fired. A supervised model has
nothing to be supervised by. EM treats the cause as latent and lets the
*context* — §20's feature table — separate the components, anchored so the
components keep the meanings their names claim.

**Anchoring is what stops EM from drifting.** Unconstrained, EM finds whatever
four clusters best explain the data and there is no reason component 2 should
be `fraud_hold` rather than "Tuesdays". So the fit is seeded from §20's
deterministic and near-deterministic codes — 51 really is `no_funds`, 91 really
is `issuer_degraded` — and those anchor rows keep full weight on their known
cause through every M-step. The 05 rows are the only ones whose responsibility
is free to move.

**Train with the outcome, serve without it.** §20: "The eventual outcome is a
noisy label for the latent cause: clearing two days later at the same amount on
day-of-month 1 indicates `no_funds`; clearing five minutes later on a different
route indicates `issuer_degraded`." That outcome exists at training time and
does not exist at decision time — the whole point is to decide *before*
retrying. So the likelihood factorises into a context part and an outcome part,
training uses both, and `predict_proba` marginalises the outcome away. Using
the outcome at serve time would post excellent offline numbers and fail in
production, which is the exact failure §43 pages on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.inference.cause import CauseContext, infer
from prayas.sim.config import FRAUD_HOLD, ISSUER_DEGRADED, LIMIT_BREACH, NO_FUNDS

#: The causes that can hide behind 05 (§20). The terminal causes are excluded:
#: `mandate_dead` and `credential_dead` resolve from their own codes with
#: certainty, and §20 is explicit that for those "no model is needed, no model
#: wanted".
COMPONENTS: Final[tuple[str, ...]] = (NO_FUNDS, ISSUER_DEGRADED, FRAUD_HOLD, LIMIT_BREACH)

#: §20's discriminating features, in fixed order.
CONTEXT_FEATURES: Final[tuple[str, ...]] = (
    "issuer_wilson_lower",
    "peer_success_rate",
    "sibling_failure_rate",
    "amount_ratio",
    "days_since_payday",
    "n_concurrent_mandates",
)

#: The outcome features. Training only — see the module docstring.
OUTCOME_FEATURES: Final[tuple[str, ...]] = ("recovered", "log_recovery_lag")

#: Variance floor. A component that collapses onto a handful of identical rows
#: gets zero variance and infinite likelihood, which lets it swallow the whole
#: dataset on the next E-step. This is the standard guard and it is load-bearing.
_MIN_VARIANCE: Final = 1e-3


@dataclass(frozen=True, slots=True)
class Observation:
    """One declined cycle, as the system can actually see it."""

    decline_code: str
    context: CauseContext
    #: Whether a later attempt eventually cleared, and how long after. Known at
    #: training time only.
    recovered: bool = False
    recovery_lag_hours: float | None = None

    def context_row(self) -> list[float]:
        c = self.context
        return [
            c.issuer_wilson_lower,
            c.peer_success_rate,
            c.sibling_failure_rate,
            c.amount_ratio,
            float(c.days_since_inferred_payday if c.days_since_inferred_payday is not None else -1),
            float(c.n_concurrent_mandates),
        ]

    def outcome_row(self) -> list[float]:
        lag = self.recovery_lag_hours
        return [
            1.0 if self.recovered else 0.0,
            float(np.log1p(lag)) if (self.recovered and lag is not None and lag >= 0) else -1.0,
        ]


def _gaussian_log_pdf(
    x: npt.NDArray[np.float64], mean: npt.NDArray[np.float64], var: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Diagonal-covariance log density, summed over features.

    Naive Bayes within each component: §20's features are not independent, but
    with four components and a few thousand 05 rows a full covariance is
    estimated worse than it is worth. The failure mode of the naive assumption
    is overconfidence, which the confusion matrix will show.
    """
    var = np.maximum(var, _MIN_VARIANCE)
    return np.sum(-0.5 * (np.log(2.0 * np.pi * var) + (x[:, None, :] - mean) ** 2 / var), axis=2)


@dataclass(frozen=True, slots=True)
class EMFit:
    """A fitted mixture. Immutable, so a pinned version cannot drift."""

    weights: npt.NDArray[np.float64]
    context_mean: npt.NDArray[np.float64]
    context_var: npt.NDArray[np.float64]
    outcome_mean: npt.NDArray[np.float64]
    outcome_var: npt.NDArray[np.float64]
    iterations: int
    log_likelihood: float


class CauseEM:
    """§20's EM over the latent cause behind code 05."""

    def __init__(self, *, max_iterations: int = 200, tolerance: float = 1e-6) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self._fit: EMFit | None = None

    @property
    def is_fitted(self) -> bool:
        return self._fit is not None

    @property
    def fit_result(self) -> EMFit:
        if self._fit is None:
            raise RuntimeError("model is not fitted")
        return self._fit

    @staticmethod
    def _initial_responsibility(observations: list[Observation]) -> npt.NDArray[np.float64]:
        """§20's "initialised from the deterministic rules".

        Every row starts at the heuristic's posterior. Rows whose code already
        names a cause start at a one-hot on that cause and are pinned there by
        `_anchors`; the 05 rows start at the heuristic's spread and are free.
        """
        rows = np.zeros((len(observations), len(COMPONENTS)), dtype=np.float64)
        for i, obs in enumerate(observations):
            posterior = infer(obs.decline_code, obs.context).posterior
            for j, cause in enumerate(COMPONENTS):
                rows[i, j] = posterior.get(cause, 0.0)
            total = rows[i].sum()
            rows[i] = rows[i] / total if total > 0 else 1.0 / len(COMPONENTS)
        return rows

    @staticmethod
    def _anchors(observations: list[Observation]) -> npt.NDArray[np.bool_]:
        """Rows whose cause the issuer already stated. These never move."""
        return np.asarray(
            [obs.decline_code != "05" for obs in observations],
            dtype=np.bool_,
        )

    def fit(self, observations: list[Observation]) -> CauseEM:
        if len(observations) < len(COMPONENTS):
            raise ValueError("need at least one observation per component")

        context = np.asarray([o.context_row() for o in observations], dtype=np.float64)
        outcome = np.asarray([o.outcome_row() for o in observations], dtype=np.float64)
        responsibility = self._initial_responsibility(observations)
        anchored = self._anchors(observations)
        pinned = responsibility[anchored].copy()

        previous = -np.inf
        weights = context_mean = context_var = outcome_mean = outcome_var = None
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            # ── M-step ──────────────────────────────────────────────────────
            mass = responsibility.sum(axis=0) + 1e-12
            weights = mass / mass.sum()
            context_mean, context_var = _weighted_moments(context, responsibility, mass)
            outcome_mean, outcome_var = _weighted_moments(outcome, responsibility, mass)

            # ── E-step ──────────────────────────────────────────────────────
            log_joint = (
                np.log(weights)
                + _gaussian_log_pdf(context, context_mean, context_var)
                + _gaussian_log_pdf(outcome, outcome_mean, outcome_var)
            )
            shift = log_joint.max(axis=1, keepdims=True)
            unnormalised = np.exp(log_joint - shift)
            total = unnormalised.sum(axis=1, keepdims=True)
            responsibility = unnormalised / total

            # Anchored rows are restored *after* the E-step, so they inform the
            # component parameters without ever being reassigned by them.
            responsibility[anchored] = pinned

            log_likelihood = float(np.sum(np.log(total) + shift))
            if abs(log_likelihood - previous) < self.tolerance:
                previous = log_likelihood
                break
            previous = log_likelihood

        assert weights is not None and context_mean is not None
        assert context_var is not None and outcome_mean is not None and outcome_var is not None

        self._fit = EMFit(
            weights=weights,
            context_mean=context_mean,
            context_var=context_var,
            outcome_mean=outcome_mean,
            outcome_var=outcome_var,
            iterations=iteration,
            log_likelihood=previous,
        )
        return self

    def predict_proba(self, contexts: list[CauseContext]) -> npt.NDArray[np.float64]:
        """Posterior over causes from context alone — the serve-time path.

        The outcome factor is *absent*, not zeroed: marginalising a variable out
        is dropping its likelihood term, and that is what makes training and
        serving consistent rather than merely similar.
        """
        fit = self.fit_result
        x = np.asarray([Observation("05", c).context_row() for c in contexts], dtype=np.float64)
        log_joint = np.log(fit.weights) + _gaussian_log_pdf(x, fit.context_mean, fit.context_var)
        shift = log_joint.max(axis=1, keepdims=True)
        unnormalised = np.exp(log_joint - shift)
        result: npt.NDArray[np.float64] = unnormalised / unnormalised.sum(axis=1, keepdims=True)
        return result

    def predict(self, contexts: list[CauseContext]) -> list[str]:
        """The most likely cause per context."""
        return [COMPONENTS[int(i)] for i in self.predict_proba(contexts).argmax(axis=1)]


def _weighted_moments(
    x: npt.NDArray[np.float64],
    responsibility: npt.NDArray[np.float64],
    mass: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Responsibility-weighted mean and variance per component."""
    mean = (responsibility.T @ x) / mass[:, None]
    var = (responsibility.T @ (x**2)) / mass[:, None] - mean**2
    return mean, np.maximum(var, _MIN_VARIANCE)


def confusion_matrix(
    truth: list[str], predicted: list[str], *, labels: tuple[str, ...] = COMPONENTS
) -> npt.NDArray[np.int64]:
    """Rows are true causes, columns predicted. §38's "strongest honest use"."""
    if len(truth) != len(predicted):
        raise ValueError("truth and predicted must be the same length")
    index = {label: i for i, label in enumerate(labels)}
    matrix = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for actual, guess in zip(truth, predicted, strict=True):
        if actual in index and guess in index:
            matrix[index[actual], index[guess]] += 1
    return matrix


def render_confusion(matrix: npt.NDArray[np.int64], *, labels: tuple[str, ...] = COMPONENTS) -> str:
    """The artifact, as a table a reviewer can read without tooling."""
    width = max(len(label) for label in labels) + 2
    header = " " * width + "".join(f"{label[:8]:>10}" for label in labels) + f"{'recall':>10}"
    lines = [header]
    for i, label in enumerate(labels):
        total = matrix[i].sum()
        recall = matrix[i, i] / total if total else 0.0
        lines.append(
            f"{label:<{width}}" + "".join(f"{int(n):>10}" for n in matrix[i]) + f"{recall:>9.1%}"
        )
    precision = "".join(
        f"{(matrix[j, j] / matrix[:, j].sum() if matrix[:, j].sum() else 0.0):>9.1%} "
        for j in range(len(labels))
    )
    lines.append(f"{'precision':<{width}}{precision}")
    accuracy = matrix.trace() / matrix.sum() if matrix.sum() else 0.0
    lines.append(f"{'accuracy':<{width}}{accuracy:>9.1%}")
    return "\n".join(lines)
