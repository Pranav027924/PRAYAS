"""Issuer nowcast (Master Spec §20; Phase 11 exit criteria).

The exit criterion is "nowcast detects injected outages within one window and
recovery within three". Detection latency is meaningless without a false-alarm
rate, so both are asserted here: a healthy stream must never raise an alarm, or
"detects every outage" is satisfied by a detector that is always alarming.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from prayas.inference.nowcast import (
    PEER_SPAN,
    UNKNOWN,
    WINDOW,
    IssuerNowcast,
    peer_context,
    wilson_interval,
    wilson_lower_bound,
    window_start,
)

T0 = datetime(2026, 3, 1, tzinfo=UTC)
ISSUER = "HDFC"


# ── the Wilson interval ────────────────────────────────────────────────────


def test_empty_sample_is_maximal_ignorance() -> None:
    """No evidence is not evidence of health, nor of an outage."""
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_the_bound_tightens_with_sample_size() -> None:
    """The property the whole detector rests on: three failures out of three is
    not the same news as three hundred out of three hundred."""
    _, thin = wilson_interval(0, 3)
    _, thick = wilson_interval(0, 300)
    assert thin > thick
    assert thin > 0.4, "a 3-attempt sample should not look conclusive"
    assert thick < 0.05, "a 300-attempt sample should"


def test_the_bound_brackets_the_point_estimate() -> None:
    for successes, attempts in ((1, 10), (5, 10), (9, 10), (50, 100)):
        lower, upper = wilson_interval(successes, attempts)
        assert 0.0 <= lower <= successes / attempts <= upper <= 1.0


def test_impossible_counts_are_rejected() -> None:
    with pytest.raises(ValueError, match="exceeds attempts"):
        wilson_lower_bound(5, 3)
    with pytest.raises(ValueError, match="non-negative"):
        wilson_lower_bound(-1, 3)


def test_naive_timestamps_are_refused() -> None:
    """Times are stored UTC and evaluated IST; a naive one has no meaning."""
    with pytest.raises(ValueError, match="timezone-aware"):
        window_start(datetime(2026, 3, 1))  # noqa: DTZ001


def test_windows_tile_without_overlap() -> None:
    a = window_start(T0 + timedelta(minutes=4, seconds=59))
    b = window_start(T0 + timedelta(minutes=5))
    assert a == T0
    assert b == T0 + WINDOW


# ── detection ──────────────────────────────────────────────────────────────


def _run(
    *,
    seed: int,
    outage: tuple[int, int] | None,
    minutes: int = 240,
    per_minute: int = 8,
    healthy_rate: float = 0.86,
    outage_rate: float = 0.05,
) -> tuple[IssuerNowcast, list[tuple[datetime, bool]]]:
    """Stream one issuer's traffic, optionally with an injected outage."""
    rng = np.random.default_rng(seed)
    # The baseline is the issuer's own healthy rate. Passing it is not a
    # convenience: the detector's threshold is a fraction of it, and a nowcast
    # told the wrong baseline is either blind or permanently alarmed.
    nowcast = IssuerNowcast(baseline_success=healthy_rate)
    observed: list[tuple[datetime, bool]] = []

    for minute in range(minutes):
        at = T0 + timedelta(minutes=minute)
        down = outage is not None and outage[0] <= minute < outage[1]
        for _ in range(per_minute):
            ok = bool(rng.random() < (outage_rate if down else healthy_rate))
            nowcast.observe(ISSUER, at + timedelta(seconds=int(rng.integers(0, 60))), success=ok)
        nowcast.flush(at)
        observed.append((at, nowcast.state(ISSUER, at).is_degraded))

    return nowcast, observed


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_an_injected_outage_is_detected_within_one_window(seed: int) -> None:
    """Phase 11 exit criterion, first half."""
    start, end = 60, 110
    _, observed = _run(seed=seed, outage=(start, end))
    onset = T0 + timedelta(minutes=start)

    detected = next((at for at, degraded in observed if degraded and at >= onset), None)
    assert detected is not None, "the outage was never detected"
    assert detected - onset <= WINDOW, (
        f"detected {(detected - onset).total_seconds() / 60:.0f} min after onset; "
        f"the criterion allows {WINDOW.total_seconds() / 60:.0f}"
    )


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_recovery_is_detected_within_three_windows(seed: int) -> None:
    """Phase 11 exit criterion, second half."""
    start, end = 60, 110
    _, observed = _run(seed=seed, outage=(start, end))
    onset, over = T0 + timedelta(minutes=start), T0 + timedelta(minutes=end)

    detected = next(at for at, degraded in observed if degraded and at >= onset)
    recovered = next((at for at, degraded in observed if at >= over and not degraded), None)
    assert recovered is not None and recovered > detected, "recovery was never seen"
    assert recovered - over <= 3 * WINDOW, (
        f"recovery seen {(recovered - over).total_seconds() / 60:.0f} min after the outage ended"
    )


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_a_healthy_stream_never_raises_an_alarm(seed: int) -> None:
    """Without this, "detects every outage" is satisfied by always alarming."""
    _, observed = _run(seed=seed, outage=None)
    false_alarms = [at for at, degraded in observed if degraded]
    assert not false_alarms, f"{len(false_alarms)} false alarms on a healthy issuer"


