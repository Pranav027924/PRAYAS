"""Hash-chain integrity (Master Spec §32; Invariant 5).

Exit criterion: "tampering with **any** ledger field is detected by the
verifier." Parametrised over every hashed column rather than one representative
field — a hash that covers most of the record is a hash that quietly permits
editing the rest.

Tampering is performed through the owner connection, which is a superuser here
and so bypasses the append-only grant. That is the point: the test simulates an
attacker who already has database access, which is the only threat a hash chain
is any use against (§41.1 T3).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.ledger.canonical import canonical
from prayas.ledger.chain import GENESIS, HASHED_FIELDS, append, compute_hash, verify_chain
from tests.conftest import TENANT_A, TENANT_B, requires_db

pytestmark = [pytest.mark.db, requires_db]


def _record(n: int, tenant: str = TENANT_A) -> dict[str, Any]:
    # decision_id must be globally unique, not merely unique per tenant: ADR-005
    # made the primary key (decision_id, ts) with no tenant component.
    return {
        "decision_id": f"{tenant}_dec_{n}",
        "ts": datetime(2026, 6, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=n),
        "action_type": "debit_attempt",
        "verdict": "ALLOW",
        "model_versions": json.dumps({"hazard": "v1"}),
        "candidate_actions": json.dumps([{"action": "retry", "ev_paise": 1200}]),
        "compliance_checks": json.dumps([{"rule_id": "NPCI-AUTOPAY-WINDOW", "verdict": "ALLOW"}]),
        "degraded": False,
    }


@pytest.fixture
async def clean_tenants(owner_engine: AsyncEngine) -> AsyncIterator[tuple[str, str]]:
    """Two tenants with an *empty* ledger.

    The shared `two_tenants` fixture seeds a placeholder `decisions` row whose
    prev_hash/record_hash are literals rather than a real chain — correct for
    the isolation suite, useless here, because the verifier rightly rejects it.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text("TRUNCATE decisions, tenants RESTART IDENTITY CASCADE"))
        for tenant in (TENANT_A, TENANT_B):
            await conn.execute(
                text("INSERT INTO tenants (tenant_id, name) VALUES (:t, :t)"), {"t": tenant}
            )

    yield TENANT_A, TENANT_B

    async with owner_engine.begin() as conn:
        await conn.execute(text("TRUNCATE decisions, tenants RESTART IDENTITY CASCADE"))


@pytest.fixture
async def chain_of_three(app_engine: AsyncEngine, clean_tenants: tuple[str, str]) -> str:
    async with tenant_transaction(app_engine, TENANT_A) as conn:
        for n in range(3):
            await append(conn, _record(n), TENANT_A)
    return TENANT_A


# ── the chain is sound when untouched ───────────────────────────────────────


async def test_an_untampered_chain_verifies(app_engine: AsyncEngine, chain_of_three: str) -> None:
    async with tenant_transaction(app_engine, chain_of_three) as conn:
        assert await verify_chain(conn, chain_of_three) == []


async def test_first_record_links_to_genesis(app_engine: AsyncEngine, chain_of_three: str) -> None:
    async with tenant_transaction(app_engine, chain_of_three) as conn:
        prev = await conn.scalar(
            text("SELECT prev_hash FROM decisions WHERE chain_seq = 0 AND tenant_id = :t"),
            {"t": chain_of_three},
        )
    assert prev == GENESIS


async def test_sequence_starts_at_zero_and_is_contiguous(
    app_engine: AsyncEngine, chain_of_three: str
) -> None:
    async with tenant_transaction(app_engine, chain_of_three) as conn:
        rows = (
            await conn.execute(
                text("SELECT chain_seq FROM decisions WHERE tenant_id = :t ORDER BY chain_seq"),
                {"t": chain_of_three},
            )
        ).scalars()
    assert list(rows) == [0, 1, 2]


# ── the exit criterion: every hashed field ──────────────────────────────────

#: A type-appropriate different value for each hashed column.
TAMPER_VALUES: dict[str, str] = {
    "decision_id": "'tampered_id'",
    "chain_seq": "chain_seq + 100",
    "prev_hash": f"'{'f' * 64}'",
    "ts": "ts + interval '1 day'",
    "trigger_event_id": "'evt_injected'",
    "mandate_id": "'mnd_injected'",
    "cycle_id": "'cyc_injected'",
    "action_type": "'notification'",
    "verdict": "'DENY'",  # must differ from the seeded ALLOW, or the UPDATE is a no-op
    "feature_snapshot_ref": "'snap_injected'",
    "model_versions": '\'{"hazard":"v99"}\'::jsonb',
    # No `:<digit>` sequences in these literals: SQLAlchemy's text() would parse
    # `:0` as a bind parameter and silently mangle the statement.
    "cause_posterior": '\'{"no_funds":"high"}\'::jsonb',
    "liquidity_curve_ref": "'curve_injected'",
    "revocation_hazard": "0.99999",
    "continuation_value": "999999",
    "candidate_actions": "'[]'::jsonb",
    "chosen_action": '\'{"action":"injected"}\'::jsonb',
    "rationale": "'rewritten rationale'",
    "compliance_checks": "'[]'::jsonb",  # erasing the evidence entirely
    "holdout_arm": "'control'",
    "propensity": "0.12345",
    "degraded": "NOT degraded",
    "outcome": "'recovered'",
    "outcome_ts": "now()",
    "recovered_paise": "4242424242",
}


