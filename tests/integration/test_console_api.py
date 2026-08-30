"""The console over HTTP (Master Spec §5, §18, §32; Phase 16).

**Phase 16 exit criteria** exercised here: replay renders any decision
*including denied ones*, and RBAC is enforced per screen.

The denied case is the one that matters. §5 gives the compliance reviewer one
job — "prove this action was lawful when it fired" — and a screen that rendered
only successes could not answer it. A DENY is a lawful outcome and its evidence
is the same evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.console.auth import (
    COMPLIANCE_REVIEWER,
    DATA_SCIENTIST,
    MERCHANT_OPS,
    SECRET_ENV,
    issue,
)
from prayas.db.tenancy import tenant_transaction
from prayas.ledger.chain import append
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

SECRET = "console-test-secret"
TS = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://console") as c:
        yield c


def _auth(role: str, tenant: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue(tenant, role, secret=SECRET.encode())}"}


async def _seed_decision(engine: AsyncEngine, tenant: str, *, verdict: str, n: int) -> str:
    decision_id = f"{tenant}_console_{verdict.lower()}_{n}"
    async with tenant_transaction(engine, tenant) as conn:
        await append(
            conn,
            {
                "decision_id": decision_id,
                "ts": TS + timedelta(minutes=n),
                "action_type": "debit_attempt",
                "verdict": verdict,
                "rationale": "insufficient expected value" if verdict == "DENY" else None,
                "candidate_actions": json.dumps(
                    [
                        {"action": "retry", "slot": 74, "ev_paise": 120_400},
                        {"action": "retry", "slot": 98, "ev_paise": 118_900},
                    ]
                ),
                "chosen_action": json.dumps({"action": "retry", "slot": 74, "ev_paise": 120_400})
                if verdict == "ALLOW"
                else None,
                "compliance_checks": json.dumps(
                    [
                        {
                            "rule_id": "NPCI-AUTOPAY-WINDOW",
                            "version": 3,
                            "verdict": verdict,
                            "citation": "UPI Autopay non-peak execution windows",
                        }
                    ]
                ),
                "model_versions": json.dumps({"hazard": "v1"}),
                "degraded": False,
            },
            tenant,
        )
    return decision_id


# ── the criterion: replay renders denied decisions ─────────────────────────


@pytest.mark.parametrize("verdict", ["ALLOW", "DENY"])
async def test_replay_renders_any_decision(
    client: AsyncClient, app_engine: AsyncEngine, two_tenants: tuple[str, str], verdict: str
) -> None:
    """**Phase 16 exit criterion.** A denial renders as fully as an allowance."""
    tenant, _ = two_tenants
    decision_id = await _seed_decision(app_engine, tenant, verdict=verdict, n=1)

    response = await client.get(
        f"/console/replay/{decision_id}", headers=_auth(COMPLIANCE_REVIEWER, tenant)
    )
    assert response.status_code == 200

    body = response.json()
    assert body["verdict"] == verdict
    # §32 requires the rejected candidates, not only the winner.
    assert body["shows_rejected_candidates"]
    assert len(body["candidates"]) == 2
    # Invariant 10 — every rule carries a citation.
    assert body["compliance_checks"][0]["citation"]
    assert body["integrity"]["record_hash"]


@pytest.mark.parametrize("verdict", ["ALLOW", "DENY"])
async def test_the_html_screen_renders_the_whole_chain(
    client: AsyncClient, app_engine: AsyncEngine, two_tenants: tuple[str, str], verdict: str
) -> None:
    """**The phase artifact.** Click one recovered rupee, see the causal chain."""
    tenant, _ = two_tenants
    decision_id = await _seed_decision(app_engine, tenant, verdict=verdict, n=2)

    response = await client.get(
        f"/console/replay/{decision_id}/view", headers=_auth(COMPLIANCE_REVIEWER, tenant)
    )
    assert response.status_code == 200

    html = response.text
    assert verdict in html
    assert "NPCI-AUTOPAY-WINDOW" in html
    assert "UPI Autopay non-peak execution windows" in html
    assert "120400" in html or "120,400" in html
    assert "Candidates considered" in html
    assert "Integrity" in html


async def test_a_missing_decision_is_a_404_not_a_500(
    client: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants
    response = await client.get(
        "/console/replay/does_not_exist", headers=_auth(COMPLIANCE_REVIEWER, tenant)
    )
    assert response.status_code == 404


# ── the criterion: RBAC per screen ─────────────────────────────────────────


async def test_a_role_without_replay_is_refused(
    client: AsyncClient, app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**Phase 16 exit criterion.** `data_scientist` has no replay surface in §5."""
    tenant, _ = two_tenants
    decision_id = await _seed_decision(app_engine, tenant, verdict="ALLOW", n=3)

    response = await client.get(
        f"/console/replay/{decision_id}", headers=_auth(DATA_SCIENTIST, tenant)
    )
    assert response.status_code == 403


