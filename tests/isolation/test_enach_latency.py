"""eNACH's outcome latency, handled without assuming synchronous results (§9, §19).

**Phase 14 exit criterion.** The specific bug this is written against: treating
an unresolved presentation as a failure and retrying. On a rail with a
one-working-day latency that presents the same debit into two successive
clearing cycles — debiting the customer twice, on a rail where reversal is slow
and manual.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.domain.rails import EnachAdapter, adapter_for
from prayas.domain.states import AttemptState
from prayas.executor.reconcile import (
    AWAITING,
    OVERDUE,
    RESOLVED,
    awaiting_outcome,
    mark_presented,
    overdue_outcomes,
    retry_is_blocked,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

# 2026-06-01 is a Monday. 09:30 IST.
PRESENTED = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)


async def _seed(conn: object, tenant: str, rail: str = "enach") -> str:
    params = {
        "t": tenant,
        "now": PRESENTED,
        "rail": rail,
        "deadline": PRESENTED + timedelta(days=7),
    }
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
            " max_amount_paise, state, consent_ref, created_at)"
            " VALUES (:t || '_enach_m', :t, 'cust_1', :rail, 500000, 'active', 'c1', :now)"
        ),
        params,
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
            " due_at, deadline_at, attempt_budget, state)"
            " VALUES (:t || '_enach_c', :t, :t || '_enach_m', 1, 149900, :now, :deadline, 3, 'scheduled')"
        ),
        params,
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO attempts (attempt_id, tenant_id, cycle_id, attempt_seq, idem_key,"
            " scheduled_for, fired_at, state, decision_id)"
            " VALUES (:t || '_enach_a', :t, :t || '_enach_c', 1, :t || '_enach_idem', :now, :now, 'fired',"
            " :t || '_enach_d')"
        ),
        params,
    )
    return f"{tenant}_enach_a"


# ── the criterion ──────────────────────────────────────────────────────────


async def test_no_outcome_exists_at_presentation_time(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**Phase 13 exit criterion.** At T+0 the attempt is presented and
    unresolved, and that is a legitimate state rather than a failure."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant)
        due = await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail="enach", presented_at=PRESENTED
        )
        assert due is not None and due > PRESENTED

        pending = await awaiting_outcome(conn, tenant, now=PRESENTED)
        assert [s.attempt_id for s in pending] == [attempt_id]
        assert pending[0].status_at(PRESENTED) == AWAITING
        assert not pending[0].is_resolved

        # And nothing is overdue yet.
        assert await overdue_outcomes(conn, tenant, now=PRESENTED) == []


async def test_a_retry_is_blocked_while_the_outcome_is_unknown(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """The bug this criterion exists to prevent: presenting the same debit into
    two successive clearing cycles because the first had not answered yet."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant)
        await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail="enach", presented_at=PRESENTED
        )
        status = (await awaiting_outcome(conn, tenant, now=PRESENTED))[0]

    # Blocked while awaiting...
    assert retry_is_blocked(status, PRESENTED)
    # ...and still blocked once overdue. Silence is not failure.
    much_later = PRESENTED + timedelta(days=5)
    assert status.status_at(much_later) == OVERDUE
    assert retry_is_blocked(status, much_later)


async def test_the_outcome_resolves_on_the_next_clearing_day(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """T+1, after which the attempt is scoreable and a retry is permitted."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant)
        await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail="enach", presented_at=PRESENTED
        )
        await conn.execute(
            text("UPDATE attempts SET state = :s WHERE attempt_id = :id"),
            {"s": AttemptState.FAILED.value, "id": attempt_id},
        )
        resolved = await awaiting_outcome(conn, tenant, now=PRESENTED + timedelta(days=2))
        assert resolved == [], "a resolved attempt is not still awaiting"


async def test_an_overdue_outcome_is_an_alert_not_a_failure(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """§19 puts an unreachable rail under degradation, not "assume the worst".
    Resolving unknowns toward "try again" is how a system double-debits during
    an NPCI incident."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant)
        await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail="enach", presented_at=PRESENTED
        )
        late = PRESENTED + timedelta(days=4)
        overdue = await overdue_outcomes(conn, tenant, now=late)

        assert [s.attempt_id for s in overdue] == [attempt_id]
        # Still `fired`, not `failed`. Nothing inferred from the silence.
        assert overdue[0].state == AttemptState.FIRED.value
        assert retry_is_blocked(overdue[0], late)


async def test_a_friday_presentation_is_not_overdue_on_saturday(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Calendar arithmetic, not `+ 24h`. A system expecting Saturday would page
    every weekend."""
    tenant, _ = two_tenants
    friday = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant)
        await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail="enach", presented_at=friday
        )
        saturday = friday + timedelta(days=1)
        assert await overdue_outcomes(conn, tenant, now=saturday) == []
        assert len(await awaiting_outcome(conn, tenant, now=saturday)) == 1


# ── the real-time rails are unaffected ─────────────────────────────────────


@pytest.mark.parametrize("rail", ["upi_autopay", "card_emandate"])
async def test_a_realtime_rail_records_no_presentation_state(
    app_engine: AsyncEngine, two_tenants: tuple[str, str], rail: str
) -> None:
    """Writing `presented_at` on a rail that resolves instantly would imply a
    distinction that does not exist there."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        attempt_id = await _seed(conn, tenant, rail=rail)
        due = await mark_presented(
            conn, tenant_id=tenant, attempt_id=attempt_id, rail=rail, presented_at=PRESENTED
        )
        assert due is None
        assert await awaiting_outcome(conn, tenant, now=PRESENTED) == []


def test_only_enach_carries_latency() -> None:
    assert adapter_for("enach").outcome_latency == EnachAdapter().outcome_latency > timedelta(0)
    assert adapter_for("upi_autopay").outcome_latency == timedelta(0)


def test_a_resolved_attempt_reports_resolved() -> None:
    from prayas.executor.reconcile import OutcomeStatus

    status = OutcomeStatus(
        attempt_id="a",
        rail="enach",
        state=AttemptState.SUCCEEDED.value,
        presented_at=PRESENTED,
        outcome_due_at=PRESENTED + timedelta(days=1),
    )
    assert status.status_at(PRESENTED) == RESOLVED
    assert not retry_is_blocked(status, PRESENTED)
