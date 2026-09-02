# PRAYAS — what it is, what is missing, and how to see it work

Last verified 2026-09-02 against the running stack.

Three questions, answered in order: what exists today, what still needs
somebody outside this repository, and how to run the demonstration yourself.

---

## Contents

1. [What PRAYAS is, as of now](#1-what-prayas-is-as-of-now)
2. [What is preventing completion, and how to clear it](#2-what-is-preventing-completion-and-how-to-clear-it)
3. [The demonstration](#3-the-demonstration)

---

## 1. What PRAYAS is, as of now

### In one paragraph

PRAYAS is a liquidity-aware recovery engine for recurring debits in India. When
a subscription payment fails, it works out **when** money is most likely to be
in the customer's account, checks that a retry at that moment would be lawful,
sends the pre-debit notice the regulator requires, and then debits — recording
every step in an append-only, hash-chained ledger. It is Razorpay-native,
multi-tenant, and abstracted over three rails (UPI Autopay, card e-mandate,
eNACH).

The thesis is that most failed recurring payments are **timing** failures, not
credit failures, and that retrying at the right hour recovers money that a
fixed day-1/day-3/day-5 schedule loses.

### What actually runs

Seven services, from `docker-compose.yml`:

| Service | What it does |
|---|---|
| `postgres` | State, with row-level security forced on every tenant-scoped table |
| `migrate` | Alembic, 13 migrations, runs once as the database owner |
| `api` | Webhook receiver and the operator console |
| `projector` | Drains the event log into domain state |
| `planner` | The decision service — solves for the retry slot and queues the work |
| `executor` | Fires actions, re-checking compliance at the moment of firing |
| `chain-verifier` | Walks the ledger hash chain and reports breaks |

The pipeline runs **api → projector → planner → executor**, and every link is
its own process. A missing one fails silently — the stack stays green and
simply stops doing work — so check all four are up before debugging anything
else.

### The path a failed payment takes

```
Razorpay webhook  ──▶ signature verified (HMAC over raw bytes), deduplicated,
                      persisted. An unverified payload is never stored.
        │
        ▼
projector         ──▶ folds the full event history into mandate and cycle state.
                      Order-independent: any permutation converges.
        │
        ▼
planner           ──▶ P(funds present) per hour × the lawful slots × the cost of
                      waiting → the hour to retry, or the decision not to.
                      Queues the pre-debit notice AND the debit together.
        │
        ▼
executor          ──▶ re-evaluates every compliance rule at FIRE time, not at
                      schedule time. Sends the notice; 24h later, debits.
        │
        ▼
ledger            ──▶ append-only, hash-chained, one row per decision, each
                      carrying the rules consulted with their citations.
```

### What it enforces, and can prove

Eight compliance rules ship as a standalone package, each with a regulator, a
citation and an `as_of` date:

| Rule | Regulator |
|---|---|
| `RBI-EMANDATE-PDN-24H` | RBI — 24-hour pre-debit notice |
| `RBI-EMANDATE-AFA-CAP` | RBI — additional-factor authentication ceiling |
| `RBI-FPC-CONTACT-WINDOW` | RBI — when a customer may be contacted |
| `NPCI-AUTOPAY-WINDOW` | NPCI — UPI Autopay non-peak execution windows |
| `NPCI-PDN-CUTOFF-2350` | NPCI — 23:50 IST submission cutoff |
| `DPDP-CONSENT-VALID` | MeitY — consent must be valid and current |
| `TRAI-DLT-TEMPLATE` | TRAI — approved SMS template |
| `PRAYAS-FATIGUE-CAP` | Internal — message fatigue ceiling |

Ten invariants hold throughout. The ones you can watch happen in the
demonstration:

- **No debit without passing the gate**, and the gate **fails closed** — errors,
  timeouts and unknown rule versions all deny.
- **Every scheduled action revalidates at fire time**, because rules and state
  both move between deciding and doing.
- **`decisions` is append-only.** No update, no delete, ever.
- **Every tenant-scoped query filters by tenant**, enforced by the database
  rather than by discipline.
- **No PAN, no bank credentials, no balances** are stored anywhere.
- Money is **integer paise**. No float touches the money path.

### Build state

18 of 20 phases closed. Phase 17 (live integration) is in progress; Phase 18
(pilot) needs a real merchant and elapsed time.

The test suite is **1,387 tests** at **~89% coverage**, with `ruff`,
`mypy --strict`, `bandit` and `pip-audit` clean. Compliance rules have their own
conformance suite (40 tests) that runs against the published package.

### What it cannot do yet

**It cannot move real money**, and it cannot send a real SMS. Both are waiting
on outside parties, not on code — see section 2. Everything else — ingestion,
projection, the decision, the compliance gate, the ledger, reconciliation —
runs today and is what the demonstration shows.

---

## 2. What is preventing completion, and how to clear it

Four things, none of which can be solved by writing more code. They are ordered
by lead time: start the first one today.

### 2.1 DLT registration for the pre-debit notice — *weeks*

**What.** The RBI requires a notice at least 24 hours before a recurring debit.
In India a transactional SMS may only be sent from a **DLT-registered sender ID
using a pre-approved template**, registered with the telecom regulator through
an operator.

**Why it blocks.** Without it no notice reaches a real phone, so no real debit
is lawful. The logic is built and working — the notice is planned, sent,
recorded, and the gate reads it — but the transport is a stand-in.

**How to clear it.**
1. Register your entity on any operator's DLT portal (Jio, Airtel, or VI).
2. Register a **sender ID** (a 6-character header, e.g. `FITFIT`).
3. Register a **content template** for the pre-debit notice. §30's required
   fields are already encoded in `prayas/notify/` — the template must name the
   amount, the date, the merchant and a way to opt out.
4. Approval typically takes several days to a few weeks.
5. Supply the credentials, and the console channel is replaced by the real one.

**Start this first.** It has the longest lead time and nothing else depends on
it, so it can run in parallel with everything below.

### 2.2 A real, authorised mandate — *hours, once a customer exists*

**What.** Razorpay Subscriptions is enabled and the credentials work. But a
mandate only becomes chargeable when a **customer completes an authorisation
flow** — approving UPI Autopay in their payment app, or a card e-mandate with
additional-factor authentication. No API call substitutes for a person tapping
approve.

**Why it blocks.** The lifecycle can be driven with synthetic webhooks (which
is what the demonstration does), but not against a real subscription until
somebody authorises one.

**How to clear it.**
1. In the Razorpay dashboard, create a **plan**, then a **subscription** against
   it.
2. Open the authorisation link on a phone and approve it. Test mode accepts
   test VPAs, so this costs nothing.
3. Point the webhook URL at your deployment and the real events flow in.

### 2.3 Three tenants, for cross-tenant liquidity priors — *structural*

**What.** The system learns *when* money arrives from pooled, anonymised
statistics. Privacy rules require a cell to hold at least **50 observations from
3 distinct merchants** before it is published — so a deployment with fewer than
three merchants publishes nothing, ever.

**Why it matters.** With no learned prior the system falls back to a built-in
one derived from population payday patterns. It works, and it is labelled as a
prior rather than as evidence, but it is not yet *your* data.

**How to clear it.** Onboard three or more merchants. Lowering the threshold
would weaken the anonymity guarantee and should be resisted — the whole point
is that no cell can be traced back to one merchant's customers.

Until then the system runs on the bootstrap prior and says so in its logs.

### 2.4 A cloud account, to run it anywhere but a laptop — *hours*

**What.** There is no AWS account yet. `docs/AWS-DEPLOYMENT.md` is complete and
executable — RDS, ECS Fargate, Secrets Manager, GitHub OIDC, cost, teardown.

**How to clear it.** Open an account, then follow that document. Everything
stays in `ap-south-1` (Mumbai): payment data must remain in India, and that is
an invariant rather than a preference. Budget roughly **$95–100/month** for a
staging-sized deployment.

### Also worth knowing

Two internal items are open and are **not** external blockers — they are honest
work-in-progress, tracked in `PROGRESS.md`:

- The gate's `hours_since()` reads the wall clock rather than the evaluation
  instant, so a single cycle cannot be demonstrated in under 24 hours. This is
  why the demonstration runs in two acts.
- The statistics aggregator connects as the database owner, which one internal
  rule reserves for migrations. The narrower fix is a privileged function.

Neither affects correctness of what runs today.

---

## 3. The demonstration

### What it demonstrates

That a failed recurring payment becomes a **lawful, recorded, successful
retry** — which is the entire claim.

Specifically, you will watch:

1. A signed webhook accepted; an unsigned one rejected and never stored.
2. A failed ₹2,499 debit projected into cycle state.
3. The engine choosing **when** to retry, and moving that choice to a later
   hour because the earlier one left no lawful moment to send the notice.
4. The pre-debit notice actually sent and recorded.
5. The compliance gate re-evaluated **at the moment of firing**, all four
   applicable rules passing, and the debit going through.
6. Every step written to a hash-chained ledger you can open in the console.

**What is real:** the projector, the decision engine, the compliance gate, the
executor, the ledger, the console — all production code paths.

**What stands in:** the payment rail (a deterministic fake, as in any test-mode
deployment) and the SMS channel (§2.1). Nothing else.

### Before you start

You need Docker and [uv](https://docs.astral.sh/uv/). Nothing else — no cloud
account, no Razorpay credentials, no phone.

```bash
git clone <your-repo> && cd PRAYAS
cp -n .env.example .env      # -n: never overwrite an existing .env
```

> **`-n` matters.** `.env` holds your Razorpay secret and your per-tenant
> webhook signing keys, and it is gitignored — so a plain `cp` destroys them
> with no way back. If you already have a `.env`, skip this step entirely.

### Run it

One command:

```bash
./scripts/demo.sh
```

It takes about a minute, and prints each step as it happens. Pass a name if you
want a specific merchant id: `./scripts/demo.sh fitfirst`.

### What you should see

```
[2/5] onboarding the merchant
      15% of mandates are held out permanently as a control arm; this demo
      follows one in the treatment arm: sub_live2_2

[3/5] the customer authorises, then the monthly debit fails
      subscription.activated  -> HTTP 200
      payment.failed          -> HTTP 200   (₹2,499, insufficient funds)

[4/5] waiting for the projector and the planner
      pdn_notice    fire_at=02 Sep 03:08 UTC
      debit_attempt fire_at=03 Sep 03:08 UTC

ACT I — a failed debit becomes a lawfully planned recovery
  ✓ notice sent and recorded: pdn_sent_at = 01 Sep 19:09 UTC
      ledger #0  pdn_notice    ALLOW

ACT II — 30 hours after its notice, the debit fires
    gate re-evaluated at fire time, as_of = now:
       ✓ DPDP-CONSENT-VALID       v1  ALLOW
       ✓ NPCI-AUTOPAY-WINDOW      v3  ALLOW
       ✓ RBI-EMANDATE-AFA-CAP     v2  ALLOW
       ✓ RBI-EMANDATE-PDN-24H     v3  ALLOW
  ✓ gate ALLOWED — debit submitted to the rail
    distinct debits at the provider: 1  (submissions: 1)
```

Three lines worth pausing on:

- **the holdout line in step 2.** `FULL` keeps a permanent 15% control arm that
  is never acted on — that is how uplift stays measurable, and it is a property
  of the stage rather than a switch someone can forget to turn off. The demo
  picks a subject outside it and says so, because a mandate in the holdout
  would correctly produce no action at all.

- **`(24h of notice)`** in step 4. The engine did not simply pick the earliest
  legal hour — it moved the debit later so that a lawful notice could precede
  it. Two regulations interacting, resolved automatically.
- **`distinct debits: 1 (submissions: 1)`**. One debit reached the rail from one
  submission. Under injected timeouts this is what proves no double charge.

### Why it runs in two acts

The regulator requires 24 hours between the notice and the debit, and the rule
is evaluated against the real clock. A single cycle therefore takes 24 real
hours. So:

- **Act I** drives a live cycle to a notice that is genuinely sent.
- **Act II** takes a cycle whose notice went out 30 hours ago and fires its
  debit.

Act II's 30-hour-old notice is seeded exactly as the project's own compliance
tests seed one, and is labelled as history rather than as something the system
just did. **No compliance record is backdated to make a rule pass.**

### Watch it in a browser

```
http://localhost:8010/console/login
```

Paste the token the demo prints. You get the live pipeline for that merchant,
refreshing every ten seconds:

- **Now** — mandates, cycles in flight, events and any unprocessed backlog,
  queued actions, decisions split into allowed and refused, and **money
  recovered**.
- **Adoption stage** — where the tenant sits on §44's ramp and what that stage
  permits: what share of mandates it may act on, and how much is held back as a
  permanent control arm.
- **Queue** — what fires next and exactly when, notices and debits alike.
- **Cycles** — amount, attempts used against the budget, whether the 24-hour
  notice has gone out, and what has been recovered.
- **Mandates** — including which arm each is in, so a mandate sitting untouched
  reads as the holdout rather than as a fault.
- **Ledger** — every decision with its verdict, and a link through to the rules
  and citations behind it.

Run `./scripts/demo.sh` in one window with this open in another and watch the
rows appear.

It is **read-only**. Every action goes through the scheduled-action path so it
is re-checked against the rules at the moment it fires and recorded — a button
here would bypass both.

### Look at what it decided

The demo prints a console token and a decision id. Open the decision:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  localhost:8010/console/replay/$DECISION_ID
```

You get the full record: the verdict, the rules consulted with their citations
and versions, the inputs, and its position in the hash chain.

Try it **without** the token — you get `401`. The tenant is taken from a
verified token and never from the URL, so no request can name a tenant it has
not proven it may see.

### Watch it refuse

A demonstration that only shows success proves less than one that shows the
system declining. Two things worth trying:

**A forged webhook.** Change one character of the signature:

```bash
curl -i -X POST localhost:8010/v1/webhooks/razorpay/<tenant> \
  -H "X-Razorpay-Signature: deadbeef" \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.failed"}'
```

`401`, and nothing is written to the event log. Unverified payloads are never
persisted — not stored-then-checked.

**A debit with no notice.** Delete the `pdn_sent_at` on a cycle and let the
executor claim its debit. The gate returns `DENY` on `RBI-EMANDATE-PDN-24H`, the
action is cancelled, and **the denial is still written to the ledger** — because
a refusal is the evidence the gate works.

### Also worth showing

The compliance audit runs with no database, no credentials and no network:

```bash
uv run prayas-audit
```

It measures what the industry-standard retry schedule — days 1, 3 and 5 at
10:00 IST — does against the rules currently in force, and reports **30,000
debit attempts outside NPCI execution windows and 30,000 debits without valid
24-hour notice, per 10,000 cycles**. It is reproducible by anyone, which is the
point.

### Stopping

```bash
docker compose down          # keeps the data
docker compose down -v       # discards it
```

---

## Where to go next

| You want | Read |
|---|---|
| The full blocker list, with what is internal vs external | [BLOCKERS.md](BLOCKERS.md) |
| To run it locally in more depth | [DEPLOYMENT.md](DEPLOYMENT.md) |
| To deploy to AWS with GitHub CI/CD | [AWS-DEPLOYMENT.md](AWS-DEPLOYMENT.md) |
| Every decision and finding, dated | [../PROGRESS.md](../PROGRESS.md) |
