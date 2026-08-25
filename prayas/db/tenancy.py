"""Tenant context binding — the mechanism Invariant 6 rests on.

Governing spec: Master Spec §18.

Three properties this module exists to guarantee:

1. **Transaction-scoped, not session-scoped.** Context is set with
   ``set_config(..., is_local => true)``, which reverts when the transaction
   ends. On a pooled connection a session-scoped ``SET`` would survive into
   whichever request borrowed the connection next — a cross-tenant read, which
   §18 classes as an incident rather than a bug.

2. **Parameterised, never interpolated.** ``SET LOCAL`` is a utility statement
   and cannot take a bind parameter, so using it would force string
   interpolation of a value into SQL. ``set_config()`` is an ordinary function
   call and takes a real parameter. That difference is the whole reason this
   module uses the function form.

3. **Bound before use, checked loudly.** Per ADR-007 the database denies
   silently when context is missing (policies match nothing), while the
   application raises. The database refusing and the application complaining are
   two different jobs; doing only one of them produces either a leak or an
   unreadable failure.

The tenant identifier itself must come from a verified token — never from a
request parameter, query string, or client-controlled header (§18).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

#: The connection-level setting §18 names.
TENANT_SETTING: Final = "app.tenant_id"

#: Conservative identifier grammar. Not a substitute for parameterisation —
#: defence in depth, and it rejects obviously malformed input at the boundary.
_TENANT_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_SET_TENANT: Final = text(f"SELECT set_config('{TENANT_SETTING}', :tenant_id, true)")
_GET_TENANT: Final = text(f"SELECT current_setting('{TENANT_SETTING}', true)")


class TenantContextError(RuntimeError):
    """A tenant-scoped operation was attempted without valid bound context.

    Raised rather than returning an empty result, so that a missing ``SET LOCAL``
    cannot be misread as "no such records" on the money path.
    """


def validate_tenant_id(tenant_id: str) -> str:
    """Reject malformed tenant identifiers at the boundary."""
    if not tenant_id or not _TENANT_ID_RE.match(tenant_id):
        raise TenantContextError(
            f"Invalid tenant_id {tenant_id!r}: expected 1-64 chars matching "
            f"[A-Za-z0-9][A-Za-z0-9_-]*"
        )
    return tenant_id


async def current_tenant(conn: AsyncConnection) -> str | None:
    """Return the tenant bound to this transaction, or None if unbound.

    Uses the ``missing_ok`` form (ADR-007), so an unbound context returns NULL
    instead of raising ``unrecognized configuration parameter``.
    """
    result = await conn.execute(_GET_TENANT)
    value = result.scalar_one_or_none()
    # Postgres reports a reset transaction-local setting as empty string, not NULL.
    return value if value else None


@asynccontextmanager
async def tenant_transaction(engine: AsyncEngine, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """Open a transaction with tenant context bound for its lifetime.

    Every tenant-scoped query must run inside this. On exit the transaction
    commits or rolls back and the setting reverts with it.
    """
    validated = validate_tenant_id(tenant_id)

    async with engine.connect() as conn, conn.begin():
        await conn.execute(_SET_TENANT, {"tenant_id": validated})

        # ADR-007: verify the binding actually took before handing over a
        # connection the caller will trust with tenant-scoped reads.
        bound = await current_tenant(conn)
        if bound != validated:
            raise TenantContextError(
                f"Failed to bind tenant context: expected {validated!r}, got {bound!r}"
            )

        yield conn


@asynccontextmanager
async def system_transaction(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Open a transaction with *no* tenant context, for genuinely global tables.

    Only for tables that are cross-tenant by design: ``compliance_rules``,
    ``segment_priors`` (k-anonymised, §27) and ``issuer_health``. Using this for
    a tenant-scoped table yields zero rows, because the policies match nothing
    when context is unbound.

    Note ``tenants`` is *not* in that list — it is tenant-scoped (ADR-013).
    """
    async with engine.connect() as conn, conn.begin():
        yield conn
