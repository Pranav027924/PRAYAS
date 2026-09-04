# PRAYAS

Liquidity-aware recovery and retention engine for recurring debits in India.

When a subscription payment fails, PRAYAS works out **when** money is most
likely to be in the customer's account, checks that a retry at that moment
would be lawful, sends the pre-debit notice the regulator requires, and then
debits — recording every step in an append-only, hash-chained ledger.

The thesis is that most failed recurring payments are **timing** failures, not
credit failures. The RBI e-mandate framework allows one execution plus three
retries inside fixed windows, so this is a budget allocation problem rather
than a scheduling one: you get four attempts, and where you spend them decides
whether the money arrives.

Razorpay-native by decision, multi-tenant, and abstracted over three rails
(UPI Autopay, card e-mandate, eNACH).

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12, strict type hints throughout |
| API | FastAPI + Uvicorn |
| Data | PostgreSQL 16, row-level security forced on every tenant-scoped table |
| Access | SQLAlchemy 2 (async) + asyncpg |
| Migrations | Alembic — 15 revisions, no hand-applied DDL |
| Models | NumPy, scikit-learn (hazard model, isotonic calibration) |
| Console | Server-rendered Jinja2, hand-written CSS, vanilla JS — no build step, no CDN |
| Rule pack | `prayas-rulepack`, a separate workspace package |
| Runtime | Docker Compose |
| Tooling | uv, pytest, ruff, mypy --strict, bandit, pip-audit |

Money is integer paise, never float. Times are stored UTC and evaluated IST,
because the rail windows are IST-defined.

---

## Architecture

Seven services. The pipeline is four of them, and each is its own process so
it scales and fails independently.

```
Razorpay webhook
      │
      ▼
   api            signature verified (HMAC over raw bytes), deduplicated,
      │           persisted. An unverified payload is never stored.
      ▼
  projector       folds the event log into mandate and cycle state
      │
      ▼
   planner        solves for the retry slot — liquidity prior, legality mask,
      │           attempt budget — and queues the work. It does not fire.
      ▼
  executor        re-checks compliance at the moment of firing, decrements the
      │           budget atomically, commits intent to an outbox, then calls
      ▼
   ledger         append-only, hash-chained, one chain per tenant
                  (chain-verifier walks it continuously)
```

A missing service fails **silently** — the stack stays green and simply stops
doing work — so the console's health strip reads evidence of work rather than
process presence.

### Design rules the code actually enforces

- No code path debits without passing the compliance gate, and the gate fails
  **closed**: errors, timeouts and unknown rule versions all deny.
- Every scheduled action revalidates state at **fire** time, not schedule time.
  Rules and state both move between deciding and doing.
- Every external side effect carries a deterministic idempotency key.
- `decisions` is append-only. Enforced by privilege, not convention — the
  application role was granted SELECT and INSERT and nothing else.
- Every tenant-scoped query filters by tenant; RLS is forced, and isolation is
  tested as a correctness property rather than assumed.
- No PAN, no bank credentials, no balances stored anywhere.
- LLM output is untrusted data with a validated schema and a bounded effect. It
  never becomes an instruction and never reaches the money path.
- Every compliance rule carries a citation and an `as_of` date.

### Documents

| Document | What it is |
|---|---|
| `docs/PRAYAS-MASTER-SPEC.md` | Authoritative for **what** to build |
| `docs/PRAYAS-EXECUTION-PLAYBOOK.md` | Authoritative for **when and in what order** |
| `PROGRESS.md` | Where the build is, with every ADR and finding |
| `docs/PRAYAS-HLD.md` | High-level design — services, data flow, boundaries |
| `docs/PRAYAS-LLD.md` | Low-level design — schemas, transactions, algorithms |
| `docs/BLOCKERS.md` | What still needs somebody outside this repository |
| `docs/DEMO-DAY-SCRIPT.md` | The full walkthrough, figure by figure |
| `docs/DEMO-3MIN.md` | The three-minute version |

The HLD and LLD are superseded by the Master Spec where they disagree; they
remain useful for background and rationale.

---

## Running it

