# PRAYAS — demo day script

Every figure below was read off the running stack on **2026-09-04**, after a
clean `scripts/seed-demo.sh`. Where the build spec's §H1 quoted an
illustrative number, this file carries the real one instead — the spec's
instruction was *correct the script, not the data*.

Numbers move on every reseed, and **a CI run empties the fleet** — the test
fixtures truncate the tenant tables. After either, re-verify and correct this
file:

```bash
uv run python scripts/verify_demo_numbers.py
```

It prints every figure below, at both windows, and says which side of zero
each interval falls on. Exit code is non-zero if the fleet is empty or the
hero cycle shows no recovery. When in doubt, say the figures off the screen
rather than off this page.

---

## Before you start

```bash
docker compose ps          # all six up; api healthy
bash scripts/seed-demo.sh  # ~11 min. Only if the fleet is stale.
```

Then open the console and **check all four services are green** in the header
strip. A missing one fails silently — the stack stays green and stops doing
work, which is precisely the failure this strip exists to catch.

Console token (platform PM sees everything):

```bash
export PRAYAS_CONSOLE_TOKEN_SECRET=local_console_secret_not_for_production
uv run python3 -c "from prayas.console.auth import issue; print(issue('fitfirst','platform_pm',ttl_seconds=28800))"
```

Paste it at `http://localhost:8010/console/login`.

| Screen | URL |
|---|---|
| Portfolio, fleet | `/console/portfolio?tenant=all&window=90d` |
| **Portfolio, FitFirst — use this for beat 2** | `/console/portfolio?tenant=fitfirst&window=90d` |
| The hero cycle | `/console/cycle/cyc_7f3a91` |
| Ledger | `/console/ledger` |
| Ledger, refusals only | `/console/ledger?verdict=refused` |

> **Use `&window=90d`.** The page defaults to 30 days, and at 30 days
> FitFirst's survival interval is **−0.5% to +4.2% — it includes zero**. Beat
> 2's "the interval excludes zero" is only true on the 90-day view. Landing on
> the default and saying that line would be false in front of the room.

---

## The eight beats

### 1 — Portfolio · the problem

> A fifth to two-fifths of Indian subscription churn is involuntary — the
> payment just failed. The reason you can't fix it the way Stripe does is that
> you can't retry more: one execution plus three retries, fixed windows,
> mandatory 24-hour notice. That's not a scheduling problem, it's a budget
> allocation problem.

### 2 — Portfolio · the matched pair

Open **`/console/portfolio?tenant=fitfirst&window=90d`** for this beat — the
fleet view suppresses the intervals deliberately, and the 30-day default puts
survival's interval across zero (see the two callouts above and below).

Read exactly what the screen prints:

| What you say | What is on screen |
|---|---|
| Incremental recovery | **₹25,66,156** |
| 95% CI | **₹24.2L – ₹27.1L** — excludes zero |
| Incremental survival | **+2.8%** |
| 95% CI | **+1.3% – +4.3%** — excludes zero |
| Holdout | **922** (15%), against **5,198** treated |

> ₹25.7 lakh incremental, and the confidence interval excludes zero. Next to
> it, survival — because recovery without survival is how a dunning system
> destroys value while looking like it works. We never show one without the
> other.

Then point at the holdout:

> We deliberately leave 15% of failed cycles alone, permanently. They get
> whatever the existing retry behaviour does and nothing more. That group is
> the only reason the recovery number on this screen means anything, and it's
> a property of the adoption stage rather than a switch someone can forget to
> turn off.

**Efficiency strip, real numbers:**

| Metric | Value | Baseline on screen |
|---|---|---|
| Attempts per recovery | **1.29** | holdout 5.75 |
| Permanent fixes | **428** | date changes proposed |
| Messages per cycle | **0.4** | cap 4 |
| Prevention rate | *not measured* | needs a counterfactual on cycles that did not fail |
| Cost per ₹ recovered | *not measured* | target < 2.0% |

Two of those say **not measured** on screen, and that is the honest reading —
do not fill them in from the spec's illustrative table.

### 3 — Portfolio · multi-tenancy and the rails

Switch tenants in the left rail.

| Tenant | Stage | Mandates | Rail |
|---|---|---|---|
| FitFirst | full | 6,120 | UPI Autopay |
| Streamly | ramp | 4,880 | card e-mandate |
| EdTechCo | canary | 1,200 | eNACH |

> Three merchants, three rails, three adoption stages. Multi-tenant from the
> first commit, row-level security in Postgres, tested as a correctness
> property. EdTechCo is at canary — 1% — so most of its portfolio is untouched
> by design.

**EdTechCo will show survival as suppressed, not as zero.** Its holdout is 0
mandates at canary, and an arm that thin cannot support a comparison. Say so —
it is the strongest possible demonstration that the number is not decorative:

