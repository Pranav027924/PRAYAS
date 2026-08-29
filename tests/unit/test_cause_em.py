"""EM cause inference and the confusion matrix (Master Spec §20, §38).

Phase 11's artifact is "a confusion matrix for latent-cause inference — a claim
that cannot be made on real data". §38 explains why it can be made here: "the
simulator emits ground-truth causes, so cause inference is reported as an actual
confusion matrix".

The matrix is only worth printing if the comparison is fair, so the bar is the
**majority-class constant**, not the V0 heuristic. A model that beats a poor
heuristic while losing to "always guess `no_funds`" has learned nothing, and
that is exactly the trap ADR-035 recorded and ADR-065 removed.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

import numpy as np
import pytest

from prayas.inference.cause import CauseContext, infer
from prayas.inference.em import (
    COMPONENTS,
    CauseEM,
    Observation,
    confusion_matrix,
    render_confusion,
)
from prayas.models.causeset import build_nowcast, build_observations
from prayas.sim.config import FRAUD_HOLD, ISSUER_DEGRADED, LIMIT_BREACH, NO_FUNDS, SimConfig
from prayas.sim.generate import generate

TENANT = "t_em"


@pytest.fixture(scope="module")
def scored() -> tuple[CauseEM, list[Observation], list[str]]:
    """One fitted model and its ground truth, shared across the module."""
    population = generate(SimConfig(), seed=2026, tenant_id=TENANT, cycles=25_000)
    observations, truth = build_observations(population, build_nowcast(population))
    return CauseEM().fit(observations), observations, truth


def _05_subset(
    observations: list[Observation], truth: list[str]
) -> tuple[list[CauseContext], list[str]]:
    index = [
        i
        for i, obs in enumerate(observations)
        if obs.decline_code == "05" and truth[i] in COMPONENTS
    ]
    return [observations[i].context for i in index], [truth[i] for i in index]


# ── the fit ────────────────────────────────────────────────────────────────


def test_em_converges(scored: tuple[CauseEM, list[Observation], list[str]]) -> None:
    model, _, _ = scored
    fit = model.fit_result
    assert 1 < fit.iterations < 200, "EM either did not move or never settled"
    assert np.isfinite(fit.log_likelihood)
    assert fit.weights.sum() == pytest.approx(1.0)
    assert (fit.weights > 0).all()


def test_the_likelihood_increases_monotonically() -> None:
    """EM's defining guarantee. If this fails the M-step is wrong, and every
    number downstream is describing a different algorithm."""
    population = generate(SimConfig(), seed=5, tenant_id=TENANT, cycles=4000)
    observations, _ = build_observations(population, build_nowcast(population))

    likelihoods = [
        CauseEM(max_iterations=n).fit(observations).fit_result.log_likelihood for n in (2, 4, 8, 16)
    ]
    for earlier, later in pairwise(likelihoods):
        assert later >= earlier - 1e-6, f"log-likelihood fell: {earlier} -> {later}"


def test_anchored_rows_keep_their_stated_cause() -> None:
    """§20: "initialised from the deterministic rules". Unanchored, EM finds
    whatever four clusters fit and no component means what its name says."""
    contexts = [CauseContext(amount_ratio=1.0 + i * 0.01) for i in range(200)]
    observations = [
        Observation("51", contexts[i], recovered=True, recovery_lag_hours=40.0) for i in range(100)
    ] + [
        Observation("91", contexts[i], recovered=True, recovery_lag_hours=0.2)
        for i in range(100, 200)
    ]
    model = CauseEM().fit(observations)

    # The component that owns the 51 rows must be `no_funds`, not whichever
    # index EM happened to settle on.
    assert model.predict([contexts[0]])[0] == NO_FUNDS


def test_serving_never_consumes_the_outcome(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    """Train with the outcome, serve without it.

    The outcome exists at training time and does not exist at decision time —
    the whole point is to decide *before* retrying. A prediction that changed
    when the outcome changed would be a train/serve mismatch, which is how a
    model posts excellent offline numbers and fails in production (§43).
    """
    model, observations, _ = scored
    context = observations[0].context
    assert np.array_equal(model.predict_proba([context]), model.predict_proba([context]))

    # `predict_proba` takes contexts, so there is no channel for an outcome to
    # arrive through — the guarantee is structural, and this pins it.
    with pytest.raises((TypeError, AttributeError)):
        model.predict_proba([observations[0]])  # type: ignore[list-item]


def test_posteriors_are_probability_distributions(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    model, observations, _ = scored
    posteriors = model.predict_proba([o.context for o in observations[:500]])
    assert posteriors.shape == (500, len(COMPONENTS))
    assert np.allclose(posteriors.sum(axis=1), 1.0)
    assert (posteriors >= 0).all()


def test_an_unfitted_model_refuses_to_predict() -> None:
    with pytest.raises(RuntimeError, match="not fitted"):
        CauseEM().predict([CauseContext()])


def test_too_few_observations_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one observation per component"):
        CauseEM().fit([Observation("05", CauseContext())])


# ── the artifact ───────────────────────────────────────────────────────────


def test_em_beats_the_majority_class_on_code_05(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    """**Phase 11 exit criterion.** The bar is the majority class, not V0.

    §20 calls 05 "the least informative signal in payments". Guessing its
    largest component is the honest floor: any model that cannot clear it has
    added nothing, however good its confusion matrix looks.
    """
    model, observations, truth = scored
    contexts, gold = _05_subset(observations, truth)
    assert len(gold) > 1000, "too few 05 rows to characterise"

    majority = Counter(gold).most_common(1)[0]
    floor = majority[1] / len(gold)

    predicted = model.predict(contexts)
    accuracy = sum(1 for a, b in zip(gold, predicted, strict=True) if a == b) / len(gold)

    assert accuracy > floor + 0.05, (
        f"EM scored {accuracy:.1%} against a majority-class floor of {floor:.1%}"
    )


def test_em_beats_the_v0_heuristic_on_code_05(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    model, observations, truth = scored
    contexts, gold = _05_subset(observations, truth)

    def accuracy(predicted: list[str]) -> float:
        return sum(1 for a, b in zip(gold, predicted, strict=True) if a == b) / len(gold)

    heuristic = accuracy([infer("05", c).most_likely for c in contexts])
    learned = accuracy(model.predict(contexts))
    assert learned > heuristic, f"EM {learned:.1%} did not beat the heuristic {heuristic:.1%}"


def test_em_recovers_a_cause_the_heuristic_never_predicts(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    """The concrete win: `fraud_hold` behind an 05 is invisible to V0's weights,
    and recoverable from §20's `sibling_failure_rate` once it carries signal."""
    model, observations, truth = scored
    contexts, gold = _05_subset(observations, truth)

    heuristic = Counter(infer("05", c).most_likely for c in contexts)
    learned = Counter(model.predict(contexts))
    assert heuristic[FRAUD_HOLD] == 0, "the heuristic is no longer the baseline described"
    assert learned[FRAUD_HOLD] > 0

    matrix = confusion_matrix(gold, model.predict(contexts))
    row = COMPONENTS.index(FRAUD_HOLD)
    recall = matrix[row, row] / matrix[row].sum()
    assert recall > 0.2, f"fraud_hold recall {recall:.1%} is not a recovery"


