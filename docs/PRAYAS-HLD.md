# PRAYAS — High-Level Design

**System:** Liquidity-aware, budget-constrained recovery engine for recurring debits
**Doc type:** HLD (architecture, decisions, capacity, failure model)
**Companion:** `PRAYAS-LLD.md` (schemas, algorithms, contracts)
**Status:** Design for Razorpay AI Buildathon Track 03; production shape with a documented hackathon-scale variant (§14)

---

## 1. Purpose and scope

Prayas decides **when, whether, and how** to re-attempt a failed recurring debit, and **when to stop**, under a hard regulator-imposed attempt budget.

**In scope:** UPI Autopay mandates (primary rail), card e-mandate (comparator rail), the pre-debit notification channel, compliance gating, audit, and incrementality measurement.

**Out of scope:** payment routing (Razorpay Optimizer's job), voice collections, cart abandonment, B2B receivables, real bank integrations, KYC/onboarding.

**The system boundary in one line:** Prayas consumes payment/subscription events and emits *gated, scheduled, idempotent actions* — debit attempts and notifications — plus an immutable record of why each one happened.

---

## 2. Context (C4 level 1)

```
   ┌──────────────┐                                      ┌──────────────┐
   │  Merchant    │  console, policy config, reports     │  Compliance  │
   │  ops user    │◄────────────────────────────────────►│  / audit     │
   └──────────────┘                                      │  reviewer    │
          ▲                                              └──────┬───────┘
          │                                                     │ replay
          ▼                                                     ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                             P R A Y A S                             │
   │   detect → diagnose → decide → gate → execute → measure → explain   │
   └─────────────────────────────────────────────────────────────────────┘
       ▲                    │                    │                 │
       │ webhooks           │ debit attempt      │ PDN / nudge     │ LLM calls
       │ (at-least-once)    │ (idempotent)       │                 │
       ▼                    ▼                    ▼                 ▼
   ┌────────────────────────────────┐   ┌───────────────┐   ┌─────────────┐
   │        Razorpay platform       │   │ Messaging     │   │ LLM         │
   │  Subscriptions · Payments ·    │   │ DLT SMS /     │   │ provider    │
   │  Payment Links · Downtime API  │   │ WhatsApp BSP  │   │             │
   └────────────────────────────────┘   └───────────────┘   └─────────────┘
                  │
                  ▼  (rails Prayas never touches directly)
           NPCI · issuer banks · card networks
```

**Trust boundaries.** Prayas never holds card PANs or bank credentials — it operates on Razorpay tokens and mandate IDs, which keeps it out of PCI-DSS cardholder-data scope and narrows the DPDP surface to identifiers the merchant already lawfully holds. Every outbound side effect crosses the compliance gate first.

---

## 3. Requirements

### 3.1 Functional

| ID | Requirement |
|---|---|
| F1 | Ingest Razorpay webhooks with at-least-once semantics; tolerate duplicates and out-of-order delivery |
| F2 | Reconstruct authoritative `Mandate → Cycle → Attempt` state from the event log |
| F3 | Infer a posterior over the *true* failure cause from decline code + context |
| F4 | Produce a per-customer liquidity hazard curve over a 30-day horizon |
| F5 | Nowcast issuer health per `(issuer, rail, method)` with a recovery ETA |
| F6 | Solve for the optimal attempt schedule under budget, window, and notice constraints |
| F7 | Stop when marginal expected value falls below marginal cost, and record why in ₹ |
| F8 | Optimise pre-debit notification timing, channel, and content |
| F9 | Gate every outbound action against versioned, cited compliance rules |
| F10 | Execute scheduled actions durably, idempotently, revalidating state at fire time |
| F11 | Append every decision — fired **or denied** — to a hash-chained ledger |
| F12 | Measure incremental recovery against a pre-registered holdout with a confidence interval |
| F13 | Replay any decision end to end from its `decision_id` |

### 3.2 Non-functional

| Dimension | Target | Rationale |
|---|---|---|
| Webhook ack latency | p99 < 100 ms | Persist-and-publish only; never process inline |
| Webhook durability | RPO = 0 | A lost `payment.failed` is un-recoverable revenue |
| Decision latency | p99 < 250 ms | Not user-facing, but bounds backlog under burst |
| Timer fire accuracy | ±60 s normal, ±5 s near the 23:50 PDN cutoff | Windows are hours wide; the cutoff is a cliff |
| Ingest availability | 99.95% | Upstream retries help, but bursts are clustered |
| Decision availability | 99.5%, degrades gracefully | Falls back to V0 hazard, then to baseline policy |
| Gate availability | Fails **closed** | A gate outage must deny, never allow |
| Ledger integrity | Verifiable, tamper-evident | Chain verifier runs continuously |
| RTO | < 5 min | Scheduled actions tolerate short delays |
| Double-debit rate | **0**, structurally enforced | The catastrophic failure mode |

---

## 4. Capacity model

Sizing against a platform-scale deployment (~10 M active mandates). The arithmetic matters because it determines whether the DP is affordable — and it is, comfortably.

| Quantity | Derivation | Value |
|---|---|---|
| Active mandates | assumption | 10 M |
| Debits/day (mean) | 10M / 30 | ~333 K |
| Debits/day (peak) | 1st/5th/7th clustering, ~6× mean | ~2 M |
| PDNs/day (peak) | one per debit, ≥24 h ahead | ~2 M |
| Failed cycles/day (peak) | 10% UPI Autopay failure rate | ~200 K |
| Decisions/day (peak) | ~2.5 decisions per failed cycle | ~500 K |
| Decision QPS (peak, spread over window) | 500K / (4 h × 3600) | ~35/s |
| Decision QPS (worst burst) | window-open thundering herd | ~500/s |
| DP cost per decision | O(B·H²) = 4 × 720², vectorised | ~8 ms |
| DP CPU at burst | 500/s × 8 ms | ~4 cores |
| Event ingest (peak) | debits + outcomes + downtime | ~5 M events/day, ~500/s burst |
| Ledger growth | 500 K records/day × ~4 KB | ~2 GB/day, ~700 GB/yr |
| Feature snapshots | 500 K/day × ~2 KB, object store | ~1 GB/day |

**Read this out loud in the pitch:** the entire decision engine fits in single-digit cores. The expensive parts of this system are the messaging pipeline and the durable timer store, not the intelligence. That is a deliberate design property — the DP was chosen partly *because* it is cheap enough to run exactly rather than approximately.

**Burst control.** Execution windows create synchronised load (everyone wants 13:00). The scheduler jitters fire times within the window using a deterministic hash of `cycle_id`, spreading load and incidentally avoiding a correlated hammer on any single issuer.

---

## 5. Container view (C4 level 2)

```
                    ┌───────────────────────────────────────────┐
  Razorpay ────────►│ ingest-svc        (stateless, autoscaled)  │
  webhooks          │ sig verify · dedupe · persist · publish    │
                    └──────────────┬────────────────────────────-┘
                                   │ events.raw
                            ┌──────▼──────┐
                            │   Kafka     │  partitioned by mandate_id
                            └──┬───┬───┬──┘
              ┌────────────────┘   │   └─────────────────┐
              ▼                    ▼                     ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐
     │ state-projector │  │  nowcast-svc    │  │ feature-builder  │
     │ event → state   │  │ Wilson + CUSUM  │  │ online + offline │
     └────────┬────────┘  └────────┬────────┘  └────────┬─────────┘
              │                    │                    │
              │        ┌───────────▼──────┐             │
              │        │ Redis (online)   │◄────────────┘
              │        │ health, features │
              │        └───────────┬──────┘
              ▼                    │
     ┌────────────────────────────-▼───────────────────────┐
     │ decision-svc                                        │
     │  ② cause inference  ③ hazard  ⑤ sequencer  ⑥ notif  │
     └────────────────────────┬────────────────────────────┘
                              │ proposed action
                     ┌────────▼─────────┐
                     │ gate-svc         │  versioned rules, fail-closed
                     │ ALLOW/DENY/DEFER │
                     └────────┬─────────┘
                              │ approved action
                     ┌────────▼─────────┐        ┌──────────────────┐
                     │ executor         │───────►│ outbox → relay   │──► Razorpay
                     │ durable workflow │        │ idempotent calls │──► DLT SMS
                     └────────┬─────────┘        └──────────────────┘
                              │
     ┌────────────────────────▼────────────────────────────┐
     │ PostgreSQL   mandates · cycles · attempts · rules ·  │
     │              decisions (hash-chained ledger)         │
     └────────────────────────┬────────────────────────────┘
                              │
              ┌───────────────┼──────────────────┐
              ▼               ▼                  ▼
     ┌────────────────┐ ┌─────────────┐ ┌──────────────────┐
     │ measurement-svc│ │ console-api │ │ chain-verifier   │
     │ holdout·CUPED· │ │ + explainer │ │ continuous audit │
     │ IPS/DR         │ │ (LLM)       │ │                  │
     └────────────────┘ └─────────────┘ └──────────────────┘
```

### Service responsibilities

| Service | Owns | Stateless? | Scaling trigger |
|---|---|---|---|
| `ingest-svc` | Signature verification, dedupe, durable append | Yes | Request rate |
| `state-projector` | Event log → `Mandate/Cycle/Attempt` state | Yes (consumer group) | Consumer lag |
| `nowcast-svc` | Issuer health, change-point detection | Windowed state in Redis | Event rate |
| `feature-builder` | Online features (Redis) + offline snapshots (S3) | Yes | Event rate |
| `decision-svc` | Cause posterior, hazard curve, DP, notification plan | Yes | Decision QPS |
| `gate-svc` | Compliance rule evaluation | Yes (rules cached) | Call rate |
| `executor` | Durable timers, fire-time revalidation, outbox | Workflow state in Temporal/PG | Pending timers |
| `measurement-svc` | Arm assignment, CUPED, IPS/DR, batch reports | Yes (batch) | Scheduled |
| `console-api` | Replay, policy simulator, LLM explanation | Yes | Request rate |
| `chain-verifier` | Ledger integrity walk | Yes (cron) | — |

**Why `gate-svc` is a service and not a library.** Rules change on a regulator's timetable, not a release train. A separate deployable lets a compliance change ship in minutes without redeploying the decision engine, and it gives you one auditable chokepoint that every side effect provably passes through. The cost is a network hop on a non-latency-critical path — an easy trade.

---

## 6. Core data flows

### 6.1 Prevention path (the shift-left lever)

```
T-72h   scheduler wakes for upcoming cycle
        │
        ├─► hazard model: P(funded at scheduled debit time) = 0.34   ← high risk
        │
        ├─► notification optimizer: send at T-25h (just past the ≥24h
        │   floor, evening before predicted salary credit), channel SMS,
        │   template with one-tap pay-now link
        │
        ├─► gate: TRAI-DLT-TEMPLATE ✓  RBI-FPC-CONTACT-WINDOW ✓
        │         PDN-CUTOFF-2350 ✓  DPDP-CONSENT ✓
        │
        └─► executor fires at T-25h → ledger record (action=pdn, prevention arm)

T-0     debit executes. If it succeeds → counted as PREVENTED, not recovered.
```

### 6.2 Recovery path

```
payment.failed  ──► ingest (dedupe) ──► state-projector: cycle.attempts_used = 1
                                             │
                                             ▼
                                     decision-svc
                                     ├─ cause: code 05, issuer healthy, amount
                                     │  1.4× customer norm, day 27 → P(no_funds)=0.71
                                     ├─ hazard: S(t) curve, trough at day 1 09:00
                                     ├─ nowcast: HDFC healthy
                                     └─ DP: V(3, now) = ₹1,184
                                            best t' = day+4 09:10 (legal window,
                                            ≥24h notice feasible, post-salary)
                                             │
                                             ▼
                                     gate ── ALLOW (all 6 rules pass, versions logged)
                                             │
                                             ▼
                                     executor: schedule PDN at t'-25h,
                                               schedule debit at t'
                                               idem_key = sha256(cycle|seq|debit)
                                             │
                                             ▼
                                     ledger: decision record, propensity 0.84
```

### 6.3 Fire path — where money safety is enforced

```
timer fires at t'
    │
    ├─► BEGIN TRANSACTION
    │     SELECT cycle FOR UPDATE
    │     revalidate:  mandate still active?
    │                  cycle still unpaid?          ← catches late payment.captured
    │                  no payment via link?          ← catches customer self-serve
    │                  attempts_used < budget?       ← atomic, not advisory
    │                  amount unchanged?
    │     re-evaluate gate with as_of = NOW         ← rules may have changed since scheduling
    │     UPDATE cycle SET attempts_used += 1, version += 1
    │     INSERT attempt (idem_key) ON CONFLICT DO NOTHING
    │     INSERT outbox row
    │   COMMIT
    │
    └─► outbox relay → Razorpay API with Idempotency-Key
          ├─ 2xx → await outcome webhook
          ├─ 5xx/timeout → retry same idem_key (safe)
          └─ ambiguous → reconciliation job queries by idem_key
```

**Revalidation happens at fire time, not schedule time.** This single choice is what prevents the two worst outcomes: double-debiting a customer, and "recovering" a payment the customer already made. It is the reason the design is event-sourced rather than state-mutating.

---

## 7. Data architecture

| Store | Contents | Why this store | Retention |
|---|---|---|---|
| **Kafka** | Raw event stream, partitioned by `mandate_id` | Ordering per mandate; replay for backfill; decouples ingest from processing | 7 days hot, archived to S3 |
| **PostgreSQL** | Mandates, cycles, attempts, rules, ledger, outbox, assignments | Transactional integrity is non-negotiable for money state; the fire-path transaction spans four tables | Ledger indefinite; operational tables 24 months |
| **Redis** | Online features, issuer health, rate-limit counters, gate rule cache | Sub-ms reads on the decision path | TTL 1–24 h |
| **S3 / object store** | Feature snapshots, model artifacts, training sets, Kafka archive | Cheap, immutable, versioned; snapshots are referenced by the ledger, not embedded | 7 years (audit) |
| **Parquet / Iceberg on S3** | Offline feature store, training tables | Point-in-time-correct joins for training | 7 years |

**Point-in-time correctness.** Training rows must join features *as they were at decision time*, never as they are now. Every decision writes an immutable feature snapshot to S3 and stores only the reference in the ledger. This kills training/serving skew and makes replay exact rather than approximate — a judge clicking replay sees the same numbers the model saw.

**Partitioning.** Ledger partitioned monthly by `ts`; operational tables partitioned by `merchant_id` hash for locality. Hash chains are **per-merchant** (see LLD §13) so writers don't serialise globally.

---

## 8. Consistency and correctness model

The system makes exactly one strong guarantee, and it is deliberately narrow.

> **At most one debit attempt is executed per `(cycle_id, attempt_seq)`, ever.**

Everything else is eventually consistent and designed to tolerate that.

| Property | Guarantee | Mechanism |
|---|---|---|
| Debit uniqueness | Exactly-once effect | DB transaction + `idem_key` unique index + Razorpay idempotency key (defence in depth: local *and* remote) |
| Budget accounting | Never exceeds `B` | `UPDATE ... WHERE attempts_used < budget`, rowcount checked |
| Event processing | At-least-once, idempotent | Unique index on `event_id`; all projections are idempotent |
| Event ordering | Per-mandate ordered, globally unordered | Kafka partition key = `mandate_id`; version guards reject stale transitions |
| State reads | Read-your-writes within a cycle | Fire path reads under `FOR UPDATE` |
| Ledger | Append-only, tamper-evident, gap-free per chain | Hash chain + monotonic `seq` per merchant + continuous verifier |
| Compliance | Fail-closed | Gate unavailable → DENY; unknown rule version → DENY |
| Measurement | Deterministic assignment | Hash of `(committed_seed, customer_id)`; no stored assignment needed |

**The defence-in-depth argument on double debit is worth stating explicitly in a review:** three independent mechanisms must all fail simultaneously to produce one. The local unique index would have to be violated, the atomic budget decrement would have to be lost, *and* Razorpay's idempotency key would have to be ignored. Each is independently sufficient.

---

## 9. Architecture decision records

### ADR-01 — Event-sourced state, not mutable rows
**Context:** Webhooks arrive at-least-once, out of order, and `payment.failed` may be followed by `payment.captured` for the same transaction.
**Decision:** Persist the raw event log as the source of truth; derive `Mandate/Cycle/Attempt` by projection.
**Consequences:** Duplicates and reordering become trivially handled. Replay and backfill are free. Cost: a projector to maintain, and eventual consistency between log and projection (bounded by consumer lag, alerted at >30 s).
**Rejected:** Mutating state on webhook receipt — loses the ability to reprocess, and makes out-of-order delivery a correctness bug rather than a non-event.

### ADR-02 — Dynamic programming, not reinforcement learning
**Context:** Four attempts per cycle, monthly cadence, multi-day delayed labels.
**Decision:** Exact DP over a calibrated discrete-time hazard model.
**Consequences:** Optimal *under the model*; ~8 ms per decision; every branch's value is printable, which is what makes the audit trail meaningful. Cost: optimality is only as good as calibration, so calibration monitoring becomes a first-class concern (§13).
**Rejected:** Contextual bandits / RL — sample efficiency is nowhere near sufficient at this cadence, and neither is auditable in the way a regulator would want.

### ADR-03 — Compliance rules as versioned data, not code
**Context:** NPCI and RBI change thresholds and windows on their own schedule; the 2026 e-mandate framework consolidated eight circulars.
**Decision:** Rules are rows carrying `{predicate, regulator, citation, as_of, version}`. Evaluated through a sandboxed expression evaluator, never `eval()`.
**Consequences:** A regulatory change is a data migration reviewable by a non-engineer. The ledger records which rule *version* governed each past decision — the property that actually matters under review. Cost: an expression language to maintain and secure.

### ADR-04 — Gate fails closed
**Context:** A gate outage during a burst is plausible.
**Decision:** Unavailable gate, unparseable predicate, or missing rule version → `DENY`.
**Consequences:** A gate outage costs recovery revenue. It cannot cost a compliance breach. Given RBI has fined lenders over recovery-agent conduct, this asymmetry is not close.

### ADR-05 — Durable workflow engine for scheduling
**Context:** Actions fire hours to days after they are decided; the process will restart in between.
**Decision:** Temporal-style durable workflows in production; durable-timer table + poller for the hackathon build (§14).
**Consequences:** Timers survive deploys and crashes. Cost: operational weight.
**Rejected:** In-memory schedulers (lose state on restart) and naive cron (no per-entity state, no retry semantics).

### ADR-06 — Hash chain, not a blockchain
**Context:** The requirement is tamper-*evidence*, not distributed consensus among mutually distrusting parties.
**Decision:** SHA-256 chain over canonicalised records, per-merchant, with a daily global checkpoint chaining all merchant heads.
**Consequences:** Detects any retroactive edit; parallel writes stay possible. Cost: does not prevent a coordinated rewrite by an operator with full DB access — mitigated by exporting daily checkpoint hashes to append-only external storage.

### ADR-07 — Propensity logging from decision one
**Context:** Off-policy evaluation is invalid unless `P(action | state)` was recorded when the action was taken.
**Decision:** Every decision logs its propensity, even when the policy is deterministic (in which case it also logs the ε used for exploration).
**Consequences:** IPS and doubly-robust estimators become available retroactively for policies never run live. Cost: a small amount of deliberate randomisation, which is also what keeps the logged data explorative enough to learn from.

### ADR-08 — Survival model, not binary classification
**Context:** The needed quantity is time-indexed, and the label is censored — an hour never attempted is an outcome never observed.
**Decision:** Discrete-time hazard model producing `S(t)`; the sequencer consumes conditional success probabilities derived from it.
**Consequences:** Correctly handles censoring, and outputs exactly the curve the DP needs. Cost: more machinery than a classifier, and an absorbing-funding assumption that must be stated and monitored (LLD §9.3).

---

## 10. Failure domains and degradation

| Component down | Blast radius | Behaviour |
|---|---|---|
| `ingest-svc` | New events queue upstream | Razorpay retries webhooks; RPO preserved. Alert at any 5xx rate |
| Kafka | Projection and nowcast stall | Ingest still persists to PG; replay on recovery |
| `state-projector` | Stale cycle state | Fire path reads PG under lock; scheduled actions revalidate and self-abort if stale beyond threshold |
| Hazard model | No personalised curve | **Degrade to V0** segment lookup table (still beats calendar) |
| V0 table also gone | No model | **Degrade to baseline calendar policy**, still fully gated and logged, flagged `degraded=true` in ledger |
| `nowcast-svc` | No issuer health | Treat all issuers as healthy (neutral prior); log degradation |
| `gate-svc` | No approvals | **Fail closed.** All actions deny and defer. Revenue loss, zero compliance risk |
| `executor` | Timers don't fire | Temporal persists; on recovery, fire late — gate re-check catches actions now outside their window and defers them |
| PostgreSQL primary | Full stop | Failover to replica; RTO < 5 min; outbox replays; idem keys make replay safe |
| LLM provider | No explanations, no NL parsing | Console shows structured record without prose; inbound replies queue for later parsing. **Never blocks a money decision** — by design |

**The degradation ladder is the point.** Notice that every rung still passes the compliance gate and still writes to the ledger. The system can lose all of its intelligence and remain compliant and auditable; it cannot lose its gate and remain either. That ordering is deliberate and worth defending in review.

---

## 11. Security and compliance architecture

**Data minimisation.** Prayas stores mandate IDs, customer IDs, amounts, timestamps, decline codes, and derived features. It stores no PANs, no bank credentials, no balances. The DPDP posture follows directly: purpose-limited to recovery, consent artifact referenced by ID on every communication, withdrawal propagates to a suppression list checked by the gate.

**Webhook authenticity.** HMAC-SHA256 signature verification against the raw request body — never a re-serialised copy, which is a classic and silent failure. Constant-time comparison. Old and new secrets accepted during rotation windows.

**Secrets.** Provider keys and DLT credentials in a secrets manager, rotated; never in config or ledger records.

**Ledger access.** Append-only at the database-grant level: the application role holds `INSERT` and `SELECT` on `decisions`, never `UPDATE` or `DELETE`. Daily checkpoint hashes exported to write-once external storage, which is what turns "we don't edit it" into a checkable claim.

**PII in logs.** Structured logging with field-level redaction; customer identifiers hashed in application logs, resolvable only through the ledger under access control.

**Communication compliance is enforced, not documented.** DND status, consent record, DLT template ID, header series, and contact-hour checks are all gate predicates — an SMS physically cannot be sent without passing them, because the send path has no other route to the provider.

---

## 12. Deployment

| Environment | Data | Rails | Purpose |
|---|---|---|---|
| `local` | Simulator only | Mocked | Development, unit tests |
| `test` | Simulator + Razorpay **test mode** | Real webhooks, real signature verification, real payment links | Integration truth |
| `staging` | Shadow of production events | Gate in shadow mode, **no side effects** | Policy comparison, gate regression |
| `prod` | Live | Live, ramped | — |

**Rollout strategy.** Shadow mode first: run the full decision loop, write ledger records, fire nothing. Compare proposed actions against the incumbent policy offline. Then ramp by merchant with the holdout already in place, so the incremental number exists from the first live rupee rather than being reconstructed later.

**Kill switches, at three granularities:** global (all actions deny), per-merchant, and per-action-type (e.g. stop all SMS, keep debits). Each is a gate rule, so flipping one is a data change that lands in the audit trail like any other.

---

## 13. Observability

**Golden signals** per service: rate, errors, duration, saturation. Standard.

**Domain metrics — the ones that actually indicate health:**

| Metric | Alert condition | Why |
|---|---|---|
| Consumer lag (projector) | > 30 s | Stale state risks acting on old reality |
| Gate deny rate by `rule_id` | Sudden spike or drop | A spike means upstream is proposing illegal actions; a drop to zero means the gate may be misconfigured |
| Hazard calibration ECE | > 0.05 over 7 d | Miscalibration silently corrupts every ₹ figure in the DP |
| Prediction PSI vs training | > 0.2 | Population shift |
| Attempts per recovery | Trending toward baseline 3.4 | The core efficiency claim is eroding |
| Mandate revocation rate | Above holdout | **Recovery that kills mandates is fake recovery** |
| Ledger chain breaks | Any | Integrity violation, page immediately |
| Outbox lag | > 60 s | Scheduled actions not reaching providers |
| Ambiguous provider responses | Any sustained rate | Reconciliation job may be falling behind |
| Degraded-decision share | > 5% | Model path is failing quietly |

**Calibration monitoring deserves emphasis.** AUC can look fine while calibration drifts, and the sequencer consumes probabilities as *expected rupees* — so a miscalibrated model does not merely rank badly, it computes the wrong money and stops at the wrong time. Reliability curves are a production alert here, not a training-time nicety.

---

## 14. Hackathon-scale variant

The production shape above is the target. This is the same design compressed to something two people can build in a week, with **identical schemas, gate, ledger, and DP** — only the infrastructure substitutions change.

| Production | Hackathon substitute | What is preserved |
|---|---|---|
| Kafka | Postgres table + `LISTEN/NOTIFY` | Event log, ordering per mandate, replay |
| Temporal | `scheduled_actions` table + 10 s poller with `FOR UPDATE SKIP LOCKED` | Durability across restarts, retry semantics |
| Redis | In-process LRU cache | Feature serving |
| S3 | Local `./snapshots/` directory | Immutable feature snapshots |
| Separate services | One FastAPI app + one worker process | Same module boundaries, same interfaces |
| Iceberg | Parquet files | Point-in-time training joins |
| GBM hazard | **V0 per-segment empirical hazard table** | The DP is unchanged; V0 already beats the calendar |

**What must not be simplified away**, because each is directly load-bearing for the judged bar:

1. The fire-time revalidation transaction (money safety)
2. The compliance gate with versioned, cited rules (compliance)
3. The hash-chained ledger and `/replay` endpoint (audit)
4. Deterministic holdout assignment from a committed seed (measurement)
5. Propensity logging (off-policy evaluation)

**Build order.** Event spine → ledger → gate → simulator → V0 hazard → DP sequencer → holdout harness → notification optimizer → console. Ledger and gate land third and fourth deliberately: retrofitted, they end up shallow, and they are half of what is being judged.

---

## 15. Open questions

| Question | Impact | Resolution path |
|---|---|---|
| Is funding genuinely absorbing over a 30-day horizon? | Biases the DP's conditional probabilities | Add a leak parameter; validate against simulator with non-absorbing dynamics (LLD §9.3) |
| Per-attempt cost calibration | Sets the stopping threshold, so it directly moves the headline number | Sensitivity analysis across a cost range; expose as a slider in the policy simulator rather than hiding one guess |
| Cross-merchant interference in the holdout | Slight attenuation of measured lift | Randomise at customer level; quantify residual interference and report it rather than ignoring it |
| eNACH batch semantics | Different budget and window model | Rail adapter interface; eNACH is a comparator, not the primary rail |
| Do issuers rate-limit repeated mandate presentations? | Could add a hidden constraint | Instrument and detect empirically from nowcast data |

---

*HLD for PRAYAS, Razorpay AI Buildathon Track 03. Regulatory thresholds referenced throughout carry `as_of` dates in the rule store and must be verified at build time.*
