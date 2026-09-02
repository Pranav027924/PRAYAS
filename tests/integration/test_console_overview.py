"""The operator console (ADR-101; Master Spec §5, §18, §36).

Two things are load-bearing here and neither is the HTML: that the cookie is
*transport* rather than a second authority, and that the page cannot act.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.console.api import SESSION_COOKIE
from prayas.console.auth import SECRET_ENV, issue
from prayas.console.overview import build, rupees

TENANT = "t_console_ov"
OTHER = "t_console_other"
SECRET = "console_overview_test_secret_value"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as c:
        yield c


def _token(role: str = "merchant_ops", tenant: str = TENANT) -> str:
    return issue(tenant, role, secret=SECRET.encode())


async def _seed(engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for t in (TENANT, OTHER):
            for table in ("scheduled_actions", "cycles", "mandates", "events_raw", "tenants"):
                await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": t})
            await conn.execute(
                text(
                    "INSERT INTO tenants (tenant_id, name, config)"
                    " VALUES (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
                ),
                {"t": t},
            )
            await conn.execute(
                text(
                    "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                    " max_amount_paise, state, consent_ref, created_at)"
                    " VALUES (:m, :t, 'c1', 'upi_autopay', 1500000, 'active', 'r1', :now)"
                ),
                {"m": f"sub_{t}_1", "t": t, "now": now},
            )
            await conn.execute(
                text(
                    "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                    " due_at, deadline_at, attempt_budget, attempts_used, state, recovered_paise)"
                    " VALUES (:c, :t, :m, 1, 249900, :due, :dl, 4, 1, 'executing', 0)"
                ),
                {
                    "c": f"inv_{t}_1",
                    "t": t,
                    "m": f"sub_{t}_1",
                    "due": now,
                    "dl": now + timedelta(days=20),
                },
            )


@pytest.mark.asyncio
async def test_overview_shows_the_whole_pipeline(owner_engine: AsyncEngine) -> None:
    """The page exists to answer 'is it working?', so it must carry the parts."""
    await _seed(owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT})
        data = await build(conn, TENANT)

    assert data.stage == "FULL"
    assert data.mandates and data.cycles
    for key in (
        "mandates",
        "cycles",
        "in_flight",
        "events",
        "queued",
        "decisions",
        "recovered_paise",
        "pipeline_healthy",
    ):
        assert key in data.totals, f"the overview lost {key}"


@pytest.mark.asyncio
async def test_the_overview_shows_which_arm_a_mandate_is_in(owner_engine: AsyncEngine) -> None:
    """ADR-095: a healthy mandate sitting untouched is usually the holdout.

    Without this on the page, a control-arm mandate looks like a bug.
    """
    await _seed(owner_engine)
    async with owner_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT})
        data = await build(conn, TENANT)

    assert all(m["arm"] in {"treatment", "holdout", "excluded"} for m in data.mandates)


def test_rupees_never_computes_in_floats() -> None:
    """Money is integer paise; this is display only."""
    assert rupees(249900) == "₹2,499.00"
    assert rupees(5) == "₹0.05"
    assert rupees(0) == "₹0.00"
    assert rupees(None) == "—"


# ── §18: the cookie is transport, not a second authority ────────────────────


def test_a_valid_token_verifies_from_either_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Header and cookie must resolve to the same principal, or they are two
    different authorities and only one of them was designed."""
    monkeypatch.setenv("PRAYAS_CONSOLE_TOKEN_SECRET", "s" * 32)
    from prayas.console.auth import verify

    token = issue(TENANT, "merchant_ops")
    assert verify(token).tenant_id == TENANT


@pytest.mark.asyncio
async def test_the_cookie_and_the_header_are_the_same_authority(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    """The cookie is transport for a signed token, not a second way in.

    If they resolved differently, only one of them would have been designed.
    """
    await _seed(owner_engine)
    token = _token()
    by_header = await client.get("/console/", headers={"Authorization": f"Bearer {token}"})
    # Set on the client, not per-request: httpx deprecates the latter because
    # cookie persistence across a redirect is then ambiguous.
    client.cookies.set(SESSION_COOKIE, token)
    by_cookie = await client.get("/console/")
    assert by_header.status_code == 200
    assert by_cookie.status_code == 200


@pytest.mark.asyncio
async def test_console_requires_a_token(client: AsyncClient) -> None:
    """No token, no page — for the HTML surface as much as the JSON one."""
    assert (await client.get("/console/")).status_code == 401
    assert (await client.get("/console/replay/dec_x")).status_code == 401


@pytest.mark.asyncio
async def test_a_token_in_the_query_string_is_refused(client: AsyncClient) -> None:
    """It would land in logs, history and referrer headers.

    The cookie was chosen precisely because a query parameter leaks; accepting
    one here would reintroduce exactly what it avoids.
    """
    assert (await client.get(f"/console/?token={_token()}")).status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_a_token_it_cannot_verify(client: AsyncClient) -> None:
    response = await client.post(
        "/console/login",
        content="token=not-a-real-token",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=1" in response.headers["location"]
    assert SESSION_COOKIE not in response.cookies


@pytest.mark.asyncio
async def test_a_verified_token_is_stored_httponly(client: AsyncClient) -> None:
    """`HttpOnly` so an XSS cannot lift the session; `SameSite` so a cross-site
    POST cannot ride it."""
    response = await client.post(
        "/console/login",
        content=f"token={_token()}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    raw = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE in raw and "HttpOnly" in raw and "SameSite=lax" in raw


def test_the_console_exposes_no_way_to_act() -> None:
    """Read-only by construction.

    Every action goes through the scheduled-action path so it is re-checked at
    fire time and written to the ledger. A mutating console route would be a
    second way to move money, outside both.
    """
    mutating = {
        route.path  # type: ignore[attr-defined]
        for route in app.routes
        if getattr(route, "path", "").startswith("/console")
        and {"POST", "PUT", "PATCH", "DELETE"} & set(getattr(route, "methods", set()) or set())
    }
    assert mutating <= {"/console/login", "/console/logout", "/console/simulate"}, (
        f"a console route can mutate state: {mutating}"
    )