**Prerequisites:** Docker, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Pranav027924/PRAYAS.git
cd PRAYAS
cp .env.example .env          # if present; otherwise defaults are local-safe
docker compose up -d
```

That brings up Postgres, applies the migrations, and starts the four pipeline
services plus the chain verifier. The API listens on **:8010**.

```bash
curl localhost:8010/health
# {"status":"ok","database":"ok"}
```

Check every service is up before anything else — a stopped worker is silent:

```bash
docker compose ps
```

### Tests

```bash
bash scripts/ci-local.sh
```

Runs exactly what CI runs, in the same order: ruff, ruff format, `mypy
--strict` over `prayas` and `tests`, migrations, the full pytest suite with an
85% coverage floor, rule-pack conformance, bandit, and pip-audit.

The suite is ~1,470 tests across unit, property, contract, integration, chaos
and isolation. It needs the database up.

> The integration suite truncates tenant tables. Running it **empties the demo
> fleet** — reseed afterwards.

---

## Running the demo

One command does a full reset and reseed:

```bash
bash scripts/seed-demo.sh
```

It takes about ten minutes and is deliberately unexciting: it wipes the volume,
brings the stack up, onboards three tenants, then posts ~52,600 **signed
webhooks** through the real ingest path and waits for the projector, planner
and executor to work through them. Nothing is inserted directly — cycles,
attempts, decisions and the ledger all arrive because the pipeline produced
them.

You get three tenants at three different adoption stages:

| Tenant | Rail | Mandates | Stage |
|---|---|---|---|
| `fitfirst` | UPI Autopay | 6,120 | full |
| `streamly` | card e-mandate | 4,880 | ramp |
| `edtechco` | eNACH | 1,200 | canary |

### Opening the console

Mint a token and sign in:

```bash
export PRAYAS_CONSOLE_TOKEN_SECRET=local_console_secret_not_for_production
uv run python -c "from prayas.console.auth import issue; print(issue('fitfirst','platform_pm',ttl_seconds=86400))"
```

Open <http://localhost:8010/console/login>, paste the token into **Experience
the demo**, and enter. The email and password fields above it are deliberately
inert — there is no user store, and adding one would put a second, weaker path
to the same data beside the signed one.

Roles change what you can open, deny-by-default: `platform_pm` opens the
portfolio, cycle and ledger; `merchant_ops` the portfolio and the policy
simulator; `compliance_reviewer` the cycle, ledger and replay drawer. A role
absent from a screen's set cannot open it, so adding a screen without deciding
who may see it locks everyone out rather than admitting everyone.

### The four screens

| Screen | Path | What it shows |
|---|---|---|
| Console | `/console/` | The pipeline end to end — mandates, events, queue, decisions |
| Portfolio | `/console/portfolio?tenant=fitfirst&window=90d` | Incremental recovery **and** survival, against a permanent holdout |
| Cycle | `/console/cycle/cyc_7f3a91` | One cycle's timeline over the rail's lawful execution windows |
| Ledger | `/console/ledger` | Every decision, allowed and refused, with the chain badge |

Use `&window=90d` on the portfolio. The 30-day default is real but thinner —
the survival interval spans zero there.

### Verifying the figures

```bash
uv run python scripts/verify_demo_numbers.py
```

Prints every number the demo script quotes, straight from the running stack,
and exits non-zero if the fleet is empty or the hero cycle shows no recovery.

### Time travel

The cycle screen has `+1 hour`, `+24 hours`, `Jump to next action` and `Reset`.
These advance a **per-tenant virtual clock**, and the executor's poll, the
gate's `as_of` and the planner's reference time all read it — so a debit that
becomes lawful after 24 virtual hours does so because the gate computed 24
hours of elapsed notice, not because anything was backdated.

It refuses outright on any tenant not seeded `demo_tenant: true`. That guard is
the only thing standing between this feature and a real merchant's clock, so it
is checked unconditionally and has its own test.

---

## What is not done

Two things, both needing somebody outside the code:

1. **A DLT-registered template** for the pre-debit notice. Without one,
   `TRAI-DLT-TEMPLATE` refuses the send rather than the send happening anyway.
2. **A live authorised mandate** on a real rail.

Neither is an engineering problem. `docs/BLOCKERS.md` has the detail.