async def test_no_token_is_refused(client: AsyncClient) -> None:
    assert (await client.get("/console/replay/anything")).status_code == 401


@pytest.mark.parametrize(
    "header",
    ["Bearer nonsense", "Basic abc", "bearer", "Bearer ", "Token abc"],
)
async def test_a_bad_token_is_refused_without_a_stack_trace(
    client: AsyncClient, header: str
) -> None:
    """Anyone can send anything here. It must refuse, not 500."""
    response = await client.get("/console/replay/anything", headers={"Authorization": header})
    assert response.status_code == 401
    assert "traceback" not in response.text.lower()


async def test_the_screens_endpoint_reflects_the_role(
    client: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """A menu of things you cannot do is worse than a shorter menu."""
    tenant, _ = two_tenants

    reviewer = (
        await client.get("/console/screens", headers=_auth(COMPLIANCE_REVIEWER, tenant))
    ).json()
    assert reviewer["screens"] == ["decision_replay"]

    ops = (await client.get("/console/screens", headers=_auth(MERCHANT_OPS, tenant))).json()
    assert set(ops["screens"]) == {"batch_result", "policy_simulator"}


# ── the tenant comes from the token (§18) ──────────────────────────────────


async def test_a_token_cannot_reach_another_tenants_decision(
    client: AsyncClient, app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """§18: the tenant is bound "from a verified token. NEVER from a request
    parameter". Reading another tenant's data would require forging a
    signature, not guessing an id."""
    tenant_a, tenant_b = two_tenants
    decision_id = await _seed_decision(app_engine, tenant_a, verdict="ALLOW", n=4)

    response = await client.get(
        f"/console/replay/{decision_id}", headers=_auth(COMPLIANCE_REVIEWER, tenant_b)
    )
    assert response.status_code == 404, "tenant B saw tenant A's decision"


async def test_no_endpoint_accepts_a_tenant_parameter() -> None:
    """Structural: there is nothing for a caller to tamper with."""
    from prayas.console import api

    for route in api.router.routes:
        path = getattr(route, "path", "")
        assert "tenant" not in path, f"{path} takes a tenant from the request"


# ── screen 3: the simulator over HTTP ──────────────────────────────────────


async def test_the_simulator_responds_and_respects_rbac(
    client: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants

    allowed = await client.post(
        "/console/simulate",
        json={"cycles": 50, "attempt_cost_multiplier": 1.0},
        headers=_auth(MERCHANT_OPS, tenant),
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["cycles"] == 50
    assert body["acted"] >= 0

    refused = await client.post(
        "/console/simulate", json={"cycles": 50}, headers=_auth(COMPLIANCE_REVIEWER, tenant)
    )
    assert refused.status_code == 403, "a compliance reviewer must not retune policy"


async def test_the_simulator_caps_how_much_cpu_one_request_can_spend(
    client: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """§41.1's T9 is denial of wallet. An uncapped `cycles` is the cheapest way
    for a caller to spend someone else's CPU."""
    tenant, _ = two_tenants
    response = await client.post(
        "/console/simulate", json={"cycles": 10_000_000}, headers=_auth(MERCHANT_OPS, tenant)
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"cycles": 0},
        {"cycles": -1},
        {"attempt_cost_multiplier": -5.0},
        {"lambda_annoyance": "not a number"},
    ],
)
async def test_incoherent_knobs_are_refused(
    client: AsyncClient, two_tenants: tuple[str, str], body: dict[str, object]
) -> None:
    tenant, _ = two_tenants
    response = await client.post(
        "/console/simulate", json=body, headers=_auth(MERCHANT_OPS, tenant)
    )
    assert response.status_code == 422


async def test_the_simulator_is_reproducible_across_requests(
    client: AsyncClient, two_tenants: tuple[str, str]
) -> None:
    """A screen whose baseline drifted underneath the slider would make every
    comparison meaningless."""
    tenant, _ = two_tenants
    payload = {"cycles": 40, "mu_revocation": 2.0}

    first = await client.post(
        "/console/simulate", json=payload, headers=_auth(MERCHANT_OPS, tenant)
    )
    second = await client.post(
        "/console/simulate", json=payload, headers=_auth(MERCHANT_OPS, tenant)
    )

    for key in ("acted", "mean_slot", "distinct_slots", "stop_rate"):
        assert first.json()[key] == second.json()[key]
