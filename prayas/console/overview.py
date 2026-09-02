"""The operator's view of one tenant (ADR-101; Master Spec §5, §18, §36).

§5 names five people who need to see this system and gives each of them a
surface. Phase 16 built the *payloads* for three screens and an HTML page for
one, which is an API for a UI that did not exist — enough for a compliance
reviewer holding a decision id, and no use at all to someone asking "is it
working?".

This assembles that missing answer: the pipeline end to end for one tenant —
what stage it is in, which mandates it holds, which cycles are in flight, what
is queued to fire and when, and what the ledger has recorded.

**Every query is tenant-scoped by the database, not by this module.** The caller
opens a `tenant_transaction` bound from a verified token, and row-level security
does the rest. Nothing here takes a tenant id, so there is nothing to tamper
with — the same property §18 gives the rest of the console.

**Read-only, and deliberately so.** An operator console that can act is a second
way to move money, outside the executor's revalidation and outside the ledger.
Every mutation in this system goes through the scheduled-action path so that it
is gated at fire time and recorded; a button here would bypass both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.adoption.cohort import arm_for
from prayas.adoption.stages import BEHAVIOUR, Stage
from prayas.adoption.store import current_stage

#: Rows per table. The console is a live view, not an export; a query that
#: returns everything a busy tenant has ever done is a slow page, not a better
#: one.
LIMIT = 50


@dataclass(frozen=True, slots=True)
class Overview:
    tenant_id: str
    stage: str
    stage_description: str
    fires: bool
    mandate_share: float
    holdout_pct: float
    mandates: list[dict[str, Any]] = field(default_factory=list)
    cycles: list[dict[str, Any]] = field(default_factory=list)
    queue: list[dict[str, Any]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def _all(conn: AsyncConnection, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = await conn.execute(text(sql), params)
    return [dict(row._mapping) for row in result]


async def build(conn: AsyncConnection, tenant_id: str) -> Overview:
    """Assemble the whole picture in one bound transaction."""
    stage = await current_stage(conn, tenant_id)
    behaviour = BEHAVIOUR[stage]

    mandates = await _all(
        conn,
        "SELECT mandate_id, rail, state, mcc, max_amount_paise, created_at"
        "  FROM mandates ORDER BY created_at DESC LIMIT :n",
        n=LIMIT,
    )
    for m in mandates:
        # Which arm this mandate is in *at the current stage* — the reason a
        # perfectly healthy mandate may be sitting untouched (ADR-095).
        m["arm"] = str(arm_for(stage, tenant_id=tenant_id, mandate_id=str(m["mandate_id"])))

    cycles = await _all(
        conn,
        "SELECT cycle_id, mandate_id, state, amount_paise, attempts_used, attempt_budget,"
        "       due_at, deadline_at, pdn_sent_at, recovered_paise"
        "  FROM cycles ORDER BY due_at DESC LIMIT :n",
        n=LIMIT,
    )

    queue = await _all(
        conn,
        "SELECT action_id, action_type, cycle_id, fire_at, state"
        "  FROM scheduled_actions ORDER BY fire_at LIMIT :n",
        n=LIMIT,
    )

    ledger = await _all(
        conn,
        "SELECT decision_id, chain_seq, action_type, verdict, ts, cycle_id, rationale"
        "  FROM decisions ORDER BY chain_seq DESC LIMIT :n",
        n=LIMIT,
    )

    counts = (
        await _all(
            conn,
            "SELECT"
            "  (SELECT count(*) FROM mandates) AS mandates,"
            "  (SELECT count(*) FROM cycles) AS cycles,"
            "  (SELECT count(*) FROM cycles WHERE state = 'executing') AS in_flight,"
            "  (SELECT count(*) FROM events_raw) AS events,"
            "  (SELECT count(*) FROM events_raw WHERE processed_at IS NULL) AS unprocessed,"
            "  (SELECT count(*) FROM scheduled_actions WHERE state = 'pending') AS queued,"
            "  (SELECT count(*) FROM decisions) AS decisions,"
            "  (SELECT count(*) FROM decisions WHERE verdict = 'ALLOW') AS allowed,"
            "  (SELECT count(*) FROM decisions WHERE verdict <> 'ALLOW') AS refused,"
            "  (SELECT coalesce(sum(recovered_paise), 0) FROM cycles) AS recovered_paise",
        )
    )[0]

    # An unprocessed backlog is the one number that means the pipeline has
    # stalled rather than merely having nothing to do (§43's projector lag).
    counts["pipeline_healthy"] = int(counts["unprocessed"]) == 0

    return Overview(
        tenant_id=tenant_id,
        stage=stage.name,
        stage_description=behaviour.description,
        fires=behaviour.fires,
        mandate_share=behaviour.mandate_share,
        holdout_pct=behaviour.holdout_pct,
        mandates=mandates,
        cycles=cycles,
        queue=queue,
        ledger=ledger,
        totals=counts,
    )


def rupees(paise: Any) -> str:
    """Paise to a readable rupee string. Display only — never arithmetic.

    Money is integer paise everywhere in this system; this exists so a page can
    show `₹2,499.00` without any code path being tempted to compute in floats.
    """
    try:
        value = int(paise)
    except (TypeError, ValueError):
        return "—"
    return f"₹{value // 100:,}.{value % 100:02d}"


def stage_order() -> list[str]:
    """§44's stages in order, for rendering the ramp."""
    return [s.name for s in Stage]
