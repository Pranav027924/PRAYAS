"""Seed a three-tenant fleet by replaying signed webhooks (Demo spec §R3).

**Everything the pipeline produces, the pipeline produces.** Cycles, attempts,
decisions and the ledger are never written here — they arrive because signed
webhooks were POSTed to `/v1/webhooks/razorpay/{tenant}` and the projector,
planner and executor did their work. That is N1, and it is not pedantry: the
hash chain has to verify afterwards, and "we replayed signed webhooks through
the production pipeline" is an answer that survives scrutiny in a way that
"we inserted rows" does not.

What *is* written directly is onboarding state — tenants, their webhook secret
refs, and mandates. A mandate is not pipeline output; the projector requires
the row to exist before it will fold events onto it, exactly as a real
onboarding would have created it.

**Determinism.** Everything derives from `--seed`. Holdout membership comes
from `adoption.cohort.arm_for`, which is the same hash the planner consults —
so the arm shown on screen is the arm the engine actually used, not a
re-derivation that could drift from it.

**Why the recovery rate is not 100%.** §R3 fixes treatment recovery near 62%.
A perfect rate reads as fabricated and invites the wrong kind of scrutiny; the
holdout exists to be compared against, and a comparison against perfection is
not one.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import hmac
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from prayas.adoption.cohort import Arm, arm_for
from prayas.adoption.stages import Stage
from prayas.memory.forget import pseudonymise
from prayas.models.bootstrap import day_presence

IST = timedelta(hours=5, minutes=30)

#: §R3.1. Mandate counts are what a reviewer reads as portfolio scale, so they
#: are the spec's numbers rather than whatever seeds quickly.
FLEET: Final[list[dict[str, Any]]] = [
    {
        "tenant_id": "fitfirst",
        "name": "FitFirst",
        "stage": Stage.FULL,
        "rail": "upi_autopay",
        "mandates": 6120,
        "mcc": "7997",
        "amounts": (49900, 249900),
        "due_days": (1,),
        "fail_rate": 0.11,
    },
    {
        "tenant_id": "streamly",
        "name": "Streamly",
        "stage": Stage.RAMP,
        "rail": "card_emandate",
        "mandates": 4880,
        "mcc": "5815",
        "amounts": (14900, 79900),
        "due_days": (3, 8, 12, 17, 22, 27),
        "fail_rate": 0.08,
    },
    {
        "tenant_id": "edtechco",
        "name": "EdTechCo",
        "stage": Stage.CANARY,
        "rail": "enach",
        "mandates": 1200,
        "mcc": "8299",
        "amounts": (499900, 2499900),
        "due_days": (5, 20),
        "fail_rate": 0.12,
    },
]

#: §R3.2 cause mix, as (code, reason, weight).
CAUSES: Final[list[tuple[str, str, float]]] = [
    ("BAD_REQUEST_ERROR", "insufficient_funds", 0.62),
    ("GATEWAY_ERROR", "technical_decline", 0.14),
    ("BAD_REQUEST_ERROR", "limit_exceeded", 0.09),
    ("GATEWAY_ERROR", "issuer_down", 0.08),
    ("BAD_REQUEST_ERROR", "mandate_issue", 0.07),
]

#: Salary lands on these days for most customers (§R3.2).
PAYDAYS: Final[tuple[int, ...]] = (1, 7, 28)

#: Share of mandates that fail every cycle. Matches the simulator's
#: `CHRONICALLY_DRY` archetype weight, and it is the population §24.3's
#: permanent fix exists for: a mandate failing month after month does not need
#: a better retry, it needs a different debit day. Without this cohort the
#: chronic condition is met by roughly nobody and the date-change path — the
#: most differentiated capability here — never fires.
CHRONIC_BASE: Final = 0.02

#: How much more likely chronic failure becomes on a low-liquidity debit day.
#: §24.3's premise is that these are the *same* thing: a customer debited on the
#: 28th when salary lands on the 1st does not have a retry problem, they have
#: the wrong debit day. Modelling chronic failure as independent of the day —
#: which the first version did — severs the correlation the permanent fix
#: exists to exploit, and leaves the date-change path with nothing to act on.
CHRONIC_DAY_SLOPE: Final = 0.30

#: Recovery among *recoverable* failures, by arm. Non-recoverable causes are
#: 16% of the mix, so these land §R3.2's ~62% in the treatment arm. Not 1.0 —
#: a perfect rate reads as fabricated and invites the wrong scrutiny.
TREATMENT_RECOVERY: Final = 0.78
HOLDOUT_RECOVERY: Final = 0.30

#: A chronic mandate in the treatment arm recovers *well*, because moving the
#: attempt to payday is precisely what fixes it — that is the product thesis,
#: and it is the comparison the holdout exists to make. In the control arm the
#: same customer keeps meeting a fixed schedule that does not fit them.
CHRONIC_TREATMENT_RECOVERY: Final = 0.72
CHRONIC_HOLDOUT_RECOVERY: Final = 0.10

#: Share of customers who have asked not to be contacted. Their notice is
#: suppressed (§24.6), so the debit that depended on it is refused at fire time
#: on `RBI-EMANDATE-PDN-24H` — a genuine refusal from a genuine condition,
#: which is the only kind worth showing.
SUPPRESSED_SHARE: Final = 0.06

HERO_TENANT: Final = "fitfirst"
HERO_CYCLE: Final = "cyc_7f3a91"
HERO_AMOUNT: Final = 249900

HISTORY_DAYS: Final = 90


@dataclass
class Stats:
    tenants: int = 0
    mandates: int = 0
    events: int = 0
    posted: int = 0
    failed_posts: int = 0
    by_tenant: dict[str, int] = field(default_factory=dict)


def _say(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _chronic_probability(due_day: int) -> float:
    """How likely a mandate on this debit day is to fail every cycle.

    Scaled by how far the day sits below the payday peak, using the same
    liquidity model the planner reads (ADR-098) — so the population the seed
    creates and the population the engine reasons about are the same one.
    """
    peak = max(day_presence(d) for d in range(1, 32))
    shortfall = 1.0 - (day_presence(due_day) / peak)
    return min(0.9, CHRONIC_BASE + CHRONIC_DAY_SLOPE * shortfall)


def _pick_cause(rng: random.Random) -> tuple[str, str]:
    roll = rng.random()
    cumulative = 0.0
    for code, reason, weight in CAUSES:
        cumulative += weight
        if roll < cumulative:
            return code, reason
    return CAUSES[-1][0], CAUSES[-1][1]


# ── onboarding state (not pipeline output — see the module docstring) ────────


async def _reset(engine: AsyncEngine, tenant_ids: list[str]) -> None:
    async with engine.begin() as conn:
        for table in (
            "scheduled_actions",
            "attempts",
            "interventions",
            "cycles",
            "events_raw",
            "webhook_secrets",
            "contact_suppressions",
            "customer_profiles",
            "mandates",
            "decisions",
            "tenants",
        ):
            await conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = ANY(:ids)"), {"ids": tenant_ids}
            )


def webhook_secret(seed_value: str, tenant_id: str) -> str:
    """The tenant's signing secret, derived from the run seed.

    Deterministic on purpose. A random secret would have to be written into
    `.env` and the API restarted *before* any webhook could verify — and a
    reseed would invalidate every secret already there. Deriving it means the
    same seed always produces the same secret, so `.env` stays valid across
    reseeds and the API never needs restarting mid-run.

    Demo-only. A real tenant's secret comes from the Razorpay dashboard and is
    never derivable from anything.
    """
    return hmac.new(
        f"prayas-demo-secret:{seed_value}".encode(), tenant_id.encode(), hashlib.sha256
    ).hexdigest()


async def _onboard(
    engine: AsyncEngine, spec: dict[str, Any], rng: random.Random, seed_value: str
) -> str:
    """Create the tenant, its webhook secret ref, and its mandates."""
    tenant_id = str(spec["tenant_id"])
    ref = f"WH_{tenant_id.upper()}_V1"
    secret = webhook_secret(seed_value, tenant_id)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config)"
                " VALUES (:t, :n, jsonb_build_object('adoption_stage', CAST(:s AS int),"
                "                                    'demo_tenant', true))"
            ),
            {"t": tenant_id, "n": spec["name"], "s": int(spec["stage"])},
        )
        await conn.execute(
            text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, :r)"),
            {"t": tenant_id, "r": ref},
        )

        now = datetime.now(UTC)
        rows = []
        cap = int(spec["amounts"][1])
        for i in range(1, int(spec["mandates"]) + 1):
            rows.append(
                {
                    "m": f"sub_{tenant_id}_{i}",
                    "t": tenant_id,
                    "c": f"cust_{tenant_id}_{i}",
                    "rail": spec["rail"],
                    "mcc": spec["mcc"],
                    "amt": cap,
                    "consent": f"cns_{tenant_id}_{i}",
                    "created": now - timedelta(days=rng.randint(HISTORY_DAYS, HISTORY_DAYS + 200)),
                }
            )
        # Executemany — 6,120 single round trips would dominate the seed budget.
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail, mcc,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, :c, :rail, :mcc, :amt, 'created', :consent, :created)"
            ),
            rows,
        )

    # Consent state, not pipeline output: some customers have opted out of
    # contact, and the pipeline must discover that rather than be told the
    # outcome. Written under the pseudonym, as §27 requires.
    suppressed = [
        {
            "t": tenant_id,
            "ref": pseudonymise(tenant_id, f"cust_{tenant_id}_{i}"),
            "reason": "opt_out",
        }
        for i in range(1, int(spec["mandates"]) + 1)
        if rng.random() < SUPPRESSED_SHARE
    ]
    if suppressed:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO contact_suppressions (tenant_id, customer_ref, reason,"
                    " suppressed_at) VALUES (:t, :ref, :reason, now())"
                    " ON CONFLICT (tenant_id, customer_ref) DO NOTHING"
                ),
                suppressed,
            )

    os.environ[f"PRAYAS_WEBHOOK_SECRET_{ref}"] = secret
    return secret


def _env_line(tenant_id: str, secret: str) -> str:
    return f"PRAYAS_WEBHOOK_SECRET_WH_{tenant_id.upper()}_V1={secret}"


# ── the events themselves ───────────────────────────────────────────────────


def _events_for_mandate(
    spec: dict[str, Any], index: int, rng: random.Random, now: datetime
) -> list[dict[str, Any]]:
    """One mandate's 90 days, as webhook payloads."""
    tenant_id = str(spec["tenant_id"])
    mandate_id = f"sub_{tenant_id}_{index}"
    low, high = spec["amounts"]
    amount = rng.randrange(low, high + 1, 100)
    due_day = int(rng.choice(spec["due_days"]))

    # The engine's own hash decides the arm, so the screen and the planner
    # cannot disagree about who is in the control group.
    arm = arm_for(spec["stage"], tenant_id=tenant_id, mandate_id=mandate_id)
    chronic = rng.random() < _chronic_probability(due_day)

    activated_at = now - timedelta(days=HISTORY_DAYS, hours=rng.randint(0, 23))
    sub = {
        "entity": {
            "id": mandate_id,
            "status": "active",
            "current_end": int((now + timedelta(days=30)).timestamp()),
        }
    }
    out: list[dict[str, Any]] = [
        {
            "_id": f"{mandate_id}_act",
            "event": "subscription.activated",
            "payload": {"subscription": sub},
            "created_at": int(activated_at.timestamp()),
        }
    ]

    # Walk the billing months inside the window.
    for cycle_no in range(3):
        # §R3.1 gives each tenant its debit days, and the day is what §24.3
        # reasons about — so land on the calendar day directly. Shifting by a
        # modulo-30 offset (the first attempt) drifts as month lengths vary and
        # produced days 1, 2, 4 and 31 for a tenant configured with 26 and 28.
        anchor = now - timedelta(days=HISTORY_DAYS - 10 - cycle_no * 30)
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        due = anchor.replace(
            day=min(due_day, last_day), hour=3, minute=30, second=0, microsecond=0
        )  # 09:00 IST
        if due >= now:
            break
        invoice = f"inv_{tenant_id}_{index}_{cycle_no}"
        payment = {
            "id": f"pay_{tenant_id}_{index}_{cycle_no}",
            "amount": amount,
            "currency": "INR",
            "invoice_id": invoice,
        }

        # A chronic mandate fails every cycle — that run is what §24.3 reads
        # as "this is not a timing problem, it is the wrong day".
        if not chronic and rng.random() >= float(spec["fail_rate"]):
            out.append(
                {
                    "_id": f"{invoice}_ok",
                    "event": "payment.captured",
                    "payload": {
                        "subscription": sub,
                        "payment": {"entity": {**payment, "status": "captured"}},
                    },
                    "created_at": int(due.timestamp()),
                }
            )
            continue

        code, reason = _pick_cause(rng)
        out.append(
            {
                "_id": f"{invoice}_fail",
                "event": "payment.failed",
                "payload": {
                    "subscription": sub,
                    "payment": {
                        "entity": {
                            **payment,
                            "status": "failed",
                            "error_code": code,
                            "error_source": "bank",
                            "error_reason": reason,
                        }
                    },
                },
                "created_at": int(due.timestamp()),
            }
        )

        # Recovery. §R3.2: ~62% in treatment, visibly lower in the holdout —
        # some of those recover unaided, which is the whole comparison.
        recoverable = reason in {"insufficient_funds", "technical_decline", "issuer_down"}
        treated = arm is Arm.TREATMENT
        if chronic:
            rate = CHRONIC_TREATMENT_RECOVERY if treated else CHRONIC_HOLDOUT_RECOVERY
        else:
            rate = TREATMENT_RECOVERY if treated else HOLDOUT_RECOVERY
        if recoverable and rng.random() < rate:
            recovered_at = due + timedelta(days=rng.randint(1, 5), hours=rng.randint(0, 10))
            # Clamp rather than drop: a recovery falling past "now" would
            # silently lower the observed rate below the one configured above.
            if recovered_at >= now:
                recovered_at = now - timedelta(hours=rng.randint(1, 36))
            if recovered_at > due:
                out.append(
                    {
                        "_id": f"{invoice}_rec",
                        "event": "payment.captured",
                        "payload": {
                            "subscription": sub,
                            "payment": {
                                "entity": {
                                    **payment,
                                    "id": f"{payment['id']}_r",
                                    "status": "captured",
                                }
                            },
                        },
                        "created_at": int(recovered_at.timestamp()),
                    }
                )
    return out


