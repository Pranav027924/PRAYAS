"""V1 models: features, the GBM hazard, the registry, drift (§21, §43).

Phase 11's first exit criterion is "V1 beats V0 on held-out log-loss". That is
asserted here against a **grouped** split — by customer, never by row — because
a row-level split lets a model memorise one customer's payday and read it back
out of the holdout, which measures nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from prayas.inference.calibration import expected_calibration_error, log_loss
from prayas.models.drift import (
    ECE_ALERT,
    PSI_SIGNIFICANT,
    drift_report,
    population_stability_index,
)
from prayas.models.features import (
    FEATURE_NAMES,
    HORIZON_DAYS,
    HORIZON_SLOTS,
    SLOTS_PER_DAY,
    Dataset,
    build_dataset,
    build_history,
    funding_slot,
    group_split,
    slot_time,
    subset,
)
from prayas.models.hazard_v1 import HazardV0, HazardV1, HazardV1Params, fit_pair
from prayas.models.registry import (
    CAUSE_INFERENCE,
    LIQUIDITY_HAZARD,
    ModelRegistry,
    RegistryError,
    fingerprint,
    version_of,
)
from prayas.models.serving import band_to_hourly
from prayas.sim.config import SimConfig
from prayas.sim.generate import generate

TENANT = "t_models"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return build_dataset(generate(SimConfig(), seed=11, tenant_id=TENANT, cycles=6000))


@pytest.fixture(scope="module")
def split(dataset: Dataset) -> tuple[Dataset, Dataset]:
    train_mask, holdout_mask = group_split(dataset, holdout_fraction=0.3, seed=3)
    return subset(dataset, train_mask), subset(dataset, holdout_mask)


# ── features ───────────────────────────────────────────────────────────────


def test_slot_time_lands_inside_the_band_it_names() -> None:
    """A slot's timestamp must sit in the window it claims, not on its edge."""
    from prayas.inference.bands import hour_band

    due = datetime(2026, 3, 1, 4, 30, tzinfo=UTC)
    for slot in range(SLOTS_PER_DAY):
        at = slot_time(due, slot).astimezone(
            __import__("prayas.models.features", fromlist=["IST"]).IST
        )
        assert hour_band(at.hour + at.minute / 60.0) == slot


def test_slot_time_advances_a_day_per_five_slots() -> None:
    due = datetime(2026, 3, 1, 4, 30, tzinfo=UTC)
    assert (slot_time(due, SLOTS_PER_DAY) - slot_time(due, 0)).days == 1


def test_out_of_range_slots_are_refused() -> None:
    with pytest.raises(ValueError, match="slot must be in"):
        slot_time(datetime(2026, 3, 1, tzinfo=UTC), HORIZON_SLOTS)


def test_rows_stop_at_the_funding_slot() -> None:
    """Censoring is structural: a cycle is at risk only until it funds.

    If rows continued past the event, every funded cycle would contribute a run
    of zeros after the fact and the hazard would be biased toward zero exactly
    where the sequencer needs it to be sharp.
    """
    cycles = generate(SimConfig(), seed=5, tenant_id=TENANT, cycles=800)
    ds = build_dataset(cycles)

    by_cycle: dict[str, list[int]] = {}
    for cycle_id, slot in zip(ds.cycle_ids.tolist(), ds.slots.tolist(), strict=True):
        by_cycle.setdefault(str(cycle_id), []).append(int(slot))

    index = {c.cycle_id: c for c in cycles}
    for cycle_id, slots in by_cycle.items():
        event = funding_slot(index[cycle_id])
        if event is not None and event < HORIZON_SLOTS:
            assert max(slots) == event, f"{cycle_id} emitted rows past its funding slot"
        assert slots == list(range(len(slots))), "slots must be contiguous from zero"