def test_a_single_unlucky_window_decays_instead_of_accumulating() -> None:
    """CUSUM's floor at zero, exercised: one bad window must not trip a detector
    that then stays tripped."""
    nowcast = IssuerNowcast(baseline_success=0.86)
    rng = np.random.default_rng(7)
    for minute in range(120):
        at = T0 + timedelta(minutes=minute)
        # One deliberately terrible minute in an otherwise healthy stream.
        rate = 0.0 if minute == 40 else 0.86
        for _ in range(8):
            nowcast.observe(ISSUER, at, success=bool(rng.random() < rate))
        nowcast.flush(at)
    assert not nowcast.state(ISSUER, T0 + timedelta(minutes=119)).is_degraded


def test_an_unseen_issuer_reports_unknown_not_healthy() -> None:
    """Silence must not license a retry. `UNKNOWN` is a real answer."""
    state = IssuerNowcast().state("NEVER_SEEN", T0)
    assert state.status == UNKNOWN
    assert not state.is_degraded
    assert state.recovery_eta is None


def test_thin_evidence_reports_unknown() -> None:
    nowcast = IssuerNowcast(baseline_success=0.86, min_attempts=5)
    for _ in range(3):
        nowcast.observe(ISSUER, T0, success=False)
    assert nowcast.state(ISSUER, T0).status == UNKNOWN


# ── recovery ETA ───────────────────────────────────────────────────────────


def test_recovery_eta_exists_only_while_degraded() -> None:
    nowcast, _ = _run(seed=0, outage=(60, 110), minutes=90)
    at = T0 + timedelta(minutes=89)
    assert nowcast.state(ISSUER, at).is_degraded
    eta = nowcast.recovery_eta(ISSUER, at)
    assert eta is not None and eta > at

    healthy, _ = _run(seed=0, outage=None, minutes=90)
    assert healthy.recovery_eta(ISSUER, at) is None


def test_a_long_outage_does_not_promise_imminent_recovery() -> None:
    """Durations are heavy-tailed (§38 draws them LogNormal), so an outage that
    has run long is evidence it will run longer. An ETA that decayed to the
    current instant would keep promising a recovery that never arrives."""
    nowcast, _ = _run(seed=0, outage=(60, 400), minutes=380)
    late = T0 + timedelta(minutes=379)
    eta = nowcast.recovery_eta(ISSUER, late)
    assert eta is not None
    assert eta - late >= timedelta(minutes=10)


# ── §20's peer context ─────────────────────────────────────────────────────


def test_peer_context_is_neutral_without_evidence() -> None:
    """`CauseContext`'s defaults mean "nothing points at the issuer"."""
    assert peer_context(IssuerNowcast(), "NEVER_SEEN", T0) == (1.0, 1.0)


def test_peer_context_separates_healthy_from_degraded() -> None:
    """The normalisation that makes `cause`'s 0.85 threshold usable.

    Without dividing by the baseline, a healthy issuer's Wilson lower bound sits
    well below 0.85 at any realistic sample size and the heuristic calls every
    decline an outage.
    """
    healthy, _ = _run(seed=0, outage=None, minutes=120)
    at = T0 + timedelta(minutes=119)
    health, peer = peer_context(healthy, ISSUER, at, span=PEER_SPAN)
    assert health >= 0.85, f"a healthy issuer reported {health:.2f} — below cause.py's threshold"
    assert peer >= 0.85

    degraded, _ = _run(seed=0, outage=(0, 120), minutes=120)
    sick, sick_peer = peer_context(degraded, ISSUER, at, span=PEER_SPAN)
    assert sick < 0.85
    assert sick_peer < 0.85


def test_trailing_counts_aggregate_across_windows() -> None:
    """§20 says five minutes; below production volume that bucket is empty."""
    nowcast = IssuerNowcast()
    for minute in range(0, 60, 5):
        nowcast.observe(ISSUER, T0 + timedelta(minutes=minute), success=True)
    at = T0 + timedelta(minutes=59)

    _, in_one_bucket = nowcast.trailing_counts(ISSUER, at, span=WINDOW)
    _, in_an_hour = nowcast.trailing_counts(ISSUER, at, span=timedelta(minutes=60))
    assert in_an_hour > in_one_bucket
