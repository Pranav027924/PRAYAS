# PRAYAS
### A liquidity-aware, budget-constrained recovery engine for recurring debits in India

> **प्रयास** — *attempt*.
> **Tagline:** *You get four attempts. Spend them well.*

**Track:** Razorpay AI Buildathon — Track 03, AI Revenue Recovery
**Primary scenario:** #5 Mandate retry sequencer, fused with #3 Failed-subscription recovery
**One-line pitch:** Recurring debits in India get a hard, regulator-set budget of attempts. Everyone spends that budget on a calendar. Prayas spends it on a *forecast of when the customer's account will actually have money* — and spends zero attempts when it never will.

---

## 0. The 30-second version

Every failed UPI Autopay debit gets **1 execution + up to 3 retries**, executable **only in non-peak windows**, each one requiring a **pre-debit notification ≥24 hours in advance**. That is four shots, pre-committed a day ahead, inside fixed windows, per cycle. Then the mandate dies.

Industry-standard dunning ignores all of this and retries on day 1, 3, 5. That is a calendar pretending to be a strategy.

Prayas treats the four attempts as **scarce capital**. It builds a per-customer *liquidity hazard curve* — when is this account likely to be funded — from payment history alone, infers the true cause behind an uninformative decline code, forecasts issuer health from ecosystem-wide signal, and then solves for the attempt schedule that maximises expected recovery subject to the retry budget, the 24-hour notice latency, the execution windows, and the cost of each attempt.

**Stopping is not a hardcoded rule. It falls out of the optimisation** — when the marginal expected value of attempt #3 drops below its cost, the loop stops and says why, in rupees.

And the sharpest move: the pre-debit notification is legally mandatory, guaranteed-delivery, and **free**. Prayas uses it as the primary intervention lever, not compliance overhead — preventing failures rather than recovering them.

---

## 1. Why this wins the room

Your brief says the differentiator is *"not the detection model — it's the holdout-measured recovery number plus the compliance gate plus the audit trail."* Agreed. But every serious team will attempt those three. Here is what separates Prayas beyond the table stakes.

| | Typical Track 03 submission | Prayas |
|---|---|---|
| **Core framing** | "LLM picks a retry day" | Budgeted optimal stopping over a survival model |
| **Attempts** | Unlimited / fixed calendar | Scarce capital with an explicit shadow price in ₹ |
| **Stopping rules** | Hardcoded `if retries > 3: stop` | Emerges from marginal value < marginal cost |
| **Decline code** | Taken at face value | Latent-cause posterior; code 05 explicitly disambiguated |
| **Failure attribution** | Assumes customer-side | Separates "no money" from "issuer having a bad hour" |
| **Pre-debit notification** | Compliance checkbox | Primary lever; **prevention** reported as a first-class metric |
| **Headline number** | "We recovered 80%!" | Incremental lift vs. pre-registered holdout, with a confidence interval |
| **LLM role** | Makes the money decision | Explains, composes, and parses — never decides |
| **Measurement** | Holdout bolted on at the end | Propensity-logged from decision one; off-policy evaluation |

The four claims to say out loud in the pitch:

1. **Attempts are scarce capital, not a schedule.** Nobody else will frame the problem this way, and it is the *correct* framing given NPCI's cap.
2. **We shift left.** The cheapest recovered rupee is the failure that never happened. We report *failures prevented* alongside *failures recovered*.
3. **We don't trust the decline code.** Code 05 is 30–40% of all declines and roughly half are insufficient funds wearing a disguise. We infer the latent cause and validate that inference against simulator ground truth.
4. **Our number has a confidence interval.** Incrementality measured against a pre-registered, seed-committed holdout, with variance reduction so it's detectable at hackathon batch sizes.

---

## 2. The problem, stated precisely

**Given:** a mandate that has just failed its scheduled debit, at time `t₀`, for amount `A`, on rail `r`, with decline code `c`, on issuer `i`.

**Choose:** a set of attempt times `t₁ < t₂ < … < t_k` and a set of notification/nudge actions, to maximise expected net recovery.

**Subject to:**

