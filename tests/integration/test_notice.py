"""Firing a pre-debit notice (ADR-099; Master Spec §30.1, §24.6, §32).

The properties here are the ones whose failure would be invisible: a notice
that spends debit budget, a `pdn_sent_at` written when nothing was sent, or a
suppressed customer messaged anyway. Each of those leaves a system that looks
like it is working.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.executor.claiming import ClaimedAction
from prayas.executor.notice import ACTION_TYPE_NOTICE, fire_notice, is_notice
from prayas.memory.forget import pseudonymise

TENANT = "t_notice"
MANDATE = "sub_notice_1"
CUSTOMER = "cust_notice_1"
CYCLE = "inv_notice_1"


async def _seed(
    engine: AsyncEngine, *, state: str = "executing", pdn: datetime | None = None
) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for table in (
            "scheduled_actions",
            "interventions",
            "contact_suppressions",
            "cycles",
            "mandates",
            "decisions",
            "tenants",
        ):
            await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(
            text("INSERT INTO tenants (tenant_id, name, config) VALUES (:t, :t, '{}'::jsonb)"),
            {"t": TENANT},
        )
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, :c, 'upi_autopay', 1500000, 'active', 'r1', :now)"
            ),
            {"m": MANDATE, "t": TENANT, "c": CUSTOMER, "now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                " due_at, deadline_at, attempt_budget, attempts_used, state, pdn_sent_at)"
                " VALUES (:c, :t, :m, 1, 249900, :due, :dl, 4, 1, :state, :pdn)"
            ),
            {
                "c": CYCLE,
                "t": TENANT,
                "m": MANDATE,
                "due": now,
                "dl": now + timedelta(days=20),
                "state": state,
                "pdn": pdn,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions (action_id, tenant_id, cycle_id, mandate_id,"
                " action_type, fire_at, state, payload)"
                " VALUES (:a, :t, :c, :m, :ty, :f, 'claimed', '{\"channel\": \"console\"}'::jsonb)"
            ),
            {
                "a": "act_notice",
                "t": TENANT,
                "c": CYCLE,
                "m": MANDATE,
                "ty": ACTION_TYPE_NOTICE,
                "f": now,
            },
        )


def _action() -> ClaimedAction:
    return ClaimedAction(
        action_id="act_notice",
        tenant_id=TENANT,
        cycle_id=CYCLE,
        mandate_id=MANDATE,
        action_type=ACTION_TYPE_NOTICE,
        fire_at=datetime.now(UTC),
        payload={"channel": "console"},
    )


async def _cycle(engine: AsyncEngine) -> dict[str, object]:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT pdn_sent_at, attempts_used, attempt_budget FROM cycles"
                    " WHERE tenant_id = :t AND cycle_id = :c"
                ),
                {"t": TENANT, "c": CYCLE},
            )
        ).one()
    return dict(row._mapping)


def test_dispatch_recognises_a_notice() -> None:
    assert is_notice(_action())


@pytest.mark.asyncio
async def test_sending_records_pdn_and_leaves_the_budget_alone(owner_engine: AsyncEngine) -> None:
    """§1's retry allowance counts debits. A message must not spend one."""
    await _seed(owner_engine)
    before = await _cycle(owner_engine)

    async with owner_engine.begin() as conn:
        outcome = await fire_notice(conn, _action())

    after = await _cycle(owner_engine)
    assert outcome.sent
    assert after["pdn_sent_at"] is not None
    assert after["attempts_used"] == before["attempts_used"], "a notice spent debit budget"


@pytest.mark.asyncio
async def test_the_notice_is_recorded_in_the_ledger(owner_engine: AsyncEngine) -> None:
    """§32: a message sent to a person is an action, so it is recorded.

    `interventions.decision_id` is NOT NULL, which is the schema enforcing it.
    """
    await _seed(owner_engine)
    async with owner_engine.begin() as conn:
        await fire_notice(conn, _action())

    async with owner_engine.begin() as conn:
        decisions = list(
            await conn.execute(
                text("SELECT action_type, verdict FROM decisions WHERE tenant_id = :t"),
                {"t": TENANT},
            )
        )
        interventions = list(
            await conn.execute(
                text("SELECT kind, decision_id FROM interventions WHERE tenant_id = :t"),
                {"t": TENANT},
            )
        )
    assert [(d.action_type, d.verdict) for d in decisions] == [(ACTION_TYPE_NOTICE, "ALLOW")]
    assert len(interventions) == 1
    assert (
        interventions[0].decision_id == decisions[0].decision_id
        if hasattr(decisions[0], "decision_id")
        else interventions[0].decision_id is not None
    )


