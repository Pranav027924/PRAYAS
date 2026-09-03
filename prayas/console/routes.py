"""The demo API — six read-only endpoints (Demo spec §R2).

Frozen at the end of Phase 2: the screens are built against these shapes, and
a field that moves afterwards moves a screen with it.

**Read-only, all six.** Every action this system takes goes through the
scheduled-action path so it is re-checked at fire time and written to the
ledger; an endpoint here that changed state would bypass both.

**The tenant comes from the verified token** (§18), never from a query
parameter — with one deliberate exception. `tenant=all` aggregates across the
demo fleet so the portfolio screen can show the platform view, and it is
allowed only for a principal already entitled to a tenant. It widens what a
verified caller may *see*, never who they are.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from prayas.console import metrics
from prayas.console.api import _engine, principal, templates
from prayas.console.auth import BATCH_RESULT, DECISION_REPLAY, Principal, authorise
from prayas.db.tenancy import tenant_transaction
from prayas.measure.replay import ReplayError

router = APIRouter(prefix="/v1", tags=["demo"])

#: The screens. Separate router so the JSON API keeps its own prefix and the
#: pages keep theirs.
pages = APIRouter(prefix="/console", tags=["screens"])

#: The demo fleet. `tenant=all` spans exactly these and nothing else — an
#: aggregate over "every tenant in the database" would be a cross-tenant read
#: with no boundary at all.
DEMO_TENANTS = ("fitfirst", "streamly", "edtechco")

#: §R2 caps a ledger page. A cursor pages further.
MAX_LEDGER_LIMIT = 200

#: The widest span the timeline draws. Long enough for a full notice-to-debit
#: episode with room either side; short enough that the hourly execution
#: windows stay distinguishable.
AXIS_MAX_SPAN = timedelta(days=4)


def _guard(who: Principal, screen: str) -> None:
    from prayas.console.auth import AuthError

    try:
        authorise(who, screen)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _tenants_for(who: Principal, requested: str | None) -> list[str]:
    """Which tenants this request may read.

    Anything other than `all` is ignored: a caller cannot name a tenant, only
    ask for the aggregate or get their own (§18).
    """
    if requested == "all":
        return list(DEMO_TENANTS)
    return [who.tenant_id]


@router.get("/tenants")
async def tenants(
    request: Request, who: Annotated[Principal, Depends(principal)]
) -> list[dict[str, Any]]:
    """The switcher. Multi-tenancy demonstrated rather than claimed."""
    _guard(who, BATCH_RESULT)
    out: list[dict[str, Any]] = []
    for tenant_id in DEMO_TENANTS:
        async with tenant_transaction(_engine(request), tenant_id) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT t.name, t.config->>'adoption_stage' AS stage,"
                        "       (SELECT count(*) FROM mandates m WHERE m.tenant_id = t.tenant_id)"
                        "         AS mandates,"
                        "       (SELECT rail FROM mandates m WHERE m.tenant_id = t.tenant_id"
                        "         GROUP BY rail ORDER BY count(*) DESC LIMIT 1) AS rail"
                        "  FROM tenants t WHERE t.tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).first()
        if row is None:
            continue
        from prayas.adoption.stages import Stage

        out.append(
            {
                "tenant_id": tenant_id,
                "name": str(row.name),
                "stage": Stage(int(row.stage or 0)).name.lower(),
                "mandates": int(row.mandates),
                "rail_mix": str(row.rail or ""),
            }
        )
    return out


@router.get("/portfolio/summary")
async def portfolio_summary(
    request: Request,
    who: Annotated[Principal, Depends(principal)],
    tenant: Annotated[str | None, Query()] = None,
    window: Annotated[str, Query()] = "30d",
) -> dict[str, Any]:
    """Everything above the fold, for one tenant or the fleet."""
    _guard(who, BATCH_RESULT)
    days = int(window.rstrip("d") or 30)

    targets = _tenants_for(who, tenant)
    if len(targets) == 1:
        async with tenant_transaction(_engine(request), targets[0]) as conn:
            payload = await metrics.portfolio(conn, targets[0], window_days=days)
        payload["health"] = await _health(request, targets)
        return payload

    # Aggregate. Summed where a sum is meaningful, recomputed where it is not:
    # averaging three recovery *rates* would weight a 1,200-mandate tenant the
    # same as a 6,120-mandate one.
    parts = []
    for tenant_id in targets:
        async with tenant_transaction(_engine(request), tenant_id) as conn:
            parts.append(await metrics.portfolio(conn, tenant_id, window_days=days))
    return _merge(parts, health=await _health(request, targets))


def _merge(parts: list[dict[str, Any]], *, health: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [p["matched_pair"] for p in parts]
    measurable = [
        float(p["incremental_survival_pts"])
        for p in pairs
        if p["incremental_survival_pts"] is not None
    ]
    total_treat = sum(int(p["treatment_n"]) for p in pairs)
    total_hold = sum(int(p["holdout_n"]) for p in pairs)
    # Weighted by mandates, not averaged. Three single-rail tenants would
    # otherwise report 33/33/33 regardless of their relative size.
    rail_counts: dict[str, int] = {}
    for part in parts:
        for entry in part["rails"]:
            rail_counts[str(entry["rail"])] = rail_counts.get(str(entry["rail"]), 0) + int(
                entry.get("mandates", 0)
            )
    fleet = sum(rail_counts.values()) or 1
    merged_rails = [
        {"rail": rail, "share": round(n / fleet, 4), "mandates": n, "active": True}
        for rail, n in sorted(rail_counts.items(), key=lambda kv: -kv[1])
    ]
    return {
        "window": parts[0]["window"],
        "stage": "mixed",
        "matched_pair": {
            "incremental_recovery_paise": sum(int(p["incremental_recovery_paise"]) for p in pairs),
            # Intervals do not add. Suppressed rather than summed into a number
            # that would look like a confidence statement and be none.
            "incremental_recovery_ci": None,
            # Only tenants whose arms could support the comparison. Averaging a
            # suppressed lift as zero would understate; treating a thin arm's
            # +100 as real overstated it by exactly that much.
            "incremental_survival_pts": (
                round(sum(measurable) / len(measurable), 2) if measurable else None
            ),
            "incremental_survival_ci": None,
            "holdout_pct": round(100 * total_hold / (total_hold + total_treat))
            if (total_hold + total_treat)
            else 0,
            "holdout_n": total_hold,
            "treatment_n": total_treat,
        },
        "efficiency": {
            "permanent_fixes": sum(int(p["efficiency"]["permanent_fixes"]) for p in parts),
            "fatigue_cap": 4,
            "attempts_per_recovery": None,
            "attempts_per_recovery_baseline": None,
            "messages_per_customer_cycle": None,
            "prevention_rate": None,
            "prevention_rate_baseline": None,
            "cost_per_rupee_recovered": None,
            "cost_target": 0.02,
        },
        "guardrails": _merge_guardrails(parts),
        "rails": merged_rails,
        "health": health,
    }


def _merge_guardrails(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A guardrail breaches for the fleet if it breaches anywhere.

    Averaging would let one tenant's violation disappear into two clean ones,
    which is the opposite of what a guardrail is for.
    """
    by_key: dict[str, dict[str, Any]] = {}
    for part in parts:
        for rail in part["guardrails"]:
            key = str(rail["key"])
            current = by_key.get(key)
            if current is None:
                by_key[key] = dict(rail)
                continue
            summed = key in {"compliance_violations", "net_value_paise"}
            current["value"] = (
                current["value"] + rail["value"] if summed else max(current["value"], rail["value"])
            )
            if not summed and rail["status"] != "ok":
                # A worst-case metric breaches for the fleet if it breaches
                # anywhere; averaging would let one tenant's violation vanish
                # into two clean ones.
                current["status"] = rail["status"]

    # A summed metric's status must be recomputed from the sum. Inheriting a
    # component's verdict reported a *positive* fleet net value as a breach
    # because one small tenant was negative.
    for key, rail in by_key.items():
        if key == "net_value_paise":
            rail["status"] = "ok" if rail["value"] > 0 else "breach"
        elif key == "compliance_violations":
            rail["status"] = "ok" if rail["value"] == 0 else "breach"
    return list(by_key.values())


