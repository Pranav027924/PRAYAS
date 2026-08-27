"""Transactional outbox relay (Master Spec §31).

The fire transaction commits *intent*. This relay acts on it afterwards, which
is the ordering §31 insists on:

    "Committing intent before the external call means a crash between the two
    loses nothing - the relay resumes with the same key. Calling first and
    recording after would risk a debit with no local record, the worst possible
    ordering."

**The ambiguous case is the reason this module is careful.** A timeout means
the debit may have happened. The row stays `ambiguous`, the attempt keeps its
budget slot, and reconciliation resolves it by key. Nothing here ever issues a
fresh charge for an outcome it does not understand - §31: "Never re-issue as a
new charge; retry with the same key, then reconcile by key."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.executor.provider import Outcome, ProviderResponse, RailProvider
from prayas.observability import metrics

log = logging.getLogger(__name__)

STATE_PENDING: Final = "pending"
STATE_SENT: Final = "sent"
STATE_FAILED: Final = "failed"
STATE_AMBIGUOUS: Final = "ambiguous"

#: Retries of a definitely-nothing-happened failure. An AMBIGUOUS row is not
#: retried on this counter; it waits for reconciliation.
MAX_RELAY_ATTEMPTS: Final = 5


@dataclass(frozen=True, slots=True)
class RelayResult:
    sent: int
    ambiguous: int
    failed: int
    retriable: int


async def _claim_outbox(conn: AsyncConnection, tenant_id: str, batch: int) -> list[Any]:
    """Claim pending rows with SKIP LOCKED, same discipline as the timers."""
    result = await conn.execute(
        text(
            "SELECT outbox_id, idem_key, target, request, attempts"
            "  FROM outbox"
            " WHERE tenant_id = :tenant_id AND state = :pending"
            "   AND attempts < :max_attempts"
            " ORDER BY outbox_id"
            " FOR UPDATE SKIP LOCKED"
            " LIMIT :batch"
        ),
        {
            "tenant_id": tenant_id,
            "pending": STATE_PENDING,
            "max_attempts": MAX_RELAY_ATTEMPTS,
            "batch": batch,
        },
    )
    return list(result)


async def relay_once(
    engine: AsyncEngine, tenant_id: str, provider: RailProvider, *, batch: int = 50
) -> RelayResult:
    """Relay one batch for a tenant.

    Each row is claimed, called, and settled in its own transaction so a
    provider call never holds a lock across the network, and a crash mid-flight
    leaves the row claimable again with its key unchanged.
    """
    sent = ambiguous = failed = retriable = 0

    async with tenant_transaction(engine, tenant_id) as conn:
        rows = await _claim_outbox(conn, tenant_id, batch)
        pending = [
            (r.outbox_id, r.idem_key, r.request if isinstance(r.request, dict) else {})
            for r in rows
        ]

    for outbox_id, key, request in pending:
        response = await provider.submit_debit(
            idem_key=key,
            amount_paise=int(request.get("amount_paise", 0)),
            mandate_id=str(request.get("mandate_id", "")),
            request=request,
        )

        async with tenant_transaction(engine, tenant_id) as conn:
            if response.outcome is Outcome.ACCEPTED:
                await _settle(conn, outbox_id, tenant_id, STATE_SENT)
                await _mark_attempt(conn, tenant_id, key, "fired", response.provider_ref, None)
                sent += 1
            elif response.outcome is Outcome.DECLINED:
                await _settle(conn, outbox_id, tenant_id, STATE_SENT)
                await _mark_attempt(conn, tenant_id, key, "failed", None, response.decline_code)
                failed += 1
            elif response.outcome is Outcome.AMBIGUOUS:
                # Budget stays consumed. Pessimistic and correct (§31).
                await _settle(conn, outbox_id, tenant_id, STATE_AMBIGUOUS)
                await _mark_attempt(conn, tenant_id, key, "ambiguous", None, None)
                metrics.increment("relay_ambiguous")
                ambiguous += 1
            else:  # RETRIABLE - definitely nothing happened; same key next time.
                await conn.execute(
                    text(
                        "UPDATE outbox SET attempts = attempts + 1"
                        " WHERE outbox_id = :outbox_id AND tenant_id = :tenant_id"
                    ),
                    {"outbox_id": outbox_id, "tenant_id": tenant_id},
                )
                metrics.increment("relay_retriable")
                retriable += 1

    return RelayResult(sent=sent, ambiguous=ambiguous, failed=failed, retriable=retriable)


async def _settle(conn: AsyncConnection, outbox_id: int, tenant_id: str, state: str) -> None:
    await conn.execute(
        text(
            "UPDATE outbox SET state = :state, attempts = attempts + 1"
            " WHERE outbox_id = :outbox_id AND tenant_id = :tenant_id"
        ),
        {"state": state, "outbox_id": outbox_id, "tenant_id": tenant_id},
    )


async def _mark_attempt(
    conn: AsyncConnection,
    tenant_id: str,
    key: str,
    state: str,
    provider_ref: str | None,
    decline_code: str | None,
) -> None:
    """Record the provider's answer against the attempt, matched by key."""
    await conn.execute(
        text(
            "UPDATE attempts SET state = :state, provider_ref = :provider_ref,"
            " decline_code = :decline_code"
            " WHERE tenant_id = :tenant_id AND idem_key = :idem_key"
        ),
        {
            "state": state,
            "provider_ref": provider_ref,
            "decline_code": decline_code,
            "tenant_id": tenant_id,
            "idem_key": key,
        },
    )


async def reconcile_ambiguous(engine: AsyncEngine, tenant_id: str, provider: RailProvider) -> int:
    """Resolve ambiguous rows by *querying* the provider for the same key.

    §31: "retry with the same key, then reconcile by key". Reconciliation uses
    `fetch_by_key`, never `submit_debit`: re-submitting to discover an outcome
    is indistinguishable from trying again, and the one thing that must not
    happen here is a second charge for a debit that may already have landed.

    A key the provider has no record of definitively did not charge. Anything
    still unknown stays ambiguous and keeps its budget slot.
    """
    async with tenant_transaction(engine, tenant_id) as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT outbox_id, idem_key, request FROM outbox"
                    " WHERE tenant_id = :tenant_id AND state = :ambiguous"
                    " ORDER BY outbox_id FOR UPDATE SKIP LOCKED"
                ),
                {"tenant_id": tenant_id, "ambiguous": STATE_AMBIGUOUS},
            )
        )
        pending = [
            (r.outbox_id, r.idem_key, r.request if isinstance(r.request, dict) else {})
            for r in rows
        ]

    resolved = 0
    for outbox_id, key, _request in pending:
        response = await provider.fetch_by_key(key)

        if response is None:
            # No record at the provider: the debit definitively did not happen.
            response = ProviderResponse(Outcome.DECLINED, decline_code="reconciled_not_charged")
        elif response.outcome is Outcome.AMBIGUOUS:
            continue  # still unknown; the budget slot stays held

        async with tenant_transaction(engine, tenant_id) as conn:
            await _settle(conn, outbox_id, tenant_id, STATE_SENT)
            if response.outcome is Outcome.ACCEPTED:
                await _mark_attempt(conn, tenant_id, key, "fired", response.provider_ref, None)
            else:
                await _mark_attempt(conn, tenant_id, key, "failed", None, response.decline_code)
        resolved += 1

    metrics.increment("relay_reconciled", count=str(resolved))
    return resolved
