"""Chain verifier entrypoint (ADR-022; Master Spec §32, §43).

Run as `python -m prayas.ledger.verify`. §15 catalogues `chain-verifier` as its
own component, so it runs outside the API rather than competing with request
handling on the same event loop.

§43: "Ledger chain breaks — Any — Page immediately." A non-zero exit code is
what a scheduler or alerting rule keys on.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import text

from prayas.config import Settings
from prayas.db.engine import create_app_engine
from prayas.db.tenancy import system_transaction, tenant_transaction
from prayas.ledger.chain import ChainBreak, verify_chain
from prayas.observability import metrics
from prayas.observability.logging import configure

log = logging.getLogger("prayas.ledger.verify")

EXIT_OK = 0
EXIT_BREAKS_FOUND = 1


async def _tenants(engine: object) -> list[str]:
    """Every tenant, via ADR-046's registry function (FINDING-P15-01).

    **Not `SELECT tenant_id FROM tenants`.** That table is RLS-forced with a
    policy on `app.tenant_id`, so in a system transaction with no tenant bound
    the policy matches nothing and the query returns zero rows — silently. The
    verifier would then report `tenants: 0, breaks: 0` and exit successfully,
    having checked no chain at all. §32's entire claim is that the ledger is
    verifiable, and a verifier that passes vacuously is worse than none: it
    produces evidence of a property it never tested.

    `prayas_tenant_ids()` is the SECURITY DEFINER function migration 0009 added
    for precisely this situation — "a worker with no tenant bound sees nothing;
    it cannot discover which tenants exist in order to bind them." The executor
    was moved onto it; this caller was not.
    """
    async with system_transaction(engine) as conn:  # type: ignore[arg-type]
        result = await conn.execute(text("SELECT tenant_id FROM prayas_tenant_ids()"))
        return sorted(row.tenant_id for row in result)


async def verify_tenant(engine: object, tenant_id: str) -> list[ChainBreak]:
    async with tenant_transaction(engine, tenant_id) as conn:  # type: ignore[arg-type]
        return await verify_chain(conn, tenant_id)


async def run(tenant_ids: list[str] | None = None) -> int:
    settings = Settings.from_env()
    configure(settings.log_level)
    engine = create_app_engine(settings.database_url_app)

    try:
        targets = tenant_ids or await _tenants(engine)
        total_breaks = 0

        for tenant_id in targets:
            breaks = await verify_tenant(engine, tenant_id)
            total_breaks += len(breaks)

            if breaks:
                metrics.increment("ledger_chain_break", tenant_id=tenant_id, count=len(breaks))
                for chain_break in breaks:
                    log.error("ledger.chain_break", extra={"detail": str(chain_break)})
            else:
                log.info("ledger.chain_verified", extra={"tenant_id": tenant_id})

        log.info(
            "ledger.verification_complete",
            extra={"tenants": len(targets), "breaks": total_breaks},
        )
        return EXIT_BREAKS_FOUND if total_breaks else EXIT_OK
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify per-tenant ledger hash chains (§32).")
    parser.add_argument(
        "--tenant",
        action="append",
        dest="tenants",
        help="Verify only this tenant. Repeatable. Defaults to every tenant.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds between passes. 0 runs once and exits (default).",
    )
    args = parser.parse_args()

    if args.interval <= 0:
        return asyncio.run(run(args.tenants))

    async def _loop() -> int:
        while True:
            await run(args.tenants)
            await asyncio.sleep(args.interval)

    try:
        return asyncio.run(_loop())
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
