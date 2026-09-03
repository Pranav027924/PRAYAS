"""The ledger and replay drawer (Demo spec §R4.3, §R4.4, N5).

This screen is the compliance reviewer's entire evaluation, so what these
assert is that it cannot flatter itself: a refusal is rendered with the same
weight as an allowance, and the chain badge reports a walk rather than a
constant.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.console.auth import SECRET_ENV, issue
from prayas.console.routes import _short_hash
from prayas.db.tenancy import tenant_transaction
from prayas.ledger.chain import append

SECRET = "ledger_screen_test_secret_00000"
TENANT = "t_ledger_screen"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ledger") as c:
        yield c


def _auth(role: str = "compliance_reviewer") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue(TENANT, role, secret=SECRET.encode())}"}


def _record(n: int, verdict: str) -> dict[str, object]:
    import json

    refused = verdict != "ALLOW"
    return {
        "decision_id": f"dec_ledger_{verdict.lower()}_{n}",
        "ts": datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        "trigger_event_id": None,
        "mandate_id": f"sub_{TENANT}_1",
        "cycle_id": f"inv_{TENANT}_{n}",
        "action_type": "debit_attempt",
        "verdict": verdict,
        "feature_snapshot_ref": None,
        "model_versions": json.dumps({}),
        "cause_posterior": None,
        "liquidity_curve_ref": None,
        "revocation_hazard": None,
        "continuation_value": None,
        "candidate_actions": json.dumps([]),
        "chosen_action": json.dumps({"action_type": "debit_attempt"}),
        "rationale": "refused at fire time" if refused else "fired attempt 1 of 4",
        "compliance_checks": json.dumps(
            [
                {
                    "rule_id": "RBI-EMANDATE-PDN-24H",
                    "version": 3,
                    "regulator": "RBI",
                    "citation": "Digital Payments - E-mandate Framework, 2026",
                    "as_of": "2026-08-30",
                    "verdict": verdict,
                }
            ]
        ),
        "holdout_arm": None,
        "propensity": None,
        "degraded": False,
        "outcome": None,
        "outcome_ts": None,
        "recovered_paise": None,
    }


@pytest.fixture
async def seeded(owner_engine: AsyncEngine) -> list[str]:
    async with owner_engine.begin() as conn:
        for table in ("decisions", "tenants"):
            await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": TENANT})
        await conn.execute(
            text("INSERT INTO tenants (tenant_id, name, config) VALUES (:t, :t, '{}'::jsonb)"),
            {"t": TENANT},
        )
    ids = []
    async with tenant_transaction(owner_engine, TENANT) as conn:
        for n, verdict in ((1, "ALLOW"), (2, "DENY"), (3, "ALLOW")):
            record = _record(n, verdict)
            await append(conn, record, TENANT)
            ids.append(str(record["decision_id"]))
    return ids


# ── N5: a refusal carries the same weight as an allowance ───────────────────


@pytest.mark.asyncio
async def test_refusals_render_as_prominently_as_allowances(
    client: AsyncClient, seeded: list[str]
) -> None:
    """A ledger that only shows success proves nothing.

    Both verdicts use the same row markup and the same chip element — only the
    colour token differs — so a refusal cannot be quietly de-emphasised into a
    footnote by a later change.
    """
    page = (await client.get("/console/ledger", headers=_auth())).text
    assert 'class="chip ok">ALLOW' in page
    assert 'class="chip deny">DENY' in page
    assert len(re.findall(r"<tr[^>]*>\s*<td class=\"num\">", page)) >= 3


@pytest.mark.asyncio
async def test_the_verdict_filters_actually_filter(client: AsyncClient, seeded: list[str]) -> None:
    refused = (await client.get("/console/ledger?verdict=refused", headers=_auth())).text
    assert "DENY" in refused
    assert ">ALLOW" not in refused

    allowed = (await client.get("/console/ledger?verdict=allow", headers=_auth())).text
    assert "ALLOW" in allowed
    assert ">DENY" not in allowed


# ── the chain badge must be earned ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_chain_badge_reports_a_real_walk(
    client: AsyncClient, seeded: list[str], owner_engine: AsyncEngine
) -> None:
    """It was hardcoded `true`.

    This is the one claim on the screen a reviewer cannot check for
    themselves, so it has to be the one most obviously earned. Breaking a
    record's hash must turn the badge.
    """
    page = (await client.get("/console/ledger", headers=_auth())).text
    assert "chain verified" in page

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE decisions SET rationale = 'tampered'"
                " WHERE tenant_id = :t AND decision_id = :d"
            ),
            {"t": TENANT, "d": seeded[1]},
        )

    broken = (await client.get("/console/ledger", headers=_auth())).text
    assert "chain BROKEN" in broken, "a tampered record left the badge green"


# ── the drawer, on both verdicts ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_drawer_opens_for_an_allowance_and_a_refusal(
    client: AsyncClient, seeded: list[str]
) -> None:
    for decision_id in (seeded[0], seeded[1]):
        page = (await client.get(f"/console/ledger?decision={decision_id}", headers=_auth())).text
        assert decision_id in page
        # §32's two load-bearing lines, both read aloud in the demo.
        assert "fire time, not schedule time" in page
        assert "RBI-EMANDATE-PDN-24H" in page
        assert "Digital Payments" in page, "the citation is missing"
        assert "v3" in page, "the rule version is missing"
        assert "Previous hash" in page and "This record" in page


@pytest.mark.asyncio
async def test_an_unknown_decision_leaves_the_page_usable(client: AsyncClient) -> None:
    """A bad link should not take the ledger down with it."""
    response = await client.get("/console/ledger?decision=dec_nope", headers=_auth())
    assert response.status_code == 200
    assert "Decisions" in response.text


def test_hashes_are_shortened_head_and_tail() -> None:
    """The middle of a hash carries nothing a reader can use, and a full 64
    characters pushes the columns that matter off the screen."""
    full = "9bfa98d788e2348178aefadbbafd4fda7c51d746065f1cee67de452a3bb213de"
    assert _short_hash(full) == "9bfa98d7…13de"
    assert _short_hash(None) == "—"
    assert _short_hash("short") == "short"