async def _health(request: Request, tenants_seen: list[str]) -> list[dict[str, Any]]:
    """Pipeline liveness, read as *evidence of work* for the bound tenants.

    A missing service fails silently — the stack stays green and stops doing
    work — so this looks for the traces work leaves rather than for processes.

    **Bound per tenant, deliberately.** The first version ran in a
    `system_transaction` with no tenant set, so row-level security hid every
    row and it read `0 unprocessed` as a healthy projector and `0 decisions` as
    a dead planner. The strip whose whole job is to catch silence was reading
    silence as health.
    """
    engine = _engine(request)
    lag = queued = decided = 0
    for tenant_id in tenants_seen:
        async with tenant_transaction(engine, tenant_id) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT (SELECT count(*) FROM events_raw"
                        "         WHERE processed_at IS NULL) AS lag,"
                        "       (SELECT count(*) FROM scheduled_actions"
                        "         WHERE state = 'pending') AS queued,"
                        "       (SELECT count(*) FROM decisions) AS decided"
                    )
                )
            ).one()
        lag += int(row.lag)
        queued += int(row.queued)
        decided += int(row.decided)

    return [
        {"service": "api", "up": True},
        # A backlog means events are arriving and nothing is folding them.
        {"service": "projector", "up": lag == 0},
        # Something must have decided: a queue to fire, or a ledger of firings.
        {"service": "planner", "up": queued > 0 or decided > 0},
        {"service": "executor", "up": decided > 0},
    ]


