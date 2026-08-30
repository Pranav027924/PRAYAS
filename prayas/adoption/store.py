"""Where a tenant's adoption stage lives (Master Spec §44; ADR-089).

`tenants.config`, alongside the kill switches — §36's schema comment already
lists that column as holding "λ, μ, caps, rails, kill switches", and an
adoption stage is the same kind of thing: per-tenant operational state that
governs what the executor may do.

**Reads fail closed to `OBSERVE`.** An unreadable or unrecognised stage means
ingest only: no decisions, no firing. That is the same reasoning as the kill
switch (ADR-084) and the compliance gate (Invariant 2) — the safe direction
when the system cannot establish what it is allowed to do is to do less, and
`OBSERVE` is the stage that does least.

Note which way that cuts. A tenant with no stage recorded is *not* treated as
fully ramped; a missing row is a tenant nobody has onboarded, and firing at a
customer on their behalf would be the worst possible reading of silence.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.adoption.stages import Evidence, Stage, advance
from prayas.observability import metrics

_STAGE_KEY = "adoption_stage"


async def current_stage(conn: AsyncConnection, tenant_id: str) -> Stage:
    """This tenant's stage, defaulting to `OBSERVE` on anything unexpected."""
    try:
        row = (
            await conn.execute(
                text("SELECT config FROM tenants WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).first()
    except SQLAlchemyError:
        metrics.increment("adoption_stage_unreadable", tenant_id=tenant_id)
        return Stage.OBSERVE

    if row is None:
        return Stage.OBSERVE

    raw = (row._mapping["config"] or {}).get(_STAGE_KEY)
    if not isinstance(raw, (int, str)):
        return Stage.OBSERVE
    try:
        return Stage(int(raw))
    except (TypeError, ValueError):
        # An unrecognised value is not a licence to guess upward.
        return Stage.OBSERVE


async def set_stage(conn: AsyncConnection, tenant_id: str, stage: Stage) -> None:
    """Write a stage directly. **Use `promote` for a normal advance.**

    This exists for onboarding and for rollback, both of which are deliberate
    acts by a person. It does not check evidence, which is precisely why it is
    not the function the ramp uses.
    """
    await conn.execute(
        text(
            "UPDATE tenants SET config = config || jsonb_build_object("
            "  'adoption_stage', CAST(:stage AS int),"
            "  'adoption_stage_set_at', CAST(:now AS text))"
            " WHERE tenant_id = :t"
        ),
        {"t": tenant_id, "stage": int(stage), "now": datetime.now(UTC).isoformat()},
    )
    metrics.increment("adoption_stage_set", tenant_id=tenant_id, stage=stage.name)


async def promote(conn: AsyncConnection, tenant_id: str, evidence: Evidence) -> Stage:
    """Advance one stage if §44's criteria are met, else raise with the reasons.

    The evidence is supplied by the caller rather than gathered here: this
    module's job is to enforce the gate, and a gate that also produced its own
    evidence would be marking its own homework.
    """
    stage = await current_stage(conn, tenant_id)
    promoted = advance(stage, evidence)  # raises AdoptionError with the unmet list
    await set_stage(conn, tenant_id, promoted)
    metrics.increment("adoption_stage_promoted", tenant_id=tenant_id, to=promoted.name)
    return promoted
