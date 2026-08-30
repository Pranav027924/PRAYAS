"""Cross-tenant aggregates and feature parity (Master Spec §27, §43).

Two Phase 12 exit criteria live here: "aggregates enforce minimum cohort size"
and "feature parity job detects injected skew".
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.memory.aggregate import (
    MIN_COHORT,
    MIN_CONTRIBUTORS,
    AggregateError,
    SegmentKey,
    SegmentObservation,
    aggregate,
)
from prayas.memory.parity import ParityError, check_parity

KEY = SegmentKey(mcc="5411", ticket_band="small", rail="upi_autopay", day_of_month=1, hour_band=0)


def _obs(tenant: str, attempts: int, successes: int, key: SegmentKey = KEY) -> SegmentObservation:
    return SegmentObservation(key=key, tenant_id=tenant, attempts=attempts, successes=successes)


# ── k-anonymity (§27) ──────────────────────────────────────────────────────


def test_a_thin_cell_is_withheld() -> None:
    """**Phase 12 exit criterion.** Below the cohort floor, nothing is published."""
    published, withheld = aggregate([_obs("t1", 10, 4), _obs("t2", 10, 5), _obs("t3", 10, 6)])
    assert published == []
    assert withheld == [KEY]


def test_a_dense_multi_tenant_cell_is_published() -> None:
    published, withheld = aggregate([_obs("t1", 40, 20), _obs("t2", 40, 18), _obs("t3", 40, 22)])
    assert withheld == []
    assert len(published) == 1
    assert published[0].n_obs == 120
    assert published[0].contributors == 3
    assert published[0].hazard == pytest.approx(60 / 120)


def test_one_tenant_alone_is_not_anonymous_however_many_rows() -> None:
    """ADR-079. A cell sourced from a single tenant is that tenant's data
    wearing an aggregate's name, and publishing it to the others is exactly the
    leak §27 forbids."""
    published, withheld = aggregate([_obs("t1", 100_000, 50_000)])
    assert published == []
    assert withheld == [KEY]


def test_the_contributor_floor_is_load_bearing() -> None:
    """Just under and just over, so the boundary is asserted rather than assumed."""
    per_tenant = MIN_COHORT
    just_under = [_obs(f"t{i}", per_tenant, 1) for i in range(MIN_CONTRIBUTORS - 1)]
    just_over = [_obs(f"t{i}", per_tenant, 1) for i in range(MIN_CONTRIBUTORS)]

    assert aggregate(just_under)[0] == []
    assert len(aggregate(just_over)[0]) == 1


def test_the_cohort_floor_is_load_bearing() -> None:
    under = [_obs(f"t{i}", (MIN_COHORT - 1) // 3, 1) for i in range(3)]
    over = [_obs(f"t{i}", MIN_COHORT, 1) for i in range(3)]

    assert aggregate(under)[0] == []
    assert len(aggregate(over)[0]) == 1


def test_withheld_cells_are_reported_not_dropped() -> None:
    """ "No prior for this segment" and "suppressed for k-anonymity" are
    different operational facts, and only one means the pipeline works."""
    other = SegmentKey("5812", "mid", "enach", 15, 2)
    published, withheld = aggregate(
        [_obs("t1", 40, 20), _obs("t2", 40, 20), _obs("t3", 40, 20), _obs("t1", 5, 1, other)]
    )
    assert len(published) == 1
    assert withheld == [other]


def test_a_published_cell_carries_no_tenant() -> None:
    """`tenant_id` is counted and discarded — §27's whole construction."""
    published, _ = aggregate([_obs(f"t{i}", 40, 20) for i in range(3)])
    assert not hasattr(published[0], "tenant_id")
    assert "tenant" not in str(published[0].key).lower()


@pytest.mark.parametrize(
    ("attempts", "successes"),
    [(-1, 0), (10, -1), (5, 10)],
)
def test_incoherent_counts_are_refused(attempts: int, successes: int) -> None:
    with pytest.raises(AggregateError):
        aggregate([_obs("t1", attempts, successes)])


# ── feature parity (§43) ───────────────────────────────────────────────────


NAMES = ("payday_density", "amount_ratio", "hour_rate")


def test_identical_paths_report_no_divergence() -> None:
    rng = np.random.default_rng(0)
    matrix = rng.normal(size=(500, 3))
    report = check_parity(matrix, matrix.copy(), NAMES)
    assert not report.alerting
    assert report.rows_compared == 500
    assert "ok" in report.render()


def test_injected_skew_is_detected() -> None:
    """**Phase 12 exit criterion.** §43's threshold is "any divergence"."""
    rng = np.random.default_rng(0)
    offline = rng.normal(size=(500, 3))
    online = offline.copy()
    online[17, 1] *= 1.01  # one row, one feature, one percent

    report = check_parity(offline, online, NAMES)
    assert report.alerting
    assert report.worst is not None and report.worst.name == "amount_ratio"
    assert "amount_ratio" in report.render()


def test_a_one_percent_drift_is_not_close_enough() -> None:
    """A feature differing by 1% between training and serving is not "close" —
    it is two features with one name."""
    offline = np.ones((10, 3))
    online = offline.copy()
    online[:, 0] *= 1.01
    assert check_parity(offline, online, NAMES).alerting


def test_the_diagnosis_separates_a_transform_drift_from_a_bad_snapshot() -> None:
    """An operator woken at 03:00 should not have to derive this from a table."""
    offline = np.ones((10, 3))

    one_bad = offline.copy()
    one_bad[:, 2] *= 2.0
    assert "definition drift" in check_parity(offline, one_bad, NAMES).diagnosis

    all_bad = offline * 3.0
    assert "wrong snapshot" in check_parity(offline, all_bad, NAMES).diagnosis


def test_a_zero_feature_does_not_register_as_infinite_error() -> None:
    matrix = np.zeros((10, 3))
    assert not check_parity(matrix, matrix.copy(), NAMES).alerting


def test_misaligned_inputs_are_refused() -> None:
    """An unaligned comparison reports skew everywhere and means nothing."""
    with pytest.raises(ParityError, match="shapes differ"):
        check_parity(np.ones((10, 3)), np.ones((9, 3)), NAMES)


def test_an_empty_check_is_not_a_passing_check() -> None:
    with pytest.raises(ParityError, match="no rows"):
        check_parity(np.ones((0, 3)), np.ones((0, 3)), NAMES)


def test_feature_names_must_match_the_matrix() -> None:
    with pytest.raises(ParityError, match="feature names"):
        check_parity(np.ones((5, 3)), np.ones((5, 3)), ("only_one",))
