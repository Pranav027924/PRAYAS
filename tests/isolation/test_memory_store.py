"""Profile persistence and its tenant boundary (Master Spec §25, §27).

Phase 12 exit criterion: "No individual profile data crosses a tenant boundary
— asserted by test." The generic isolation suite already parametrises over
`customer_profiles`; this asserts the same property *through the memory API*,
because that is the path application code will actually take, and an API that
quietly ignored its tenant argument would pass the generic test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.memory.profile import (
    CustomerPaymentProfile,
    empty_profile,
    record_contact,
    record_declared_hint,
    update_payday,
)
from prayas.memory.store import load, load_or_empty, save
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

T0 = datetime(2026, 3, 1, tzinfo=UTC)
CUSTOMER = "cust_store"


def _rich(tenant: str) -> CustomerPaymentProfile:
    profile = empty_profile(tenant, CUSTOMER)
    for month in range(6):
        profile = update_payday(profile, 5, at=T0 + timedelta(days=30 * month))
    profile = record_declared_hint(profile, 7, at=T0)
    return record_contact(profile, at=T0)


async def test_a_profile_round_trips(app_engine: AsyncEngine, two_tenants: tuple[str, str]) -> None:
    tenant, _ = two_tenants
    original = _rich(tenant)

    async with tenant_transaction(app_engine, tenant) as conn:
        await save(conn, original)
        restored = await load(conn, tenant, CUSTOMER)

    assert restored is not None
    assert restored.modal_payday() == original.modal_payday()
    assert restored.declared_funding_day == 7
    assert restored.messages_30d == 1


async def test_the_stored_posterior_keeps_its_accumulated_evidence(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-077, at the storage boundary. Persisting the *normalised* posterior
    would discard the count on every round-trip, so a profile written and read
    back would forget how much it had seen — the same defect ADR-077 removed
    from the update rule, reintroduced through the database."""
    tenant, _ = two_tenants
    original = _rich(tenant)

    async with tenant_transaction(app_engine, tenant) as conn:
        await save(conn, original)
        restored = await load(conn, tenant, CUSTOMER)

    assert restored is not None
    assert restored.observations == pytest.approx(original.observations, rel=1e-6)
    assert restored.observations > 1.0, "the posterior was normalised away in storage"
    assert restored.payday_confidence == pytest.approx(original.payday_confidence, abs=0.01)


async def test_a_profile_does_not_cross_a_tenant_boundary(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**Phase 12 exit criterion**, through the API rather than raw SQL.

    §27: individual memory is strictly tenant-scoped, because a cross-merchant
    behavioural profile "looks like a feature and is actually a liability".
    """
    tenant_a, tenant_b = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        await save(conn, _rich(tenant_a))

    async with tenant_transaction(app_engine, tenant_b) as conn:
        assert await load(conn, tenant_b, CUSTOMER) is None, "profile leaked across tenants"
        # Asking for it under A's name from B's session must also see nothing:
        # RLS confines the row, not merely the predicate.
        assert await load(conn, tenant_a, CUSTOMER) is None

        cold = await load_or_empty(conn, tenant_b, CUSTOMER)
        assert cold.observations == 0.0, "cold start read another tenant's evidence"


async def test_the_same_person_at_two_merchants_stays_two_profiles(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """The exact linkage §27 forbids: one customer id, two tenants, and the two
    payday distributions must not merge."""
    tenant_a, tenant_b = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        profile = empty_profile(tenant_a, CUSTOMER)
        for month in range(6):
            profile = update_payday(profile, 1, at=T0 + timedelta(days=30 * month))
        await save(conn, profile)

    async with tenant_transaction(app_engine, tenant_b) as conn:
        profile = empty_profile(tenant_b, CUSTOMER)
        for month in range(6):
            profile = update_payday(profile, 25, at=T0 + timedelta(days=30 * month))
        await save(conn, profile)
        stored_b = await load(conn, tenant_b, CUSTOMER)
        assert stored_b is not None and stored_b.modal_payday() == 25

    async with tenant_transaction(app_engine, tenant_a) as conn:
        stored_a = await load(conn, tenant_a, CUSTOMER)
        assert stored_a is not None and stored_a.modal_payday() == 1


async def test_never_seen_is_distinguishable_from_seen_and_empty(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Only the caller knows which default is right for what it is about to do."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        assert await load(conn, tenant, "never_seen") is None

        await save(conn, empty_profile(tenant, "seen_but_empty"))
        seen = await load(conn, tenant, "seen_but_empty")
        assert seen is not None and seen.observations == 0.0