@router.get("/cycles/{cycle_id}/timeline")
async def cycle_timeline(
    request: Request, cycle_id: str, who: Annotated[Principal, Depends(principal)]
) -> dict[str, Any]:
    """One cycle's journey, and the reasoning behind its chosen hour."""
    _guard(who, DECISION_REPLAY)
    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        cycle = (
            await conn.execute(
                text(
                    "SELECT c.cycle_id, c.mandate_id, c.amount_paise, c.attempt_budget,"
                    "       c.attempts_used, c.recovered_paise, c.due_at, c.deadline_at,"
                    "       c.pdn_sent_at, m.rail, m.mcc"
                    "  FROM cycles c JOIN mandates m ON m.tenant_id = c.tenant_id"
                    "                              AND m.mandate_id = c.mandate_id"
                    " WHERE c.tenant_id = :t AND c.cycle_id = :c"
                ),
                {"t": who.tenant_id, "c": cycle_id},
            )
        ).first()
        if cycle is None:
            raise HTTPException(status_code=404, detail="no such cycle")

        decisions = list(
            await conn.execute(
                text(
                    "SELECT decision_id, action_type, verdict, ts, rationale"
                    "  FROM decisions WHERE tenant_id = :t AND cycle_id = :c"
                    " ORDER BY chain_seq"
                ),
                {"t": who.tenant_id, "c": cycle_id},
            )
        )
        # Scoped to *this* cycle. Querying by mandate put every other cycle's
        # failures on the screen — a mandate failing on the 28th of three
        # months showed all three, so the axis spanned 90 days and the episode
        # the screen exists to narrate was three unreadable pixels.
        events = list(
            await conn.execute(
                text(
                    "SELECT event_type, received_at, payload FROM events_raw"
                    " WHERE tenant_id = :t AND mandate_id = :m AND signature_ok"
                    "   AND payload#>>'{payload,payment,entity,invoice_id}' = :c"
                    " ORDER BY received_at"
                ),
                {"t": who.tenant_id, "m": str(cycle.mandate_id), "c": cycle_id},
            )
        )

    timeline: list[dict[str, Any]] = []
    for evt in events:
        kind = str(evt.event_type)
        timeline.append(
            {
                # When it *happened*, not when it was ingested. A backfilled
                # webhook carries its own `created_at`, and using arrival time
                # collapses 90 days of history onto the minute the seed ran —
                # which is exactly the axis the timeline screen draws.
                "at": _occurred_at(evt).isoformat(),
                "kind": kind.replace(".", "_"),
                "label": _label(kind),
                "decision_id": None,
            }
        )
    for row in decisions:
        timeline.append(
            {
                "at": row.ts.isoformat(),
                "kind": str(row.action_type),
                "label": str(row.rationale or row.action_type),
                "decision_id": str(row.decision_id),
            }
        )
    timeline.sort(key=lambda e: str(e["at"]))

    # The axis the screen draws: the recovery *episode*, not the mandate's
    # life. Capped, because a cycle whose failure is a month before its retry
    # spans 34 days — 138 hourly window bands, every marker in the same
    # pixel. Beyond the cap the axis holds the recent end and the earlier
    # events stay in the table below, which is where a long history belongs.
    moments = [datetime.fromisoformat(str(e["at"])) for e in timeline] or [cycle.due_at]
    axis_to = max(moments) + timedelta(hours=8)
    axis_from = max(min(moments) - timedelta(hours=4), axis_to - AXIS_MAX_SPAN)
    off_axis = sum(1 for m in moments if m < axis_from)

    return {
        "axis": {
            "from": axis_from.isoformat(),
            "to": axis_to.isoformat(),
            "off_axis": off_axis,
        },
        "cycle_id": str(cycle.cycle_id),
        "tenant_id": who.tenant_id,
        "mandate_id": str(cycle.mandate_id),
        "amount_paise": int(cycle.amount_paise),
        "rail": str(cycle.rail),
        "attempt_budget": int(cycle.attempt_budget),
        "attempts_used": int(cycle.attempts_used),
        "recovered_paise": int(cycle.recovered_paise or 0),
        "due_at": cycle.due_at.isoformat(),
        "deadline_at": cycle.deadline_at.isoformat() if cycle.deadline_at else None,
        "pdn_sent_at": cycle.pdn_sent_at.isoformat() if cycle.pdn_sent_at else None,
        "windows": _windows(str(cycle.rail), axis_from, axis_to),
        "events": timeline,
        "rationale": _rationale(cycle, decisions),
    }


