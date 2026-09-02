"""Seed one tenant so a lifecycle can be run against a live stack.

Phase 17's first exit criterion is a full lifecycle — mandate, notice, debit,
failure, decision, retry, success — and none of that is reachable without a
tenant. Tenants exist only in test fixtures today; product onboarding is
Phase 18 work. This is the operational stand-in, not that feature.

Two things it deliberately does not do:

**It does not skip the ramp.** A new tenant is written at `OBSERVE`, which
fires nothing (§44). Advancing is a separate, explicit act — `--stage` — so
that reaching a firing stage is always something someone chose and never a
side effect of onboarding. In production that advance must come from
`adoption.store.promote`, which checks the evidence gates; `--stage` bypasses
them and is for test-mode demonstration only.

**It seeds the mandate as `created`, not `active`.** `project_mandate` folds
the event history to derive state, starting from `CREATED`. A mandate seeded
`active` with no `subscription.authenticated` event behind it is a state
nothing justifies, and the first projection silently resets it.

**It does not invent the webhook secret.** The material is generated here and
printed once, because it has to be typed into the Razorpay dashboard to be of
any use. It is stored nowhere: `webhook_secrets` holds only the *reference*
(ADR-014), and the material belongs in the environment as
`PRAYAS_WEBHOOK_SECRET_<REF>`.

Runs as the OWNER role. RLS is FORCEd on `tenants`, so the application role
cannot insert one — which is the point.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prayas.adoption.stages import Stage


def _owner_url() -> str:
    url = os.environ.get("PRAYAS_DATABASE_URL_OWNER")
    if not url:
        sys.exit(
            "PRAYAS_DATABASE_URL_OWNER is not set.\n"
            "Seeding a tenant requires the owner role: RLS is FORCEd on `tenants`,\n"
            "so prayas_app cannot insert one."
        )
    return url


async def bootstrap(
    *,
    tenant_id: str,
    name: str,
    stage: Stage,
    rail: str,
    max_amount_paise: int,
    mandate_id: str,
) -> None:
    engine = create_async_engine(_owner_url())
    secret_ref = f"WH_{tenant_id.upper().replace('-', '_')}_V1"
    secret_material = secrets.token_urlsafe(32)

    try:
        async with engine.begin() as conn:
            existing = await conn.execute(
                text("SELECT 1 FROM tenants WHERE tenant_id = :t"), {"t": tenant_id}
            )
            if existing.first() is not None:
                sys.exit(f"tenant {tenant_id!r} already exists — refusing to overwrite it")

            await conn.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name, config)"
                    " VALUES (:t, :n, jsonb_build_object('adoption_stage', CAST(:s AS int)))"
                ),
                {"t": tenant_id, "n": name, "s": int(stage)},
            )
            await conn.execute(
                text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, :ref)"),
                {"t": tenant_id, "ref": secret_ref},
            )
            await conn.execute(
                text(
                    "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                    " max_amount_paise, state, consent_ref, created_at)"
                    " VALUES (:m, :t, :c, :rail, :amt, 'created', :consent, :now)"
                ),
                {
                    "m": mandate_id,
                    "t": tenant_id,
                    "c": f"{tenant_id}_customer_1",
                    "rail": rail,
                    "amt": max_amount_paise,
                    "consent": f"{tenant_id}_consent_1",
                    "now": datetime.now(UTC),
                },
            )
    finally:
        await engine.dispose()

    fires = "  (fires nothing)" if stage is Stage.OBSERVE else ""
    sys.stdout.write(
        f"tenant        {tenant_id}\n"
        f"stage         {stage.name}{fires}\n"
        f"mandate       {mandate_id}  ({rail}, cap {max_amount_paise} paise)\n"
        f"secret_ref    {secret_ref}\n"
        "\n"
        "Put the webhook secret in the environment and in the Razorpay dashboard.\n"
        "It is not stored anywhere and will not be shown again:\n"
        "\n"
        f"  PRAYAS_WEBHOOK_SECRET_{secret_ref}={secret_material}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id")
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--stage",
        default="OBSERVE",
        choices=[s.name for s in Stage],
        help="OBSERVE fires nothing. Anything else bypasses §44's evidence gates "
        "and is for test-mode demonstration only.",
    )
    parser.add_argument(
        "--mandate-id",
        default=None,
        help="In Razorpay the mandate IS the subscription, so this should be the "
        "subscription id (`sub_...`). `extract_mandate_id` reads "
        "`payload.subscription.entity.id` ahead of the payment's `token_id`, so a "
        "mandate seeded under any other id is invisible to events that carry a "
        "subscription entity. Defaults to `sub_<tenant_id>_1`.",
    )
    parser.add_argument("--rail", default="upi_autopay")
    parser.add_argument("--max-amount-paise", type=int, default=1_500_000)
    args = parser.parse_args()

    asyncio.run(
        bootstrap(
            tenant_id=args.tenant_id,
            name=args.name or args.tenant_id,
            stage=Stage[args.stage],
            rail=args.rail,
            max_amount_paise=args.max_amount_paise,
            mandate_id=args.mandate_id or f"sub_{args.tenant_id}_1",
        )
    )


if __name__ == "__main__":
    main()
