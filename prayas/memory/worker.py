"""Aggregator service — builds `segment_priors` from observed outcomes (ADR-097).

`aggregate()` and `publish()` were written, tested and called from nowhere
(FINDING-P17-07). The consequence was not a crash but silence: `segment_priors`
stayed empty, `load_priors` fell through to the cold-start rate, and the planner
correctly declined every cycle because at a 2% presence an attempt risks far
more continuation value than it stands to gain. This is the collection step
that was missing between the two.

**It reads `events_raw`, not `attempts`, and that is the point.** §44 starts
every tenant in `OBSERVE`, where Prayas fires nothing — so `attempts` is empty
exactly when the priors are most needed. `events_raw` meanwhile holds the
*merchant's own* baseline retries and their outcomes. Building the prior from
the observed stream is what lets a tenant arrive at CANARY with a usable curve
without Prayas having debited anyone. Reading `attempts` instead would deadlock:
no priors, so no attempts, so no priors.

**Runs as the owner, and that is unresolved.** `segment_priors` is cross-tenant
by construction and the app role holds SELECT on it only, so publishing needs
the owner URL. But `config.owner_database_url()` states the owner connection is
"used by Alembic only; the application must never call this" (ADR-004). This
worker is the first thing to break that, so it reads the variable directly and
the widening is recorded as FINDING-P17-08 rather than assumed. A SECURITY
DEFINER publisher — the pattern `prayas_tenant_ids()` already uses (ADR-046) —
would keep the app role narrow, and needs a migration.

**k-anonymity is enforced upstream and is not negotiable here.** `aggregate()`
withholds any cell below `MIN_COHORT` (50 observations) or `MIN_CONTRIBUTORS`
(3 distinct tenants). Withheld cells are counted and logged, because "no prior
yet" and "suppressed for anonymity" are different operational facts and only
one of them means the pipeline is healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from prayas.config import Settings
from prayas.db.tenancy import system_transaction
from prayas.executor.claiming import active_tenants
from prayas.inference.bands import hour_band, ticket_band
from prayas.memory.aggregate import SegmentKey, SegmentObservation, aggregate, publish
from prayas.observability import metrics
from prayas.observability.logging import configure

log = logging.getLogger(__name__)

#: Cells are pooled across tenants, so a pass that runs hourly is ample — the
#: floors mean a cell needs 50 observations from 3 tenants before it publishes
#: at all, and that does not move minute to minute.
DEFAULT_INTERVAL_SECONDS = 3600

EXIT_OK = 0

#: Outcome counting, keyed on the same grid `segment_priors` uses. Bands are
#: computed in Python rather than SQL so the definitions in `inference.bands`
#: stay the single source of truth.
_OUTCOMES = text(
    "SELECT m.mcc AS mcc, m.rail AS rail, c.amount_paise AS amount_paise,"
    "       e.event_type AS event_type,"
    "       (e.received_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata') AS local_at"
    "  FROM events_raw e"
    "  JOIN mandates m ON m.tenant_id = e.tenant_id AND m.mandate_id = e.mandate_id"
    "  LEFT JOIN cycles c ON c.tenant_id = e.tenant_id AND c.mandate_id = e.mandate_id"
    " WHERE e.tenant_id = :tenant_id AND e.signature_ok"
    "   AND e.event_type IN ('payment.failed', 'payment.captured',"
    "                        'subscription.charged', 'invoice.paid')"
)

_SUCCESS = frozenset({"payment.captured", "subscription.charged", "invoice.paid"})


async def collect_tenant(engine: AsyncEngine, tenant_id: str) -> list[SegmentObservation]:
    """One tenant's contribution to every cell it touches."""
    counts: dict[SegmentKey, list[int]] = {}

    async with system_transaction(engine) as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        rows = await conn.execute(_OUTCOMES, {"tenant_id": tenant_id})

        for row in rows:
            if row.amount_paise is None:
                # An event whose cycle was never created — no deadline yet, so
                # no ticket band. Counting it under a guessed band would put a
                # made-up number into a cross-tenant prior.
                continue
            local = row.local_at
            key = SegmentKey(
                mcc=str(row.mcc or "0000"),
                ticket_band=str(ticket_band(int(row.amount_paise))),
                rail=str(row.rail),
                day_of_month=int(local.day),
                hour_band=hour_band(local.hour + local.minute / 60.0),
            )
            slot = counts.setdefault(key, [0, 0])
            slot[1] += 1
            if row.event_type in _SUCCESS:
                slot[0] += 1

    return [
        SegmentObservation(key=key, tenant_id=tenant_id, successes=s, attempts=a)
        for key, (s, a) in counts.items()
    ]


async def tick(owner_engine: AsyncEngine) -> tuple[int, int]:
    """One aggregation pass. Returns `(published, withheld)`."""
    observations: list[SegmentObservation] = []
    for tenant_id in await active_tenants(owner_engine):
        try:
            observations.extend(await collect_tenant(owner_engine, tenant_id))
        except Exception:
            metrics.increment("aggregator_tenant_error", tenant_id=tenant_id)
            log.exception("aggregator.tenant_failed", extra={"tenant_id": tenant_id})

    publishable, withheld = aggregate(observations)

    async with owner_engine.begin() as conn:
        written = await publish(conn, publishable)

    metrics.increment("segment_priors_published", amount=written)
    metrics.increment("segment_priors_withheld", amount=len(withheld))

    if publishable:
        log.info(
            "aggregator.published",
            extra={"cells": written, "withheld": len(withheld)},
        )
    elif withheld:
        # The single-tenant case lands here permanently: `MIN_CONTRIBUTORS` is
        # 3, so one merchant can never publish a cell however much data it has.
        # Silence would look identical to a healthy pipeline with nothing to do.
        log.warning(
            "aggregator.all_withheld",
            extra={
                "withheld": len(withheld),
                "detail": (
                    "every cell fell below the k-anonymity floors (§27: 50 observations "
                    "from 3 distinct tenants). Expected on a deployment with fewer than "
                    "3 tenants, and it means the planner will keep declining."
                ),
            },
        )

    return written, len(withheld)


async def run(
    owner_engine: AsyncEngine,
    *,
    interval: float,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await tick(owner_engine)
        except Exception:
            metrics.increment("aggregator_tick_error")
            log.exception("aggregator.tick_failed")

        if interval <= 0:
            return  # one pass, for a scheduled invocation
        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def main() -> int:  # pragma: no cover - process entry point
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds between passes. 0 (the default) runs one pass and exits, "
        "which is what a scheduled invocation wants; a positive value runs "
        "resident, like the other workers.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    configure(settings.log_level)

    # Read directly rather than through `config.owner_database_url()`, whose
    # contract is "Alembic only; the application must never call this"
    # (ADR-004). This worker is the first non-Alembic owner user, and that
    # widening is a Class A question rather than something to assume — see
    # FINDING-P17-08. The narrower long-term fix is a SECURITY DEFINER
    # publisher, matching `prayas_tenant_ids()` (ADR-046), which needs a
    # migration.
    owner_url = os.environ.get("PRAYAS_DATABASE_URL_OWNER")
    if not owner_url:
        sys.stderr.write(
            "PRAYAS_DATABASE_URL_OWNER is required: segment_priors is cross-tenant "
            "and the app role holds SELECT on it only.\n"
        )
        return 1

    engine = create_async_engine(owner_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    log.info("aggregator.starting", extra={"env": settings.env, "interval": args.interval})
    try:
        await run(engine, interval=args.interval, stop=stop)
    finally:
        await engine.dispose()
        log.info("aggregator.stopped")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(asyncio.run(main()))
