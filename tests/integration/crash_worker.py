"""A worker that stops at a chosen point so the test can SIGKILL it.

Run as a real subprocess, not a thread or a patched exception. The Phase 6
criteria are about surviving a *process death*, and only an actual SIGKILL
exercises the thing being relied on: Postgres rolling back an abandoned
transaction and releasing its locks. A raised exception would exercise Python's
`finally` blocks instead, which is a different and much weaker guarantee.

Modes:
  mid_transaction  - inside the fire transaction, after the budget decrement,
                     before COMMIT. Death here must leave nothing behind.
  after_outbox     - after the fire transaction has COMMITTED, before the
                     provider is ever called. Death here must leave the intent
                     durable so the relay resumes on the same key.

Usage: crash_worker.py <mode> <tenant_id> <action_id> <marker_path> <now_iso>

`now_iso` is the instant the fire transaction evaluates against. It is passed
in rather than read from the clock so the gate's NPCI window check is
deterministic — otherwise the test would pass or fail depending on the hour it
happened to run at, which is the gate working correctly but a useless test.

The marker file is written once the process is parked at the kill point.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime

from sqlalchemy import text

from prayas.config import Settings
from prayas.db.engine import create_app_engine
from prayas.db.tenancy import tenant_transaction
from prayas.executor.claiming import claim_due_actions
from prayas.executor.firing import fire_action


async def _run(
    mode: str, tenant_id: str, action_id: str, marker: pathlib.Path, now: datetime
) -> None:
    engine = create_app_engine(Settings.from_env().database_url_app)

    if mode == "mid_transaction":
        async with tenant_transaction(engine, tenant_id) as conn:
            actions = await claim_due_actions(conn, tenant_id)
            target = next((a for a in actions if a.action_id == action_id), None)
            if target is None:
                marker.write_text("no-action")
                return

            # Reproduce the fire transaction's first half by hand so the kill
            # lands *inside* it: lock the cycle, consume the budget, then park
            # without committing.
            await conn.execute(
                text(
                    "SELECT cycle_id FROM cycles WHERE tenant_id = :t AND cycle_id = :c FOR UPDATE"
                ),
                {"t": tenant_id, "c": target.cycle_id},
            )
            await conn.execute(
                text(
                    "UPDATE cycles SET attempts_used = attempts_used + 1"
                    " WHERE tenant_id = :t AND cycle_id = :c"
                    "   AND attempts_used < attempt_budget"
                ),
                {"t": tenant_id, "c": target.cycle_id},
            )
            marker.write_text("parked")
            await asyncio.sleep(300)  # killed here; never commits

    elif mode == "after_outbox":
        async with tenant_transaction(engine, tenant_id) as conn:
            actions = await claim_due_actions(conn, tenant_id)
            target = next((a for a in actions if a.action_id == action_id), None)
            if target is None:
                marker.write_text("no-action")
                return
            outcome = await fire_action(conn, target, now=now)
        # Transaction has COMMITTED. Intent is durable; nothing has been called.
        marker.write_text(outcome.key or f"refused:{outcome.reason}")
        await asyncio.sleep(300)  # killed before the relay ever runs

    else:  # pragma: no cover
        raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    _, mode, tenant, action, marker_path, now_iso = sys.argv
    asyncio.run(
        _run(mode, tenant, action, pathlib.Path(marker_path), datetime.fromisoformat(now_iso))
    )