| Constraint | Value | Source |
|---|---|---|
| Attempt budget `B` | UPI Autopay: 1 execution + 3 retries | NPCI |
| Execution windows | Before 10:00, 13:00–17:00, after 21:30 IST | NPCI (non-peak enforcement) |
| Notice lead time `L` | ≥ 24h pre-debit notification before every debit | RBI E-mandate Framework 2026 |
| PDN cutoff | Requests at/after 23:50 for a `T+1` debit are rejected | NPCI operating guidelines |
| AFA-free ceiling | ₹15,000 (₹1,00,000 for insurance / MF / credit-card bills) | RBI E-mandate Framework 2026 |
| Contact window | 08:00–19:00 IST, all channels, no harassment | RBI Fair Practices Code |
| Messaging | DLT-registered template, correct header series, DND honoured, consent on record | TRAI TCCCPR 2018 |
| Data use | Consent-bound, purpose-limited, withdrawable | DPDP Act 2023 |
| Cycle deadline `T` | Before mandate revocation / subscription halt | Rail + merchant policy |

> ⚠️ **Verify every threshold at build time and stamp it with an `as_of` date.** Your brief is right that this epistemic honesty is itself part of the bar. In Prayas it isn't a disclaimer — every compliance rule is a versioned data record carrying its own citation and `as_of` field, and the audit log records which version was in force at decision time.

**This is not a scheduling problem. It is a finite-horizon, budget-constrained optimal stopping problem under a time-varying hazard.** That reframe is the entire project.

---

## 3. Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │            AUDIT LEDGER (hash-chained)      │
                    │   every decision, fired or denied, replayable│
                    └─────────────────────────────────────────────┘
                                        ▲
   Razorpay webhooks                    │ writes
   ─────────────────                    │
   subscription.pending  ┌──────────────┴───────────────┐
   subscription.halted   │                              │
   payment.failed     ─► │  ① EVENT SPINE               │
   payment.captured      │  dedupe · idempotency ·      │
   payment.downtime.*    │  late-capture guard          │
   invoice.*             └──────────────┬───────────────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
        ┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐
        │ ② CAUSE        │    │ ③ LIQUIDITY      │   │ ④ ISSUER         │
        │   INFERENCE    │    │   HAZARD MODEL   │   │   NOWCAST        │
        │ posterior over │    │ P(funded | t)    │   │ health(bank, t)  │
        │ true cause     │    │ survival model   │   │ + recovery ETA   │
        └────────┬───────┘    └────────┬─────────┘   └────────┬─────────┘
                 └──────────────────────┼──────────────────────┘
                                        ▼
                          ┌─────────────────────────────┐
                          │  ⑤ THE SEQUENCER            │
                          │  DP over (attempts left,    │
                          │  time, cause posterior)     │
                          │  → attempt times OR stop    │
                          └──────────────┬──────────────┘
                                         ▼
                          ┌─────────────────────────────┐
                          │  ⑥ NOTIFICATION OPTIMIZER   │
                          │  when / channel / content   │
                          │  (the shift-left lever)     │
                          └──────────────┬──────────────┘
                                         ▼
                    ┌────────────────────────────────────────┐
                    │  ⑦ COMPLIANCE GATE                     │
                    │  ALLOW / DENY(rule, citation) / ESCALATE│
                    └──────────────┬─────────────────────────┘
                                   ▼
                    ┌────────────────────────────────────────┐
                    │  ⑧ DURABLE EXECUTOR                    │
                    │  idempotent · revalidate-at-fire-time  │
                    └──────────────┬─────────────────────────┘
                                   ▼
                    ┌────────────────────────────────────────┐
                    │  ⑨ MEASUREMENT PLANE                   │
                    │  holdout · CUPED · off-policy eval      │
                    └────────────────────────────────────────┘