def _hero_events(now: datetime) -> list[dict[str, Any]]:
    """§R3.2's hero cycle — the clean story the timeline screen renders."""
    mandate_id = "sub_fitfirst_1"
    sub = {
        "entity": {
            "id": mandate_id,
            "status": "active",
            "current_end": int((now + timedelta(days=30)).timestamp()),
        }
    }
    failed_at = now - timedelta(days=2, hours=6)
    payment = {"id": "pay_hero", "amount": HERO_AMOUNT, "currency": "INR", "invoice_id": HERO_CYCLE}
    return [
        {
            "_id": "hero_fail",
            "event": "payment.failed",
            "payload": {
                "subscription": sub,
                "payment": {
                    "entity": {
                        **payment,
                        "status": "failed",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_source": "bank",
                        "error_reason": "insufficient_funds",
                    }
                },
            },
            "created_at": int(failed_at.timestamp()),
        }
    ]


# ── posting ─────────────────────────────────────────────────────────────────


async def _post_all(
    base_url: str,
    tenant_id: str,
    secret: str,
    events: list[dict[str, Any]],
    concurrency: int,
    stats: Stats,
) -> None:
    url = f"{base_url}/v1/webhooks/razorpay/{tenant_id}"
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(limits=limits, timeout=60.0) as client:

        async def one(evt: dict[str, Any]) -> None:
            event_id = str(evt.pop("_id"))
            body = json.dumps(evt, separators=(",", ":")).encode()
            async with sem:
                try:
                    r = await client.post(
                        url,
                        content=body,
                        headers={
                            "X-Razorpay-Signature": _sign(secret, body),
                            "X-Razorpay-Event-Id": event_id,
                            "Content-Type": "application/json",
                        },
                    )
                    if r.status_code == 200:
                        stats.posted += 1
                    else:
                        stats.failed_posts += 1
                except Exception:
                    stats.failed_posts += 1

        # Chunked so a tenant's 30,000 coroutines are not all live at once.
        for start in range(0, len(events), 2000):
            await asyncio.gather(*(one(e) for e in events[start : start + 2000]))


