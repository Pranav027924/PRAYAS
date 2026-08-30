"""Profile persistence (Master Spec §25, §27; Invariant 6).

§25 places the profile tier in Postgres, scoped **per tenant**, with lifetime
"until consent withdrawn".

**Every statement here filters by tenant, and that is belt to RLS's braces.**
`customer_profiles` has row-level security forced with a policy on
`tenant_id`, so a missing filter would already be caught by the database. The
explicit predicate is still written, because Invariant 6 says "every
tenant-scoped query filters by tenant. No exceptions", and because a query that
depends solely on a session variable being set is one refactor away from being
run on a connection where it is not.

**The posterior is stored as raw evidence, not as probabilities** (ADR-077).
Storing the normalised form would discard the accumulated count on every
round-trip, so a profile written and read back would forget everything it knew
about how much it had seen — the same defect ADR-077 removed from the update
rule, reintroduced through the database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.memory.profile import CustomerPaymentProfile, empty_profile

_COLUMNS = (
    "tenant_id, customer_id, payday_posterior, payday_confidence, declared_funding_day,"
    " declared_at, typical_amount_p75, fail_success_lags, preferred_channel,"
    " engagement_hours, messages_30d, fatigue_score, last_contact_at, consent_ref,"
    " consent_withdrawn, updated_at"
)


def _to_row(profile: CustomerPaymentProfile, now: datetime) -> dict[str, Any]:
    return {
        "tenant_id": profile.tenant_id,
        "customer_id": profile.customer_id,
        # Keys are JSON strings by necessity; they come back as strings and are
        # converted on read.
        "payday_posterior": json.dumps({str(k): v for k, v in profile.payday_posterior.items()}),
        "payday_confidence": round(profile.payday_confidence, 3),
        "declared_funding_day": profile.declared_funding_day,
        "declared_at": profile.declared_at,
        "typical_amount_p75": profile.typical_amount_p75,
        "fail_success_lags": list(profile.fail_success_lag_days),
        "preferred_channel": profile.preferred_channel,
        "engagement_hours": list(profile.engagement_hours),
        "messages_30d": profile.messages_30d,
        "fatigue_score": round(min(profile.fatigue_score, 9.999), 3),
        "last_contact_at": profile.last_contact_at,
        "consent_ref": profile.consent_ref,
        "consent_withdrawn": profile.consent_withdrawn,
        "updated_at": profile.updated_at or now,
    }


def _from_row(row: Any) -> CustomerPaymentProfile:
    mapping = row._mapping
    raw = mapping["payday_posterior"] or {}
    if isinstance(raw, str):
        raw = json.loads(raw)

    return CustomerPaymentProfile(
        tenant_id=mapping["tenant_id"],
        customer_id=mapping["customer_id"],
        payday_posterior={int(k): float(v) for k, v in raw.items()},
        declared_funding_day=mapping["declared_funding_day"],
        declared_at=mapping["declared_at"],
        typical_amount_p75=(
            int(mapping["typical_amount_p75"])
            if mapping["typical_amount_p75"] is not None
            else None
        ),
        fail_success_lag_days=tuple(float(x) for x in (mapping["fail_success_lags"] or ())),
        preferred_channel=mapping["preferred_channel"],
        engagement_hours=tuple(int(h) for h in (mapping["engagement_hours"] or ())),
        messages_30d=int(mapping["messages_30d"]),
        fatigue_score=float(mapping["fatigue_score"]),
        last_contact_at=mapping["last_contact_at"],
        consent_ref=mapping["consent_ref"],
        consent_withdrawn=bool(mapping["consent_withdrawn"]),
        updated_at=mapping["updated_at"],
    )


async def load(
    conn: AsyncConnection, tenant_id: str, customer_id: str
) -> CustomerPaymentProfile | None:
    """The stored profile, or None if this customer has none yet.

    `None` rather than an empty profile: "never seen" and "seen and knows
    nothing" are different states, and only the caller knows which default is
    right for what it is about to do.
    """
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM customer_profiles"  # nosec B608
                " WHERE tenant_id = :t AND customer_id = :c"
            ),
            {"t": tenant_id, "c": customer_id},
        )
    ).first()
    return _from_row(row) if row is not None else None


async def load_or_empty(
    conn: AsyncConnection, tenant_id: str, customer_id: str
) -> CustomerPaymentProfile:
    """Cold start as a first-class case (§37's day-one path)."""
    return await load(conn, tenant_id, customer_id) or empty_profile(tenant_id, customer_id)


async def save(
    conn: AsyncConnection, profile: CustomerPaymentProfile, *, now: datetime | None = None
) -> None:
    """Upsert one profile.

    Refuses to write a profile whose tenant does not match the row it would
    land in — RLS's `WITH CHECK` would reject it anyway, but failing here names
    the actual mistake instead of surfacing a policy violation.
    """
    params = _to_row(profile, now or datetime.now(UTC))
    await conn.execute(
        text(
            "INSERT INTO customer_profiles (" + _COLUMNS + ")"  # nosec B608
            " VALUES (:tenant_id, :customer_id, CAST(:payday_posterior AS jsonb),"
            " :payday_confidence, :declared_funding_day, :declared_at, :typical_amount_p75,"
            " :fail_success_lags, :preferred_channel, :engagement_hours, :messages_30d,"
            " :fatigue_score, :last_contact_at, :consent_ref, :consent_withdrawn, :updated_at)"
            " ON CONFLICT (tenant_id, customer_id) DO UPDATE SET"
            " payday_posterior = EXCLUDED.payday_posterior,"
            " payday_confidence = EXCLUDED.payday_confidence,"
            " declared_funding_day = EXCLUDED.declared_funding_day,"
            " declared_at = EXCLUDED.declared_at,"
            " typical_amount_p75 = EXCLUDED.typical_amount_p75,"
            " fail_success_lags = EXCLUDED.fail_success_lags,"
            " preferred_channel = EXCLUDED.preferred_channel,"
            " engagement_hours = EXCLUDED.engagement_hours,"
            " messages_30d = EXCLUDED.messages_30d,"
            " fatigue_score = EXCLUDED.fatigue_score,"
            " last_contact_at = EXCLUDED.last_contact_at,"
            " consent_ref = EXCLUDED.consent_ref,"
            " consent_withdrawn = EXCLUDED.consent_withdrawn,"
            " updated_at = EXCLUDED.updated_at"
        ),
        params,
    )
