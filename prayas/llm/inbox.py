"""Inbound replies and the human queue (Master Spec §41.2; ADR-081).

§41.2 (2): "anything unparseable is discarded and the reply routed to a human
queue."

**A discard nobody sees is indistinguishable from a parser that silently
stopped working.** So every reply is recorded — the ones that parsed, the ones
that did not, and why. The queue is the ones that need a person: a rejection is
either a customer writing something the schema cannot express, or a model
starting to emit something new, and both want human eyes.

**Provider outages are recorded but not queued.** During an outage every reply
would be a "rejection", and burying the handful that genuinely need attention
under a flood of infrastructure noise is how a queue stops being read. §19 calls
this degradation, not failure.

**`raw_text` is personal data.** A person triaging a parse failure needs to see
what actually arrived, so the raw message is stored — which puts this table
squarely inside §28's forgetting cascade and inside the tenant boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.llm.parse import ParseOutcome


@dataclass(frozen=True, slots=True)
class QueuedReply:
    """One row of the human queue, as a reviewer sees it."""

    reply_id: str
    customer_ref: str
    received_at: datetime
    raw_text: str
    rejection: str | None


async def record_reply(
    conn: AsyncConnection,
    *,
    reply_id: str,
    tenant_id: str,
    customer_ref: str,
    raw_text: str,
    outcome: ParseOutcome,
    received_at: datetime | None = None,
) -> None:
    """Persist a reply and its parse outcome, queueing it if a human is needed."""
    parsed = None
    if outcome.reply is not None:
        parsed = json.dumps(
            {
                "intent": outcome.reply.intent,
                "confidence": outcome.reply.confidence,
                "declared_funding_day": outcome.reply.declared_funding_day,
                "language": outcome.reply.language,
            }
        )

    await conn.execute(
        text(
            "INSERT INTO inbound_replies (reply_id, tenant_id, customer_ref, received_at,"
            " raw_text, parsed, rejection, needs_human)"
            " VALUES (:id, :t, :ref, :at, :raw, CAST(:parsed AS jsonb), :rejection, :human)"
        ),
        {
            "id": reply_id,
            "t": tenant_id,
            "ref": customer_ref,
            "at": received_at or datetime.now(UTC),
            "raw": raw_text,
            "parsed": parsed,
            "rejection": str(outcome.rejection) if outcome.rejection else None,
            "human": outcome.needs_human,
        },
    )


async def human_queue(
    conn: AsyncConnection, tenant_id: str, *, limit: int = 100
) -> list[QueuedReply]:
    """Unresolved replies awaiting a person, oldest first."""
    rows = await conn.execute(
        text(
            "SELECT reply_id, customer_ref, received_at, raw_text, rejection"
            " FROM inbound_replies"
            " WHERE tenant_id = :t AND needs_human AND resolved_at IS NULL"
            " ORDER BY received_at LIMIT :limit"
        ),
        {"t": tenant_id, "limit": limit},
    )
    return [
        QueuedReply(
            reply_id=row._mapping["reply_id"],
            customer_ref=row._mapping["customer_ref"],
            received_at=row._mapping["received_at"],
            raw_text=row._mapping["raw_text"],
            rejection=row._mapping["rejection"],
        )
        for row in rows
    ]


async def resolve(conn: AsyncConnection, tenant_id: str, reply_id: str) -> None:
    """Mark a queued reply handled by a person."""
    await conn.execute(
        text(
            "UPDATE inbound_replies SET resolved_at = now() WHERE tenant_id = :t AND reply_id = :id"
        ),
        {"t": tenant_id, "id": reply_id},
    )
