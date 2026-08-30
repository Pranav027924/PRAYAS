"""Consent withdrawal, and what it does not delete (Master Spec §28; ADR-078).

§28's cascade:

    delete_profile           -> the profile tier
    purge_working_memory     -> the cache
    suppress_all_future_contact
    pseudonymise_ledger      <- not delete
    exclude_from_training

**Why the ledger is pseudonymised rather than deleted.** §28 says it plainly:
"Financial transaction records carry statutory retention obligations, and an
audit trail with holes is not an audit trail. The resolution is to sever the
link to the natural person — replace identifiers with an irreversible
pseudonym — while retaining the decision record itself."

**Where the severing actually happens.** `decisions` carries no `customer_id`;
it references `mandate_id` and `cycle_id`. The link to the person runs
`decisions.mandate_id -> mandates.customer_id`, so pseudonymising *that* column
severs it. `decisions` itself is never touched, so Invariant 5 — append-only,
no UPDATE, no DELETE, ever — is not bent, the hash chain still verifies, and
every decision remains replayable. The person simply cannot be recovered from
it.

**Order is deliberate and is the opposite of §28's listing.** Suppression is
written *first*. Every step can fail, and the failure modes are not
symmetrical: a run that suppressed contact but did not finish erasing leaves
data to retry against, while a run that erased the profile and then failed to
suppress has destroyed the record of who must not be contacted. One of those is
recoverable.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

#: Environment variable holding the pseudonymisation pepper.
PEPPER_ENV: Final = "PRAYAS_PSEUDONYM_PEPPER"

#: Domain separator, so a pseudonym from this routine can never collide with a
#: hash computed for some other purpose against the same pepper.
_DOMAIN: Final = b"prayas.forget.v1"

#: Prefix on every pseudonym, so a value in `mandates.customer_id` is
#: self-describing: an operator reading the column can see at a glance that the
#: row has been through §28 rather than wondering at an opaque hex string.
PSEUDONYM_PREFIX: Final = "anon_"

CONSENT_WITHDRAWN: Final = "consent_withdrawn"


class ForgetError(RuntimeError):
    """The cascade cannot be completed safely."""


def _pepper() -> bytes:
    """The deployment pepper. **Absent means refuse, not fall back.**

    A pseudonym computed without a secret is reversible by anyone holding a
    list of plausible customer ids, which is every merchant. Deriving one from
    the identifiers alone would produce something that looks irreversible,
    satisfies the schema, and protects nobody — so this fails closed instead.
    """
    value = os.environ.get(PEPPER_ENV)
    if not value:
        raise ForgetError(
            f"{PEPPER_ENV} is not set. §28 requires an *irreversible* pseudonym, and "
            f"a hash over identifiers alone is reversible by anyone holding a customer list."
        )
    return value.encode()


def pseudonymise(tenant_id: str, customer_id: str, *, pepper: bytes | None = None) -> str:
    """An irreversible, stable pseudonym for one person within one tenant.

    Stable so that every mandate belonging to that person maps to the same
    value and the audit trail stays internally consistent. Tenant-scoped so the
    same person at two merchants does not collide into one identifier — §27
    forbids exactly that linkage, and a global pseudonym would rebuild the
    cross-merchant profile that section exists to prevent.
    """
    if not tenant_id or not customer_id:
        raise ForgetError("tenant_id and customer_id are both required")

    key = pepper if pepper is not None else _pepper()
    digest = hmac.new(
        key, _DOMAIN + b"|" + tenant_id.encode() + b"|" + customer_id.encode(), hashlib.sha256
    ).hexdigest()
    return f"{PSEUDONYM_PREFIX}{digest[:32]}"


def is_pseudonym(customer_id: str) -> bool:
    return customer_id.startswith(PSEUDONYM_PREFIX)


@dataclass(frozen=True, slots=True)
class ForgetResult:
    """What the cascade actually did, for the audit record."""

    tenant_id: str
    pseudonym: str
    profiles_deleted: int
    mandates_pseudonymised: int
    suppression_written: bool
    at: datetime

    @property
    def complete(self) -> bool:
        return self.suppression_written


async def forget(
    conn: AsyncConnection,
    tenant_id: str,
    customer_id: str,
    *,
    reason: str = CONSENT_WITHDRAWN,
    pepper: bytes | None = None,
    now: datetime | None = None,
) -> ForgetResult:
    """§28's cascade, inside the caller's transaction.

    Deliberately not opening its own transaction: erasure must be atomic with
    whatever recorded the withdrawal, or a crash between the two leaves a
    request that was honoured with no evidence, or evidence with nothing
    honoured.

    The connection must already be bound to `tenant_id` — every statement here
    relies on RLS to confine it, rather than on the WHERE clause alone
    (Invariant 6).
    """
    at = now or datetime.now(UTC)
    pseudonym = pseudonymise(tenant_id, customer_id, pepper=pepper)

    # 1. Suppress first. See the module docstring on ordering.
    await conn.execute(
        text(
            "INSERT INTO contact_suppressions (tenant_id, customer_ref, reason, suppressed_at)"
            " VALUES (:t, :ref, :reason, :at)"
            " ON CONFLICT (tenant_id, customer_ref) DO NOTHING"
        ),
        {"t": tenant_id, "ref": pseudonym, "reason": reason, "at": at},
    )

    # 2. Sever the person-link in the ledger's join path. `decisions` is not
    #    touched: Invariant 5 holds and the hash chain still verifies.
    mandates = await conn.execute(
        text("UPDATE mandates SET customer_id = :ref WHERE tenant_id = :t AND customer_id = :cust"),
        {"t": tenant_id, "ref": pseudonym, "cust": customer_id},
    )

    # 3. Delete the profile tier outright. §25 gives it lifetime "until consent
    #    withdrawn", and unlike the ledger it carries no retention obligation.
    profiles = await conn.execute(
        text("DELETE FROM customer_profiles WHERE tenant_id = :t AND customer_id = :cust"),
        {"t": tenant_id, "cust": customer_id},
    )

    return ForgetResult(
        tenant_id=tenant_id,
        pseudonym=pseudonym,
        profiles_deleted=profiles.rowcount or 0,
        mandates_pseudonymised=mandates.rowcount or 0,
        suppression_written=True,
        at=at,
    )


async def is_suppressed(
    conn: AsyncConnection,
    tenant_id: str,
    customer_ref: str,
    *,
    pepper: bytes | None = None,
) -> bool:
    """Whether this reference must not be contacted.

    Accepts either a live `customer_id` or a pseudonym: a caller holding a
    pre-erasure identifier must still get the right answer, so the raw value is
    checked alongside its pseudonym.

    **Raises rather than guessing when the pseudonym cannot be computed.**
    Falling back to checking the raw id alone would answer "not suppressed" for
    a person whose identifier has already been replaced — the one wrong answer
    that results in contacting someone who withdrew consent. The safe direction
    for this predicate is to fail, not to permit.
    """
    refs = [customer_ref]
    if not is_pseudonym(customer_ref):
        refs.append(pseudonymise(tenant_id, customer_ref, pepper=pepper))

    found = await conn.scalar(
        text(
            "SELECT count(*) FROM contact_suppressions"
            " WHERE tenant_id = :t AND customer_ref = ANY(:refs)"
        ),
        {"t": tenant_id, "refs": refs},
    )
    return bool(found)


async def excluded_from_training(conn: AsyncConnection, tenant_id: str) -> set[str]:
    """Every suppressed reference for a tenant — §28's `exclude_from_training`.

    Returned as a set for the training pipeline to filter on. Pseudonymised
    mandates keep their rows, so without this filter an erased person's history
    would still shape the model that acts on everyone else.
    """
    rows = await conn.execute(
        text("SELECT customer_ref FROM contact_suppressions WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    return {str(row[0]) for row in rows}