@pytest.mark.parametrize(
    "column",
    sorted(set(HASHED_FIELDS) - {"tenant_id"}),
)
async def test_tampering_with_any_field_is_detected(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, chain_of_three: str, column: str
) -> None:
    """Every hashed column, one at a time.

    `tenant_id` is excluded only because changing it moves the record into a
    different tenant's chain, which is a distinct scenario covered separately.
    """
    assert column in TAMPER_VALUES, f"no tamper value defined for hashed column {column!r}"

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                f"UPDATE decisions SET {column} = {TAMPER_VALUES[column]}"
                " WHERE tenant_id = :t AND chain_seq = 1"
            ),
            {"t": chain_of_three},
        )

    async with tenant_transaction(app_engine, chain_of_three) as conn:
        breaks = await verify_chain(conn, chain_of_three)

    assert breaks, f"tampering with {column!r} went undetected"


async def test_tampering_with_record_hash_itself_is_detected(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, chain_of_three: str
) -> None:
    """Rewriting the stored hash to match nothing is still a break."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE decisions SET record_hash = :h WHERE tenant_id = :t AND chain_seq = 1"),
            {"h": "a" * 64, "t": chain_of_three},
        )

    async with tenant_transaction(app_engine, chain_of_three) as conn:
        breaks = await verify_chain(conn, chain_of_three)

    reasons = {b.reason for b in breaks}
    assert "hash mismatch" in reasons or "broken link" in reasons


async def test_deleting_a_record_leaves_a_sequence_gap(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, chain_of_three: str
) -> None:
    """ADR-005 removed the DB's cross-partition uniqueness guarantee.

    Contiguity checking is what replaces it, so a silently removed record is
    still visible as a gap.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM decisions WHERE tenant_id = :t AND chain_seq = 1"),
            {"t": chain_of_three},
        )

    async with tenant_transaction(app_engine, chain_of_three) as conn:
        breaks = await verify_chain(conn, chain_of_three)

    assert any(b.reason == "sequence gap" for b in breaks)


async def test_one_tampered_record_reports_one_break_not_a_cascade(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, chain_of_three: str
) -> None:
    """The verifier resumes from stored state, so damage is located, not smeared."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE decisions SET rationale = 'x' WHERE tenant_id = :t AND chain_seq = 1"),
            {"t": chain_of_three},
        )

    async with tenant_transaction(app_engine, chain_of_three) as conn:
        breaks = await verify_chain(conn, chain_of_three)

    assert len(breaks) == 1
    assert breaks[0].chain_seq == 1


# ── append-only is enforced by privilege, not by this module ────────────────


async def test_the_app_role_cannot_tamper_at_all(
    app_engine: AsyncEngine, chain_of_three: str
) -> None:
    """Invariant 5: the verifier is the second line of defence, not the first."""
    ledger = "decisions"
    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, chain_of_three) as conn:
            await conn.execute(text(f"UPDATE {ledger} SET verdict = 'ALLOW'"))
    assert "permission denied" in str(exc.value).lower()


# ── per-tenant chains are independent ───────────────────────────────────────


async def test_chains_are_per_tenant(
    app_engine: AsyncEngine, clean_tenants: tuple[str, str]
) -> None:
    """§32 — per-tenant chains, so one merchant cannot bottleneck another."""
    tenant_a, tenant_b = clean_tenants

    for tenant in (tenant_a, tenant_b):
        async with tenant_transaction(app_engine, tenant) as conn:
            await append(conn, _record(0, tenant), tenant)

    for tenant in (tenant_a, tenant_b):
        async with tenant_transaction(app_engine, tenant) as conn:
            assert await verify_chain(conn, tenant) == []
            seq = await conn.scalar(
                text("SELECT chain_seq FROM decisions WHERE tenant_id = :t"), {"t": tenant}
            )
        assert seq == 0, "each tenant's chain starts independently at zero"


# ── canonical serialisation is what every hash rests on ─────────────────────


def test_canonical_is_key_order_independent() -> None:
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_canonical_emits_no_whitespace() -> None:
    assert canonical({"a": 1, "b": 2}) == b'{"a":1,"b":2}'


def test_canonical_does_not_escape_non_ascii() -> None:
    """A Devanagari merchant name must hash identically either way."""
    assert canonical({"name": "प्रयास"}) == '{"name":"प्रयास"}'.encode()


def test_canonical_handles_datetimes_deterministically() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert canonical({"ts": moment}) == canonical({"ts": moment})


def test_hash_covers_every_declared_field() -> None:
    """A field added to the table but not to HASHED_FIELDS would be editable."""
    base = dict.fromkeys(HASHED_FIELDS)
    baseline = compute_hash(base)

    for field in HASHED_FIELDS:
        mutated = {**base, field: "changed"}
        assert compute_hash(mutated) != baseline, f"{field} does not affect the hash"
