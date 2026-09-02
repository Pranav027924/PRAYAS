"""Live hazard/presence serving (ADR-094; Master Spec §21, §27).

The load-bearing test here is the one separating presence from hazard. Both
functions return a plausible-looking array of the right shape, and confusing
them costs a factor of the band width in every decision the DP makes — which
is how it survived a first draft.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.models.live import (
    COLD_START_HAZARD,
    PriorSource,
    PriorTable,
    hazard_curve,
    presence_curve,
)

HORIZON = 720


def _table(rate: float = 0.40, n_obs: int = 500) -> PriorTable:
    """A prior where every cell this test asks for is populated."""
    cells = {
        ("5411", 1, "upi_autopay", day, band): (rate, n_obs)
        for day in range(1, 32)
        for band in range(5)
    }
    return PriorTable(cells=cells, global_hazard=rate)


def _curve(fn, table: PriorTable):  # type: ignore[no-untyped-def]
    return fn(
        table,
        mcc="5411",
        rail="upi_autopay",
        amount_paise=250_000,
        due_day_of_month=15,
        horizon_slots=HORIZON,
    )


def test_presence_is_not_the_hazard_decomposition() -> None:
    """Presence is a state; hazard is an event rate. They must not agree.

    `band_to_hourly` splits a band hazard into per-hour rates whose k-fold
    survival matches it. Applying that to presence understates it by roughly
    the band width — measured at ~3x against real priors — and biases every
    decision toward stopping.
    """
    table = _table(rate=0.40)
    presence = _curve(presence_curve, table)
    hazard = _curve(hazard_curve, table)

    # Presence is served flat at the cell rate; the hazard decomposition pulls
    # every hour strictly below it, most steeply in the widest bands. Comparing
    # maxima understates the gap (the narrowest band barely decomposes), so
    # compare across the curve.
    assert np.isclose(presence.max(), 0.40, atol=1e-6)
    assert (hazard < presence).all()
    assert presence.mean() > hazard.mean() * 2.0


def test_presence_returns_the_cell_rate_unchanged() -> None:
    """A populated cell with heavy support is served as-is, not reshaped."""
    presence = _curve(presence_curve, _table(rate=0.37, n_obs=10_000))
    assert np.allclose(presence, 0.37, atol=1e-3)


def test_shrinkage_pulls_a_thin_cell_toward_the_global_rate() -> None:
    """§21's `w = n/(n+kappa)` — a smooth transition, not a cliff."""
    thin = PriorTable(cells={("5411", 1, "upi_autopay", 15, 2): (0.90, 1)}, global_hazard=0.10)
    served = thin.hazard(("5411", 1, "upi_autopay", 15, 2))
    assert 0.10 < served < 0.90
    # One observation against kappa=5 puts most of the weight on the global rate.
    assert served < 0.30


def test_a_missing_cell_is_the_global_rate() -> None:
    table = PriorTable(cells={}, global_hazard=0.123)
    assert table.hazard(("nope", 9, "nope", 1, 0)) == pytest.approx(0.123)


def test_empty_priors_report_cold_and_serve_the_cold_start_rate() -> None:
    """FINDING-P17-07: an empty table must be visibly cold, not silently zero."""
    table = PriorTable(cells={}, global_hazard=COLD_START_HAZARD, source=PriorSource.COLD)
    assert table.is_cold
    presence = _curve(presence_curve, table)
    assert np.allclose(presence, COLD_START_HAZARD)


def test_curves_have_the_requested_shape_and_stay_in_range() -> None:
    table = _table()
    for fn in (presence_curve, hazard_curve):
        curve = _curve(fn, table)
        assert curve.shape == (HORIZON,)
        assert curve.min() >= 0.0
        assert curve.max() <= 1.0


def test_the_day_of_month_walks_with_the_horizon() -> None:
    """A payday prior is only legible if day 1 of the cycle maps to the calendar.

    Money arrives on the 1st whichever day of the cycle that is, so the cell
    consulted for slot `t` must advance with the calendar rather than staying
    on the due date's day.
    """
    # Only the 17th is rich; due on the 15th means slots on day 2 should see it.
    cells = {("5411", 1, "upi_autopay", 17, band): (0.80, 500) for band in range(5)}
    table = PriorTable(cells=cells, global_hazard=0.05)
    presence = presence_curve(
        table,
        mcc="5411",
        rail="upi_autopay",
        amount_paise=250_000,
        due_day_of_month=15,
        horizon_slots=HORIZON,
    )
    day0 = presence[0:24]
    day2 = presence[48:72]
    assert day0.max() < 0.1, "day 0 is the 15th and should be poor"
    assert day2.min() > 0.7, "day 2 is the 17th and should be rich"
