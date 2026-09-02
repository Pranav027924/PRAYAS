"""End-to-end demonstration: a failed debit, recovered.

Runs the **real** pipeline — the same projector, planner, gate, executor and
ledger the service runs — against the live database. Nothing is stubbed except
the rail (`FakeProvider`, as in any test-mode deployment) and the notification
channel, which needs a DLT-registered sender to reach a real phone
(`docs/BLOCKERS.md` §1.2).

**Why it is in two acts.** §30.1 requires 24 hours between the notice and the
debit, and the rule pack's `hours_since()` reads the wall clock rather than the
evaluation instant (FINDING-P17-12), so that gap cannot be compressed. One
continuous cycle therefore takes 24 real hours.

* **Act I** drives a live cycle from a failed payment to a lawfully planned and
  *actually sent* notice. Every step is real: the projector, the DP, the
  contact-window and notice-lead constraints, the ledger.
* **Act II** takes a cycle whose notice went out 30 hours ago and fires its
  debit. The 30-hour-old notice is seeded exactly as every gate test in this
  repository seeds one — `now() - 30h` — and is labelled as history rather
  than as something Prayas just did.

Nothing is backdated to make a rule pass. In Act I the notice is genuinely
sent before `pdn_sent_at` is written. In Act II the cycle openly *starts* from
a noticed state; the debit, the gate and the ledger entry are all real.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prayas.executor.provider import FakeProvider
from prayas.executor.worker import drain_tenant

OKAY = "  ✓"
INFO = "   "


def _say(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


async def _rows(engine: object, tenant: str, sql: str) -> list[dict[str, object]]:
    async with engine.begin() as conn:  # type: ignore[attr-defined]
        result = await conn.execute(text(sql), {"t": tenant})
        return [dict(r._mapping) for r in result]


async def act_one(engine: object, tenant: str, provider: FakeProvider) -> bool:
    """A live cycle, from failed payment to a notice actually sent."""
    actions = await _rows(
        engine,
        tenant,
        "SELECT action_id, action_type, fire_at FROM scheduled_actions"
        " WHERE tenant_id = :t AND state = 'pending' ORDER BY fire_at",
    )
    notice = next((a for a in actions if a["action_type"] == "pdn_notice"), None)
    debit = next((a for a in actions if a["action_type"] == "debit_attempt"), None)
    if notice is None or debit is None:
        sys.stderr.write(
            f"expected a notice and a debit pending for {tenant!r}; found "
            f"{[a['action_type'] for a in actions]}.\n"
            "Send the webhooks and let the projector and planner run first.\n"
        )
        return False

    n_at, d_at = notice["fire_at"], debit["fire_at"]
    assert isinstance(n_at, datetime) and isinstance(d_at, datetime)
    _say(f"{INFO} the DP chose a debit slot that admits a lawful notice:")
    _say(
        f"{INFO}   notice {n_at:%d %b %H:%M} UTC   debit {d_at:%d %b %H:%M} UTC"
        f"   ({(d_at - n_at).total_seconds() / 3600:.0f}h of notice)"
    )

    async with engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(
            text(
                "UPDATE scheduled_actions SET fire_at = now() - interval '1 minute'"
                " WHERE action_id = :a"
            ),
            {"a": notice["action_id"]},
        )
    await drain_tenant(engine, tenant, provider, now=datetime.now(UTC))

    cycles = await _rows(
        engine, tenant, "SELECT cycle_id, pdn_sent_at FROM cycles WHERE tenant_id = :t"
    )
    sent = cycles[0]["pdn_sent_at"] if cycles else None
    if sent is None:
        sys.stderr.write("the notice did not record pdn_sent_at\n")
        return False
    assert isinstance(sent, datetime)
    _say(f"{OKAY} notice sent and recorded: pdn_sent_at = {sent:%d %b %H:%M} UTC")

    ledger = await _rows(
        engine,
        tenant,
        "SELECT action_type, verdict, chain_seq FROM decisions"
        " WHERE tenant_id = :t ORDER BY chain_seq",
    )
    for row in ledger:
        _say(f"{INFO}   ledger #{row['chain_seq']}  {row['action_type']:<13} {row['verdict']}")
    _say(f"{INFO} the debit is queued for {d_at:%d %b %H:%M} UTC and will be lawful then.")
    return True


async def act_two(engine: object, tenant: str, provider: FakeProvider) -> bool:
    """A cycle already 30 hours past its notice: the debit fires."""
    cycle_id = f"inv_{tenant}_settled"
    # Reuse whichever mandate this tenant was onboarded with — the demo picks
    # one in the treatment arm, which is not necessarily `_1`.
    existing = await _rows(
        engine, tenant, "SELECT mandate_id FROM mandates WHERE tenant_id = :t LIMIT 1"
    )
    if not existing:
        sys.stderr.write(f"no mandate found for {tenant!r}\n")
        return False
    mandate = str(existing[0]["mandate_id"])
    now = datetime.now(UTC)

    async with engine.begin() as conn:  # type: ignore[attr-defined]
        await conn.execute(
            text("DELETE FROM scheduled_actions WHERE tenant_id = :t AND cycle_id = :c"),
            {"t": tenant, "c": cycle_id},
        )
        await conn.execute(
            text("DELETE FROM cycles WHERE tenant_id = :t AND cycle_id = :c"),
            {"t": tenant, "c": cycle_id},
        )
        # Seeded history: the notice went out 30 hours ago. Anchored to `now()`
        # exactly as every gate test in this repository anchors one, because
        # `hours_since()` reads the wall clock (FINDING-P17-12).
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                " due_at, deadline_at, attempt_budget, attempts_used, state, pdn_sent_at)"
                " VALUES (:c, :t, :m, 2, :amt, :due, :dl, 4, 1, 'executing', :pdn)"
            ),
            {
                "c": cycle_id,
                "t": tenant,
                "m": mandate,
                "amt": 249900,
                "due": now - timedelta(days=2),
                "dl": now + timedelta(days=18),
                "pdn": now - timedelta(hours=30),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions (action_id, tenant_id, cycle_id, mandate_id,"
                " action_type, fire_at, state, payload)"
                " VALUES (:a, :t, :c, :m, 'debit_attempt', now() - interval '1 minute',"
                "         'pending', CAST(:p AS jsonb))"
            ),
            {
                "a": f"{tenant}:{cycle_id}:1",
                "t": tenant,
                "c": cycle_id,
                "m": mandate,
                "p": '{"amount_paise": 249900}',
            },
        )
    _say(f"{INFO} cycle {cycle_id}: ₹2,499 owed, 1 attempt used, notice sent 30h ago")

    await drain_tenant(engine, tenant, provider, now=now)

    decisions = await _rows(
        engine,
        tenant,
        "SELECT verdict, compliance_checks, chain_seq FROM decisions"
        " WHERE tenant_id = :t ORDER BY chain_seq DESC LIMIT 1",
    )
    if not decisions:
        sys.stderr.write("no decision was recorded\n")
        return False

    verdict = decisions[0]["verdict"]
    _say(f"{INFO} gate re-evaluated at fire time (§32), as_of = now:")
    for check in decisions[0]["compliance_checks"]:
        mark = "✓" if check["verdict"] == "ALLOW" else "✗"
        _say(f"       {mark} {check['rule_id']:<24} v{check['version']}  {check['verdict']}")

    if verdict != "ALLOW":
        _say()
        _say("   The gate refused. That is the system working, not failing.")
        return False

    _say(f"{OKAY} gate ALLOWED — debit submitted to the rail")
    _say(
        f"{INFO} distinct debits at the provider: {provider.distinct_debits}"
        f"  (submissions: {len(provider.submissions)})"
    )

    state = await _rows(
        engine,
        tenant,
        f"SELECT state, attempts_used FROM cycles WHERE tenant_id = :t AND cycle_id = '{cycle_id}'",
    )
    _say(f"{INFO} cycle now: state={state[0]['state']} attempts={state[0]['attempts_used']}/4")
    return True


async def run(tenant: str) -> int:
    url = os.environ.get("PRAYAS_DATABASE_URL_OWNER")
    if not url:
        sys.stderr.write("PRAYAS_DATABASE_URL_OWNER must be set\n")
        return 1
    engine = create_async_engine(url)
    provider = FakeProvider()  # deterministic: no injected failures

    try:
        _say()
        _say("=" * 62)
        _say("ACT I — a failed debit becomes a lawfully planned recovery")
        _say("=" * 62)
        if not await act_one(engine, tenant, provider):
            return 1

        _say()
        _say("=" * 62)
        _say("ACT II — 30 hours after its notice, the debit fires")
        _say("=" * 62)
        if not await act_two(engine, tenant, provider):
            return 1

        _say()
        _say("  Money moved. The gate allowed it because the notice preceded it")
        _say("  by more than 24 hours, and the ledger records both.")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant")
    args = parser.parse_args()
    return asyncio.run(run(args.tenant))


if __name__ == "__main__":
    raise SystemExit(main())
