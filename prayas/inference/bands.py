"""Segment band definitions (ADR-032; Master Spec §36, §1, §9).

`segment_priors` is keyed on `(mcc, ticket_band, rail, day_of_month, hour_band)`
and §36 defines neither band. These are the definitions, in one place, because
band edges shape every hazard lookup in the system.

**Hour bands follow the NPCI execution windows** (§1: "before 10:00, 13:00-17:00,
and after 21:30 IST"). Resolution is spent only where the system can act — a
finer split inside a peak window buys nothing, because the gate denies those
attempts anyway. It also keeps cells dense enough to clear §27's `n_obs >= 50`
k-anonymity floor.

**Ticket bands follow the regulatory ceilings** already carrying citations under
ADR-021, so Invariant 10 is satisfied without inventing thresholds.
"""

from __future__ import annotations

from typing import Final

# ── hour bands ──────────────────────────────────────────────────────────────

HOUR_BAND_PRE_10: Final = 0
HOUR_BAND_PEAK_MORNING: Final = 1
HOUR_BAND_AFTERNOON: Final = 2
HOUR_BAND_PEAK_EVENING: Final = 3
HOUR_BAND_LATE: Final = 4

#: Upper edges in fractional IST hours. Half-open [lower, upper).
_HOUR_EDGES: Final[tuple[tuple[float, int], ...]] = (
    (10.0, HOUR_BAND_PRE_10),
    (13.0, HOUR_BAND_PEAK_MORNING),
    (17.0, HOUR_BAND_AFTERNOON),
    (21.5, HOUR_BAND_PEAK_EVENING),
    (24.0, HOUR_BAND_LATE),
)

#: Bands in which UPI Autopay execution is permitted (§1). The peak bands are
#: excluded, which is why they carry no useful hazard resolution.
LEGAL_UPI_BANDS: Final[frozenset[int]] = frozenset(
    {HOUR_BAND_PRE_10, HOUR_BAND_AFTERNOON, HOUR_BAND_LATE}
)

HOUR_BANDS: Final[tuple[int, ...]] = (0, 1, 2, 3, 4)


def hour_band(hour_ist: float) -> int:
    """Band for a fractional IST hour. Half-open, so bands tile without overlap."""
    if not 0.0 <= hour_ist < 24.0:
        raise ValueError(f"hour_ist must be in [0, 24), got {hour_ist}")
    for upper, band in _HOUR_EDGES:
        if hour_ist < upper:
            return band
    raise AssertionError("unreachable: edges cover [0, 24)")


def is_legal_upi_band(band: int) -> bool:
    """§1 — UPI Autopay execution is confined to non-peak windows."""
    return band in LEGAL_UPI_BANDS


# ── ticket bands ────────────────────────────────────────────────────────────

TICKET_BAND_MICRO: Final = 0  # < ₹1,000
TICKET_BAND_SMALL: Final = 1  # ₹1,000 - ₹5,000
TICKET_BAND_MID: Final = 2  # ₹5,000 - ₹15,000  (to the AFA-free ceiling)
TICKET_BAND_LARGE: Final = 3  # ₹15,000 - ₹1,00,000
TICKET_BAND_JUMBO: Final = 4  # >= ₹1,00,000  (exempt-MCC ceiling and above)

#: Upper edges in integer paise. Money is never float (project standard).
#: ₹15,000 and ₹1,00,000 are the ceilings ADR-021 loads with citations.
_TICKET_EDGES: Final[tuple[tuple[int, int], ...]] = (
    (100_000, TICKET_BAND_MICRO),
    (500_000, TICKET_BAND_SMALL),
    (1_500_000, TICKET_BAND_MID),
    (10_000_000, TICKET_BAND_LARGE),
)

TICKET_BANDS: Final[tuple[int, ...]] = (0, 1, 2, 3, 4)


def ticket_band(amount_paise: int) -> int:
    """Band for an amount in integer paise. Half-open, like the hour bands.

    `₹15,000` lands in `TICKET_BAND_LARGE`, not `MID`: the AFA-free rule is
    `amount_paise <= cap`, so exactly-at-the-cap is still AFA-free, but it sits
    at the top of its range and behaves like the band above it.
    """
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int — money is integer paise")
    if amount_paise < 0:
        raise ValueError(f"amount_paise must be non-negative, got {amount_paise}")
    for upper, band in _TICKET_EDGES:
        if amount_paise < upper:
            return band
    return TICKET_BAND_JUMBO