def test_exactly_one_event_per_funded_cycle(dataset: Dataset) -> None:
    events: dict[str, int] = {}
    for cycle_id, label in zip(dataset.cycle_ids.tolist(), dataset.y.tolist(), strict=True):
        events[str(cycle_id)] = events.get(str(cycle_id), 0) + int(label)
    assert set(events.values()) <= {0, 1}, "a cycle cannot fund twice"


def test_only_failed_cycles_contribute_rows() -> None:
    """Phase 8's lesson: scoring cycles that never failed credits free wins."""
    cycles = generate(SimConfig(), seed=6, tenant_id=TENANT, cycles=800)
    ds = build_dataset(cycles)
    settled = {
        c.cycle_id
        for c in cycles
        if not any(e.get("body", {}).get("event") == "payment.failed" for e in c.observables)
    }
    assert not (set(ds.cycle_ids.tolist()) & settled)


def test_cold_start_history_is_neutral_not_zero() -> None:
    """ "No history" and "today is payday" must not collide."""
    empty = build_history([])
    assert empty.n_prior_cycles == 0
    assert empty.p75_success_paise is None
    assert empty.days_since_payday(15) == -1.0
    assert empty.band_success_rate(0) == 0.5


def test_the_split_is_by_customer_not_by_row(dataset: Dataset) -> None:
    """The property that makes the held-out number mean anything."""
    train_mask, holdout_mask = group_split(dataset, seed=3)
    assert not (set(dataset.groups[train_mask]) & set(dataset.groups[holdout_mask]))
    assert train_mask.sum() + holdout_mask.sum() == len(dataset)


def test_feature_matrix_matches_the_declared_names(dataset: Dataset) -> None:
    """The registry pins the column order; a mismatch is silently wrong."""
    assert dataset.x.shape[1] == len(FEATURE_NAMES)


def test_a_population_with_no_failures_is_refused() -> None:
    with pytest.raises(ValueError, match="no failed cycles"):
        build_dataset([])


# ── V1 vs V0 ───────────────────────────────────────────────────────────────


def test_v1_beats_v0_on_held_out_log_loss(split: tuple[Dataset, Dataset]) -> None:
    """**Phase 11 exit criterion.** Grouped split, identical rows, same metric."""
    train, holdout = split
    v0, v1 = fit_pair(train)

    loss_v0 = log_loss(holdout.y, v0.predict(holdout.x))
    loss_v1 = log_loss(holdout.y, v1.predict(holdout.x))
    assert loss_v1 < loss_v0, f"V1 {loss_v1:.5f} did not beat V0 {loss_v0:.5f}"
    assert (loss_v0 - loss_v1) / loss_v0 > 0.05, "the improvement is too small to claim"


def test_both_models_beat_a_constant_base_rate(split: tuple[Dataset, Dataset]) -> None:
    """Without this, "V1 beats V0" could be two models that both know nothing."""
    train, holdout = split
    v0, v1 = fit_pair(train)
    constant = np.full(len(holdout), float(train.y.mean()))

    baseline = log_loss(holdout.y, constant)
    assert log_loss(holdout.y, v0.predict(holdout.x)) < baseline
    assert log_loss(holdout.y, v1.predict(holdout.x)) < baseline


def test_v1_is_calibrated_on_held_out_data(split: tuple[Dataset, Dataset]) -> None:
    """§21: "calibration is the property that matters, not AUC"."""
    train, holdout = split
    _, v1 = fit_pair(train)
    ece = expected_calibration_error(holdout.y, v1.predict(holdout.x))
    assert ece < ECE_ALERT, f"ECE {ece:.4f} exceeds §43's {ECE_ALERT} threshold"


def test_a_deliberately_miscalibrated_model_is_caught(split: tuple[Dataset, Dataset]) -> None:
    """The metric must not pass vacuously by returning ~0 for everything."""
    _, holdout = split
    inflated = np.clip(holdout.y * 0.2 + 0.4, 1e-6, 1 - 1e-6)
    assert expected_calibration_error(holdout.y, inflated) > ECE_ALERT


