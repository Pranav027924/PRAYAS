"""The one clock the demo is allowed to bend (Demo spec Phase 6, N2).

Every tenant runs on the real clock. A demo tenant may additionally carry an
offset in `demo_clock_state` — a table that holds nothing else, on purpose
(migration `0014_demo_clock_state`): `tenants.config` carries rail
preferences, fatigue caps and kill switches and was deliberately left
SELECT-only for the application role (ADR-013), so this feature's one write
lives somewhere a bug in it cannot reach any of that. `advance`, `reset` and
`jump_to_next_action` all refuse outright unless `tenants.config.demo_tenant
is true` — that guard is the entire reason this module exists apart from the
three call sites it feeds (`prayas.executor.claiming`,
`prayas.executor.worker`, `prayas.planner.worker`): an accidental offset on a
real tenant is the one way this feature can move money at the wrong time, so
the check is unconditional and the first thing this module does, before
anything else.

`current_now` does not tell the gate or the executor to trust a claim of
elapsed time — it composes their real notion of "now" with a stored offset,
so a 26-hour PDN gap after a virtual +24h advance is a genuine 26-hour gap by
the gate's own arithmetic, computed the same way it always is. Nothing here
backdates a record or invents a timestamp after the fact; it changes what
"now" resolves to, once, at the one place every caller already asks for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

#: The one flag that may enable clock travel. Set by the seeder, never by
#: this module — `advance`, `reset` and `jump_to_next_action` only ever check
#: it, against `tenants.config`, which this module never writes.
DEMO_TENANT_KEY: Final = "demo_tenant"

#: A demo is a few minutes, not a career. Bounds one advance call so a typo
#: cannot send a tenant's clock decades forward.
MAX_ADVANCE_SECONDS: Final = 30 * 24 * 3600


class ClockError(RuntimeError):
    """A clock advance or reset was refused."""


def now_for(offset_seconds: int) -> datetime:
    """Real time, shifted by an already-known offset. Pure — no I/O.

    An offset of zero — every non-demo tenant, always, and a demo tenant that
    has never been advanced — gets exactly `datetime.now(UTC)`.
    """
    return datetime.now(UTC) + timedelta(seconds=int(offset_seconds))


async def tenant_config(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    """This tenant's `config`, read inside its own bound transaction (RLS)."""
    row = (
        await conn.execute(
            text("SELECT config FROM tenants WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).first()
    if row is None or row.config is None:
        return {}
    return dict(row.config)


async def is_demo_tenant(conn: AsyncConnection, tenant_id: str) -> bool:
    return (await tenant_config(conn, tenant_id)).get(DEMO_TENANT_KEY) is True


async def offset_seconds_for(conn: AsyncConnection, tenant_id: str) -> int:
    """This tenant's stored offset. Zero for a tenant never advanced — there
    is no row yet, and no row means no offset, which is exactly the real
    clock."""
    row = (
        await conn.execute(
            text("SELECT offset_seconds FROM demo_clock_state WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).first()
    return int(row.offset_seconds) if row is not None else 0


async def current_now(conn: AsyncConnection, tenant_id: str) -> datetime:
    """`now_for`, resolved end to end for a tenant. What the executor's poll,
    the gate's `as_of`, and the planner's reference time all call instead of
    reading the wall clock directly."""
    return now_for(await offset_seconds_for(conn, tenant_id))


async def _write_offset(conn: AsyncConnection, tenant_id: str, offset_seconds: int) -> None:
    await conn.execute(
        text(
            "INSERT INTO demo_clock_state (tenant_id, offset_seconds) VALUES (:t, :o)"
            " ON CONFLICT (tenant_id) DO UPDATE SET offset_seconds = :o"
        ),
        {"t": tenant_id, "o": offset_seconds},
    )


async def _require_demo_tenant(conn: AsyncConnection, tenant_id: str, verb: str) -> None:
    if not await is_demo_tenant(conn, tenant_id):
        raise ClockError(f"tenant {tenant_id!r} is not a demo tenant; clock {verb} refused")


async def advance(conn: AsyncConnection, tenant_id: str, *, seconds: int) -> datetime:
    """Move this tenant's virtual clock forward (or back) by `seconds`.

    Raises `ClockError` unless `tenants.config.demo_tenant is true` — the one
    check this entire module exists to enforce. A tenant seeded without that
    flag cannot be reached by this function no matter what a caller passes in.
    """
    await _require_demo_tenant(conn, tenant_id, "advance")
    if not -MAX_ADVANCE_SECONDS <= seconds <= MAX_ADVANCE_SECONDS:
        raise ClockError(f"advance of {seconds}s exceeds the {MAX_ADVANCE_SECONDS}s bound")

    new_offset = await offset_seconds_for(conn, tenant_id) + int(seconds)
    await _write_offset(conn, tenant_id, new_offset)
    return now_for(new_offset)


async def reset(conn: AsyncConnection, tenant_id: str) -> datetime:
    """Zero this tenant's offset — the `[ Reset ]` button."""
    await _require_demo_tenant(conn, tenant_id, "reset")
    await _write_offset(conn, tenant_id, 0)
    return now_for(0)


async def jump_to_next_action(
    conn: AsyncConnection, tenant_id: str, *, cycle_id: str | None = None
) -> datetime:
    """Advance straight to the next pending action, plus one second.

    Reads `scheduled_actions` for the earliest `fire_at` still `pending` or
    `claimed`, scoped to one cycle when the caller names it — the button
    lives on the cycle screen and should not jump the clock past unrelated
    action on the same tenant. Raises `ClockError` if nothing is pending, or
    if the tenant is not a demo tenant.
    """
    await _require_demo_tenant(conn, tenant_id, "advance")

    current = now_for(await offset_seconds_for(conn, tenant_id))
    query = (
        "SELECT MIN(fire_at) AS next_at FROM scheduled_actions"
        " WHERE tenant_id = :t AND state IN ('pending', 'claimed')"
    )
    params: dict[str, Any] = {"t": tenant_id}
    if cycle_id is not None:
        query += " AND cycle_id = :c"
        params["c"] = cycle_id

    row = (await conn.execute(text(query), params)).first()
    if row is None or row.next_at is None:
        raise ClockError(f"no pending action to jump to for tenant {tenant_id!r}")

    delta = max(0, int((row.next_at - current).total_seconds()) + 1)
    new_offset = await offset_seconds_for(conn, tenant_id) + delta
    await _write_offset(conn, tenant_id, new_offset)
    return now_for(new_offset)
