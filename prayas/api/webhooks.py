"""Webhook ingestion (Appendix B; Playbook Phase 1; §41.1 T6).

Ack fast, process later: verify, persist, return. Projection happens separately
so the handler stays inside Appendix B's <100ms budget.

Ordering here is load-bearing and deliberate:

1. Read the **raw bytes**. Never parse before verifying — the signature is over
   what the sender sent, and re-serialising changes the bytes.
2. Resolve secret refs through the SECURITY DEFINER function, with **no tenant
   context bound**. §18 forbids setting `app.tenant_id` from a request
   parameter, and the path segment is exactly that until the HMAC proves it.
3. Verify. On failure return 401 having persisted nothing (T6).
4. Only now bind tenant context — the signature is the verified credential —
   and insert.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import (
    TenantContextError,
    system_transaction,
    tenant_transaction,
    validate_tenant_id,
)
from prayas.ingest import verify as sig
from prayas.ingest.envelope import MalformedEventError, parse
from prayas.observability import metrics

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["ingest"])

#: Cap the body we will hash. An unbounded body is a cheap denial-of-wallet
#: vector (§41.1 T9) since HMAC cost scales with length.
MAX_BODY_BYTES = 1 * 1024 * 1024


async def _active_secret_refs(engine: AsyncEngine, tenant_id: str) -> list[str]:
    """Read secret refs with no tenant context bound (ADR-014)."""
    async with system_transaction(engine) as conn:
        result = await conn.execute(
            text("SELECT secret_ref FROM prayas_active_webhook_secret_refs(:tenant_id)"),
            {"tenant_id": tenant_id},
        )
        return [row.secret_ref for row in result]


@router.post("/razorpay/{tenant_id}")
async def razorpay_webhook(
    tenant_id: str,
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
) -> JSONResponse:
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        return JSONResponse(status_code=503, content={"status": "degraded"})

    try:
        claimed_tenant = validate_tenant_id(tenant_id)
    except TenantContextError:
        # Do not echo the value back; it is attacker-controlled.
        metrics.increment("webhook_rejected", reason="malformed_tenant")
        return JSONResponse(status_code=401, content={"status": "rejected"})

    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        metrics.increment("webhook_rejected", reason="body_too_large")
        return JSONResponse(status_code=413, content={"status": "rejected"})

    refs = await _active_secret_refs(engine, claimed_tenant)
    secrets = sig.resolve_all(refs)

    if not sig.verify(raw_body, x_razorpay_signature, secrets):
        # T6: unverified payloads are never persisted. Nothing has been written
        # at this point, and nothing will be.
        metrics.increment("webhook_rejected", reason="bad_signature", tenant_id=claimed_tenant)
        log.warning("webhook.signature_rejected", extra={"tenant_id": claimed_tenant})
        return JSONResponse(status_code=401, content={"status": "rejected"})

    # The signature verified, so the tenant claim is now proven.
    tenant = claimed_tenant

    try:
        event = parse(raw_body, tenant_id=tenant, event_id=x_razorpay_event_id or None)
    except MalformedEventError as exc:
        metrics.increment("webhook_rejected", reason="malformed_body", tenant_id=tenant)
        log.warning("webhook.malformed", extra={"tenant_id": tenant, "error": str(exc)})
        return JSONResponse(status_code=400, content={"status": "malformed"})

    async with tenant_transaction(engine, tenant) as conn:
        result = await conn.execute(
            text(
                "INSERT INTO events_raw"
                " (event_id, tenant_id, event_type, mandate_id, payload, signature_ok)"
                " VALUES (:event_id, :tenant_id, :event_type, :mandate_id,"
                "         CAST(:payload AS jsonb), true)"
                " ON CONFLICT (event_id) DO NOTHING"
            ),
            {
                "event_id": event.event_id,
                "tenant_id": tenant,
                "event_type": event.event_type,
                "mandate_id": event.mandate_id,
                "payload": _json(event.payload),
            },
        )
        duplicate = result.rowcount == 0

    metrics.increment(
        "webhook_accepted",
        tenant_id=tenant,
        event_type=event.event_type,
        duplicate=duplicate,
    )
    # 200 either way: a duplicate is a successful delivery of something already
    # known, and returning an error would make the provider retry forever.
    return JSONResponse(status_code=200, content={"status": "accepted", "duplicate": duplicate})


def _json(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