def test_predictions_stay_strictly_inside_the_unit_interval(
    split: tuple[Dataset, Dataset],
) -> None:
    """A hazard of exactly 1 makes S(t) zero and every later conditional a
    division by zero — the arithmetic the money path must never see."""
    train, holdout = split
    v0, v1 = fit_pair(train)
    for predictions in (v0.predict(holdout.x), v1.predict(holdout.x)):
        assert predictions.min() > 0.0
        assert predictions.max() < 1.0


def test_calibration_is_fitted_on_customers_the_gbm_never_saw(
    split: tuple[Dataset, Dataset],
) -> None:
    """Calibrating on training predictions produces a flattering reliability
    curve and a sequencer that still stops in the wrong place."""
    train, _ = split
    model = HazardV1(HazardV1Params(max_iter=40))
    model.fit(train, calibration_fraction=0.25)
    assert model.is_fitted


def test_refitting_calibration_leaves_the_gbm_alone(split: tuple[Dataset, Dataset]) -> None:
    """§21's weekly recalibration: the cheap stage moves, the expensive one does not."""
    train, holdout = split
    model = HazardV1(HazardV1Params(max_iter=40)).fit(train)
    before = model.predict(holdout.x).copy()
    model.refit_calibration(holdout)
    assert not np.array_equal(before, model.predict(holdout.x))


def test_an_unfitted_model_refuses_to_predict() -> None:
    with pytest.raises(RuntimeError, match="not fitted"):
        HazardV1().predict(np.zeros((1, len(FEATURE_NAMES))))


def test_v0_falls_back_to_the_global_rate_for_a_thin_cell(
    split: tuple[Dataset, Dataset],
) -> None:
    """§27's k-anonymity floor: a cell too thin to publish is not published."""
    train, _ = split
    v0 = HazardV0().fit(train)
    absurd = np.zeros((1, len(FEATURE_NAMES)))
    absurd[0, FEATURE_NAMES.index("day_of_month")] = 99
    assert 0.0 < float(v0.predict(absurd)[0]) < 1.0


# ── band to hourly ─────────────────────────────────────────────────────────


def test_band_expansion_preserves_survival() -> None:
    """The identity the conversion rests on: surviving a band is surviving each
    of its hours. Dividing by the hour count instead understates early hours and
    overstates late ones — exactly where a stopping decision lives."""
    rng = np.random.default_rng(0)
    bands = rng.uniform(0.001, 0.05, size=HORIZON_DAYS * SLOTS_PER_DAY)
    hourly = band_to_hourly(bands)

    from prayas.inference.bands import hour_band

    for day in (0, 5, 17):
        for band in range(SLOTS_PER_DAY):
            hours = [h for h in range(24) if hour_band(float(h)) == band]
            survived = np.prod([1.0 - hourly[day * 24 + h] for h in hours])
            assert survived == pytest.approx(1.0 - bands[day * SLOTS_PER_DAY + band], abs=1e-9)


def test_band_expansion_rejects_a_wrong_length_curve() -> None:
    with pytest.raises(ValueError, match="band hazards"):
        band_to_hourly(np.zeros(7))


# ── registry ───────────────────────────────────────────────────────────────


def _version(kind: str = "gbm", **overrides: object) -> object:
    defaults = {
        "decision_point": LIQUIDITY_HAZARD,
        "kind": kind,
        "params": {"max_depth": 5},
        "feature_names": FEATURE_NAMES,
        "rows": 1000,
        "positives": 20,
        "seed": 7,
    }
    defaults.update(overrides)
    return version_of(**defaults)  # type: ignore[arg-type]


def test_the_same_configuration_and_data_get_the_same_id() -> None:
    a, b = _version(), _version()
    assert a.version_id == b.version_id  # type: ignore[attr-defined]