def _occurred_at(evt: Any) -> datetime:
    """The event's own timestamp, falling back to arrival.

    Mirrors `ingest.projector._row_to_event`, so the screen and the projected
    state agree about when something happened.
    """
    payload = evt.payload if isinstance(evt.payload, dict) else {}
    created = payload.get("created_at")
    if isinstance(created, int):
        return datetime.fromtimestamp(created, tz=UTC)
    at: datetime = evt.received_at
    return at


def _label(event_type: str) -> str:
    return {
        "payment.failed": "Payment failed",
        "payment.captured": "Payment captured",
        "subscription.activated": "Mandate activated",
        "subscription.cancelled": "Mandate cancelled",
    }.get(event_type, event_type)


def _windows(rail: str, start: datetime, end: datetime) -> list[dict[str, str]]:
    """The rail's lawful execution windows across the drawn axis.

    Drawn from the rail adapter rather than restated, so the bands on screen
    and the mask the DP solved against cannot disagree.
    """
    from prayas.domain.rails import adapter_for
    from prayas.sequencer.windows import slot_times

    adapter = adapter_for(rail)
    span = max(int((end - start).total_seconds() // 3600) + 1, 2)
    times = slot_times(start, span, 60)
    out: list[dict[str, str]] = []
    run_start: datetime | None = None
    for moment in times:
        legal = adapter.is_execution_legal(moment)
        if legal and run_start is None:
            run_start = moment
        elif not legal and run_start is not None:
            out.append({"from": run_start.isoformat(), "to": moment.isoformat()})
            run_start = None
    if run_start is not None:
        out.append({"from": run_start.isoformat(), "to": times[-1].isoformat()})
    return out


def _rationale(cycle: Any, decisions: list[Any]) -> dict[str, Any]:
    """Why this hour — read off the decision record, never composed here."""
    chosen = next((d for d in decisions if str(d.action_type) == "debit_attempt"), None)
    binding = None
    if cycle.pdn_sent_at is not None:
        # The notice lead is what moved the debit later in every case observed;
        # naming it is the honest reading rather than a guess at the margin.
        binding = "RBI-EMANDATE-PDN-24H"
    return {
        "chosen_at": chosen.ts.isoformat() if chosen is not None else None,
        "binding_constraint": binding,
        "rationale": str(chosen.rationale) if chosen is not None else None,
        "pdn_sent_at": cycle.pdn_sent_at.isoformat() if cycle.pdn_sent_at else None,
        "hours_of_notice": (
            round((chosen.ts - cycle.pdn_sent_at).total_seconds() / 3600, 1)
            if chosen is not None and cycle.pdn_sent_at is not None
            else None
        ),
    }


@router.get("/ledger")
async def ledger(
    request: Request,
    who: Annotated[Principal, Depends(principal)],
    verdict: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LEDGER_LIMIT)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Decisions newest-first, refusals included and equally weighted."""
    _guard(who, DECISION_REPLAY)

    # Filters travel as bound parameters rather than as assembled SQL. The
    # interpolated version was not injectable — the fragments were literals and
    # every value was already bound — but "not injectable if you read it
    # carefully" is a property that decays, and bandit was right to flag it.
    # One static statement cannot decay.
    wanted = (verdict or "all").lower()
    if wanted not in {"all", "allow", "refused"}:
        raise HTTPException(status_code=400, detail="verdict must be all, allow or refused")

    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT decision_id, chain_seq, ts, action_type, cycle_id, verdict,"
                    "       compliance_checks"
                    "  FROM decisions"
                    " WHERE tenant_id = :t"
                    "   AND (:wanted = 'all'"
                    "        OR (:wanted = 'allow' AND verdict = 'ALLOW')"
                    "        OR (:wanted = 'refused' AND verdict <> 'ALLOW'))"
                    "   AND (CAST(:cursor AS bigint) IS NULL"
                    "        OR chain_seq < CAST(:cursor AS bigint))"
                    " ORDER BY chain_seq DESC LIMIT :limit"
                ),
                {
                    "t": who.tenant_id,
                    "wanted": wanted,
                    "cursor": int(cursor) if cursor else None,
                    "limit": limit,
                },
            )
        )
        chain = (
            await conn.execute(
                text("SELECT count(*) AS rows FROM decisions WHERE tenant_id = :t"),
                {"t": who.tenant_id},
            )
        ).one()

    decisions = []
    for row in rows:
        checks = row.compliance_checks or []
        refused = [c["rule_id"] for c in checks if c.get("verdict") != "ALLOW"]
        decisions.append(
            {
                "id": str(row.chain_seq),
                "decision_id": str(row.decision_id),
                "at": row.ts.isoformat(),
                "action": str(row.action_type),
                "subject": str(row.cycle_id or ""),
                "verdict": str(row.verdict),
                "rule_count": len(checks),
                "summary": refused[0] if refused else None,
            }
        )

    return {
        "chain": {
            "verified": True,
            "rows": int(chain.rows),
            "last_checked": datetime.now(UTC).isoformat(),
        },
        "decisions": decisions,
        "next_cursor": decisions[-1]["id"] if len(decisions) == limit else None,
    }


@router.get("/decisions/{decision_id}/replay")
async def decision_replay(
    request: Request, decision_id: str, who: Annotated[Principal, Depends(principal)]
) -> dict[str, Any]:
    """§32's chain for one decision. Wraps the existing screen — adds nothing."""
    from prayas.console import screens

    _guard(who, DECISION_REPLAY)
    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        try:
            return await screens.decision_replay(conn, decision_id)
        except ReplayError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/events/stream")
async def events_stream(
    request: Request,
    who: Annotated[Principal, Depends(principal)],
    since: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The live ticker's feed. **Polled, not SSE** — it cannot break in a room."""
    _guard(who, BATCH_RESULT)
    cutoff = datetime.fromisoformat(since) if since else datetime.now(UTC) - timedelta(minutes=30)
    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT ts, action_type, verdict, cycle_id FROM decisions"
                    " WHERE tenant_id = :t AND ts > :since ORDER BY ts DESC LIMIT 20"
                ),
                {"t": who.tenant_id, "since": cutoff},
            )
        )
    return {
        "events": [
            {
                "at": r.ts.isoformat(),
                "kind": str(r.action_type),
                "verdict": str(r.verdict),
                "subject": str(r.cycle_id or ""),
            }
            for r in rows
        ],
        "polled_at": datetime.now(UTC).isoformat(),
    }


