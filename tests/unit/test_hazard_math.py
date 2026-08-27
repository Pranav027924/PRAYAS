"""Hazard mathematics (Master Spec §21; ADR-032, ADR-034).

The conditional `p(t|t_last)` gets the most attention here because §21 says it is
"the subtlety most implementations miss" and the modelling rules call it
"the most likely error in the whole project". Every value below is hand-computed
from the definitions, not from the implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.inference.bands import (
    HOUR_BAND_AFTERNOON,
    HOUR_BAND_LATE,
    HOUR_BAND_PEAK_EVENING,
    HOUR_BAND_PEAK_MORNING,
    HOUR_BAND_PRE_10,
    TICKET_BAND_JUMBO,
    TICKET_BAND_LARGE,
    TICKET_BAND_MICRO,
    TICKET_BAND_MID,
    TICKET_BAND_SMALL,
    hour_band,
    is_legal_upi_band,
    ticket_band,
)
from prayas.inference.calibration import (
    ECE_ALERT_THRESHOLD,
    brier_score,
    expected_calibration_error,
    is_calibrated,
    log_loss,
    reliability_curve,
)
from prayas.inference.hazard import (
    HazardTable,
    SegmentKey,
    conditional_curve,
    conditional_probability,
    marginal_probability,
    shrink,
    survival,
    uniform_hazards,
)

# ── bands: both sides of every edge (§40.2) ────────────────────────────────


@pytest.mark.parametrize(
    ("hour_ist", "expected"),
    [
        (0.0, HOUR_BAND_PRE_10),
        (9.9972, HOUR_BAND_PRE_10),  # 09:59:50
        (10.0, HOUR_BAND_PEAK_MORNING),  # the cliff
        (12.9972, HOUR_BAND_PEAK_MORNING),
        (13.0, HOUR_BAND_AFTERNOON),
        (16.9972, HOUR_BAND_AFTERNOON),
        (17.0, HOUR_BAND_PEAK_EVENING),
        (21.4972, HOUR_BAND_PEAK_EVENING),
        (21.5, HOUR_BAND_LATE),
        (23.99, HOUR_BAND_LATE),
    ],
)
def test_hour_band_edges(hour_ist: float, expected: int) -> None:
    assert hour_band(hour_ist) == expected


@pytest.mark.parametrize("hour_ist", [-0.1, 24.0, 25.0])
def test_hour_band_rejects_out_of_range(hour_ist: float) -> None:
    with pytest.raises(ValueError, match="hour_ist"):
        hour_band(hour_ist)


def test_legal_bands_are_exactly_the_non_peak_windows() -> None:
    """§1 — before 10:00, 13:00-17:00, after 21:30."""
    assert is_legal_upi_band(HOUR_BAND_PRE_10)
    assert is_legal_upi_band(HOUR_BAND_AFTERNOON)
    assert is_legal_upi_band(HOUR_BAND_LATE)
    assert not is_legal_upi_band(HOUR_BAND_PEAK_MORNING)
    assert not is_legal_upi_band(HOUR_BAND_PEAK_EVENING)


@pytest.mark.parametrize(
    ("paise", "expected"),
    [
        (0, TICKET_BAND_MICRO),
        (99_999, TICKET_BAND_MICRO),  # ₹999.99
        (100_000, TICKET_BAND_SMALL),  # ₹1,000 — the cliff
        (499_999, TICKET_BAND_SMALL),
        (500_000, TICKET_BAND_MID),  # ₹5,000
        (1_499_999, TICKET_BAND_MID),  # ₹14,999.99
        (1_500_000, TICKET_BAND_LARGE),  # ₹15,000 — the AFA ceiling
        (9_999_999, TICKET_BAND_LARGE),
        (10_000_000, TICKET_BAND_JUMBO),  # ₹1,00,000
    ],
)
def test_ticket_band_edges(paise: int, expected: int) -> None:
    assert ticket_band(paise) == expected


def test_ticket_band_rejects_float_amounts() -> None:
    """Money is integer paise, never float (project standard)."""
    with pytest.raises(TypeError, match="integer paise"):
        ticket_band(1500.0)  # type: ignore[arg-type]


def test_ticket_band_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ticket_band(-1)


# ── survival ───────────────────────────────────────────────────────────────


def test_survival_is_the_running_product_of_non_events() -> None:
    """S(t) = prod(1 - h(u)) for u <= t. Hand-computed."""
    s = survival([0.1, 0.2, 0.3])

    assert s[0] == pytest.approx(0.9)
    assert s[1] == pytest.approx(0.9 * 0.8)  # 0.72
    assert s[2] == pytest.approx(0.9 * 0.8 * 0.7)  # 0.504


def test_survival_is_monotonically_non_increasing() -> None:
    """An account cannot become less likely to have been funded over time."""
    s = survival([0.05, 0.4, 0.02, 0.6, 0.1])
    assert np.all(np.diff(s) <= 0)


def test_zero_hazards_leave_survival_at_one() -> None:
    s = survival([0.0, 0.0, 0.0])
    assert np.allclose(s, 1.0, atol=1e-5)


@pytest.mark.parametrize("bad", [[], [[0.1, 0.2]], [1.5], [-0.1]])
def test_survival_rejects_malformed_input(bad: object) -> None:
    with pytest.raises(ValueError):
        survival(bad)  # type: ignore[arg-type]


# ── the conditional: §21's subtlety ────────────────────────────────────────


def test_conditional_is_hand_computable() -> None:
    """p(t|t_last) = 1 - S(t)/S(t_last), computed from the definition.

    h = [0.1, 0.2, 0.3] -> S = [0.9, 0.72, 0.504]
    p(2 | 0) = 1 - 0.504/0.9   = 0.44
    p(2 | 1) = 1 - 0.504/0.72  = 0.30
    p(1 | 0) = 1 - 0.72/0.9    = 0.20
    """
    h = [0.1, 0.2, 0.3]

    assert conditional_probability(h, 2, 0) == pytest.approx(0.44)
    assert conditional_probability(h, 2, 1) == pytest.approx(0.30)
    assert conditional_probability(h, 1, 0) == pytest.approx(0.20)


def test_conditional_and_marginal_genuinely_differ() -> None:
    """The error §21 warns about, made explicit.

    Using the marginal after an observed failure overstates the probability,
    because it never discounts the evidence that the account was unfunded at
    t_last. That produces systematically wrong expected rupees and therefore
    systematically wrong stopping points.
    """
    h = [0.1, 0.2, 0.3]

    marginal = marginal_probability(h, 2)  # 1 - 0.504 = 0.496
    conditional = conditional_probability(h, 2, 0)  # 0.44

    assert marginal == pytest.approx(0.496)
    assert conditional == pytest.approx(0.44)
    assert marginal > conditional, "the marginal must overstate; if not, the maths moved"


def test_conditioning_later_raises_the_remaining_probability() -> None:
    """Later evidence of failure discounts more of the curve, so what remains
    is renormalised upward relative to a shorter conditioning window."""
    h = [0.3, 0.3, 0.3, 0.3]

    from_zero = conditional_probability(h, 3, 0)
    from_two = conditional_probability(h, 3, 2)

    # Conditioning on more observed failure leaves a shorter, less likely span.
    assert from_zero > from_two


def test_conditional_at_or_before_t_last_is_rejected() -> None:
    """Not a forecast — it contradicts something already observed."""
    h = [0.1, 0.2, 0.3]

    with pytest.raises(ValueError, match="strictly after"):
        conditional_probability(h, 1, 1)
    with pytest.raises(ValueError, match="strictly after"):
        conditional_probability(h, 0, 2)


def test_conditional_curve_zeroes_the_observed_past() -> None:
    curve = conditional_curve([0.1, 0.2, 0.3, 0.4], t_last=1)

    assert curve[0] == 0.0
    assert curve[1] == 0.0
    assert curve[2] == pytest.approx(conditional_probability([0.1, 0.2, 0.3, 0.4], 2, 1))
    assert curve[3] == pytest.approx(conditional_probability([0.1, 0.2, 0.3, 0.4], 3, 1))


def test_conditional_curve_is_non_decreasing_after_t_last() -> None:
    """Cumulative probability of funding can only accumulate."""
    curve = conditional_curve([0.2] * 6, t_last=1)
    tail = curve[2:]
    assert np.all(np.diff(tail) >= 0)


def test_conditional_is_a_probability_across_many_shapes() -> None:
    rng = np.random.default_rng(7)
    for _ in range(200):
        h = rng.uniform(0.001, 0.5, size=8)
        t_last = int(rng.integers(0, 6))
        t = int(rng.integers(t_last + 1, 8))
        p = conditional_probability(h, t, t_last)
        assert 0.0 <= p <= 1.0


# ── shrinkage (§21 cold start) ─────────────────────────────────────────────


def test_no_observations_returns_the_segment_prior_exactly() -> None:
    assert shrink(0.9, 0, 0.2, kappa=5.0) == pytest.approx(0.2)


def test_shrinkage_weight_is_n_over_n_plus_kappa() -> None:
    """w = 5/(5+5) = 0.5 -> the midpoint of observed and segment."""
    assert shrink(0.8, 5, 0.2, kappa=5.0) == pytest.approx(0.5)


def test_many_observations_approach_the_observed_rate() -> None:
    assert shrink(0.8, 500, 0.2, kappa=5.0) == pytest.approx(0.8, abs=0.01)


def test_shrinkage_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        shrink(0.5, -1, 0.2)
    with pytest.raises(ValueError):
        shrink(0.5, 10, 0.2, kappa=0.0)


# ── HazardTable ────────────────────────────────────────────────────────────


def _key(day: int = 1, band: int = HOUR_BAND_PRE_10) -> SegmentKey:
    return SegmentKey(
        mcc="5812",
        ticket_band=TICKET_BAND_MID,
        rail="upi_autopay",
        day_of_month=day,
        hour_band=band,
    )


def test_unpopulated_segment_falls_back_to_the_global_prior() -> None:
    """§27's n_obs >= 50 floor means sparse cells cannot be stored at all."""
    table = HazardTable({}, global_prior=0.11)
    assert table.segment_hazard(_key()) == pytest.approx(0.11)


