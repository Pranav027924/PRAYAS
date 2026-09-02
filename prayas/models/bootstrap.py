"""A bootstrap liquidity prior for a deployment with no observations (ADR-098).

§27's floors — 50 observations from **3 distinct tenants** — mean a deployment
with fewer than three tenants can never publish a `segment_priors` cell,
however much data it gathers (FINDING-P17-09). The planner then reads the
cold-start rate, and §23.4's continuation term makes every attempt look like a
bad trade: risking `0.04 x ₹30,000` to gain `₹25`. The arithmetic is right and
the system is inert.

This is the prior that lets a first tenant run. **It is a prior, not evidence,
and the distinction is kept visible**: `PriorTable.source` says which one is in
play, the aggregator logs when nothing can be published, and any real cell that
clears §27's floors displaces the bootstrap for that key immediately.

**Where the numbers come from.** Not invented — derived from the archetype mix
the simulator already declares in `prayas/sim/config.py`, which is §38's model
of Indian recurring-debit liquidity:

* 45% are paid on the 1st, 20% on the 7th (`payday_mix`)
* 25% are gig workers with income spread across the month
* 10% are chronically dry — the population §1 says no schedule can help

Presence decays after a credit because money gets spent; `_SPEND_TAU_DAYS`
sets how fast. Salary lands in a tight early window and gig income across the
working day (`_funding_hour`), so the hour bands are weighted rather than flat.

**It is deliberately conservative.** Being wrong high fires attempts the
economics do not support and burns a customer's retry budget on our guess.
Being wrong low delays recovery, which is recoverable. So the curve sits below
what the simulator's own population would produce.
"""

from __future__ import annotations

import math
from typing import Final

from prayas.inference.bands import HOUR_BANDS, TICKET_BANDS
from prayas.sim.config import (
    CHRONICALLY_DRY,
    GIG_IRREGULAR,
    SALARIED_1ST,
    SALARIED_7TH,
)

#: Calendar day on which each salaried archetype is credited.
_PAYDAY: Final[dict[str, int]] = {SALARIED_1ST: 1, SALARIED_7TH: 7}

#: Population weights, mirroring `SimulationConfig.payday_mix`.
_MIX: Final[dict[str, float]] = {
    SALARIED_1ST: 0.45,
    SALARIED_7TH: 0.20,
    GIG_IRREGULAR: 0.25,
    CHRONICALLY_DRY: 0.10,
}

#: Days over which a credit is drawn down. Presence falls as `exp(-d/tau)`.
_SPEND_TAU_DAYS: Final = 9.0

#: Presence for a salaried customer on payday itself. Someone credited that
#: morning almost certainly covers a recurring debit — the residual is the
#: minority whose salary is already committed on arrival.
_PAYDAY_PEAK: Final = 0.90

#: Gig income is spread thin and roughly flat across the month.
_GIG_FLAT: Final = 0.20

#: The chronically dry are the population no schedule reaches.
_DRY_FLAT: Final = 0.03

#: Share of a day's presence attributable to each hour band. Salary credits
#: clear early (`_funding_hour` gives salaried 0-8h), so the pre-10 band carries
#: most of it. The two NPCI peak bands are excluded from UPI execution anyway,
#: so resolution there buys nothing.
_BAND_WEIGHT: Final[dict[int, float]] = {0: 1.00, 1: 0.85, 2: 0.80, 3: 0.70, 4: 0.55}

#: Larger tickets clear less often at the same liquidity — a ₹40,000 debit
#: needs more of the balance still present than a ₹500 one.
_TICKET_WEIGHT: Final[dict[int, float]] = {0: 1.00, 1: 0.92, 2: 0.80, 3: 0.62, 4: 0.45}

#: Held below the simulator's own population rate. See the module docstring.
_CONSERVATISM: Final = 0.80

_DAYS_IN_MONTH: Final = 31


def _days_since_credit(day_of_month: int, payday: int) -> int:
    """Days since the most recent credit, wrapping across the month boundary."""
    delta = day_of_month - payday
    return delta if delta >= 0 else delta + _DAYS_IN_MONTH


def _salaried_presence(day_of_month: int, payday: int) -> float:
    return _PAYDAY_PEAK * math.exp(-_days_since_credit(day_of_month, payday) / _SPEND_TAU_DAYS)


def day_presence(day_of_month: int) -> float:
    """Population-average `P(funds present)` on a calendar day."""
    total = 0.0
    for archetype, weight in _MIX.items():
        if archetype in _PAYDAY:
            total += weight * _salaried_presence(day_of_month, _PAYDAY[archetype])
        elif archetype == GIG_IRREGULAR:
            total += weight * _GIG_FLAT
        else:
            total += weight * _DRY_FLAT
    return total


def cell_hazard(*, day_of_month: int, hour_band: int, ticket_band: int) -> float:
    """The bootstrap prior for one `segment_priors` cell."""
    value = (
        day_presence(day_of_month)
        * _BAND_WEIGHT[hour_band]
        * _TICKET_WEIGHT[ticket_band]
        * _CONSERVATISM
    )
    return min(max(value, 1e-4), 1.0)


def bootstrap_cells(
    *, mcc: str, rail: str
) -> dict[tuple[str, int, str, int, int], tuple[float, int]]:
    """Every cell of the bootstrap prior, in `PriorTable.cells` shape.

    `n_obs` is **1**, deliberately. §21's shrinkage is `w = n/(n+kappa)`, so a
    single nominal observation lets any real cell that clears §27's floors
    dominate this one the moment it exists — the bootstrap yields to evidence
    rather than competing with it.
    """
    return {
        (mcc, ticket, rail, day, band): (
            cell_hazard(day_of_month=day, hour_band=band, ticket_band=ticket),
            1,
        )
        for day in range(1, _DAYS_IN_MONTH + 1)
        for band in HOUR_BANDS
        for ticket in TICKET_BANDS
    }


def bootstrap_global_hazard() -> float:
    """The month-average, used where no cell matches."""
    days = range(1, _DAYS_IN_MONTH + 1)
    return _CONSERVATISM * sum(day_presence(d) for d in days) / _DAYS_IN_MONTH