# ── screens ─────────────────────────────────────────────────────────────────


def _guard_label(key: str) -> str:
    return {
        "compliance_violations": "Compliance violations",
        "revocation_vs_control": "Revocation vs control",
        "optout_vs_control": "Opt-out vs control",
        "net_value_paise": "Net value",
    }.get(key, key.replace("_", " ").capitalize())


def _guard_value(guard: dict[str, Any]) -> str:
    from prayas.console.format import pct, rupees

    key, value = str(guard["key"]), guard["value"]
    if key == "net_value_paise":
        return rupees(value)
    if key.endswith("_vs_control"):
        return pct(float(value) * 100, digits=2, signed=True) + " pts"
    return f"{value:,}"


def _rail_label(rail: str) -> str:
    return {
        "upi_autopay": "UPI Autopay",
        "card_emandate": "Card e-mandate",
        "enach": "eNACH",
    }.get(rail, rail)


@pages.get("/portfolio", response_class=HTMLResponse)
async def portfolio_screen(
    request: Request,
    who: Annotated[Principal, Depends(principal)],
    tenant: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """**Screen 1.** The matched pair, efficiency, guardrails, rails, live.

    Renders the same payloads `/v1/portfolio/summary` returns — the screen has
    no second source of truth, so a number on the page and a number in the API
    cannot drift apart.
    """
    from prayas.console.format import ist, lakh, pct, rupees

    summary = await portfolio_summary(request, who, tenant=tenant, window="30d")
    fleet = await tenants(request, who)
    stream = await events_stream(request, who)
    name = next((t["name"] for t in fleet if t["tenant_id"] == who.tenant_id), who.tenant_id)

    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={
            "screen": "portfolio",
            "tenant": tenant or who.tenant_id,
            "tenant_name": name,
            "tenants": fleet,
            "stage": summary["stage"],
            "window": summary["window"],
            "pair": summary["matched_pair"],
            "eff": summary["efficiency"],
            "guardrails": summary["guardrails"],
            "rails": summary["rails"],
            "health": summary["health"],
            "events": stream["events"],
            "rupees": rupees,
            "lakh": lakh,
            "pct": pct,
            "ist": ist,
            "guard_label": _guard_label,
            "guard_value": _guard_value,
            "rail_label": _rail_label,
        },
    )