def test_populated_segment_is_used() -> None:
    table = HazardTable({_key(): 0.42}, global_prior=0.11)
    assert table.segment_hazard(_key()) == pytest.approx(0.42)


def test_estimate_shrinks_customer_history_toward_the_segment() -> None:
    table = HazardTable({_key(): 0.20}, global_prior=0.05, kappa=5.0)
    estimate = table.estimate(_key(), observed_hazard=0.80, n_observed=5)
    assert estimate == pytest.approx(0.5)


def test_curve_returns_one_hazard_per_slot() -> None:
    table = HazardTable({_key(day=1): 0.3}, global_prior=0.1)
    curve = table.curve([_key(day=1), _key(day=2), _key(day=3)])

    assert curve.shape == (3,)
    assert curve[0] == pytest.approx(0.3)
    assert curve[1] == pytest.approx(0.1)


def test_uniform_hazards_encode_no_payday_information() -> None:
    """The baseline V0 must beat — what a calendar amounts to (§21)."""
    h = uniform_hazards(0.2, 5)
    assert h.shape == (5,)
    assert len(set(h.tolist())) == 1


# ── calibration metrics, against hand-computed values (ADR-034) ────────────


def test_log_loss_matches_the_closed_form() -> None:
    """Single observation, y=1, p=0.5 -> -ln(0.5) = 0.693147..."""
    assert log_loss([1], [0.5]) == pytest.approx(0.6931471805599453)
    assert log_loss([0], [0.5]) == pytest.approx(0.6931471805599453)