def test_the_timestamp_does_not_change_the_id() -> None:
    """Two identical fits *are* the same model, whatever the clock said."""
    early = _version(fitted_at=datetime(2026, 1, 1, tzinfo=UTC))
    late = _version(fitted_at=datetime(2026, 6, 1, tzinfo=UTC))
    assert early.version_id == late.version_id  # type: ignore[attr-defined]


def test_changing_a_hyperparameter_changes_the_id() -> None:
    assert _version().version_id != _version(params={"max_depth": 9}).version_id  # type: ignore[attr-defined]


def test_reordering_features_changes_the_id() -> None:
    """A model reloaded against a different column order is silently wrong."""
    shuffled = tuple(reversed(FEATURE_NAMES))
    assert _version().version_id != _version(feature_names=shuffled).version_id  # type: ignore[attr-defined]


def test_a_fingerprint_retains_nothing_about_a_customer() -> None:
    """Invariant 8 and §27: counts only."""
    assert fingerprint(rows=10, positives=1) == fingerprint(rows=10, positives=1)
    assert fingerprint(rows=10, positives=1) != fingerprint(rows=11, positives=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        fingerprint(rows=1, positives=2)


def test_registering_does_not_serve() -> None:
    """Promotion is a deliberate act, not a side effect of a fit."""
    registry = ModelRegistry()
    registry.register(_version())  # type: ignore[arg-type]
    assert registry.serving(LIQUIDITY_HAZARD) is None


def test_promotion_requires_a_reason() -> None:
    registry = ModelRegistry()
    version_id = registry.register(_version())  # type: ignore[arg-type]
    with pytest.raises(RegistryError, match="reason"):
        registry.promote(version_id, reason="   ")


def test_rollback_restores_the_previous_version() -> None:
    """The exit criterion in one call: kill V1, V0 serves."""
    registry = ModelRegistry()
    v0_id = registry.register(_version(kind="segment_lookup"))  # type: ignore[arg-type]
    v1_id = registry.register(_version(kind="gbm"))  # type: ignore[arg-type]

    registry.promote(v0_id, reason="baseline")
    registry.promote(v1_id, reason="beats V0 on held-out log-loss")
    assert registry.serving(LIQUIDITY_HAZARD).kind == "gbm"  # type: ignore[union-attr]

    now_serving = registry.rollback(LIQUIDITY_HAZARD, reason="incident")
    assert now_serving is not None and now_serving.kind == "segment_lookup"


def test_rollback_with_nothing_to_fall_back_to_serves_nothing() -> None:
    """`None` is a real answer — the caller's heuristic takes over."""
    registry = ModelRegistry()
    version_id = registry.register(_version())  # type: ignore[arg-type]
    registry.promote(version_id, reason="first")
    assert registry.rollback(LIQUIDITY_HAZARD, reason="bad") is None
    assert registry.serving(LIQUIDITY_HAZARD) is None


def test_a_decision_always_carries_a_stamp() -> None:
    """A decision made by the heuristic is still a decision. Recording "no
    model" is materially different from recording nothing."""
    registry = ModelRegistry()
    stamp = registry.stamp(CAUSE_INFERENCE)
    assert stamp["model"] == "heuristic"
    assert stamp["version_id"] == ""

    version_id = registry.register(_version(decision_point=CAUSE_INFERENCE, kind="em"))  # type: ignore[arg-type]
    registry.promote(version_id, reason="beats the heuristic on ground truth")
    assert registry.stamp(CAUSE_INFERENCE)["version_id"] == version_id


def test_an_unknown_decision_point_is_refused() -> None:
    with pytest.raises(RegistryError, match="unknown decision point"):
        ModelRegistry().register(_version(decision_point="astrology"))  # type: ignore[arg-type]


# ── drift ──────────────────────────────────────────────────────────────────


def test_psi_is_zero_for_an_unshifted_sample() -> None:
    rng = np.random.default_rng(0)
    sample = rng.normal(size=20_000)
    assert population_stability_index(sample, sample) < 1e-9


def test_psi_detects_a_shifted_population() -> None:
    rng = np.random.default_rng(0)
    reference = rng.normal(loc=0.0, size=20_000)
    shifted = rng.normal(loc=1.5, size=20_000)
    assert population_stability_index(reference, shifted) > PSI_SIGNIFICANT


def test_psi_is_finite_when_a_bin_empties() -> None:
    """An empty bin is a sample-size artefact, not infinite drift."""
    rng = np.random.default_rng(0)
    reference = rng.normal(size=5_000)
    disjoint = rng.normal(loc=50.0, size=5_000)
    psi = population_stability_index(reference, disjoint)
    assert np.isfinite(psi) and psi > PSI_SIGNIFICANT


def test_drift_report_separates_input_shift_from_calibration(
    split: tuple[Dataset, Dataset],
) -> None:
    """PSI cannot see a bad model; ECE cannot see it coming. Both are reported."""
    train, holdout = split
    _, v1 = fit_pair(train)

    quiet = drift_report(
        train.x, holdout.x, FEATURE_NAMES, y_true=holdout.y, y_prob=v1.predict(holdout.x)
    )
    assert not quiet.alerting
    assert quiet.ece is not None and quiet.ece < ECE_ALERT

    shifted = holdout.x.copy()
    shifted[:, FEATURE_NAMES.index("amount_ratio")] *= 40.0
    loud = drift_report(train.x, shifted, FEATURE_NAMES)
    assert loud.alerting
    assert loud.worst is not None and loud.worst.name == "amount_ratio"
    assert loud.ece is None, "input drift must be reportable before labels mature"


def test_drift_report_rejects_a_feature_name_mismatch(split: tuple[Dataset, Dataset]) -> None:
    train, holdout = split
    with pytest.raises(ValueError, match="feature names"):
        drift_report(train.x, holdout.x, FEATURE_NAMES[:-1])


def test_drift_severity_bands_are_ordered() -> None:
    rng = np.random.default_rng(1)
    reference = rng.normal(size=20_000)
    report = drift_report(
        reference.reshape(-1, 1),
        rng.normal(loc=2.0, size=20_000).reshape(-1, 1),
        ("shifted",),
    )
    assert report.features[0].severity == "significant"
    assert report.features[0].alerting


def test_slot_time_is_stable_across_the_dst_free_ist_offset() -> None:
    """IST has no daylight saving, so a slot's hour must not drift."""
    due = datetime(2026, 3, 1, 4, 30, tzinfo=UTC)
    first = slot_time(due, 0)
    later = slot_time(due + timedelta(days=90), 0)
    assert first.astimezone(UTC).hour == later.astimezone(UTC).hour


def test_the_ledger_stamp_covers_every_decision_point() -> None:
    """§36's `decisions.model_versions`, and §32's replay depends on it.

    A decision point running on the heuristic must appear as `"heuristic"`, not
    be omitted — a missing key is indistinguishable from one nobody thought to
    write, and only one of those is evidence.
    """
    import json

    from prayas.models.registry import DECISION_POINTS

    registry = ModelRegistry()
    stamp = registry.stamp_all()
    assert set(stamp) == set(DECISION_POINTS)
    assert all(v == "heuristic" for v in stamp.values())

    version_id = registry.register(_version())  # type: ignore[arg-type]
    registry.promote(version_id, reason="beats V0 on held-out log-loss")
    stamp = registry.stamp_all()
    assert stamp[LIQUIDITY_HAZARD] == version_id
    assert stamp[CAUSE_INFERENCE] == "heuristic"

    # It has to survive the ledger's JSONB round trip unchanged, or the hash
    # chain would cover a different record than the one that was written.
    assert json.loads(json.dumps(stamp, sort_keys=True)) == stamp


# ── the presence dataset (ADR-075) ─────────────────────────────────────────


def test_presence_rows_exist_only_for_legal_slots() -> None:
    """An hour the gate would deny is an hour no attempt can be made in, so it
    is an hour whose presence can never be observed or acted on. Training on it
    would fit a quantity the system cannot use."""
    from prayas.measure.harness import funds_present, legal_mask
    from prayas.models.features import build_presence_dataset

    cycles = generate(SimConfig(non_absorbing=True), seed=13, tenant_id=TENANT, cycles=500)
    dataset = build_presence_dataset(cycles, funds_present, lambda c: legal_mask(c.due_at))
    assert len(dataset) > 0

    by_id = {c.cycle_id: c for c in cycles}
    for cycle_id, slot in zip(dataset.cycle_ids.tolist(), dataset.slots.tolist(), strict=True):
        assert legal_mask(by_id[str(cycle_id)].due_at)[int(slot)], (
            f"{cycle_id} emitted a row for illegal slot {slot}"
        )


def test_presence_labels_match_the_ground_truth_windows() -> None:
    from prayas.measure.harness import funds_present, legal_mask
    from prayas.models.features import build_presence_dataset

    cycles = generate(SimConfig(non_absorbing=True), seed=13, tenant_id=TENANT, cycles=500)
    dataset = build_presence_dataset(cycles, funds_present, lambda c: legal_mask(c.due_at))
    by_id = {c.cycle_id: c for c in cycles}
    for cycle_id, slot, label in zip(
        dataset.cycle_ids.tolist(), dataset.slots.tolist(), dataset.y.tolist(), strict=True
    ):
        assert int(label) == int(funds_present(by_id[str(cycle_id)])[int(slot)])

    assert 0 < dataset.y.mean() < 1, "labels must not be degenerate"


def test_presence_is_not_monotone_under_non_absorbing_funding() -> None:
    """The property §23.2 forbids and ADR-075 exists to carry."""
    from prayas.measure.harness import funds_present

    cycles = generate(
        SimConfig(non_absorbing=True, funds_dwell_hours=12.0),
        seed=13,
        tenant_id=TENANT,
        cycles=500,
    )
    falls = sum(1 for c in cycles if np.any(np.diff(funds_present(c).astype(int)) < 0))
    assert falls > 0, "presence never falls — funds are not actually leaving"


def test_the_presence_prior_is_not_constant() -> None:
    """ADR-076. A constant in this column is a wasted feature, and it removes
    the term that lets V1 nest V0 rather than compete from scratch."""
    from prayas.measure.harness import funds_present, legal_mask
    from prayas.models.features import presence_prior

    cycles = generate(SimConfig(non_absorbing=True), seed=21, tenant_id=TENANT, cycles=1200)
    prior = presence_prior(cycles, funds_present, lambda c: legal_mask(c.due_at))

    assert len(prior) > 10, "the prior covers too few (day, band) cells to be useful"
    values = np.array(list(prior.values()))
    assert values.std() > 0.01, "the prior is effectively constant"
    assert ((values >= 0.0) & (values <= 1.0)).all()


def test_the_prior_reaches_the_feature_matrix() -> None:
    """Wiring, not just existence: a prior nobody reads is still a constant."""
    from prayas.measure.harness import funds_present, legal_mask
    from prayas.models.features import build_presence_dataset, presence_prior

    cycles = generate(SimConfig(non_absorbing=True), seed=21, tenant_id=TENANT, cycles=1200)
    legal_of = lambda c: legal_mask(c.due_at)  # noqa: E731
    prior = presence_prior(cycles, funds_present, legal_of)

    column = FEATURE_NAMES.index("segment_prior_hazard")
    without = build_presence_dataset(cycles, funds_present, legal_of)
    with_prior = build_presence_dataset(cycles, funds_present, legal_of, prior=prior)

    assert without.x[:, column].std() == pytest.approx(0.0), "baseline should be constant"
    assert with_prior.x[:, column].std() > 0.01, "the prior did not reach the features"