def test_the_confusion_matrix_is_well_formed(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    model, observations, truth = scored
    contexts, gold = _05_subset(observations, truth)
    matrix = confusion_matrix(gold, model.predict(contexts))

    assert matrix.shape == (len(COMPONENTS), len(COMPONENTS))
    assert matrix.sum() == len(gold)
    assert (matrix >= 0).all()

    rendered = render_confusion(matrix)
    for cause in (NO_FUNDS, ISSUER_DEGRADED, FRAUD_HOLD, LIMIT_BREACH):
        assert cause in rendered
    assert "accuracy" in rendered and "precision" in rendered


def test_confusion_matrix_rejects_misaligned_inputs() -> None:
    with pytest.raises(ValueError, match="same length"):
        confusion_matrix([NO_FUNDS], [NO_FUNDS, FRAUD_HOLD])


def test_every_component_is_present_in_the_05_population(
    scored: tuple[CauseEM, list[Observation], list[str]],
) -> None:
    """ADR-065's precondition, asserted where it matters.

    If 05 collapses back to one component, every number above is measuring
    something easier than §20 describes and the artifact proves nothing.
    """
    _, observations, truth = scored
    _, gold = _05_subset(observations, truth)
    present = Counter(gold)
    for cause in COMPONENTS:
        assert present[cause] > 0, f"{cause} absent from the 05 bucket"
    assert present[NO_FUNDS] / len(gold) < 0.75, "05 is degenerating toward one component"