> There's no survival figure for EdTechCo because at 1% its holdout is empty,
> and a rate over an empty arm isn't a rate. The screen suppresses it rather
> than printing a zero.

Then the rails strip. **This needs `tenant=all`** — on a single merchant it
correctly reads "UPI Autopay 100%", which is true and tells the wrong story.
At fleet scope it reads **UPI Autopay 50%, Card e-mandate 40%, eNACH 10%**:

> Three Razorpay rails, one engine. We do not abstract over processors. That's
> a locked decision, not an omission.

### 4 — Cycle · the core

`/console/cycle/cyc_7f3a91`

Header reads **FitFirst · ₹2,499 · UPI Autopay · treatment arm**, budget
**1 of 4 used**. The timeline, in IST:

| When | What |
|---|---|
| **02 Sep 07:32** | Payment failed |
| **04 Sep 08:30** | Pre-debit notice sent |
| **05 Sep 09:30** | Debit fired — **25h of notice, floor is 24h** |
| **05 Sep 09:33** | Payment captured |

"Why this hour" panel: notice given **25h**, binding constraint
**`RBI-EMANDATE-PDN-24H`**, deadline **04 Oct**.

> **One thing to have an answer for.** The budget reads *1 of 4 used* while
> the decision rationale reads *fired attempt 2 of 4*. Both are true from
> where they sit: the ledger recorded the attempt number at fire time, and the
> projection counts attempts back from the events the rail actually returned.
> If asked, say that — do not improvise a reconciliation.

Point at the shaded bands first:

> Those are the NPCI execution windows, drawn from the same rail adapter the
> sequencer solved against — not a picture of the rule, the rule itself. Here's
> the notice floor. Here's the attempt budget.

Then the time travel controls, then read the "why this hour" panel aloud:

> The engine moved the debit later so a lawful notice could precede it. Two
> regulations interacting, resolved automatically. Twenty-five hours of notice
> against a twenty-four hour floor.

### 5 — Terminal · the refusal

**Do not cut this beat.**

Forged webhook:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST http://localhost:8010/v1/webhooks/razorpay/fitfirst \
  -H 'X-Razorpay-Signature: forged' -H 'X-Razorpay-Event-Id: evt_forged' \
  -H 'Content-Type: application/json' -d '{"event":"payment.captured"}'