async def plan(rounds: int = 400) -> None:
    """Drive the planner in-process until the backlog is decided.

    The same `planner.worker.tick` the service runs — the container polls every
    15 s, which is right for production and wrong for seeding a fleet: 4,000
    cycles would take twenty minutes of mostly waiting.
    """
    from prayas.planner import worker as planner

    engine = create_async_engine(os.environ["PRAYAS_DATABASE_URL_APP"])
    try:
        scheduled = proposed = 0
        idle = 0
        for _ in range(rounds):
            r = await planner.tick(engine)
            scheduled += r.scheduled
            proposed += r.proposed
            # `examined`, not `considered`: a page that happens to be entirely
            # holdout mandates considers nothing while thousands of cycles are
            # still waiting behind it. Ending on that reads "done" off a page
            # the planner deliberately skipped.
            idle = idle + 1 if r.examined == 0 else 0
            if idle >= 4:
                break
            if _ % 40 == 39:
                _say(f"    …{scheduled:,} scheduled so far")
        _say(f"    scheduled {scheduled:,} actions, proposed {proposed:,} date changes")
    finally:
        await engine.dispose()


async def settle(rounds: int = 60) -> None:
    """Fire the planned queue through the real executor and gate.

    Two passes per round, and the order is the point. Notices go first, at real
    time, so `cycles.pdn_sent_at` is written by the notice actually being sent.
    Debits follow, evaluated 25 hours later — the gate genuinely computes
    `hours_since(pdn_sent_at)` from that instant, so `RBI-EMANDATE-PDN-24H`
    passes because 25 hours of notice elapsed.

    Nothing is backdated. The only shortcut is *when* the gate is told it is,
    which is now an input to the gate rather than something it reads off the
    wall (FINDING-P17-12) — the same mechanism Phase 6's time travel uses.

    Refusals arise on their own: a suppressed customer's notice is never sent
    (§24.6), so their debit finds no valid notice and is denied.
    """
    from prayas.executor.provider import FakeProvider
    from prayas.executor.worker import drain_tenant

    engine = create_async_engine(os.environ["PRAYAS_DATABASE_URL_OWNER"])
    provider = FakeProvider()
    tenants = [str(spec["tenant_id"]) for spec in FLEET]
    try:
        for round_no in range(rounds):
            async with engine.begin() as conn:
                notices = await conn.execute(
                    text(
                        "UPDATE scheduled_actions SET fire_at = now() - interval '2 minutes'"
                        " WHERE action_id IN (SELECT action_id FROM scheduled_actions"
                        "   WHERE state = 'pending' AND action_type = 'pdn_notice'"
                        "   ORDER BY fire_at LIMIT 600)"
                    )
                )
            if notices.rowcount:
                for tenant in tenants:
                    await drain_tenant(engine, tenant, provider)

            later = datetime.now(UTC) + timedelta(hours=25)
            async with engine.begin() as conn:
                debits = await conn.execute(
                    text(
                        "UPDATE scheduled_actions SET fire_at = now() - interval '1 minute'"
                        " WHERE action_id IN (SELECT a.action_id FROM scheduled_actions a"
                        "   WHERE a.state = 'pending' AND a.action_type = 'debit_attempt'"
                        "   ORDER BY a.fire_at LIMIT 600)"
                    )
                )
            if debits.rowcount:
                for tenant in tenants:
                    await drain_tenant(engine, tenant, provider, now=later)

            if not notices.rowcount and not debits.rowcount:
                break
            _say(f"    round {round_no + 1}: {notices.rowcount} notices, {debits.rowcount} debits")
    finally:
        await engine.dispose()