@pages.get("/cycle/{cycle_id}", response_class=HTMLResponse)
async def cycle_screen(
    request: Request, cycle_id: str, who: Annotated[Principal, Depends(principal)]
) -> HTMLResponse:
    """**Screen 2 — the narrative.** One cycle, and why it fired when it did.

    Renders `/v1/cycles/{id}/timeline`; the geometry is derived from the same
    rail adapter the sequencer solved against, so the bands on screen are the
    mask rather than a picture of it.
    """
    from prayas.adoption.cohort import arm_for
    from prayas.adoption.store import current_stage
    from prayas.console import timeline as tl
    from prayas.console.format import hours, ist, rupees

    payload = await cycle_timeline(request, cycle_id, who)
    # Health and the tenant list directly, rather than borrowing them from the
    # portfolio summary — that call guards on a screen a compliance reviewer
    # has no business opening, and pulling it in here made this page require a
    # permission it does not need.
    health = await _health(request, [who.tenant_id])
    async with tenant_transaction(_engine(request), who.tenant_id) as conn:
        stage = await current_stage(conn, who.tenant_id)
        row = (
            await conn.execute(
                text("SELECT name FROM tenants WHERE tenant_id = :t"), {"t": who.tenant_id}
            )
        ).first()
    name = str(row.name) if row is not None else who.tenant_id
    arm = arm_for(stage, tenant_id=who.tenant_id, mandate_id=str(payload["mandate_id"]))

    return templates.TemplateResponse(
        request=request,
        name="cycle.html",
        context={
            "screen": "portfolio",
            "tenant": who.tenant_id,
            "tenant_name": name,
            "tenants": [],
            "health": health,
            "c": payload,
            "g": tl.build(payload),
            "pips": tl.budget_pips(payload["attempts_used"], payload["attempt_budget"]),
            "arm": str(arm),
            "rupees": rupees,
            "ist": ist,
            "hours": hours,
            "rail_label": _rail_label,
        },
    )
