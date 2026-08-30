"""Cross-tenant learning without cross-tenant profiling (Master Spec §27).

§27 draws the line precisely:

| Shared across tenants | Never shared |
|---|---|
| Segment hazard priors (`mcc x ticket_band x day_of_month`) | Individual payday distributions |
| Issuer health and outage patterns | Individual contact preferences |
| Decline-code base rates by issuer | Individual failure histories |
| Model *parameters* trained on pooled data | Individual profile records |

The reason is DPDP purpose limitation: data collected for one merchant's
recurring billing "cannot be repurposed into a cross-merchant behavioural
profile without a separate lawful basis". §27 is blunt that building one is
"the kind of thing that looks like a feature and is actually a liability".

**The k-anonymity floor is the whole safeguard, so it is enforced twice.** The
database carries `CHECK (n_obs >= 50)` on `segment_priors`, and this module
refuses to emit a thin cell before the insert is attempted. Belt and braces on
purpose: the CHECK is what makes the guarantee real even against a bug here,
and the pre-filter is what makes the failure legible instead of an integrity
error surfacing three layers up.

**A cell with one contributing tenant is not anonymous** however many
observations it holds — it is that tenant's data with a different label. So a
minimum number of distinct contributors is required as well as a minimum count,
which §27's own phrasing ("aggregated statistics", plural sources) implies and
the schema alone cannot express.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

#: §27 / §36 — "minimum cohort size", mirrored by `CHECK (n_obs >= 50)`.
MIN_COHORT: Final = 50

#: ADR-079. Distinct tenants a cell must draw on before it may be published.
#: A cell sourced from one tenant is that tenant's data wearing an aggregate's
#: name, and publishing it to the others is precisely the leak §27 forbids.
MIN_CONTRIBUTORS: Final = 3


class AggregateError(ValueError):
    """The aggregate cannot be published safely."""


@dataclass(frozen=True, slots=True)
class SegmentKey:
    """§36's `segment_priors` key. Carries no customer, by construction."""

    mcc: str
    ticket_band: str
    rail: str
    day_of_month: int
    hour_band: int


@dataclass(frozen=True, slots=True)
class SegmentObservation:
    """One tenant's contribution to one cell.

    `tenant_id` is here to be *counted and discarded*: it decides whether the
    cell is anonymous enough to publish, and it is never written.
    """

    key: SegmentKey
    tenant_id: str
    successes: int
    attempts: int


@dataclass(frozen=True, slots=True)
class SegmentPrior:
    """A publishable cell: a rate, a count, and nothing identifying."""

    key: SegmentKey
    hazard: float
    n_obs: int
    contributors: int
    updated_at: datetime


def aggregate(
    observations: list[SegmentObservation],
    *,
    min_cohort: int = MIN_COHORT,
    min_contributors: int = MIN_CONTRIBUTORS,
    now: datetime | None = None,
) -> tuple[list[SegmentPrior], list[SegmentKey]]:
    """Pool observations into publishable cells.

    Returns `(publishable, withheld)`. Withheld keys are returned rather than
    dropped silently, because "this segment has no prior" and "this segment was
    suppressed for k-anonymity" are different operational facts and only one of
    them means the pipeline is working correctly.
    """
    if min_cohort < 1 or min_contributors < 1:
        raise AggregateError("floors must be positive")

    pooled: dict[SegmentKey, list[SegmentObservation]] = defaultdict(list)
    for observation in observations:
        if observation.attempts < 0 or observation.successes < 0:
            raise AggregateError("counts must be non-negative")
        if observation.successes > observation.attempts:
            raise AggregateError("successes cannot exceed attempts")
        pooled[observation.key].append(observation)

    at = now or datetime.now(UTC)
    publishable: list[SegmentPrior] = []
    withheld: list[SegmentKey] = []

    for key, group in pooled.items():
        attempts = sum(o.attempts for o in group)
        successes = sum(o.successes for o in group)
        contributors = len({o.tenant_id for o in group})

        if attempts < min_cohort or contributors < min_contributors:
            withheld.append(key)
            continue

        publishable.append(
            SegmentPrior(
                key=key,
                hazard=successes / attempts,
                n_obs=attempts,
                contributors=contributors,
                updated_at=at,
            )
        )

    return publishable, withheld


async def publish(conn: AsyncConnection, priors: list[SegmentPrior]) -> int:
    """Write cells to the global `segment_priors` table.

    Runs as the **owner**, not the app role: `segment_priors` is cross-tenant
    by design and the app role holds SELECT on it only. A tenant-bound session
    must not be able to write something every other tenant will read.
    """
    written = 0
    for prior in priors:
        if prior.n_obs < MIN_COHORT:
            raise AggregateError(
                f"refusing to publish a cell with n_obs={prior.n_obs} below the "
                f"{MIN_COHORT} floor — §27's minimum cohort size"
            )
        await conn.execute(
            text(
                "INSERT INTO segment_priors"
                " (mcc, ticket_band, rail, day_of_month, hour_band, hazard, n_obs, updated_at)"
                " VALUES (:mcc, :band, :rail, :dom, :hour, :hazard, :n, :at)"
                " ON CONFLICT (mcc, ticket_band, rail, day_of_month, hour_band)"
                " DO UPDATE SET hazard = EXCLUDED.hazard, n_obs = EXCLUDED.n_obs,"
                " updated_at = EXCLUDED.updated_at"
            ),
            {
                "mcc": prior.key.mcc,
                "band": prior.key.ticket_band,
                "rail": prior.key.rail,
                "dom": prior.key.day_of_month,
                "hour": prior.key.hour_band,
                "hazard": prior.hazard,
                "n": prior.n_obs,
                "at": prior.updated_at,
            },
        )
        written += 1
    return written