```

---

## 4. Components in detail

### ① Event spine

Ingests Razorpay webhooks. Three production details that most submissions will get wrong, and which your brief explicitly flags:

- **At-least-once delivery** → dedupe on event ID, idempotency keys on every downstream action.
- **`payment.failed` can be followed by `payment.captured` for the same transaction** (late authorisation, or an in-app UPI retry by the customer). A naive agent "recovers" a payment the customer already made. Prayas revalidates state at fire time, not at schedule time.
- **Ordering is not guaranteed.** State is reconstructed from an event log, not mutated in place.

The entity model: `Mandate → Cycle → Attempt`. A *cycle* owns the attempt budget. This is the unit of randomisation, the unit of measurement, and the unit of audit.

### ② Cause inference layer

Maps `(decline code, context) → posterior over true cause`.

| Cause | Recoverable? | Right action |
|---|---|---|
| `no_funds` | Yes, time-dependent | Retry at forecast funding time |
| `issuer_degraded` | Yes, short horizon | Wait for recovery ETA, retry |
| `fraud_hold` | Sometimes | Reduce frequency; nudge customer to authorise |
| `limit_breach` | Yes | Nudge for AFA / split / alternate rail |
| `mandate_dead` | No | **Stop.** Re-enrolment flow |
| `credential_dead` | No | **Stop.** Card updater / new method |

Hard codes (41, 43, 14, revoked mandate) resolve deterministically → STOP, no model needed. The interesting case is **code 05, "Do Not Honor"** — 30–40% of all declines and the least informative signal in payments.

Prayas infers the latent cause behind an 05 from context:

- Is this issuer currently degraded? *(from ④ — if the whole bank is failing right now, it's not this customer's balance)*
- Amount relative to this customer's historical successful debits
- Day of month relative to their inferred funding cycle
- Did this customer's *other* mandates fail in the same window? *(customer-side)*
- Did other customers on this issuer succeed in the same window? *(rules out issuer-side)*

**Training signal:** the eventual retry outcome is a noisy label for the latent cause. An 05 that clears two days later at the same amount on the 1st of the month is almost certainly `no_funds`. An 05 that clears five minutes later on a different route is `issuer_degraded`. Trained semi-supervised with EM over the latent cause.

**Validation:** the simulator (§6) emits ground-truth causes, so you can report actual confusion-matrix accuracy on cause inference — something you fundamentally *cannot* do on real data. This is a strong, honest use of synthetic data.

### ③ Liquidity hazard model — the technical core

**Question:** for this customer, what is `P(account funded ≥ A)` as a function of time over the next 30 days?

**Why survival analysis, not classification.** The label is censored: if you never retry at hour *t*, you never observe whether it would have succeeded. And what the sequencer needs is a *time-indexed hazard curve*, not a single score. A discrete-time hazard model handles both.

**Formulation.** One row per `(cycle, candidate hour)`; label = funded at that hour. Predict `h(t) = P(funded at t | not funded before t)`. Survival `S(t) = ∏(1 − h(u))`. The sequencer consumes `1 − S(t)`.

**Features — observable history only, no bank balance access:**

- Day-of-month distribution of this customer's *historical successful* debits (payday proxy)
- Empirical fail→success lag from prior cycles
- Amount vs. their own successful-debit distribution
- Hour-of-day and weekday success pattern
- Rail, ticket band, merchant category, city tier
- Number of concurrent mandates on the same account *(competition for the same rupees — a genuinely underused signal)*
- Any customer-declared funding date parsed from an inbound reply *(see §5)*

**Models.** V0: per-segment empirical hazard (a lookup table; good enough to beat the calendar and ships in a day). V1: gradient-boosted discrete-time hazard. **Hierarchical partial pooling** for cold start — new customers shrink toward `merchant × category × ticket-band` priors, then toward global.

**Calibration is the thing that matters**, not AUC. The sequencer consumes probabilities as expected values, so miscalibration directly corrupts the money decision. Report reliability curves and expected calibration error, and monitor calibration drift in production. Saying this out loud signals you've actually deployed a model before.

**No PII beyond what the merchant already holds.** This is your DPDP story, and it's clean.

### ④ Issuer nowcast

Streaming success rate per `(issuer, rail, method)` over sliding windows:

- **Wilson lower bound** rather than raw rate, so a low-volume bank with 3 failures doesn't get declared down.
- **CUSUM change-point detection** for degradation onset *and* recovery.
- Consumes `payment.downtime.*` as a supervisory signal but does not depend on it — platform downtime flags are coarse and lag the actual event.
- Outputs `health(bank, t)` plus a **recovery ETA** from historical outage-duration distributions.

This is what lets the sequencer say: *"Don't burn an attempt now. HDFC is degraded; median recovery is 40 minutes; the next legal window opens at 13:00 anyway."*

This is also the component that only makes sense at platform scale — a natural "this belongs at Razorpay" argument.

### ⑤ The sequencer

**Objective.** Choose attempt times `t₁ … t_k`, `k ≤ B`, maximising:

```
E[net recovery] = A · P(at least one attempt succeeds)
                  − Σᵢ cost(tᵢ)
                  − λ · annoyance(schedule)
                  − μ · P(mandate revoked | schedule)
