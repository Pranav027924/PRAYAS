"""A mandate bills more than once (FINDING-P17-13; Master Spec §11, §17.2).

`cycles` enforces UNIQUE (mandate_id, seq_no) and the projector wrote a literal
`1`, so a mandate could hold exactly one cycle ever. Every mandate's *second*
billing month raised `UniqueViolation`, and because the projector catches per
tenant, that tenant's whole backlog stopped draining — silently, with the stack
still green.

It survived 1,404 tests because every one of them gives a mandate a single
cycle. Only 90 days of seeded history produced a second.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.ingest.worker import drain_tenant

TENANT = "t_multicycle"
MANDATE = "sub_multicycle_1"
NOW = datetime.now(UTC)


def _event(event_id: str, invoice: str, kind: str, at: datetime) -> dict[str, object]:
    sub = {
        "entity": {
            "id": MANDATE,
            "status": "active",
            "current_end": int((NOW + timedelta(days=40)).timestamp()),
        }
    }
    return {
        "event_id": event_id,
        "type": kind,
        "body": {
            "event": kind,
            "payload": {
                "subscription": sub,
                "payment": {
                    "entity": {"id": f"pay_{invoice}", "amount": 249900, "invoice_id": invoice}
                },
            },
            "created_at": int(at.timestamp()),
        },
    }


async def _seed(engine: AsyncEngine, cycles: int) -> None:
    async with engine.begin() as conn:
        for table in ("scheduled_actions", "cycles", "events_raw", "mandates", "tenants"):
            await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config)"
                " VALUES (:t, :t, '{\"adoption_stage\": 0}'::jsonb)"
            ),
            {"t": TENANT},
        )
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail, mcc,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, 'c1', 'upi_autopay', '5411', 1500000, 'created', 'r1', :now)"
            ),
            {"m": MANDATE, "t": TENANT, "now": NOW},
        )
        for i in range(cycles):
            at = NOW - timedelta(days=60 - i * 30)
            evt = _event(f"evt_mc_{i}", f"inv_mc_{i}", "payment.failed", at)
            await conn.execute(
                text(
                    "INSERT INTO events_raw (event_id, tenant_id, event_type, mandate_id,"
                    " payload, signature_ok)"
                    " VALUES (:e, :t, :ty, :m, CAST(:p AS jsonb), true)"
                ),
                {
                    "e": evt["event_id"],
                    "t": TENANT,
                    "ty": evt["type"],
                    "m": MANDATE,
                    "p": json.dumps(evt["body"]),
                },
            )


@pytest.mark.asyncio
async def test_a_mandate_can_hold_more_than_one_cycle(owner_engine: AsyncEngine) -> None:
    """Three billing months on one mandate must produce three cycles."""
    await _seed(owner_engine, cycles=3)

    projected = await drain_tenant(owner_engine, TENANT)
    assert projected == 3

    async with owner_engine.begin() as conn:
        rows = list(
            await conn.execute(
                text("SELECT cycle_id, seq_no FROM cycles WHERE tenant_id = :t ORDER BY seq_no"),
                {"t": TENANT},
            )
        )
    assert len(rows) == 3, "the projector dropped a billing cycle"
    assert [r.seq_no for r in rows] == [1, 2, 3], "seq_no must number the billing sequence"


@pytest.mark.asyncio
async def test_reprojection_does_not_renumber_a_cycle(owner_engine: AsyncEngine) -> None:
    """Projection is idempotent — it recomputes from full history every pass.

    If `seq_no` were re-derived on every conflict the numbers would climb on
    each tick and the unique constraint would eventually collide again.
    """
    await _seed(owner_engine, cycles=2)
    await drain_tenant(owner_engine, TENANT)

    async with owner_engine.begin() as conn:
        before = {
            r.cycle_id: r.seq_no
            for r in await conn.execute(
                text("SELECT cycle_id, seq_no FROM cycles WHERE tenant_id = :t"), {"t": TENANT}
            )
        }
        # Re-arm the whole log so the next pass reprojects the same mandate.
        await conn.execute(
            text("UPDATE events_raw SET processed_at = NULL WHERE tenant_id = :t"), {"t": TENANT}
        )

    await drain_tenant(owner_engine, TENANT)

    async with owner_engine.begin() as conn:
        after = {
            r.cycle_id: r.seq_no
            for r in await conn.execute(
                text("SELECT cycle_id, seq_no FROM cycles WHERE tenant_id = :t"), {"t": TENANT}
            )
        }
    assert after == before, "reprojection renumbered the billing sequence"


@pytest.mark.asyncio
async def test_one_tenants_failure_does_not_stall_its_backlog(
    owner_engine: AsyncEngine,
) -> None:
    """The watermark must advance, or the backlog grows forever.

    This is what made the original defect invisible: the projector catches per
    tenant and keeps ticking, so the stack stayed healthy while that tenant
    stopped making progress entirely.
    """
    await _seed(owner_engine, cycles=3)
    await drain_tenant(owner_engine, TENANT)

    async with owner_engine.begin() as conn:
        pending = (
            (
                await conn.execute(
                    text(
                        "SELECT count(*) c FROM events_raw"
                        " WHERE tenant_id = :t AND processed_at IS NULL"
                    ),
                    {"t": TENANT},
                )
            )
            .one()
            .c
        )
    assert pending == 0
