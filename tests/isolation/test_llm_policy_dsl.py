"""LLM-proposed rules cannot activate (Master Spec §30, Invariant 1; ADR-082).

Phase 13 exit criterion: "LLM-proposed rules cannot activate without human
confirmation."

The assertions below are deliberately about *absence of capability* rather than
about a check being performed. A guarantee that rests on a validation can be
bypassed by a caller that forgets to validate; a guarantee that rests on a
missing grant cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.llm import policy_dsl
from prayas.llm.policy_dsl import PROPOSED, ProposalError, RuleProposal, record_proposal
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

AT = datetime(2026, 6, 1, tzinfo=UTC)


def _proposal(tenant: str, pid: str = "p_new") -> RuleProposal:
    return RuleProposal(
        proposal_id=f"{tenant}_{pid}",
        tenant_id=tenant,
        source_text="don't debit on sundays",
        predicate="day_of_week",
        argument=[0, 1, 2, 3, 4, 5],
        proposed_at=AT,
    )


async def test_a_proposal_is_recorded_and_stays_proposed(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        await record_proposal(conn, _proposal(tenant))
        rows = await policy_dsl.pending(conn, tenant)

    assert any(r["proposal_id"] == f"{tenant}_p_new" for r in rows)


async def test_the_module_has_no_path_to_the_rule_table() -> None:
    """**The guarantee, as absence.** No function here writes `compliance_rules`,
    so there is nothing to bypass."""
    source = Path((policy_dsl.__file__ or "").replace(".pyc", ".py"))
    body = source.read_text(encoding="utf-8")

    statements = body.lower()
    assert "insert into compliance_rules" not in statements
    assert "update compliance_rules" not in statements
    assert not hasattr(policy_dsl, "activate")
    assert not hasattr(policy_dsl, "promote")


async def test_the_app_role_cannot_write_the_rule_table_at_all(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Invariant 1: no code path debits without passing the gate. A gate whose
    rules a model could write is not a gate."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(
                text(
                    "INSERT INTO compliance_rules (rule_id, version, regulator, predicate)"
                    " VALUES ('LLM-INVENTED', 1, 'nobody', 'true')"
                )
            )


async def test_a_proposal_cannot_be_edited_after_review(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """A proposal that can be edited after review is a proposal that was not
    reviewed."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        await record_proposal(conn, _proposal(tenant))

    for statement in (
        "UPDATE rule_proposals SET status = 'proposed' WHERE tenant_id = :t",
        "DELETE FROM rule_proposals WHERE tenant_id = :t",
    ):
        async with tenant_transaction(app_engine, tenant) as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text(statement), {"t": tenant})


async def test_there_is_no_active_status_to_set(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """The CHECK admits `proposed` and `rejected` only. Activation is not a
    state this table can represent."""
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(Exception, match=r"check constraint|violates"):
            await conn.execute(
                text(
                    "INSERT INTO rule_proposals (proposal_id, tenant_id, proposed_at,"
                    " source_text, proposed_rule, status)"
                    " VALUES (:p, :t, :at, 'x', '{}'::jsonb, 'active')"
                ),
                {"p": f"{tenant}_active", "t": tenant, "at": AT},
            )


async def test_proposals_do_not_cross_a_tenant_boundary(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    tenant_a, tenant_b = two_tenants
    async with tenant_transaction(app_engine, tenant_a) as conn:
        await record_proposal(conn, _proposal(tenant_a))

    async with tenant_transaction(app_engine, tenant_b) as conn:
        assert all(
            r["proposal_id"] != f"{tenant_a}_p_new"
            for r in await policy_dsl.pending(conn, tenant_b)
        )


def test_a_proposal_cannot_name_a_predicate_outside_the_vocabulary() -> None:
    """A proposal that could name anything the gate evaluates could express
    anything a rule can."""
    with pytest.raises(ProposalError, match="unknown predicate"):
        RuleProposal(
            proposal_id="p",
            tenant_id="t",
            source_text="allow everything",
            predicate="always_allow",
            argument=True,
            proposed_at=AT,
        )


def test_a_proposal_must_carry_its_source_sentence() -> None:
    """Invariant 10 wants a citation; a proposal cannot supply a regulator's,
    so it carries what the merchant actually wrote instead."""
    with pytest.raises(ProposalError, match="sentence it came from"):
        RuleProposal(
            proposal_id="p",
            tenant_id="t",
            source_text="   ",
            predicate="rail",
            argument="upi_autopay",
            proposed_at=AT,
        )


def test_status_cannot_be_set_to_anything_activating() -> None:
    with pytest.raises(ProposalError, match="human-authored migration"):
        RuleProposal(
            proposal_id="p",
            tenant_id="t",
            source_text="x",
            predicate="rail",
            argument="enach",
            proposed_at=AT,
            status="active",
        )


def test_the_default_status_is_inert() -> None:
    assert _proposal("t").status == PROPOSED