```

where `P(success at tᵢ)` combines the liquidity hazard, the cause posterior, and issuer health at `tᵢ`.

**Solution: dynamic programming** over discretised time. State = `(attempts remaining, current time, cause posterior)`. With `B ≤ 4` and a 30-day horizon at hourly resolution (~720 slots), the exact DP is trivially fast. Value function:

```
V(b, t) = max over legal t' > t + L of:
            p(t') · A  +  (1 − p(t')) · V(b−1, t')  −  cost(t')
          versus STOP: 0
```

**Why not reinforcement learning — and say this in the pitch.** With four attempts per cycle, monthly cadence, and multi-day delayed labels, you do not have the sample efficiency for RL. A DP over a calibrated hazard model is optimal under the model, three orders of magnitude cheaper, and — critically — **auditable**: you can print the value of every branch. Choosing the simpler correct tool over the flashier one is exactly the judgment call senior engineers make, and judges notice.

**Stopping falls out of the math.** When `V(b, t) ≤ 0` — the marginal expected value of the next attempt is below its cost — the loop stops, and the audit log records *"stopped: EV of attempt 3 = ₹4.20 < cost ₹6.50."* Compare that to a hardcoded `if retries > 3`. This directly and elegantly satisfies the bar's stopping-rules requirement.

**Hard-stop overrides** still exist as compliance rules, not economics: hard decline, mandate revoked, customer opted out, disputed invoice, budget exhausted.

### ⑥ Notification optimizer — the shift-left lever

**This is the unique reframe. Do not bury it.**

RBI *requires* a pre-debit notification ≥24h before every recurring debit, containing merchant name, amount, date, and an opt-out. Every other team will treat this as a compliance checkbox. It is in fact:

- **Legally guaranteed delivery** — the debit is blocked without it
- **Free** — costs no attempt from the NPCI budget
- **Pre-failure** — it lands *before* the money is lost

So Prayas optimises it. For each upcoming debit, it predicts failure risk from the hazard model and chooses:

- **Timing** — as close to the ≥24h floor as legally allowed, and aligned to the customer's attention pattern. A notice sent 72h early is forgotten; one sent at the 24h boundary, the evening before payday lands, is acted on. *(Mind the 23:50 cutoff for `T+1` debits.)*
- **Channel** — DLT-registered template, 160-series transactional header, DND status checked, consent artifact referenced.
- **Content** — required RBI fields, plus, for high-risk mandates, a **one-tap alternate payment path** so the customer can pay before the debit even runs.
- **Escalation** — high-risk + high-value → offer a date change or partial collection rather than letting the debit fail and burning the budget.

**New first-class metric: prevention rate.** *"N debits that our model flagged as high-risk succeeded on first execution after an optimised notification, versus the holdout's baseline notification."*

No other submission will report a prevention number, because no other submission will have noticed that the compliance requirement is also the highest-leverage channel in the system.

### ⑦ Compliance gate

Every outbound action — debit, SMS, WhatsApp, call, link — passes through a declarative gate before firing.

**Rules are versioned data, not code.** Each rule carries `{rule_id, predicate, regulator, citation, as_of, version}`. When NPCI changes an execution window, you ship a YAML change, not a deploy — and the audit log still shows which version governed a decision made last week.

```yaml
- rule_id: NPCI-AUTOPAY-WINDOW
  regulator: NPCI
  citation: "UPI Autopay non-peak execution windows"
  as_of: 2026-08-01
  applies_to: [debit_attempt]
  predicate: "hour_ist < 10 or 13 <= hour_ist < 17 or hour_ist >= 21.5"
  on_fail: DENY

- rule_id: RBI-EMANDATE-PDN-24H
  regulator: RBI
  citation: "Digital Payments – E-mandate Framework, 2026"
  as_of: 2026-04-21
  applies_to: [debit_attempt]
  predicate: "pdn_sent_at is not null and now - pdn_sent_at >= 24h"
  on_fail: DENY

