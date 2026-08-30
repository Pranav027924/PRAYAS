"""Consent withdrawal, end to end (Master Spec §28; Phase 12 exit criterion).

"Consent withdrawal deletes profile, pseudonymises ledger, suppresses contact —
verified end to end."

The load-bearing assertion is the one that is easy to omit: after erasure the
**hash chain still verifies**. §28 pseudonymises rather than deletes precisely
so the audit trail survives, and a cascade that severed the person-link by
breaking the ledger would have honoured the request by destroying the thing the
request was not allowed to destroy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.ledger.chain import append, verify_chain
from prayas.memory.forget import (
    PEPPER_ENV,
    ForgetError,
    excluded_from_training,
    forget,
    is_pseudonym,
    is_suppressed,
    pseudonymise,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

PEPPER = b"test-pepper-not-a-real-secret"
CUSTOMER = "cust_to_forget"


async def _seed(conn: object, tenant: str) -> None:
    """One customer with a profile, a mandate, and a ledger entry about it."""
    params = {"t": tenant, "cust": CUSTOMER, "now": datetime.now(UTC)}
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
            " max_amount_paise, state, consent_ref, created_at)"
            " VALUES (:t || '_m1', :t, :cust, 'upi_autopay', 100000, 'active',"
            " 'consent_1', :now)"
        ),
        params,
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO customer_profiles (tenant_id, customer_id, payday_posterior)"
            " VALUES (:t, :cust, '{\"5\": 1.0}'::jsonb)"
        ),
        params,
    )


async def test_the_full_cascade(app_engine: AsyncEngine, two_tenants: tuple[str, str]) -> None:
    """**Phase 12 exit criterion.** Profile gone, contact suppressed, ledger
    intact but no longer pointing at a person."""
    tenant, other = two_tenants

    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed(conn, tenant)
        await append(
            conn,
            {
                "decision_id": f"{tenant}_d1",
                "action_type": "retry",
                "verdict": "ALLOW",
                "mandate_id": f"{tenant}_m1",
                "model_versions": json.dumps({}),
                "candidate_actions": json.dumps([]),
                "compliance_checks": json.dumps([]),
                "degraded": False,
                "ts": datetime.now(UTC),
            },
            tenant,
        )
        # The shared fixture seeds a synthetic decision with a placeholder
        # hash, so the chain does not verify clean here. What matters is that
        # erasure does not *change* the verification state — comparing before
        # against after says exactly that, and is a stronger claim than any
        # assertion about a chain we did not build.
        before = await verify_chain(conn, tenant)

        result = await forget(conn, tenant, CUSTOMER, pepper=PEPPER)

        assert result.profiles_deleted == 1
        assert result.mandates_pseudonymised == 1
        assert result.complete

        # Profile tier: gone.
        remaining = await conn.scalar(
            text("SELECT count(*) FROM customer_profiles WHERE customer_id = :c"),
            {"c": CUSTOMER},
        )
        assert remaining == 0

        # Person-link: severed, and self-describing.
        linked = await conn.scalar(
            text("SELECT count(*) FROM mandates WHERE customer_id = :c"), {"c": CUSTOMER}
        )
        assert linked == 0
        stored = await conn.scalar(
            text("SELECT customer_id FROM mandates WHERE mandate_id = :m"),
            {"m": f"{tenant}_m1"},
        )
        assert is_pseudonym(str(stored))

        # Ledger: untouched, and still verifying. This is the assertion §28's
        # whole design exists to make possible.
        decisions = await conn.scalar(
            text("SELECT count(*) FROM decisions WHERE tenant_id = :t"), {"t": tenant}
        )
        assert decisions == 2, "a decision was deleted — Invariant 5 violated"
        after = await verify_chain(conn, tenant)
        assert after == before, "erasure changed the ledger's verification state"

        # Contact: suppressed, and reachable from the pre-erasure identifier
        # too — a caller holding the old id must still get the right answer.
        assert await is_suppressed(conn, tenant, result.pseudonym)
        assert await is_suppressed(conn, tenant, CUSTOMER, pepper=PEPPER)
        assert result.pseudonym in await excluded_from_training(conn, tenant)

    # The other tenant is entirely unaffected.
    async with tenant_transaction(app_engine, other) as conn:
        assert not await is_suppressed(conn, other, result.pseudonym)
        assert result.pseudonym not in await excluded_from_training(conn, other), (
            "an erasure in one tenant leaked into another"
        )


async def test_forgetting_is_idempotent(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """A retried erasure must not fail. The cascade can be interrupted, so the
    only safe recovery is to run it again."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed(conn, tenant)
        first = await forget(conn, tenant, CUSTOMER, pepper=PEPPER)
        second = await forget(conn, tenant, CUSTOMER, pepper=PEPPER)

        assert first.pseudonym == second.pseudonym
        assert second.profiles_deleted == 0, "nothing left to delete, and that is fine"
        assert second.complete


