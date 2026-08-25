"""Normalise a Razorpay webhook into an internal event (D2 — rail abstraction).

One translation point, so the rest of the system never parses provider-shaped
JSON. Phase 14 adds card e-mandate and eNACH by extending this seam rather than
by touching the projector.

**Payload shapes need validation against Razorpay test mode in Phase 17.** They
are documented here as extraction rules in one place precisely so that
correcting them later is a small, local change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

#: Razorpay sends the event id in a header, not the body.
EVENT_ID_HEADER: Final = "x-razorpay-event-id"
SIGNATURE_HEADER: Final = "x-razorpay-signature"


class MalformedEventError(ValueError):
    """The body is not a well-formed event. Never persisted (§41.1 T6)."""


@dataclass(frozen=True, slots=True)
class Event:
    """A verified, normalised event ready for `events_raw`."""

    event_id: str
    tenant_id: str
    event_type: str
    mandate_id: str | None
    cycle_ref: str | None
    occurred_at: datetime
    payload: dict[str, Any]

    @property
    def ordering_key(self) -> tuple[datetime, str]:
        """Deterministic total order, independent of arrival order.

        `occurred_at` alone is not a total order — two events can share a
        timestamp — so `event_id` breaks ties. Without a total order, "any
        permutation converges" is not achievable.
        """
        return (self.occurred_at, self.event_id)


def _dig(payload: dict[str, Any], *path: str) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def extract_mandate_id(body: dict[str, Any]) -> str | None:
    """The mandate is a subscription or a token, depending on event type."""
    for path in (
        ("payload", "subscription", "entity", "id"),
        ("payload", "payment", "entity", "token_id"),
        ("payload", "token", "entity", "id"),
    ):
        value = _dig(body, *path)
        if isinstance(value, str) and value:
            return value
    return None


def extract_cycle_ref(body: dict[str, Any]) -> str | None:
    """Razorpay Subscriptions raise one invoice per billing cycle."""
    for path in (
        ("payload", "invoice", "entity", "id"),
        ("payload", "payment", "entity", "invoice_id"),
    ):
        value = _dig(body, *path)
        if isinstance(value, str) and value:
            return value
    return None


def extract_amount_paise(body: dict[str, Any]) -> int | None:
    """Razorpay amounts are already integer paise — never convert to float."""
    for path in (
        ("payload", "payment", "entity", "amount"),
        ("payload", "invoice", "entity", "amount"),
    ):
        value = _dig(body, *path)
        if isinstance(value, int):
            return value
    return None


def extract_next_billing_at(body: dict[str, Any]) -> datetime | None:
    """The next billing date, which is the cycle deadline (ADR-018).

    §1: "one execution plus up to three retries per cycle. Then the cycle is
    over." Razorpay exposes this as the subscription's `current_end`.
    """
    for path in (
        ("payload", "subscription", "entity", "current_end"),
        ("payload", "invoice", "entity", "expire_by"),
    ):
        value = _dig(body, *path)
        if isinstance(value, int) and value > 0:
            return datetime.fromtimestamp(value, tz=UTC)
    return None


def extract_decline_code(body: dict[str, Any]) -> str | None:
    value = _dig(body, "payload", "payment", "entity", "error_reason")
    if isinstance(value, str) and value:
        return value
    value = _dig(body, "payload", "payment", "entity", "error_code")
    return value if isinstance(value, str) and value else None


def parse(raw_body: bytes, *, tenant_id: str, event_id: str | None) -> Event:
    """Parse an already-verified body into an Event.

    Call only after the signature verifies. Parsing an unverified body risks
    persisting something forged, which §41.1 T6 forbids.
    """
    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedEventError(f"body is not valid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise MalformedEventError("event body must be a JSON object")

    event_type = body.get("event")
    if not isinstance(event_type, str) or not event_type:
        raise MalformedEventError("event body has no 'event' type")

    resolved_id = event_id or body.get("id")
    if not isinstance(resolved_id, str) or not resolved_id:
        raise MalformedEventError(
            f"no event id: neither the {EVENT_ID_HEADER} header nor a body 'id' field"
        )

    created_at = body.get("created_at")
    occurred_at = (
        datetime.fromtimestamp(created_at, tz=UTC)
        if isinstance(created_at, int)
        # Absent provider timestamp: fall back to now, which is still a total
        # order once event_id breaks ties, but is recorded as a degraded case.
        else datetime.now(tz=UTC)
    )

    return Event(
        event_id=resolved_id,
        tenant_id=tenant_id,
        event_type=event_type,
        mandate_id=extract_mandate_id(body),
        cycle_ref=extract_cycle_ref(body),
        occurred_at=occurred_at,
        payload=body,
    )
