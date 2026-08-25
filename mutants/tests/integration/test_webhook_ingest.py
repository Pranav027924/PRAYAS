"""Webhook ingestion: dedupe, authentication, and the late-capture guard.

Covers three of Phase 1's four exit criteria. The fourth — permutation
convergence — is in `tests/property/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.ingest.projector import project_tenant
from tests.conftest import TENANT_A, razorpay_event, requires_db, sign

pytestmark = [pytest.mark.db, requires_db]

PATH = f"/v1/webhooks/razorpay/{TENANT_A}"


@pytest.fixture
async def client(app_engine: AsyncEngine, webhook_tenant: str) -> AsyncIterator[AsyncClient]:
    """Drive the ASGI app in-process on the *current* event loop.

    `TestClient` spins up its own loop, which strands the asyncpg pool created
    on pytest-asyncio's loop ("Event loop is closed"). ASGITransport keeps the
    app and the database on one loop. Starlette also now deprecates TestClient
    with httpx, so this is the forward path regardless.
    """
    app.state.engine = app_engine
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://prayas.test"
    ) as async_client:
        yield async_client


async def _post(
    client: AsyncClient, body: bytes, *, signature: str | None = None, event_id: str = "evt_1"
) -> Response:
    return await client.post(
        PATH,
        content=body,
        headers={
            "x-razorpay-signature": signature if signature is not None else sign(body),
            "x-razorpay-event-id": event_id,
            "content-type": "application/json",
        },
    )


# ── Exit criterion: same event 100x → exactly one state transition ───────────


async def test_same_event_delivered_100_times_produces_one_transition(
    client: AsyncClient, app_engine: AsyncEngine, webhook_tenant: str
) -> None:
    """Asserts projected state, not just row count.

    Dedupe that stores once but projects twice would satisfy a row-count-only
    assertion while still double-counting the regulator's budget.
    """
    body = razorpay_event(
        event_id="evt_dup", event_type="payment.failed", mandate_id=f"{TENANT_A}_mnd"
    )

    codes = {(await _post(client, body, event_id="evt_dup")).status_code for _ in range(100)}
    assert codes == {200}, f"expected every delivery to ack, got {codes}"

    await project_tenant(app_engine, webhook_tenant)

    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": webhook_tenant}
        )
        stored = await conn.scalar(text("SELECT count(*) FROM events_raw"))
        attempts_used = await conn.scalar(
            text("SELECT attempts_used FROM cycles WHERE tenant_id = :t"), {"t": webhook_tenant}
        )

    assert stored == 1, f"100 deliveries persisted {stored} rows"
    assert attempts_used == 1, f"budget consumed {attempts_used} times, must be 1"


# ── Exit criterion: unverified payloads rejected and never persisted ─────────


@pytest.mark.parametrize(
    ("label", "signature"),
    [
        ("wrong signature", "0" * 64),
        ("empty signature", ""),
        ("truncated signature", "abc123"),
        ("signature from another secret", None),  # filled in below
    ],
)
async def test_unverified_payloads_are_rejected_and_never_persisted(
    client: AsyncClient,
    app_engine: AsyncEngine,
    webhook_tenant: str,
    label: str,
    signature: str | None,
) -> None:
    """§41.1 T6 — webhook forgery. Rejection alone is not enough; nothing may land."""
    body = razorpay_event(
        event_id="evt_forged", event_type="payment.failed", mandate_id=f"{TENANT_A}_mnd"
    )
    sig_value = sign(body, "an_entirely_different_secret") if signature is None else signature

    response = await _post(client, body, signature=sig_value, event_id="evt_forged")
    assert response.status_code == 401, f"{label}: expected 401, got {response.status_code}"

    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": webhook_tenant}
        )
        stored = await conn.scalar(text("SELECT count(*) FROM events_raw"))

    assert stored == 0, f"{label}: rejected payload was persisted anyway"


async def test_body_tampering_after_signing_is_rejected(
    client: AsyncClient, app_engine: AsyncEngine, webhook_tenant: str
) -> None:
    """The signature is over raw bytes, so any mutation must invalidate it."""
    body = razorpay_event(
        event_id="evt_tamper", event_type="payment.failed", mandate_id=f"{TENANT_A}_mnd"
    )
    signature = sign(body)
    tampered = body.replace(b'"amount":49900', b'"amount":1')
    assert tampered != body, "test setup failed to mutate the body"

    response = await _post(client, tampered, signature=signature, event_id="evt_tamper")
    assert response.status_code == 401


async def test_valid_signature_from_a_rotated_secret_is_accepted(
    client: AsyncClient,
    app_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    webhook_tenant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotation window: two secrets valid at once, either one verifies."""
    monkeypatch.setenv("PRAYAS_WEBHOOK_SECRET_WH_TEST_V2", "second_secret_material")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, 'WH_TEST_V2')"),
            {"t": webhook_tenant},
        )

    body = razorpay_event(
        event_id="evt_rot", event_type="payment.failed", mandate_id=f"{TENANT_A}_mnd"
    )
    response = await _post(
        client, body, signature=sign(body, "second_secret_material"), event_id="evt_rot"
    )
    assert response.status_code == 200, "the newly rotated-in secret was not accepted"


# ── Exit criterion: failed → captured leaves zero pending actions ────────────


async def test_late_capture_cancels_pending_scheduled_actions(
    client: AsyncClient, app_engine: AsyncEngine, owner_engine: AsyncEngine, webhook_tenant: str
) -> None:
    """`payment.failed` then `payment.captured` must leave nothing pending.

    Nothing schedules actions until Phase 6, so the pending row is seeded
    directly — the guard must already be correct when Phase 6 arrives.
    """
    mandate = f"{TENANT_A}_mnd"
    invoice = f"{mandate}_inv1"

    failed = razorpay_event(
        event_id="evt_fail", event_type="payment.failed", mandate_id=mandate, invoice_id=invoice
    )
    assert (await _post(client, failed, event_id="evt_fail")).status_code == 200
    await project_tenant(app_engine, webhook_tenant)

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions"
                " (action_id, tenant_id, cycle_id, mandate_id, action_type, fire_at, payload)"
                " VALUES ('act_1', :t, :c, :m, 'retry', now() + interval '1 day', '{}'::jsonb)"
            ),
            {"t": webhook_tenant, "c": invoice, "m": mandate},
        )

    captured = razorpay_event(
        event_id="evt_cap",
        event_type="payment.captured",
        mandate_id=mandate,
        invoice_id=invoice,
        created_at=1_767_312_000,
    )
    assert (await _post(client, captured, event_id="evt_cap")).status_code == 200
    await project_tenant(app_engine, webhook_tenant)

    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": webhook_tenant}
        )
        pending = await conn.scalar(
            text(
                "SELECT count(*) FROM scheduled_actions"
                " WHERE tenant_id = :t AND cycle_id = :c AND state = 'pending'"
            ),
            {"t": webhook_tenant, "c": invoice},
        )
        state = await conn.scalar(
            text("SELECT state FROM cycles WHERE tenant_id = :t AND cycle_id = :c"),
            {"t": webhook_tenant, "c": invoice},
        )

    assert pending == 0, f"{pending} actions still pending after the cycle was paid"
    assert state == "succeeded"
