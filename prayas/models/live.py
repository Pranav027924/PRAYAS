"""Serving a hazard curve for a *live* cycle (ADR-094; §21, §27, §36).

Every model in `prayas/models/` is fitted and served against
`prayas.sim.generate.SimulatedCycle`. Nothing could produce a curve for a row
out of `cycles`, which is half of why no decision service existed
(FINDING-P17-04). This is that path.

**V0, and deliberately so.** V1's end-to-end lift is `+1.014% ± 2.425%`, a 95%
interval that includes zero (FINDING-P11-02), so it is unproven where it
counts. The decisive argument is colder than that: V1 needs a fitted model, and
a tenant in `OBSERVE` has no history to fit one on. V0 served from *cross-tenant*
`segment_priors` is the only thing that can price a first cycle at all. V1 drops
in behind the same `HazardCurve` signature once a tenant has history.

**Why cross-tenant is legal here.** §27's aggregates carry no customer and no
tenant: `segment_priors` is keyed on `(mcc, ticket_band, rail, day_of_month,
hour_band)` with `CHECK (n_obs >= 50)`, and `aggregate()` counts contributors
precisely so a cell cannot be traced to one tenant. Invariant 8 forbids an
individual profile crossing a tenant boundary; a k-anonymised rate is not one.
Read through `system_transaction` because the table is deliberately not
tenant-scoped.

**Shrinkage, not a cliff.** §21's `w = n/(n+kappa)` blends a cell's own rate
toward the global rate, so a thin cell degrades smoothly instead of asserting a
number it cannot support. A missing cell is the global rate outright.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.inference.bands import hour_band, ticket_band
from prayas.memory.profile import DEFAULT_KAPPA
from prayas.models.bootstrap import bootstrap_cells, bootstrap_global_hazard
from prayas.models.features import HORIZON_DAYS, SLOTS_PER_DAY
from prayas.models.serving import FloatArray, band_to_hourly
from prayas.observability import metrics

log = logging.getLogger(__name__)

#: Used when `segment_priors` is empty — a brand-new deployment where no tenant
#: has yet contributed 50 observations to any cell. Deliberately pessimistic:
#: §23.3 stops on expected value, so an over-optimistic default would fire
#: attempts the economics do not support. Any real cell displaces it.
COLD_START_HAZARD: Final = 0.02

_Key = tuple[str, int, str, int, int]


class PriorSource(StrEnum):
    """Where the numbers in a `PriorTable` came from.

    Carried so a decision can be traced to evidence or to a prior. The three
    are not interchangeable and conflating them in a log is how a bootstrap
    silently becomes "what we measured".
    """

    #: Cells published by the aggregator from observed outcomes.
    OBSERVED = "observed"
    #: ADR-098's derived prior. No tenant has cleared §27's floors yet.
    BOOTSTRAP = "bootstrap"
    #: Neither. A flat rate at which the DP declines everything.
    COLD = "cold"


@dataclass(frozen=True, slots=True)
class PriorTable:
    """`segment_priors` in memory, with the global rate to shrink toward."""

    cells: dict[_Key, tuple[float, int]]
    global_hazard: float
    kappa: float = DEFAULT_KAPPA
    source: PriorSource = PriorSource.OBSERVED

    @property
    def is_cold(self) -> bool:
        return self.source is PriorSource.COLD

    def hazard(self, key: _Key) -> float:
        """The cell's rate, shrunk toward the global rate by `n/(n+kappa)`."""
        cell = self.cells.get(key)
        if cell is None:
            return self.global_hazard
        rate, n_obs = cell
        weight = n_obs / (n_obs + self.kappa)
        return weight * rate + (1.0 - weight) * self.global_hazard