@pytest.mark.asyncio
async def test_a_suppressed_customer_is_not_messaged(owner_engine: AsyncEngine) -> None:
    """§24.6 and the opt-out path. Silence is an outcome, not an omission.

    The suppression is stored under the **pseudonym** (§27), which is what the
    lookup must resolve — checking the raw customer id would message someone
    who asked not to be.
    """
    await _seed(owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO contact_suppressions (tenant_id, customer_ref, reason, suppressed_at)"
                " VALUES (:t, :ref, 'opt_out', now())"
            ),
            {"t": TENANT, "ref": pseudonymise(TENANT, CUSTOMER)},
        )
        outcome = await fire_notice(conn, _action())

    assert not outcome.sent
    assert outcome.reason.startswith("suppressed:")
    assert (await _cycle(owner_engine))["pdn_sent_at"] is None, (
        "a suppressed notice must not unlock the debit"
    )


@pytest.mark.asyncio
async def test_an_erased_customer_is_still_suppressed(owner_engine: AsyncEngine) -> None:
    """Erasure rewrites `mandates.customer_id` to the pseudonym in place.

    After a forget request the stored id already *is* the ref, so hashing it
    again looks up a value that was never written. Both forms must be checked
    or an erased customer keeps receiving messages.
    """
    await _seed(owner_engine)
    ref = pseudonymise(TENANT, CUSTOMER)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE mandates SET customer_id = :ref WHERE tenant_id = :t"),
            {"ref": ref, "t": TENANT},
        )
        await conn.execute(
            text(
                "INSERT INTO contact_suppressions (tenant_id, customer_ref, reason, suppressed_at)"
                " VALUES (:t, :ref, 'erasure', now())"
            ),
            {"t": TENANT, "ref": ref},
        )
        outcome = await fire_notice(conn, _action())

    assert not outcome.sent
    assert (await _cycle(owner_engine))["pdn_sent_at"] is None


@pytest.mark.asyncio
async def test_a_second_notice_does_not_resend(owner_engine: AsyncEngine) -> None:
    """A duplicate message reaches a real person. The gate is already satisfied."""
    await _seed(owner_engine, pdn=datetime.now(UTC) - timedelta(hours=30))
    before = await _cycle(owner_engine)

    async with owner_engine.begin() as conn:
        outcome = await fire_notice(conn, _action())

    assert not outcome.sent
    assert outcome.reason == "already_sent"
    assert (await _cycle(owner_engine))["pdn_sent_at"] == before["pdn_sent_at"]


@pytest.mark.asyncio
async def test_a_settled_cycle_needs_no_notice(owner_engine: AsyncEngine) -> None:
    """§32 revalidates at fire time: a cycle that paid while queued is done."""
    await _seed(owner_engine, state="succeeded")
    async with owner_engine.begin() as conn:
        outcome = await fire_notice(conn, _action())

    assert not outcome.sent
    assert outcome.reason == "cycle_settled"
    assert (await _cycle(owner_engine))["pdn_sent_at"] is None


@pytest.mark.asyncio
async def test_withdrawn_consent_denies_the_debit(owner_engine: AsyncEngine) -> None:
    """DPDP-CONSENT-VALID must be able to fire.

    `_gate_context` hardcoded `consent_withdrawn: False`, so the rule was in
    the pack, listed in every ledger citation, and structurally incapable of
    denying — a customer who had withdrawn consent would still be debited.
    """
    from prayas.executor.firing import _gate_context

    class _Cycle:
        amount_paise = 249900
        mcc = "7997"
        pdn_sent_at = datetime.now(UTC) - timedelta(hours=30)
        consent_ref = "cns_live"
        consent_withdrawn = True
        attempts_used = 1
        attempt_budget = 4

    ctx = _gate_context(_Cycle(), _action(), datetime.now(UTC))
    assert ctx["consent_withdrawn"] is True, "a withdrawal must reach the gate"

    _Cycle.consent_withdrawn = False
    assert _gate_context(_Cycle(), _action(), datetime.now(UTC))["consent_withdrawn"] is False