```

Expect **401**.

> Unverified payloads are never persisted — not stored-then-checked.

Then the ledger's refusals. This fleet carries **222 genuine refusals**, none
of them manufactured — 1,026 of 1,246 debits were allowed, so the gate is
refusing about one in six:

| Rule | Refusals |
|---|---|
| `NPCI-AUTOPAY-WINDOW` | 98 |
| `RBI-EMANDATE-PDN-24H` | 93 |
| `DPDP-CONSENT-VALID` | 30 |
| `RBI-EMANDATE-AFA-CAP` | 1 |

> A demonstration that only shows success proves less than one that shows the
> system declining. The gate denied this debit because the notice wasn't valid,
> the action was cancelled, and the denial is still in the ledger — because a
> refusal is the evidence the gate works.

### 6 — Ledger · the drawer

`/console/ledger` — then click **Refused**, and open the top row. Do not open
an allowance here; the refusal is the stronger artefact and you have already
shown allowances on the cycle screen. (Decision ids change on every reseed, so
navigate by the filter, never by a memorised id.)

- Chain badge reads **chain verified · 2,619 rows**. It is a real walk on every
  page load, not a constant — tamper with a stored record and it turns.
- The drawer shows **4 rules**, each with version, regulator, citation, `as_of`,
  the fire-time instant, and the record's position in the hash chain with both
  hashes.

> Evaluated at fire time, not schedule time — rules and state both move between
> deciding and doing. Four rules, each with a version and a citation. And its
> position in the hash chain.

### 7 — Portfolio · guardrails and cost

At `tenant=all&window=90d`, exactly as printed:

| Guardrail | Value | Threshold |
|---|---|---|
| Compliance violations | **0** ✓ | must be zero |
| Revocation vs control | **−2.80% pts** ✓ | not above control |
| Net value | **₹33,99,712** ✓ | must be positive |

> Zero violations. Revocation below control. Net value positive. All reported
> unprompted, including when unflattering.

Then, spoken (not on screen):

> Eight milliseconds a decision. Single-digit cores at ten million mandates.
> The expensive part is messaging, and the fatigue cap is a cost control as
> well as a customer protection.

### 8 — The ask

> Everything you've seen runs today. What it hasn't had is your data. The
> smallest useful next step is Stage 0 — observe only, ingest webhooks, fire
> nothing, one merchant, two weeks. At the end you get the compliance audit on
> real cycles and a projection with a confidence interval. If the number isn't
> there, you've spent two weeks of webhook traffic.

Then table, without pressing: the compliance rule pack is being open-sourced
with citations and `as_of` dates regardless.

---

## What the screen will not show you

Know these before someone asks, because each is a deliberate suppression
rather than a gap:

| Where | What is missing | Why |
|---|---|---|
| Portfolio, `tenant=all` | Both intervals — screen reads *"interval suppressed"* | Intervals do not add. Summing them produces something that looks like a confidence statement and is not one. Switch to a single tenant to show a CI. |
| Portfolio, `tenant=all` | Attempts per recovery, messages per cycle — *"not measurable at this scope"* | Rates across merchants of different sizes are not averageable. |
| Portfolio, default 30-day window | Survival's interval spans zero on FitFirst | Genuinely does at 30 days. Use `&window=90d`, or say it plainly. |
| EdTechCo | Survival lift and its interval | Holdout of 0 at canary. A rate over an empty arm is not a rate. |
| Efficiency strip | Prevention rate, cost per rupee | Not measured by this build. Shown as *not measured*, never as zero. |
| Guardrails | Opt-out vs control | Three guardrails present, not the spec's four. |

---

## One number to decide before you walk in

FitFirst's treated arm recovers **84.1%** of failed cycles against a holdout of
**17.4%** — a 67-point lift. §R3.2 of the build spec targets ~62% treatment
recovery precisely because *"a perfect rate reads as fabricated and invites the
wrong kind of scrutiny."*

84% is not perfect, and the holdout comparison is real and hard-won. But it is
above the spec's own target, and a room of payments people may well push on it.
Two honest options:

1. **Present as is**, and be ready to explain that the seeded population is
   synthetic and calibrated, that the holdout is what makes the *lift*
   meaningful rather than the level, and that the level itself is not the claim.
2. **Re-calibrate before the demo** by lowering `TREATMENT_RECOVERY` (currently
   0.78) and/or `LIVE_CAPTURE_RATE` (0.70) in `scripts/seed_demo.py`, then
   reseeding — about 11 minutes, plus re-reading every figure on this page.

This is a judgement call about how conservative you want the headline, not a
defect. Decide it before the room, not in it.

---

## Cut list, decided in advance

| Order | Cut | Cost |
|---|---|---|
| 1 | Live event ticker | Small loss of liveness |
| 2 | Capacity numbers on screen | Say them instead |
| 3 | Rails strip | Say it instead — but *say* it |
| 4 | Tenant switcher | Real loss; multi-tenancy becomes a claim |
| 5 | Time travel → replay | None, if narrated |
| 6 | Replay drawer | **Do not cut** |
| 7 | Refusal demo | **Do not cut** |
| 8 | Matched pair | **Do not cut** |

## If something breaks

| Risk | Move |
|---|---|
| A service is down | Health strip in the header — check four green before you start |
| Something breaks live | Screen recording, on the local disk |
| Network fails | Everything is `localhost`; no CDN, no external fetch anywhere in the console |
| Laptop dies | Second machine, or the video on a phone |
| Seed stale or half-applied | `bash scripts/seed-demo.sh`, then re-verify every figure above |
| You run long | Cut in the order above — never mid-sentence |

---

## Questions they will ask

Two sentences, then stop.

| Question | Answer |
|---|---|
| Doesn't this compete with Optimizer? | Different axis. Optimizer routes live traffic for success rate; PRAYAS allocates a fixed attempt budget in time on already-failed recurring debits. |
| What if the model is wrong? | It degrades to a segment lookup, then a baseline calendar, and every rung still passes the gate and still writes to the ledger. |
| Could it double-charge? | Atomic budget decrement with the rowcount checked, idempotency key on the attempt, intent durable before any provider call. |
| How do you know the recovery is incremental? | The holdout, and it's never retired. Assignment frozen against a seed committed to git before the run. |
| Where does customer data live? | `ap-south-1`, as an invariant. No PANs, no bank credentials, no balances — tokens and mandate IDs only. |
| What happens when RBI changes a rule? | Rules are versioned data with an `as_of`, in a separately deployable service. |
| Integration cost for merchants? | Zero. Webhooks that already exist, APIs that already exist. |
| Is this production-ready? | The engine paths are. Two things outstanding, both needing someone outside the code: a DLT-registered template, and a live authorised mandate. |

**If you don't know, say so and write it down.** A confident wrong answer about
NPCI is more damaging than an unanswered question.

---

## The sentence to deliver exactly

On the audit finding — the highest-risk line in the demo:

> The day-1/3/5 retry schedule is the documented default in dunning tools
> generally — it's what the category inherited from card rails, where retries
> are cheap. Run against the e-mandate framework currently in force, it
> produces roughly 30,000 out-of-window attempts and 30,000 debits without
> valid 24-hour notice per 10,000 cycles. That's an ecosystem finding, not a
> finding about anyone in this room, and the rule pack that produces it is
> being open-sourced with its citations either way.