async def load_priors(
    conn: AsyncConnection,
    *,
    use_bootstrap: bool = True,
    bootstrap_mcc: str = "0000",
    bootstrap_rail: str = "upi_autopay",
) -> PriorTable:
    """Load `segment_priors`. Must run with no tenant bound (§27, ADR-046).

    Falls back to ADR-098's bootstrap when nothing has been published, which is
    the normal state below §27's 3-contributor floor. Pass `use_bootstrap=False`
    to get the cold table instead — useful for asserting the difference.
    """
    result = await conn.execute(
        text(
            "SELECT mcc, ticket_band, rail, day_of_month, hour_band, hazard, n_obs"
            " FROM segment_priors"
        )
    )
    cells: dict[_Key, tuple[float, int]] = {}
    weighted_sum = 0.0
    total_obs = 0
    for row in result:
        key: _Key = (
            str(row.mcc),
            int(row.ticket_band),
            str(row.rail),
            int(row.day_of_month),
            int(row.hour_band),
        )
        hazard, n_obs = float(row.hazard), int(row.n_obs)
        cells[key] = (hazard, n_obs)
        weighted_sum += hazard * n_obs
        total_obs += n_obs

    if cells:
        return PriorTable(
            cells=cells,
            global_hazard=weighted_sum / total_obs,
            source=PriorSource.OBSERVED,
        )

    # No published cell. Expected below §27's 3-contributor floor and on any
    # first deployment (FINDING-P17-09). Serve ADR-098's derived prior so the
    # system can act at all, and say so — a curve built on a prior rather than
    # on evidence is a fact every decision it drives should be traceable to.
    if use_bootstrap:
        metrics.increment("hazard_priors_bootstrap")
        log.warning(
            "hazard.priors_bootstrap",
            extra={
                "detail": (
                    "segment_priors holds no published cell, so decisions rest on "
                    "ADR-098's derived prior, not on observed outcomes. Any real "
                    "cell clearing §27's floors displaces it immediately."
                )
            },
        )
        return PriorTable(
            cells=bootstrap_cells(mcc=bootstrap_mcc, rail=bootstrap_rail),
            global_hazard=bootstrap_global_hazard(),
            source=PriorSource.BOOTSTRAP,
        )

    metrics.increment("hazard_priors_empty")
    log.warning(
        "hazard.priors_empty",
        extra={"detail": "segment_priors is empty and the bootstrap is disabled"},
    )
    return PriorTable(cells={}, global_hazard=COLD_START_HAZARD, source=PriorSource.COLD)


def presence_curve(
    priors: PriorTable,
    *,
    mcc: str,
    rail: str,
    amount_paise: int,
    due_day_of_month: int,
    horizon_slots: int = HORIZON_DAYS * 24,
) -> FloatArray:
    """`P(funds present at t)` over the hourly horizon (ADR-075).

    **Expanded by repetition, not by `band_to_hourly`.** That function converts
    a band *hazard* into the per-hour hazard whose k-fold survival matches it —
    right for an event rate, wrong for a state probability. Presence is a
    state: if funds are present during an evening band, they are present in
    every hour of it, not in a k-th root of one. Running presence through the
    hazard decomposition understates it by roughly the band width, which at the
    cold-start rate is the difference between 2% and 0.2%.

    `segment_priors.hazard` is successes over attempts in the cell, which is
    what makes it readable as presence in the first place.
    """
    band = ticket_band(amount_paise)
    curve = np.empty(horizon_slots, dtype=np.float64)
    for slot in range(horizon_slots):
        day, hour = divmod(slot, 24)
        day_of_month = ((due_day_of_month - 1 + day) % 31) + 1
        curve[slot] = priors.hazard((mcc, band, rail, day_of_month, hour_band(float(hour))))
    return np.clip(curve, 0.0, 1.0)


def hazard_curve(
    priors: PriorTable,
    *,
    mcc: str,
    rail: str,
    amount_paise: int,
    due_day_of_month: int,
    horizon_slots: int = HORIZON_DAYS * 24,
) -> FloatArray:
    """An hourly hazard curve for one live cycle.

    Built on the `(day, band)` grid the priors are keyed on, then expanded to
    the sequencer's hourly grid by `band_to_hourly` — the same expansion the
    simulated path uses, so a live curve and a measured one are comparable.
    """
    band = ticket_band(amount_paise)
    bands = np.empty(HORIZON_DAYS * SLOTS_PER_DAY, dtype=np.float64)
    for day in range(HORIZON_DAYS):
        # The prior is keyed on the calendar day the *slot* falls on, which is
        # what makes a payday pattern legible: the money arrives on the 1st
        # whatever day of the cycle that happens to be.
        day_of_month = ((due_day_of_month - 1 + day) % 31) + 1
        for hour_slot in range(SLOTS_PER_DAY):
            bands[day * SLOTS_PER_DAY + hour_slot] = priors.hazard(
                (mcc, band, rail, day_of_month, hour_slot)
            )

    return band_to_hourly(bands, horizon=horizon_slots)


def hour_band_of(hour_ist: float) -> int:
    """Re-exported so callers need not reach past this module into `bands`."""
    return hour_band(hour_ist)
