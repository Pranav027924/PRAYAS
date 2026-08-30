"""`console-api` — three screens over HTTP (Master Spec §36, §5; ADR-087).

§36 lists `console-api` as "replay, policy simulator, explanations | stateless
| request rate". Stateless is the operative word: every request carries its own
principal, and nothing here holds a session.

**The tenant comes from the token, never from the request.** §18 is explicit —
`app.tenant_id` is set "from a verified token. NEVER from a request parameter,
query string, or header the client controls." So no endpoint here takes a
`tenant_id`, and there is nothing for a caller to tamper with: reading someone
else's data would require forging a signature.

**Authorisation happens before the query, not after.** A screen that loaded the
data and then decided whether to show it has already read it, which is the
wrong order on a system where reading is the thing being controlled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from prayas.console import screens
from prayas.console.auth import (
    BATCH_RESULT,
    DECISION_REPLAY,
    POLICY_SIMULATOR,
    AuthError,
    Principal,
    authorise,
    verify,
)
from prayas.console.simulator import (
    MAX_SIMULATED_CYCLES,
    PolicyKnobs,
    SimulatorError,
    simulate_default_population,
)
from prayas.db.tenancy import tenant_transaction
from prayas.measure.replay import ReplayError

router = APIRouter(prefix="/console", tags=["console"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Establish who is asking, from the bearer token alone.

    A 401 carries no detail about *which* check failed. Distinguishing "no such
    tenant" from "bad signature" tells someone probing which half to keep
    working on.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        return verify(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="authentication failed") from exc


def _engine(request: Request) -> Any:
    """The app engine, from application state.

    Taken from the request rather than a module global so a test can mount this
    router against its own engine, and so the lifespan owns the connection pool
    exactly as it does for every other route.
    """
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return engine


def _guard(who: Principal, screen: str) -> None:
    """Authorise before touching data. See the module docstring on ordering."""
    try:
        authorise(who, screen)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/replay/{decision_id}")
async def replay_json(
    request: Request, decision_id: str, who: Annotated[Principal, Depends(principal)]
) -> dict[str, Any]:
    """§32's chain for one decision, denials included.

    The tenant is bound from `who`, so a decision belonging to another tenant
    is invisible under RLS rather than refused after the fact — the boundary is
    the database's, not this handler's.
    """
    _guard(who, DECISION_REPLAY)

    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        try:
            return await screens.decision_replay(conn, decision_id)
        except ReplayError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/replay/{decision_id}/view", response_class=HTMLResponse)
async def replay_view(
    request: Request, decision_id: str, who: Annotated[Principal, Depends(principal)]
) -> HTMLResponse:
    """**Screen 2 — the phase artifact.** Click one recovered rupee, see the
    entire causal chain."""
    _guard(who, DECISION_REPLAY)

    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        try:
            payload = await screens.decision_replay(conn, decision_id)
        except ReplayError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return templates.TemplateResponse(
        request=request, name="replay.html", context={"d": payload, "who": who}
    )


@router.post("/simulate")
async def simulate_policy(
    body: dict[str, Any], who: Annotated[Principal, Depends(principal)]
) -> dict[str, Any]:
    """**Screen 3.** Recompute the policy under a different set of knobs.

    A POST because it carries a body, not because it changes anything: nothing
    here writes, schedules or fires. `prayas.console.simulator` imports nothing
    that could (ADR-088), and a test asserts that absence.
    """
    _guard(who, POLICY_SIMULATOR)

    try:
        knobs = PolicyKnobs(
            attempt_cost_multiplier=float(body.get("attempt_cost_multiplier", 1.0)),
            lambda_annoyance=float(body.get("lambda_annoyance", 1.0)),
            mu_revocation=float(body.get("mu_revocation", 1.0)),
        )
        cycles = int(body.get("cycles", 200))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid knobs: {exc}") from exc

    if not 1 <= cycles <= MAX_SIMULATED_CYCLES:
        raise HTTPException(
            status_code=422,
            detail=f"cycles must be between 1 and {MAX_SIMULATED_CYCLES}",
        )

    try:
        result = simulate_default_population(cycles, knobs)
    except SimulatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "cycles": result.cycles,
        "knobs": {
            "attempt_cost_multiplier": knobs.attempt_cost_multiplier,
            "lambda_annoyance": knobs.lambda_annoyance,
            "mu_revocation": knobs.mu_revocation,
        },
        "acted": result.acted,
        "stop_rate": result.stop_rate,
        "mean_slot": result.mean_slot,
        "distinct_slots": result.distinct_slots,
        "elapsed_seconds": result.elapsed_seconds,
    }


@router.get("/screens")
async def available_screens(who: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
    """What this principal may open.

    Exists so a client can render a navigation that matches its permissions
    rather than offering links that 403 — a menu of things you cannot do is a
    worse experience than a shorter menu.
    """
    return {
        "tenant_id": who.tenant_id,
        "role": who.role,
        "screens": sorted(
            s for s in (BATCH_RESULT, DECISION_REPLAY, POLICY_SIMULATOR) if who.may_open(s)
        ),
    }
