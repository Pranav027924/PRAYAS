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
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from prayas.console import overview, screens
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


#: Name of the cookie carrying the console token.
SESSION_COOKIE = "prayas_console"


async def principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Establish who is asking, from a signed token.

    Accepted from the `Authorization` header or from an `HttpOnly` cookie. The
    cookie exists so a person can open a page in a browser, which cannot attach
    a bearer header to a plain navigation — **it is transport, not a second
    authority.** The same signature is verified either way, so §18 still holds:
    the tenant comes from a verified token and never from anything the client
    can set. A token in a query string was rejected for the opposite reason —
    it lands in logs, history and referrers.

    A 401 carries no detail about *which* check failed. Distinguishing "no such
    tenant" from "bad signature" tells someone probing which half to keep
    working on.
    """
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    else:
        token = request.cookies.get(SESSION_COOKIE)

    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        return verify(token)
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


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    """Paste a token to open the console in a browser.

    Deliberately not a username and password. This system has no user store —
    tokens are minted by whatever issues credentials (`console.auth.issue`) —
    and inventing an account system here would put a second, weaker path to the
    same data beside the signed one.
    """
    return templates.TemplateResponse(request=request, name="login.html", context={})


@router.post("/login")
async def login(request: Request) -> RedirectResponse:
    """Verify a pasted token and keep it in an `HttpOnly` cookie."""
    # Parsed from the raw body rather than via `request.form()`, which pulls in
    # `python-multipart`. The form is url-encoded and the standard library
    # already reads that, so a login page is not a reason to add a dependency
    # to the money path's image.
    body = (await request.body()).decode("utf-8", errors="replace")
    token = parse_qs(body).get("token", [""])[0].strip()
    try:
        verify(token)
    except AuthError:
        return RedirectResponse(url="/console/login?error=1", status_code=303)

    response = RedirectResponse(url="/console/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,  # unreadable from JavaScript, so an XSS cannot lift it
        samesite="lax",  # not sent on cross-site POSTs
        secure=request.url.scheme == "https",
        max_age=8 * 3600,
    )
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/console/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/", response_class=HTMLResponse)
async def overview_page(
    request: Request, who: Annotated[Principal, Depends(principal)]
) -> HTMLResponse:
    """**The operator's view.** The pipeline end to end for one tenant.

    Read-only. Every mutation in this system goes through the scheduled-action
    path so it is gated at fire time and recorded in the ledger; a button here
    would bypass both.
    """
    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        data = await overview.build(conn, who.tenant_id)

    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "o": data,
            "who": who,
            "rupees": overview.rupees,
            "stages": overview.stage_order(),
        },
    )


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
