"""The demo API (Demo spec §R2).

Shapes are frozen at the end of Phase 2 — the screens are built against them,
so a field that moves later moves a screen with it. These tests are the freeze.

The statistical assertions matter more than the shapes. A recovery rate above
1.0 or an attempts-per-recovery below 1.0 is arithmetically impossible, and
both occurred: a cycle that succeeded without ever failing was counted as a
recovery while not counting toward the population it recovered from.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.adoption.cohort import Arm
from prayas.api.main import app
from prayas.console.auth import SECRET_ENV, issue
from prayas.console.metrics import ArmStats, _diff_ci, matched_pair

SECRET = "demo_api_test_secret_value_00000"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://demo") as c:
        yield c


def _auth(role: str = "platform_pm", tenant: str = "t_demo_api") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue(tenant, role, secret=SECRET.encode())}"}


# ── the arithmetic ──────────────────────────────────────────────────────────


def test_a_recovery_rate_cannot_exceed_one() -> None:
    """The denominator defines the population.

    Counting a first-time success as a "recovery" without counting it as a
    failed cycle produced rates of 2.68, an attempts-per-recovery of 0.46, and
    a negative variance that raised inside the request handler.
    """
    stats = ArmStats(failed_cycles=100, recovered_cycles=65)
    assert 0.0 <= stats.recovery_rate <= 1.0
    assert stats.recovery_rate == 0.65


def test_the_interval_is_suppressed_rather_than_raised() -> None:
    """A proportion outside [0, 1] is a bug upstream; say so by being absent."""
    assert _diff_ci(1.4, 100, 0.2, 100) is None
    assert _diff_ci(0.6, 10, 0.2, 100) is None, "too thin to approximate"
    ci = _diff_ci(0.65, 500, 0.15, 200)
    assert ci is not None and ci[0] > 0, "a real lift must exclude zero"


def test_recovery_is_never_reported_without_survival() -> None:
    """§6, and N4 of the demo spec. One card, both numbers, always."""
    split = {
        Arm.TREATMENT: ArmStats(
            mandates=500,
            failed_cycles=300,
            recovered_cycles=200,
            recovered_paise=2_000_000,
            attempts=400,
            alive_mandates=480,
        ),
        Arm.HOLDOUT: ArmStats(
            mandates=100,
            failed_cycles=60,
            recovered_cycles=12,
            recovered_paise=120_000,
            attempts=180,
            alive_mandates=90,
        ),
        Arm.EXCLUDED: ArmStats(),
    }
    pair = matched_pair(split)
    for key in (
        "incremental_recovery_paise",
        "incremental_survival_pts",
        "holdout_n",
        "treatment_n",
    ):
        assert key in pair, f"the matched pair lost {key}"
    assert pair["incremental_recovery_paise"] > 0
    assert pair["incremental_survival_pts"] > 0


# ── the endpoints ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_endpoint_requires_a_token(client: AsyncClient) -> None:
    for path in (
        "/v1/tenants",
        "/v1/portfolio/summary",
        "/v1/ledger",
        "/v1/events/stream",
        "/v1/cycles/x/timeline",
        "/v1/decisions/x/replay",
    ):
        assert (await client.get(path)).status_code == 401, path


@pytest.mark.asyncio
async def test_a_caller_cannot_name_another_tenant(client: AsyncClient) -> None:
    """§18: the tenant comes from the verified token.

    `tenant=all` is the one exception and widens what a verified caller may
    *see*, never who they are — naming a specific other tenant must not work.
    """
    r = await client.get("/v1/portfolio/summary?tenant=fitfirst", headers=_auth())
    assert r.status_code == 200
    # The parameter was ignored, so the answer is still the token's tenant.
    assert r.json()["matched_pair"]["treatment_n"] == 0


@pytest.mark.asyncio
async def test_the_demo_api_is_read_only() -> None:
    """No write endpoint, by construction — every side effect belongs to the
    scheduled-action path so it is gated at fire time and ledgered."""
    mutating = {
        route.path  # type: ignore[attr-defined]
        for route in app.routes
        if getattr(route, "path", "").startswith("/v1/")
        and {"POST", "PUT", "PATCH", "DELETE"} & set(getattr(route, "methods", set()) or set())
        and "webhooks" not in getattr(route, "path", "")
    }
    assert mutating == set(), f"a demo endpoint can mutate state: {mutating}"