- rule_id: RBI-EMANDATE-AFA-CAP
  regulator: RBI
  citation: "Digital Payments – E-mandate Framework, 2026"
  as_of: 2026-04-21
  applies_to: [debit_attempt]
  predicate: "amount <= afa_free_cap(mcc)"   # 15000, or 100000 for insurance/MF/CC-bill
  on_fail: ESCALATE_TO_AFA_FLOW

- rule_id: RBI-FPC-CONTACT-WINDOW
  regulator: RBI
  citation: "Fair Practices Code, circular 12 Aug 2022"
  as_of: 2022-08-12
  applies_to: [sms, whatsapp, voice]
  predicate: "8 <= hour_ist < 19"
  on_fail: DEFER

- rule_id: TRAI-DLT-TEMPLATE
  regulator: TRAI
  citation: "TCCCPR 2018"
  as_of: 2025-01-01
  applies_to: [sms]
  predicate: "dlt_template_id is not null and header_series in ['160'] and not dnd_registered"
  on_fail: DENY
```

Gate returns `ALLOW` / `DENY(rule_id, citation)` / `ESCALATE_HUMAN` / `DEFER(until)`.

**Log the denials as loudly as the approvals.** A denied action is the *proof* that the gate works. Your demo should show the counter of blocked violations climbing.

**Killer comparison slide:** run the naive day-1/3/5 baseline through the same gate in shadow mode. *"The industry-standard policy would have committed 213 compliance violations across this batch — retries outside execution windows, debits without valid pre-debit notice, messages to DND numbers. Prayas committed zero."*

### ⑧ Durable executor

Scheduled actions fire hours or days later, so this is a scheduling system, not a request/response service. Temporal-style durable workflows in production; durable timers + a transactional outbox is sufficient for the hackathon.

Every action carries:

- an **idempotency key** — *double-debiting a customer is the catastrophic failure mode of this entire product, and you should say so explicitly*
- a **revalidation step at fire time** — is it still owed? mandate still active? did a late capture land? did the customer pay via the link?
- **compensation** on partial failure
- **propensity logging** — record `P(action | state)` at decision time, so off-policy evaluation is valid later. This is the single most-forgotten detail in production ML, and mentioning it marks you as someone who has shipped.

### ⑨ Audit ledger

Append-only, **hash-chained** (each record embeds the hash of its predecessor → tamper-evident). One record per decision:

```json
{
  "decision_id": "dec_01J...",
  "prev_hash": "sha256:...",
  "ts": "2026-08-22T13:04:11+05:30",
  "trigger_event_id": "evt_...",
  "entity": {"mandate_id": "...", "cycle_id": "...", "amount": 249900},
  "feature_snapshot_ref": "s3://.../fs_01J...",
  "model_versions": {"hazard": "v1.3.0", "cause": "v0.9.2", "nowcast": "v1.0.1"},
  "cause_posterior": {"no_funds": 0.71, "issuer_degraded": 0.18, "fraud_hold": 0.07, "...": 0.04},
  "candidate_actions": [
    {"action": "retry", "at": "2026-08-24T09:10+05:30", "ev_paise": 118400},
    {"action": "retry", "at": "2026-08-23T14:00+05:30", "ev_paise":  61200},
    {"action": "stop",  "ev_paise": 0}
  ],
  "chosen_action": "retry@2026-08-24T09:10+05:30",
  "rationale": "Liquidity hazard peaks day-of-month 1 (salary credit, p=0.63); issuer healthy; attempt 2 of 4.",
  "compliance_checks": [
    {"rule_id": "NPCI-AUTOPAY-WINDOW", "version": 3, "as_of": "2026-08-01", "verdict": "ALLOW"},
    {"rule_id": "RBI-EMANDATE-PDN-24H", "version": 2, "as_of": "2026-04-21", "verdict": "ALLOW"}
  ],
  "holdout_arm": "treatment",
  "propensity": 0.84,
  "outcome": "success",
  "outcome_ts": "2026-08-24T09:10:47+05:30",
  "recovered_paise": 249900
}
```

**Build a `/replay/{decision_id}` endpoint.** A judge clicks any single recovered rupee and sees the entire chain — trigger, features, posterior, alternatives considered with their expected values, rules checked with citations, outcome. That is your demo money-shot, and it satisfies the audit-trail requirement in a way a log file never will.

### ⑩ Measurement plane — where you actually win

The bar's first clause is *"measured money recovered across a batch."* This is where most teams will hand-wave.

**Pre-registered holdout.** Randomise at the **customer** level, not the cycle or attempt level — one customer may hold mandates with several merchants, and cycle-level randomisation leaks. Stratify by merchant, rail, and ticket band. Commit the seed to git *before* the run and show the commit timestamp. 15% control, running the naive day-1/3/5 baseline.

**Primary metric:** incremental recovery rate and incremental ₹, **with a confidence interval**. A team reporting `+11.3pp, 95% CI [7.1, 15.5]` instantly outclasses one reporting "80% recovery!"

**CUPED variance reduction** using pre-period recovery rate as the covariate. This matters practically: it lets you detect a real effect on a hackathon-sized batch instead of shrugging at a wide interval.

**Off-policy evaluation** (inverse propensity scoring + doubly-robust) on the logged data, so you can honestly compare policies you *didn't* run live. This is why ⑧ logs propensities.

**Guardrail metrics — report these unprompted, because it shows maturity:**

| Metric | Why it matters |
|---|---|
| Attempts consumed per recovery | Efficiency. Baseline burns ~3.4; target <2 |
| **Mandate revocation rate** | Recovery that kills the mandate is fake recovery |
| Compliance violations | Must be exactly 0 |
| Prevention rate | Failures avoided via optimised PDN |
| Cost per ₹ recovered | Fees + fines + messaging |
| **Net revenue** | Recovered − costs − churn induced by over-messaging |

The mandate-revocation guardrail is important and subtle: your brief notes ~20 million UPI Autopay mandates are revoked monthly over insufficient balances. A recovery system that boosts this month's collection while killing the mandate has destroyed lifetime value. Measure it.

---

## 5. Where the LLM actually belongs

This is an *AI* track, so be deliberate — and be ready for the question *"why is this an AI project and not a scheduler?"*

**Not in the money decision.** Retry timing needs calibrated probabilities, determinism, and auditability. An LLM gives you none of the three. Say this confidently; the restraint is the point.

**Yes in four places:**

1. **Message composition** — Hinglish register-matching within DLT-approved template slots. The template is fixed by law; the variable slots and the register are not.
2. **Explanation** — turning an audit record into plain English for the merchant console, strictly grounded in the record. LLM as narrator, never as decision-maker.
3. **Policy parsing** — merchant types *"never chase invoices under ₹500, always escalate above ₹50k, no messages on Sundays"* → structured compliance-gate rules, surfaced for human confirmation before activation.
4. **Inbound reply understanding — the best one.** Customer replies *"salary 5 tarikh ko aati hai, uske baad try karna"*. An LLM extracts a structured liquidity hint `{funding_day: 5, confidence: high, source: customer_declared}` and feeds it as a **strong prior into the hazard model**.

That fourth one is your answer to the "why AI" question: the customer *tells you their payday in free-form code-switched Hindi-English*, and that natural-language signal becomes a feature in a calibrated survival model that a rule engine could never have acquired. LLM does what only an LLM can do; the statistical model does what only a statistical model should do. That division of labour *is* the sophisticated answer.

---

## 6. The data problem — turn it into a strength

You won't have real mandate data. Don't apologise; engineer around it.

**Hybrid: simulator for statistical claims, Razorpay test mode for integration truth.**

### The simulator

A documented generative process for mandate cycles:

- **Customer liquidity arrival** — mixture over day-of-month (salaried spike on 1st/7th; gig-income irregular tail; a "chronically dry" segment that should never be retried)
- **Issuer outages** — Poisson onset, log-normal duration, correlated across customers on the same bank
- **Decline-code emission conditioned on true cause** — including the **05-masking mechanism**, so ~half of `no_funds` events surface as "Do Not Honor"
- **Mandate revocation dynamics** — revocation hazard rising with consecutive failures and with over-messaging
- **Customer response** — probability of acting on a nudge, decaying with message fatigue

**The simulator gives you ground-truth latent causes.** That's exactly what real data cannot give you, and it's how you validate the cause-inference layer with a real confusion matrix.

### Robustness — the move that separates you

Publish the simulator parameters and **perturb them in front of the judges**. Shift the payday distribution. Triple the outage rate. Make 70% of customers gig-income instead of salaried.

*"Under a payday-distribution shift the model never saw, Prayas still beats the calendar baseline by X pp."*

That is a rigour signal almost nobody in a hackathon will produce, and it pre-empts the sharpest possible critique: *"you built a simulator and then beat your own simulator."*

### Live arm

Run the full loop against **Razorpay test mode** — real subscriptions, real webhooks, real payment links, real signature verification — on a smaller batch (~20–50 mandates). Simulator for the statistics; test mode for the proof that it's wired to reality, not a notebook.

---

## 7. Failure modes — name them before the judges do

| Risk | Mitigation |
|---|---|
| **Double debit** | Idempotency keys + fire-time revalidation. The catastrophic failure; call it out first. |
| Recovering an already-paid transaction | Late-capture guard; `payment.failed` → `payment.captured` sequence handled explicitly |
| Over-retrying trips issuer fraud models | Attempt budget + explicit cost term + revocation guardrail metric |
| Rules change (NPCI/RBI) | Rules as versioned data with `as_of`; audit records which version applied |
| Model drift | Calibration monitoring (reliability curves, PSI), not just AUC |
| Cold start, new merchant | Hierarchical priors: customer → merchant×category → global |
| Holdout contamination | Randomise at customer level; document residual interference |
| Learning from your own logs (selection bias) | Propensity logged at decision time → valid IPS / doubly-robust evaluation |
| Simulator overfitting | Parameter perturbation robustness suite (§6) |
| LLM hallucinating a compliance rule | LLM output is a *proposal*; the gate only executes human-confirmed rules |

---

## 8. Scope — what to actually build

**Must-have (the judged loop — cut anything else before you cut these):**

- Event spine with dedupe + late-capture guard
- Cause inference: deterministic hard/soft split + a code-05 classifier
- Hazard model at **V0 = per-segment empirical hazard** *(a lookup table beats the calendar; ship this first)*
- DP sequencer with economic stopping
- Compliance gate with versioned, cited rules
- Notification optimizer
- Hash-chained audit ledger + `/replay` endpoint
- Holdout measurement with confidence intervals
- Batch: ~5,000–10,000 simulated cycles + ~20 live test-mode mandates

**Stretch, in priority order:**

1. Issuer nowcast with change-point detection
2. GBM discrete-time hazard (upgrade from V0)
3. Inbound NL liquidity parsing
4. Off-policy evaluation
5. Policy simulator in the console
6. Robustness-under-perturbation suite

**Explicitly out of scope — say so, it reads as focus, not omission:**
Voice (that's scenario 6 and a different project), rails beyond UPI Autopay + one comparator, real bank integrations, cart abandonment.

**Suggested build order:** event spine → audit ledger → compliance gate → simulator → V0 hazard → DP sequencer → holdout harness → notification optimizer → console. Note that the **audit ledger and compliance gate come third and fourth, not last.** If you retrofit them, they'll be shallow, and they're half the bar.

---

## 9. The console — three screens, not a dashboard

Your brief warns against dashboard-as-product. So build exactly three purposeful views:

1. **Batch result** — headline number, holdout comparison, confidence interval, guardrail metrics, compliance-violation counter (treatment: 0, shadow baseline: N).
2. **Decision replay** — search any mandate, see the full chain: trigger → features → cause posterior → candidate actions with EVs → rules checked with citations → outcome. This is the screen that wins.
3. **Policy simulator** — drag the cost-per-attempt slider or the annoyance weight λ and watch the recommended policy shift live. Runs instantly because the DP is cheap. It makes the economics *tangible* to a judge in a way no chart does.

---

## 10. Your one honest headline number

> *"Across 8,412 failed mandate cycles, Prayas recovered ₹—, a **+X.X pp incremental lift** over a pre-registered, seed-committed 15% holdout running the industry-standard day-1/3/5 policy (95% CI [a, b], CUPED-adjusted). It used **1.7 attempts per recovery versus the baseline's 3.4**, prevented Y failures outright via optimised pre-debit notifications, held mandate revocation **below** baseline, and committed **zero compliance-gate breaches** across N gated actions — every one of them replayable from a hash-chained audit ledger."*

Parse what that sentence does: **batch** ✓, **incrementality with uncertainty** ✓, **efficiency** ✓, **prevention** ✓, **guardrail against fake recovery** ✓, **compliant escalation** ✓, **stopping rules** *(implied by attempts-per-recovery)* ✓, **audit trail** ✓.

Every clause of the bar, in one breath, with a confidence interval. That's the submission.

---

## 11. Questions you'll get, and the answers

**"Isn't this just smart retries?"**
No. Smart retries pick a better day on an unlimited budget. Prayas solves for the optimal allocation of a *hard-capped, pre-committed* budget under a time-varying hazard, and derives the stopping point economically. The constraint is what makes it a different problem.

**"Why not use an LLM to pick the retry time?"**
Because the decision needs calibrated probabilities and an audit trail, and an LLM provides neither. The LLM does the things only it can do: parse a customer's code-switched reply into a liquidity signal, compose within legal template slots, and explain a decision. See §5.

**"Why not RL?"**
Four attempts per cycle, monthly cadence, multi-day delayed labels. There isn't enough signal, and RL isn't auditable. A DP over a calibrated hazard model is optimal under the model and printable branch by branch. See §5 of the sequencer discussion.

**"Your data is synthetic."**
Partly, and deliberately. The simulator is the only way to get ground-truth latent causes to validate cause inference, and we perturb its parameters to prove we're not just beating our own generator. The loop also runs live against Razorpay test mode.

**"How do you know you recovered money that wouldn't have arrived anyway?"**
Pre-registered, seed-committed, customer-level randomised holdout, with the seed commit visible in git history. That's what the confidence interval is measuring.

**"What if NPCI changes the rules next month?"**
Rules are versioned data with citations and `as_of` dates. Change a YAML file. And the audit log still shows which version governed any past decision — which is the part that actually matters to a regulator.

---

## 12. Positioning: why this belongs at Razorpay specifically

- **The issuer nowcast only works at platform scale.** A single merchant can't distinguish "my customer has no money" from "HDFC is having a bad hour." Razorpay sees the whole ecosystem. This is a capability that is structurally Razorpay's and nobody else's.
- **It slots directly into an existing surface.** Subscriptions already models `pending → halted`. Prayas is the intelligence layer over that state machine, consuming webhooks that already exist.
- **It complements Optimizer rather than duplicating it.** Optimizer improves *routing* on live traffic. Prayas optimises *timing* on failed recurring debits. Different axis, same thesis.
- **It's compliance-native, not compliance-retrofitted** — which, per your own brief, is Razorpay's house style across every Buildathon track.

---

## Appendix — regulatory figures used, with `as_of`

| Constraint | Value | As of | Verify at build |
|---|---|---|---|
| UPI Autopay attempt budget | 1 execution + up to 3 retries | Aug 2025 onward | ✔ NPCI circulars |
| UPI Autopay execution windows | <10:00, 13:00–17:00, >21:30 IST | 2025–26 enforcement | ✔ |
| Pre-debit notification lead | ≥ 24 hours | RBI E-mandate Framework, 21 Apr 2026 | ✔ |
| PDN cutoff for `T+1` debits | Rejected at/after 23:50 | NPCI operating guidelines | ✔ |
| AFA-free recurring cap | ₹15,000 | RBI E-mandate Framework 2026 | ✔ |
| Raised cap (insurance / MF / credit-card bills) | ₹1,00,000 | RBI, from Dec 2023; carried into 2026 framework | ✔ |
| Collections contact window | 08:00–19:00 IST | RBI Fair Practices Code, 12 Aug 2022 | ✔ |
| Commercial messaging | DLT registration, 140 promo / 160 txn, DND, consent | TRAI TCCCPR 2018, amended 2025 | ✔ |
| Personal data | Consent-first, purpose-limited | DPDP Act 2023; Rules notified Nov 2025 | ✔ |

Treat every number here as *verify-at-build-time* and carry the `as_of` into the code. The 2026 e-mandate framework consolidated eight circulars from 2019–2024; thresholds and enforcement dates in this space move regularly.

---

*Prepared as a build proposal against the AI Revenue Recovery problem brief (Razorpay AI Buildathon, Track 03). Vendor-published recovery and uplift figures cited in the source brief are directional, not audited — which is precisely why the holdout exists.*