def test_log_loss_rewards_confident_correctness() -> None:
    assert log_loss([1], [0.99]) < log_loss([1], [0.6]) < log_loss([1], [0.5])


def test_log_loss_punishes_confident_error() -> None:
    assert log_loss([1], [0.01]) > log_loss([1], [0.4])


def test_log_loss_is_finite_at_the_extremes() -> None:
    """Clipping: one confident mistake must not make the metric infinite."""
    assert np.isfinite(log_loss([1], [0.0]))
    assert np.isfinite(log_loss([0], [1.0]))


def test_log_loss_rejects_non_binary_labels() -> None:
    with pytest.raises(ValueError, match="binary"):
        log_loss([0.5], [0.5])


def test_log_loss_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        log_loss([1, 0], [0.5])


def test_perfect_calibration_has_zero_ece() -> None:
    """Ten predictions at 0.5, five positive -> observed matches predicted."""
    y = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    p = [0.5] * 10
    assert expected_calibration_error(y, p) == pytest.approx(0.0, abs=1e-12)


def test_total_miscalibration_has_ece_near_one() -> None:
    y = [0] * 10
    p = [1.0] * 10
    assert expected_calibration_error(y, p) == pytest.approx(1.0)


def test_ece_is_count_weighted_not_bin_weighted() -> None:
    """A bin holding 2 predictions must not outweigh one holding 98.

    Bin A: 98 predictions at 0.05, all negative -> gap 0.05
    Bin B:  2 predictions at 0.95, all negative -> gap 0.95
    Count-weighted: (98*0.05 + 2*0.95)/100 = 0.068
    Unweighted mean would be (0.05+0.95)/2 = 0.5 — very different.
    """
    y = [0] * 100
    p = [0.05] * 98 + [0.95] * 2
    assert expected_calibration_error(y, p) == pytest.approx(0.068, abs=1e-9)


def test_is_calibrated_uses_the_section_43_threshold() -> None:
    y = [1, 1, 0, 0]
    assert is_calibrated(y, [0.5] * 4)
    assert not is_calibrated([0] * 10, [0.9] * 10)
    assert ECE_ALERT_THRESHOLD == 0.05


def test_reliability_curve_bins_and_counts() -> None:
    curve = reliability_curve([1, 1, 0, 0], [0.9, 0.9, 0.1, 0.1], bins=10)

    assert int(curve.count.sum()) == 4
    # Bins are half-open [lower, upper), so p=0.1 belongs to bin 1 ([0.1, 0.2)),
    # not bin 0 ([0.0, 0.1)). p=0.9 belongs to bin 9.
    assert curve.count[1] == 2  # the 0.1s
    assert curve.count[9] == 2  # the 0.9s
    assert curve.count[0] == 0
    assert curve.observed_frequency[9] == pytest.approx(1.0)
    assert curve.observed_frequency[1] == pytest.approx(0.0)


def test_probability_of_one_lands_in_the_final_bin() -> None:
    """Right-closed final bin, or p == 1.0 would fall out of range."""
    curve = reliability_curve([1], [1.0], bins=10)
    assert curve.count[9] == 1


def test_ece_needs_a_populated_bin() -> None:
    with pytest.raises(ValueError):
        expected_calibration_error([], [])


def test_brier_score_matches_the_closed_form() -> None:
    """(0.5-1)^2 = 0.25."""
    assert brier_score([1], [0.5]) == pytest.approx(0.25)
    assert brier_score([1, 0], [1.0, 0.0]) == pytest.approx(0.0)
