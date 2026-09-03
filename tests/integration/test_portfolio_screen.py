"""The portfolio screen (Demo spec §R4.1, N4, N6).

The layout assertions are the ones that matter. Two of this screen's rules are
about *structure* rather than content — recovery may never appear without
survival, and the holdout may never be coloured as a failure — and both are the
kind of thing that survives a redesign only if something checks.
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.console.auth import SECRET_ENV, issue
from prayas.console.format import ist, lakh, pct, rupees

SECRET = "portfolio_screen_test_secret_000"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://screen") as c:
        yield c


def _auth(role: str = "platform_pm", tenant: str = "t_screen") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue(tenant, role, secret=SECRET.encode())}"}


# ── N6: Indian grouping, and money that never becomes a float ───────────────


def test_rupees_uses_indian_grouping() -> None:
    """₹24,86,400 — last three digits, then pairs.

    A payments audience in India reads `₹2,486,400` as a mistake, and it is
    one: the groups carry lakh and crore, which is how the amount is spoken.
    """
    assert rupees(24_86_400_00) == "₹24,86,400"
    assert rupees(1_84_23_000_0) == "₹18,42,300"
    assert rupees(2_49_900) == "₹2,499"
    assert rupees(49900) == "₹499"
    assert rupees(0) == "₹0"


def test_rupees_keeps_paise_when_asked_and_never_returns_a_float() -> None:
    assert rupees(249999, decimals=True) == "₹2,499.99"
    assert isinstance(rupees(1), str)
    assert rupees(None) == "—"
    assert rupees("nonsense") == "—"


def test_lakh_and_crore_shorthand() -> None:
    assert lakh(1_84_23_000_0) == "₹18.4L"
    assert lakh(15_00_00_000_00) == "₹15.0Cr"


def test_percentages_carry_their_sign_when_a_direction_matters() -> None:
    assert pct(4.1, signed=True) == "+4.1%"
    assert pct(-0.03, signed=True) == "-0.0%"
    assert pct(31.0) == "31.0%"


def test_times_render_in_ist() -> None:
    """Regulatory windows are IST-defined; a UTC screen asks its audience to do
    arithmetic before they can tell whether a debit was lawful."""
    assert ist("2026-09-03T03:30:00+00:00", "%H:%M") == "09:00"


# ── N4: the matched pair is structural ──────────────────────────────────────


@pytest.mark.asyncio
async def test_recovery_never_renders_without_survival(client: AsyncClient) -> None:
    """One card, one border. Not a convention — a container.

    §6: reporting recovery alone is how a dunning system destroys value while
    appearing to create it. If a redesign ever splits these, this fails.
    """
    page = (await client.get("/console/portfolio", headers=_auth())).text
    pair = re.search(r'<section class="pair">(.*?)</section>', page, re.S)
    assert pair is not None, "the matched pair card is gone"
    body = pair.group(1)
    assert "Incremental recovery" in body
    assert "Incremental survival" in body, "survival left the recovery card"


@pytest.mark.asyncio
async def test_the_holdout_is_never_coloured_as_a_failure(client: AsyncClient) -> None:
    """The control arm is deliberately untouched, never failed.

    A red or amber badge would undo the argument the holdout exists to make.
    """
    page = (await client.get("/console/portfolio", headers=_auth())).text
    holdout = re.search(r'<span class="chip ([a-z]+)">holdout', page)
    assert holdout is not None, "the holdout badge is gone"
    assert holdout.group(1) == "held", "the holdout must use the neutral token"


@pytest.mark.asyncio
async def test_unmeasurable_metrics_say_so_rather_than_showing_a_number(
    client: AsyncClient,
) -> None:
    """§R2 shows shapes, not values. A plausible constant in a gap would make
    the whole surface worthless, and this audience checks."""
    page = (await client.get("/console/portfolio", headers=_auth())).text
    assert "not measured" in page
    assert "needs a counterfactual" in page


@pytest.mark.asyncio
async def test_guardrails_are_always_rendered(client: AsyncClient) -> None:
    """Always visible, including when unflattering — that is what makes them
    worth anything when they are flattering."""
    page = (await client.get("/console/portfolio", headers=_auth())).text
    assert "Compliance violations" in page
    assert "Net value" in page


@pytest.mark.asyncio
async def test_the_health_strip_names_all_four_services(client: AsyncClient) -> None:
    """A missing service fails silently — the stack stays green and stops doing
    work — so the strip is the first thing to check before a run."""
    page = (await client.get("/console/portfolio", headers=_auth())).text
    strip = re.search(r'<div class="health">(.*?)</div>\s*</div>', page, re.S)
    assert strip is not None
    for service in ("api", "projector", "planner", "executor"):
        assert service in strip.group(1), f"the health strip lost {service}"


@pytest.mark.asyncio
async def test_the_screen_needs_a_token(client: AsyncClient) -> None:
    assert (await client.get("/console/portfolio")).status_code == 401


@pytest.mark.asyncio
async def test_assets_are_vendored_not_fetched(client: AsyncClient) -> None:
    """N3: the demo runs on localhost with the network off.

    Any external origin in the markup is a dependency on a request leaving the
    machine, which is the one thing that cannot be allowed to fail in a room.
    """
    page = (await client.get("/console/portfolio", headers=_auth())).text
    assert "//cdn" not in page
    assert "https://" not in page.split("<body")[0], "the head reaches off-machine"
