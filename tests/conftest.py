"""Shared fixtures.

Database-backed tests need a live PostgreSQL 16 instance and both connection
URLs. They skip cleanly when those are absent so that `pytest` on a laptop
without Docker still runs the unit tests.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from prayas.db.engine import create_app_engine, create_owner_engine

TENANT_A = "t_alpha"
TENANT_B = "t_beta"

_OWNER_URL = os.environ.get("PRAYAS_DATABASE_URL_OWNER")
_APP_URL = os.environ.get("PRAYAS_DATABASE_URL_APP")

requires_db = pytest.mark.skipif(
    not (_OWNER_URL and _APP_URL),
    reason="PRAYAS_DATABASE_URL_OWNER and PRAYAS_DATABASE_URL_APP must be set",
)


@pytest.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    """Superuser/owner engine. Bypasses RLS, so it is used only for setup."""
    assert _OWNER_URL is not None
    engine = create_owner_engine(_OWNER_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    """The engine under test: non-owner role, RLS applies unconditionally."""
    assert _APP_URL is not None
    engine = create_app_engine(_APP_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_tenant(conn: AsyncConnection, tenant: str) -> None:
    """Insert exactly one row into every tenant-scoped table for `tenant`.

    One row per table per tenant is what lets the isolation test assert an exact
    count of 1 for the bound tenant and 0 for the other — a weaker "no rows from
    B" assertion would pass even if the query returned nothing at all.
    """
    now = datetime.now(tz=UTC)
    params = {"t": tenant, "now": now}

    await conn.execute(text("INSERT INTO tenants (tenant_id, name) VALUES (:t, :t)"), params)
    await conn.execute(
        text(
            "INSERT INTO events_raw (event_id, tenant_id, event_type, payload, signature_ok)"
            " VALUES (:t || '_evt', :t, 'payment.failed', '{}'::jsonb, true)"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
            " max_amount_paise, state, consent_ref, created_at)"
            " VALUES (:t || '_mnd', :t, :t || '_cust', 'upi_autopay',"
            " 1500000, 'active', :t || '_consent', :now)"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
            " due_at, deadline_at, attempt_budget, state)"
            " VALUES (:t || '_cyc', :t, :t || '_mnd', 1, 49900, :now, :now, 4, 'pending')"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO attempts (attempt_id, tenant_id, cycle_id, attempt_seq,"
            " idem_key, scheduled_for, state, decision_id)"
            " VALUES (:t || '_att', :t, :t || '_cyc', 1, :t || '_idem', :now,"
            " 'scheduled', :t || '_dec')"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO interventions (intervention_id, tenant_id, mandate_id, kind,"
            " proposed_at, payload, decision_id)"
            " VALUES (:t || '_int', :t, :t || '_mnd', 'date_change', :now,"
            " '{}'::jsonb, :t || '_dec')"
        ),
        params,
    )
    await conn.execute(
        text("INSERT INTO customer_profiles (tenant_id, customer_id) VALUES (:t, :t || '_cust')"),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO decisions (decision_id, tenant_id, chain_seq, prev_hash,"
            " record_hash, ts, action_type, verdict, model_versions, candidate_actions,"
            " compliance_checks)"
            " VALUES (:t || '_dec', :t, 1, 'genesis', 'h0', :now, 'retry', 'ALLOW',"
            " '{}'::jsonb, '[]'::jsonb, '[]'::jsonb)"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO scheduled_actions (action_id, tenant_id, action_type, fire_at, payload)"
            " VALUES (:t || '_sch', :t, 'retry', :now, '{}'::jsonb)"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO outbox (tenant_id, idem_key, target, request)"
            " VALUES (:t, :t || '_obx', 'razorpay', '{}'::jsonb)"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO experiment_config (experiment_id, tenant_id, seed, control_pct,"
            " git_commit, committed_at)"
            " VALUES (:t || '_exp', :t, 'seed', 0.100, 'abc123', :now)"
        ),
        params,
    )


async def _truncate_all(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "TRUNCATE experiment_config, outbox, scheduled_actions, decisions,"
            " customer_profiles, interventions, attempts, cycles, mandates,"
            " events_raw, tenants RESTART IDENTITY CASCADE"
        )
    )


@pytest.fixture
async def two_tenants(owner_engine: AsyncEngine) -> AsyncIterator[tuple[str, str]]:
    """Two fully-populated tenants. Torn down after each test."""
    async with owner_engine.begin() as conn:
        await _truncate_all(conn)
        await _seed_tenant(conn, TENANT_A)
        await _seed_tenant(conn, TENANT_B)

    yield TENANT_A, TENANT_B

    async with owner_engine.begin() as conn:
        await _truncate_all(conn)
