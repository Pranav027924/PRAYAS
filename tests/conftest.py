"""Shared fixtures.

Database-backed tests need a live PostgreSQL 16 instance and both connection
URLs. They skip cleanly when those are absent so that `pytest` on a laptop
without Docker still runs the unit tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

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

    # ADR-089 — `adoption_stage: 4` is FULL. Tests written before the adoption
    # ramp existed assume a fully-onboarded tenant, and a tenant with no stage
    # now reads as OBSERVE and fires nothing.
    #
    # **If a new test fails because nothing fired, seed the stage here — do not
    # change the default.** OBSERVE-on-missing is deliberate: a tenant nobody
    # has onboarded is not one to start debiting for, and firing on silence
    # would be the worst available reading of an absent row.
    await conn.execute(
        text(
            "INSERT INTO tenants (tenant_id, name, config) VALUES"
            " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
        ),
        params,
    )
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
    # ADR-078. Keyed on the pseudonym, because §28 severs the person link and a
    # suppression keyed on a customer_id that no longer exists matches nothing.
    await conn.execute(
        text(
            "INSERT INTO contact_suppressions (tenant_id, customer_ref, reason)"
            " VALUES (:t, :t || '_pseudo', 'consent_withdrawn')"
        ),
        params,
    )
    # ADR-081 / ADR-082.
    await conn.execute(
        text(
            "INSERT INTO inbound_replies (reply_id, tenant_id, customer_ref, received_at,"
            " raw_text) VALUES (:t || '_r1', :t, :t || '_cust', :now, 'salary 5 tarikh')"
        ),
        params,
    )
    await conn.execute(
        text(
            "INSERT INTO rule_proposals (proposal_id, tenant_id, proposed_at, source_text,"
            " proposed_rule) VALUES (:t || '_p1', :t, :now, 'no debits on sundays',"
            " '{}'::jsonb)"
        ),
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
    await conn.execute(
        text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, 'WH_SEED_V1')"),
        params,
    )


async def _truncate_all(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "TRUNCATE sim_ground_truth, webhook_secrets, experiment_config, outbox, scheduled_actions,"
            " decisions, rule_proposals, inbound_replies, contact_suppressions,"
            " customer_profiles, interventions, attempts,"
            " cycles, mandates, events_raw, tenants RESTART IDENTITY CASCADE"
        )
    )


WEBHOOK_SECRET_REF = "WH_TEST_V1"
WEBHOOK_SECRET = "test_webhook_secret_material"


def sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Produce the signature Razorpay would send for this exact byte sequence."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def razorpay_event(
    *,
    event_id: str,
    event_type: str,
    mandate_id: str,
    invoice_id: str | None = None,
    amount_paise: int = 49900,
    created_at: int = 1_767_225_600,  # 2026-01-01T00:00:00Z, fixed for determinism
    next_billing_at: int = 1_769_904_000,  # 2026-02-01T00:00:00Z
) -> bytes:
    """A Razorpay-shaped webhook body, serialised exactly once.

    Returned as bytes because the signature is over bytes; a test that
    re-serialises would not be testing what production verifies.
    """
    body: dict[str, Any] = {
        "entity": "event",
        "event": event_type,
        "created_at": created_at,
        "contains": ["payment", "subscription"],
        "payload": {
            "subscription": {"entity": {"id": mandate_id, "current_end": next_billing_at}},
            "payment": {
                "entity": {
                    "id": event_id + "_pay",
                    "amount": amount_paise,
                    "invoice_id": invoice_id or f"{mandate_id}_inv1",
                }
            },
        },
    }
    return json.dumps(body, separators=(",", ":")).encode()


@pytest.fixture
async def webhook_tenant(
    owner_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """A tenant with an active webhook secret ref and its material in the env."""
    monkeypatch.setenv(f"PRAYAS_WEBHOOK_SECRET_{WEBHOOK_SECRET_REF}", WEBHOOK_SECRET)

    async with owner_engine.begin() as conn:
        await _truncate_all(conn)
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config) VALUES"
                " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
            ),
            {"t": TENANT_A},
        )
        await conn.execute(
            text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, :ref)"),
            {"t": TENANT_A, "ref": WEBHOOK_SECRET_REF},
        )
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, 'cust_1', 'upi_autopay', 1500000, 'active', 'c1', now())"
            ),
            {"m": f"{TENANT_A}_mnd", "t": TENANT_A},
        )

    yield TENANT_A

    async with owner_engine.begin() as conn:
        await _truncate_all(conn)


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
