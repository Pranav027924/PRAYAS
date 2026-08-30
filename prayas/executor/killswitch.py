"""Kill switches at three granularities (Master Spec §18, §45; ADR-084).

§45 requires an emergency stop "executable in under sixty seconds by one
person". §18 puts the per-tenant knobs in `tenants.config`, which §36's schema
comment already lists as holding "λ, μ, caps, rails, kill switches".

**Three granularities, because incidents have three shapes.**

* **Platform** — a regulator or a Sev-1 says stop everything. One row, no
  enumeration of tenants, so it cannot be half-applied while someone pages.
* **Tenant** — one merchant's configuration is wrong, or they asked. The other
  tenants keep collecting.
* **Mandate** — one customer disputes, and stopping the whole tenant would be
  a disproportionate response to a single complaint.

**It fails closed, and that is the entire point.** If the switch state cannot
be read, `is_stopped` returns True. Invariant 2 already makes the compliance
gate deny on error; a kill switch that failed *open* would mean the one control
an operator reaches for during an incident stops working precisely when the
database is unhealthy — which is when incidents happen.

**Stopping is cheap; restarting is not.** `stop` is a single statement an
operator can run under pressure. There is deliberately no `resume_all`:
restarting is per-scope and requires naming what is being restarted, because an
accidental un-stop should not be as easy as the stop was. The asymmetry is
chosen (ADR-084), not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.observability import metrics

PLATFORM: Final = "platform"
TENANT: Final = "tenant"
MANDATE: Final = "mandate"

SCOPES: Final[tuple[str, ...]] = (PLATFORM, TENANT, MANDATE)

#: The tenant row that carries the platform-wide switch. A reserved tenant
#: rather than a separate table: §36 already puts kill switches in
#: `tenants.config`, and one storage location means one thing to check when the
#: question is "is anything stopped right now".
PLATFORM_TENANT: Final = "__platform__"

_STOPPED_KEY: Final = "stopped"
_STOPPED_MANDATES_KEY: Final = "stopped_mandates"


class KillSwitchError(RuntimeError):
    """The switch state could not be established."""


@dataclass(frozen=True, slots=True)
class StopVerdict:
    """Whether firing is permitted, and which scope refused."""

    stopped: bool
    scope: str | None
    reason: str

    def __bool__(self) -> bool:
        return self.stopped


ALLOWED: Final = StopVerdict(stopped=False, scope=None, reason="no kill switch engaged")


async def is_stopped(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    mandate_id: str | None = None,
) -> StopVerdict:
    """May anything fire for this tenant and mandate?

    **Returns stopped=True on any error.** An operator reaches for this during
    an incident, which is exactly when the database is least healthy; a switch
    that failed open would stop working when it is most needed.
    """
    try:
        rows = await conn.execute(
            text("SELECT tenant_id, config FROM tenants WHERE tenant_id IN (:platform, :tenant)"),
            {"platform": PLATFORM_TENANT, "tenant": tenant_id},
        )
        config = {row._mapping["tenant_id"]: (row._mapping["config"] or {}) for row in rows}
    except SQLAlchemyError as exc:
        metrics.increment("killswitch_unreadable", tenant_id=tenant_id)
        return StopVerdict(
            stopped=True,
            scope=None,
            reason=f"kill switch state unreadable, failing closed: {exc}",
        )

    platform = config.get(PLATFORM_TENANT, {})
    if platform.get(_STOPPED_KEY):
        return StopVerdict(True, PLATFORM, "platform emergency stop engaged")

    own = config.get(tenant_id, {})
    if own.get(_STOPPED_KEY):
        return StopVerdict(True, TENANT, f"tenant {tenant_id} stopped")

    if mandate_id is not None and mandate_id in (own.get(_STOPPED_MANDATES_KEY) or []):
        return StopVerdict(True, MANDATE, f"mandate {mandate_id} stopped")

    return ALLOWED


async def stop(
    conn: AsyncConnection,
    *,
    scope: str,
    tenant_id: str | None = None,
    mandate_id: str | None = None,
    reason: str = "operator",
) -> None:
    """Engage a kill switch. One statement, runnable under pressure.

    The platform scope takes no tenant argument on purpose: an emergency stop
    that required enumerating tenants could be half-applied while an operator
    is being paged, and "most of it stopped" is not a state anyone wants to
    reason about at 3am.
    """
    now = datetime.now(UTC).isoformat()

    if scope == PLATFORM:
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config)"
                " VALUES (:t, 'platform kill switch',"
                "         jsonb_build_object('stopped', true, 'reason', CAST(:reason AS text),"
                "                            'stopped_at', CAST(:now AS text)))"
                " ON CONFLICT (tenant_id) DO UPDATE SET config ="
                "   tenants.config || jsonb_build_object('stopped', true,"
                "     'reason', CAST(:reason AS text), 'stopped_at', CAST(:now AS text))"
            ),
            {"t": PLATFORM_TENANT, "reason": reason, "now": now},
        )
    elif scope == TENANT:
        if not tenant_id:
            raise KillSwitchError("tenant scope requires a tenant_id")
        await conn.execute(
            text(
                "UPDATE tenants SET config = config || jsonb_build_object("
                "  'stopped', true, 'reason', CAST(:reason AS text), 'stopped_at', CAST(:now AS text))"
                " WHERE tenant_id = :t"
            ),
            {"t": tenant_id, "reason": reason, "now": now},
        )
    elif scope == MANDATE:
        if not tenant_id or not mandate_id:
            raise KillSwitchError("mandate scope requires a tenant_id and a mandate_id")
        await conn.execute(
            text(
                "UPDATE tenants SET config = jsonb_set("
                "  config, '{stopped_mandates}',"
                "  COALESCE(config->'stopped_mandates', '[]'::jsonb) || to_jsonb(CAST(:m AS text)))"
                " WHERE tenant_id = :t"
            ),
            {"t": tenant_id, "m": mandate_id},
        )
    else:
        raise KillSwitchError(f"unknown scope {scope!r}; expected one of {SCOPES}")

    metrics.increment("killswitch_engaged", scope=scope, reason=reason)


async def resume(
    conn: AsyncConnection,
    *,
    scope: str,
    tenant_id: str | None = None,
    mandate_id: str | None = None,
) -> None:
    """Lift one kill switch. **Deliberately per-scope, with no `resume_all`.**

    Restarting collection after an emergency stop is a decision someone should
    have to make explicitly, for a named scope. Making it as easy as the stop
    was would mean an accidental un-stop costs as little as the stop did, and
    the two are not symmetrical in consequence (ADR-084).
    """
    if scope == PLATFORM:
        await conn.execute(
            text("UPDATE tenants SET config = config - 'stopped' WHERE tenant_id = :t"),
            {"t": PLATFORM_TENANT},
        )
    elif scope == TENANT:
        if not tenant_id:
            raise KillSwitchError("tenant scope requires a tenant_id")
        await conn.execute(
            text("UPDATE tenants SET config = config - 'stopped' WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    elif scope == MANDATE:
        if not tenant_id or not mandate_id:
            raise KillSwitchError("mandate scope requires a tenant_id and a mandate_id")
        await conn.execute(
            text(
                "UPDATE tenants SET config = jsonb_set(config, '{stopped_mandates}',"
                "  COALESCE((SELECT jsonb_agg(v) FROM jsonb_array_elements("
                "    COALESCE(config->'stopped_mandates', '[]'::jsonb)) v"
                "    WHERE v <> to_jsonb(CAST(:m AS text))), '[]'::jsonb))"
                " WHERE tenant_id = :t"
            ),
            {"t": tenant_id, "m": mandate_id},
        )
    else:
        raise KillSwitchError(f"unknown scope {scope!r}; expected one of {SCOPES}")

    metrics.increment("killswitch_lifted", scope=scope)
