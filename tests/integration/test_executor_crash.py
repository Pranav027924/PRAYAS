"""Crash-safety criteria (Playbook Phase 6).

Two exit criteria live here, both requiring a real `SIGKILL` against a real
process. See `crash_worker.py` for why a patched exception would not do.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.executor.outbox import relay_once
from prayas.executor.provider import FakeProvider
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_exec"
CYCLE = "cyc_exec"
MANDATE = "mnd_exec"
ACTION = "act_exec"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 09:00 IST — inside the NPCI pre-10:00 execution window, so the gate allows.
#: Fixed rather than "now" because the gate's window check would otherwise make
#: these tests pass or fail on the hour they happened to run at. It sits in the
#: real past so `fire_at <= now()` still makes the action claimable.
FIRE_AT = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)


@pytest.fixture
async def executor_cycle(owner_engine: AsyncEngine) -> AsyncIterator[str]:
    """One tenant with an active mandate, an unpaid cycle, and a due action."""
    now = FIRE_AT
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE decisions, outbox, scheduled_actions, attempts, cycles,"
                " mandates, tenants RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config) VALUES"
                " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
            ),
            {"t": TENANT},
        )
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at, mcc)"
                " VALUES (:m, :t, 'cust', 'upi_autopay', 5000000, 'active',"
                " 'consent_1', :now, '5411')"
            ),
            {"m": MANDATE, "t": TENANT, "now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no,"
                " amount_paise, due_at, deadline_at, attempt_budget, attempts_used,"
                " state, pdn_sent_at)"
                " VALUES (:c, :t, :m, 1, 49900, :now, :deadline, 4, 0, 'executing', :pdn)"
            ),
            {
                "c": CYCLE,
                "t": TENANT,
                "m": MANDATE,
                "now": now,
                "deadline": now + timedelta(days=20),
                "pdn": now - timedelta(hours=30),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions (action_id, tenant_id, cycle_id,"
                " mandate_id, action_type, fire_at, state, payload)"
                " VALUES (:a, :t, :c, :m, 'debit_attempt', :fire, 'pending', '{}'::jsonb)"
            ),
            {
                "a": ACTION,
                "t": TENANT,
                "c": CYCLE,
                "m": MANDATE,
                "fire": now - timedelta(minutes=1),
            },
        )
    yield TENANT
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE decisions, outbox, scheduled_actions, attempts, cycles,"
                " mandates, tenants RESTART IDENTITY CASCADE"
            )
        )


async def _kill_at(mode: str, tmp_path: pathlib.Path) -> None:
    """Run the crash worker to its park point, then SIGKILL it."""
    marker = tmp_path / f"{mode}.marker"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "tests/integration/crash_worker.py"),
            mode,
            TENANT,
            ACTION,
            str(marker),
            FIRE_AT.isoformat(),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    try:
        for _ in range(200):  # up to ~20s
            if marker.exists():
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError(f"{mode}: worker never reached its park point")

        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)

    assert proc.returncode != 0, "process exited normally; it was supposed to be killed"


async def test_kill_mid_transaction_leaves_no_orphan_and_no_lost_timer(
    app_engine: AsyncEngine, executor_cycle: str, tmp_path: pathlib.Path
) -> None:
    """Exit criterion: no orphaned debits, no lost timers.

    The killed process had already decremented the budget. Because it never
    committed, Postgres must roll all of it back — and the timer must return to
    the pool once its lease expires rather than being stranded.
    """
    await _kill_at("mid_transaction", tmp_path)

    async with tenant_transaction(app_engine, TENANT) as conn:
        used = await conn.scalar(
            text("SELECT attempts_used FROM cycles WHERE tenant_id = :t AND cycle_id = :c"),
            {"t": TENANT, "c": CYCLE},
        )
        attempts = await conn.scalar(text("SELECT count(*) FROM attempts"))
        outbox = await conn.scalar(text("SELECT count(*) FROM outbox"))
        state = await conn.scalar(
            text("SELECT state FROM scheduled_actions WHERE action_id = :a"), {"a": ACTION}
        )

    assert used == 0, f"budget was consumed by an uncommitted transaction (used={used})"
    assert attempts == 0, "an attempt row survived a rolled-back transaction"
    assert outbox == 0, "an outbox row survived a rolled-back transaction"
    assert state != "done", "the timer was settled by a transaction that never committed"


async def test_timer_survives_the_kill_and_is_immediately_claimable(
    app_engine: AsyncEngine, executor_cycle: str, tmp_path: pathlib.Path
) -> None:
    """No lost timers: the work is still there to be done.

    The dead worker had claimed the action *inside* the transaction it never
    committed, so the claim rolled back with everything else and there is not
    even a lease to wait out. Nothing was lost and nothing is stranded.
    """
    from prayas.executor.claiming import claim_due_actions

    await _kill_at("mid_transaction", tmp_path)

    async with tenant_transaction(app_engine, TENANT) as conn:
        reclaimed = await claim_due_actions(conn, TENANT)

    assert [a.action_id for a in reclaimed] == [ACTION], (
        "the timer did not survive the crash; the debit would never be retried"
    )


async def test_a_live_lease_is_not_stolen_but_an_expired_one_is_reclaimed(
    app_engine: AsyncEngine, executor_cycle: str
) -> None:
    """Lease semantics in both directions (ADR-044).

    A claim held by a worker that may still be alive must not be handed to a
    second worker — that is the risk a short lease creates. Once the lease
    expires the row must return to the pool, or a crash strands it forever.
    """
    from prayas.executor.claiming import claim_due_actions

    async with tenant_transaction(app_engine, TENANT) as conn:
        first = await claim_due_actions(conn, TENANT, lease_seconds=60)
    assert [a.action_id for a in first] == [ACTION]

    async with tenant_transaction(app_engine, TENANT) as conn:
        contending = await claim_due_actions(conn, TENANT)
    assert contending == [], "a live lease was stolen from a possibly-running worker"

    # Expire the lease the way wall-clock time would.
    async with tenant_transaction(app_engine, TENANT) as conn:
        await conn.execute(
            text(
                "UPDATE scheduled_actions SET locked_until = now() - interval '1 second'"
                " WHERE action_id = :a"
            ),
            {"a": ACTION},
        )

    async with tenant_transaction(app_engine, TENANT) as conn:
        reclaimed = await claim_due_actions(conn, TENANT)
    assert [a.action_id for a in reclaimed] == [ACTION], "expired lease was not reclaimed"


async def test_kill_after_outbox_insert_resumes_with_the_same_key(
    app_engine: AsyncEngine, executor_cycle: str, tmp_path: pathlib.Path
) -> None:
    """Exit criterion: relay resumes with the same key.

    §31: committing intent before the call means a crash between the two loses
    nothing. The killed process committed the outbox row and died before
    calling anything; the relay must pick it up under the identical key.
    """
    await _kill_at("after_outbox", tmp_path)

    async with tenant_transaction(app_engine, TENANT) as conn:
        rows = list(
            await conn.execute(text("SELECT idem_key, state FROM outbox ORDER BY outbox_id"))
        )
        attempt_keys = [
            r.idem_key for r in await conn.execute(text("SELECT idem_key FROM attempts"))
        ]
        used = await conn.scalar(
            text("SELECT attempts_used FROM cycles WHERE cycle_id = :c"), {"c": CYCLE}
        )

    assert len(rows) == 1, f"expected exactly one durable intent, got {len(rows)}"
    assert rows[0].state == "pending", "intent was settled by a process that never called out"
    assert attempt_keys == [rows[0].idem_key], "attempt and outbox disagree on the key"
    assert used == 1, "the committed transaction did not consume exactly one attempt"

    committed_key = rows[0].idem_key

    # The relay now resumes, with no knowledge of the crash.
    provider = FakeProvider()
    result = await relay_once(app_engine, TENANT, provider)

    assert result.sent == 1
    assert provider.submissions == [committed_key], (
        f"relay used {provider.submissions}, expected the committed key {committed_key}"
    )
    assert provider.distinct_debits == 1, "the crash produced more than one debit"