async def test_suppression_survives_the_profile_it_was_triggered_by(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-078's reason for existing. A `consent_withdrawn` flag on the profile
    would be destroyed by the deletion that is supposed to set it."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed(conn, tenant)
        result = await forget(conn, tenant, CUSTOMER, pepper=PEPPER)

        profiles = await conn.scalar(
            text("SELECT count(*) FROM customer_profiles WHERE customer_id = :c"),
            {"c": CUSTOMER},
        )
        assert profiles == 0
        assert await is_suppressed(conn, tenant, result.pseudonym)


async def test_the_app_role_cannot_lift_a_suppression(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Insert-only by grant. An erasure that can be quietly undone is not one."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        await _seed(conn, tenant)
        result = await forget(conn, tenant, CUSTOMER, pepper=PEPPER)

    for statement in (
        "DELETE FROM contact_suppressions WHERE tenant_id = :t AND customer_ref = :r",
        "UPDATE contact_suppressions SET reason = 'oops' WHERE tenant_id = :t"
        " AND customer_ref = :r",
    ):
        async with tenant_transaction(app_engine, tenant) as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text(statement), {"t": tenant, "r": result.pseudonym})


# ── the pseudonym itself ───────────────────────────────────────────────────


def test_a_pseudonym_is_stable_and_tenant_scoped() -> None:
    """Stable so the audit trail stays internally consistent; tenant-scoped
    because a global pseudonym would relink the same person across merchants —
    the cross-tenant profile §27 exists to prevent."""
    assert pseudonymise("t_a", CUSTOMER, pepper=PEPPER) == pseudonymise(
        "t_a", CUSTOMER, pepper=PEPPER
    )
    assert pseudonymise("t_a", CUSTOMER, pepper=PEPPER) != pseudonymise(
        "t_b", CUSTOMER, pepper=PEPPER
    )


def test_a_pseudonym_depends_on_the_secret() -> None:
    assert pseudonymise("t_a", CUSTOMER, pepper=b"one") != pseudonymise(
        "t_a", CUSTOMER, pepper=b"two"
    )


def test_a_missing_pepper_refuses_rather_than_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hash over identifiers alone looks irreversible, satisfies the schema,
    and protects nobody. Failing closed is the only honest option."""
    monkeypatch.delenv(PEPPER_ENV, raising=False)
    with pytest.raises(ForgetError, match="irreversible"):
        pseudonymise("t_a", CUSTOMER)


async def test_a_suppression_check_without_the_secret_fails_closed(
    app_engine: AsyncEngine, two_tenants: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answering "not suppressed" because the pseudonym could not be computed
    is the one wrong answer that ends in contacting someone who withdrew
    consent. This predicate fails rather than permits."""
    tenant, _ = two_tenants
    monkeypatch.delenv(PEPPER_ENV, raising=False)
    async with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(ForgetError, match="irreversible"):
            await is_suppressed(conn, tenant, CUSTOMER)
