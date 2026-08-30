"""The ledger verifier must actually verify something (FINDING-P15-01, §32).

The verifier previously enumerated tenants with `SELECT tenant_id FROM tenants`
inside a system transaction. That table is RLS-forced on `app.tenant_id`, so
with no tenant bound the policy matched nothing and the query returned zero
rows — silently. The verifier then reported `tenants: 0, breaks: 0` and exited
successfully, having checked no chain at all.

**A verifier that passes vacuously is worse than no verifier**: it produces
evidence of a property it never tested, and §32's whole claim is that the
ledger is verifiable.

Found by the Phase 15 backup/restore drill, which noticed the verification step
reporting success over an empty result.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction, tenant_transaction
from prayas.ledger.chain import append, verify_chain
from prayas.ledger.verify import _tenants
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]


async def _seed_chain(engine: AsyncEngine, tenant: str, n: int = 5) -> None:
    async with tenant_transaction(engine, tenant) as conn:
        for i in range(n):
            await append(
                conn,
                {
                    "decision_id": f"{tenant}_vac_{i:03d}",
                    "ts": datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=i),
                    "action_type": "debit_attempt",
                    "verdict": "ALLOW",
                    "model_versions": json.dumps({}),
                    "candidate_actions": json.dumps([]),
                    "compliance_checks": json.dumps([]),
                    "degraded": False,
                },
                tenant,
            )


async def test_the_verifier_enumerates_tenants_without_a_bound_context(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**The bug.** With no tenant bound, RLS on `tenants` hides every row —
    so the enumeration must go through ADR-046's SECURITY DEFINER registry."""
    tenant_a, tenant_b = two_tenants
    found = await _tenants(app_engine)

    assert tenant_a in found and tenant_b in found
    assert len(found) >= 2


async def test_the_naive_query_really_does_return_nothing(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Pins *why* the fix is needed, so a future refactor back to the obvious
    query fails here with the reason attached rather than passing silently."""
    async with system_transaction(app_engine) as conn:
        rows = (await conn.execute(text("SELECT tenant_id FROM tenants"))).fetchall()

    assert rows == [], (
        "RLS no longer hides tenants from an unbound context — if this changed "
        "deliberately, FINDING-P15-01's reasoning needs revisiting"
    )


async def test_verification_covers_a_seeded_chain(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """End to end: a real chain exists, and the verifier reaches it."""
    tenant, _ = two_tenants

    # The shared fixture seeds one synthetic decision with a placeholder hash,
    # so the chain does not verify clean here. What matters is that appending a
    # real chain introduces no *new* breaks — a stronger claim than any
    # assertion about a chain we did not build.
    async with tenant_transaction(app_engine, tenant) as conn:
        before = await verify_chain(conn, tenant)

    await _seed_chain(app_engine, tenant)

    assert tenant in await _tenants(app_engine)

    async with tenant_transaction(app_engine, tenant) as conn:
        after = await verify_chain(conn, tenant)

    assert len(after) == len(before), "appending a valid chain introduced a break"


async def test_a_tampered_chain_is_actually_caught(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """The decisive check. If the verifier cannot fail, its passing means
    nothing — and a vacuous verifier is exactly the failure mode that hid
    behind `tenants: 0`."""
    tenant, _ = two_tenants
    await _seed_chain(app_engine, tenant)

    # Tamper as the owner: the app role holds no UPDATE on `decisions`, which
    # is what Invariant 5 is for.
    async with system_transaction(owner_engine) as conn:
        await conn.execute(
            text(
                "UPDATE decisions SET action_type = 'tampered'"
                " WHERE tenant_id = :t AND chain_seq = 2"
            ),
            {"t": tenant},
        )

    async with tenant_transaction(app_engine, tenant) as conn:
        breaks = await verify_chain(conn, tenant)

    assert any("hash mismatch" in b.reason for b in breaks), (
        "a tampered ledger verified clean — if the verifier cannot fail, its passing means nothing"
    )