async def seed(base_url: str, seed_value: str, concurrency: int, *, stage: str = "all") -> Stats:
    owner = os.environ.get("PRAYAS_DATABASE_URL_OWNER")
    if not owner:
        sys.exit("PRAYAS_DATABASE_URL_OWNER must be set")

    engine = create_async_engine(owner)
    stats = Stats()
    now = datetime.now(UTC)
    env_lines: list[str] = []

    try:
        if stage in ("all", "onboard"):
            await _reset(engine, [str(s["tenant_id"]) for s in FLEET])
        for spec in FLEET:
            tenant_id = str(spec["tenant_id"])
            rng = random.Random(f"{seed_value}:{tenant_id}")

            secret = webhook_secret(seed_value, tenant_id)
            env_lines.append(_env_line(tenant_id, secret))
            stats.tenants += 1
            stats.mandates += int(spec["mandates"])

            if stage in ("all", "onboard"):
                _say(f"  {tenant_id}: onboarding {spec['mandates']:,} mandates…")
                await _onboard(engine, spec, rng, seed_value)
            if stage == "onboard":
                continue

            events: list[dict[str, Any]] = []
            for i in range(1, int(spec["mandates"]) + 1):
                events.extend(_events_for_mandate(spec, i, rng, now))
            if tenant_id == HERO_TENANT:
                events.extend(_hero_events(now))

            _say(f"  {tenant_id}: posting {len(events):,} signed webhooks…")
            await _post_all(base_url, tenant_id, secret, events, concurrency, stats)
            stats.events += len(events)
            stats.by_tenant[tenant_id] = len(events)
    finally:
        await engine.dispose()

    _say()
    _say("  Webhook secrets — append these to .env so the API can verify:")
    for line in env_lines:
        _say(f"    {line}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", default="prayas-demo-2026")
    parser.add_argument(
        "--base-url", default=f"http://localhost:{os.environ.get('API_PORT', '8010')}"
    )
    parser.add_argument("--concurrency", type=int, default=48)
    parser.add_argument(
        "--stage",
        choices=("all", "onboard", "events", "plan", "settle"),
        default="all",
        help="`onboard` creates tenants and mandates and prints the secrets; "
        "`events` posts the webhooks. Split so the wrapper can install "
        "the secrets into the API's environment in between.",
    )
    args = parser.parse_args()

    started = datetime.now(UTC)
    if args.stage in {"plan", "settle"}:
        asyncio.run(plan() if args.stage == "plan" else settle())
        _say(f"  settle took {(datetime.now(UTC) - started).total_seconds():.0f}s")
        return 0
    stats = asyncio.run(seed(args.base_url, args.seed, args.concurrency, stage=args.stage))
    elapsed = (datetime.now(UTC) - started).total_seconds()

    _say()
    _say(
        f"  tenants {stats.tenants}  mandates {stats.mandates:,}"
        f"  events {stats.events:,}  accepted {stats.posted:,}"
        f"  rejected {stats.failed_posts:,}"
    )
    _say(f"  ingest took {elapsed:.0f}s")
    return 1 if stats.failed_posts else 0


if __name__ == "__main__":
    raise SystemExit(main())
