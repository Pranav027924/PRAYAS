# PRAYAS — Execution Playbook

**Companion to:** `PRAYAS-MASTER-SPEC.md`
**Purpose:** Phased delivery plan with explicit exit criteria, dependencies, and demonstrable artifacts per phase

---

## How to use this

Each phase has a **goal**, a **build list**, **exit criteria**, and a **demonstrable artifact** — something you can show a person at the end of the phase that did not exist at the start.

Two rules govern sequencing, and both are deliberate inversions of how projects like this usually get built:

**Rule 1 — Trust infrastructure comes before intelligence.** The ledger and compliance gate are Phase 2, not Phase 9. Retrofitted, they end up shallow, and they are half of what makes this credible. A recovery system with a great model and a bolted-on audit trail is a demo. The reverse is a product.

**Rule 2 — Measurement exists before the thing being measured.** The holdout harness lands before the sophisticated models. Otherwise every number you produce in the first six phases is unfalsifiable, and you will be tempted to believe them.

Phase estimates assume one focused engineer. Compress where you have help; do not reorder.

---

## Phase map

```
 0  Foundations              ── repo, CI, schema, tenancy
 1  Event spine              ── ingest, projection, late-capture guard
 2  Trust layer              ── ledger, compliance gate          ◄ EARLY BY DESIGN
 3  Simulator                ── synthetic cycles with ground truth
 4  V0 intelligence          ── lookup hazard, rule-based cause
 5  Sequencer                ── the DP, economic stopping
 6  Executor                 ── durable timers, fire-time revalidation
 7  Measurement plane        ── holdout, CUPED, guardrails       ◄ EARLY BY DESIGN
 8  ── FIRST DEFENSIBLE NUMBER ──────────────────────────────────
 9  Notification optimizer   ── prevention, the shift-left lever
10  Retention subsystem      ── revocation hazard, LTV, interventions
11  V1 models                ── GBM hazard, EM cause, nowcast
12  Memory subsystem         ── profiles, decay, forgetting
13  LLM layer                ── reply parsing, explanation, policy DSL
14  Multi-rail               ── card e-mandate, eNACH
15  Hardening                ── security, chaos, SLOs, runbooks
16  Console                  ── three screens
17  Live integration         ── Razorpay test mode
18  Pilot                    ── real merchant, real holdout
19  Publication              ── rule pack, audit report
```

**Phase 8 is the milestone that matters.** Everything before it exists to produce one honest, defensible number. Everything after it makes that number bigger or the system more adoptable.

---

## Phase 0 — Foundations

**Goal.** A repository where correctness is enforced by machinery rather than discipline.

**Build.**
- Monorepo, module boundaries per Master Spec §15
- PostgreSQL with migrations (Alembic or equivalent) — never hand-applied DDL
- Full schema from Master Spec §36, including RLS policies
- Tenancy scaffolding: tenant context propagation, connection-level `app.tenant_id`
- CI: lint, type-check (strict mode), unit tests, coverage floor, dependency vulnerability scan
- Docker Compose for local; Terraform skeleton targeting an Indian region
- Structured logging with field-level PII redaction
- Secrets via environment injection from a manager, never files

**Exit criteria.**
- `docker compose up` produces a working stack from a clean clone
- Migrations run forward and backward cleanly
- **Tenant isolation test passes against every tenant-scoped table** — this test exists before any feature does
- CI green on an empty feature set

**Artifact.** A reproducible environment plus a passing isolation test. Unglamorous, and the reason nothing later needs rework.

---

## Phase 1 — Event spine

**Goal.** Reality lands in the system correctly, including the awkward parts.

**Build.**
- Webhook receiver with HMAC verification over **raw bytes**, constant-time comparison, rotation-window support
- Dedupe by unique constraint on `event_id`
- Event log as source of truth; state derived by projection
- Projector: `Mandate → Cycle → Attempt` with terminal-state absorption
- **Late-capture guard**: `payment.failed` followed by `payment.captured` cancels scheduled actions
- Out-of-order tolerance with version guards and a `stale_transition` metric

**Exit criteria.**
- Same event delivered 100× produces exactly one state transition
- Every permutation of a fixed event set converges to identical state (property test)
- `failed → captured` sequence leaves zero pending actions
- Unverified payloads are rejected and never persisted

**Artifact.** Replay a recorded webhook stream in any order and get the same state every time.

---

## Phase 2 — Trust layer

**Goal.** Before anything can act, everything that acts is recorded and gated.

**Build.**
- Hash-chained ledger, per-tenant chains, canonical serialisation
- Append-only enforced at grant level
- Chain verifier running continuously
- Compliance gate: rule store, versioned records with citations and `as_of`
- Sandboxed predicate evaluator (AST whitelist, empty builtins)
- Initial rule pack: NPCI windows, PDN 24h, PDN cutoff, AFA cap, contact window, DLT, DPDP consent, internal fatigue cap
- Gate fails closed on every error path
- Shadow-mode evaluation harness

**Exit criteria.**
- Tampering with any ledger field is detected by the verifier
- Every rule has a unit test covering both sides of its boundary
- `safe_eval` rejects `__import__`, attribute access, comprehensions, lambdas
- Gate returns DENY when the rule store is unreachable
- **Mutation testing on gate predicates: no surviving mutants**

**Artifact.** Feed the day-1/3/5 baseline policy through the gate in shadow mode and print a violation count. This is the seed of the Master Spec §8 audit report, and it exists before the product does.

---

## Phase 3 — Simulator

**Goal.** Data with ground truth, which real data can never provide.

**Build.**
- Generative process per Master Spec §38: payday mixtures, issuer outages, decline-code emission with 05-masking, revocation dynamics, fatigue decay
- Emits `true_cause`, `true_funding_time`, `issuer_state` alongside observables
- Configurable, seeded, reproducible
- Output flows through the **same projector** as live events — one code path, no drift

**Exit criteria.**
- Generated distributions match configured parameters within tolerance
- Same seed produces byte-identical output
- Simulated events project to valid state through the production projector
- 10,000 cycles generate in under 60 seconds

**Artifact.** A labelled dataset where you know the answer, which makes every subsequent model claim checkable.

---

## Phase 4 — V0 intelligence

**Goal.** Beat the calendar with the simplest thing that can.

**Build.**
- Deterministic cause layer: hard codes resolve terminally, code 51 near-deterministic
- Code-05 placeholder: contextual heuristic, no model yet
- **V0 liquidity hazard**: per-segment empirical lookup on `(mcc, ticket_band, day_of_month, hour_band)`
- Hierarchical shrinkage toward segment priors
- Survival curve construction and the conditional `p(t | t_last)`
- Calibration harness: reliability curves, ECE

**Exit criteria.**
- V0 hazard beats a uniform prior on simulated data, measured by log-loss
- ECE below 0.05 on held-out simulator data
- **Conditional probability verified against simulator ground truth** — this is where an error in `p(t|t_last)` would surface, and it is the most likely place to get the mathematics wrong

**Artifact.** A hazard curve for a simulated customer that visibly peaks on their payday.

---

## Phase 5 — Sequencer

**Goal.** Attempts become priced capital.

**Build.**
- Rail adapter interface; UPI Autopay implementation
- Legality mask construction (windows, notice lead, PDN cutoff, deadline)
- The DP over `(attempts remaining, last-failure time)`
- Cost model: fees, convex fraud risk, annoyance
- Economic stopping with rupee-denominated rationale
- Hard overrides evaluated before economics

**Note.** The LTV formulation (Master Spec §23.1) requires the revocation model, which arrives in Phase 10. Build Phase 5 with a **fixed `W` placeholder** and a clearly marked upgrade point. The DP structure does not change; only `W` becomes dynamic.

**Exit criteria.**
- DP output matches brute-force enumeration for `B ≤ 3`, `H ≤ 40`
- Value monotone non-decreasing in `B`, `A`, `W` (property test)
- Solve time under 15 ms at `B=4`, `H=720`
- Stopping produces a rationale containing actual rupee figures
- Legality mask verified against every rail constraint independently

**Artifact.** Given a failed cycle, print every candidate attempt time with its expected value, and the reason the chosen one won.

---

## Phase 6 — Executor

**Goal.** Decisions survive time, crashes, and deploys — without ever double-debiting.

**Build.**
- Durable scheduled actions with `FOR UPDATE SKIP LOCKED` claiming
- `locked_until` reclamation from crashed workers
- **Fire-time revalidation transaction** (Master Spec §17.4) — the most important code in the system
- Deterministic idempotency key derivation
- Transactional outbox; commit intent before any external call
- Ambiguous-response handling and reconciliation by key
- Deterministic jitter within execution windows

**Exit criteria.**
- Kill worker mid-transaction: no orphaned debits, no lost timers
- Kill after outbox insert, before provider call: relay resumes with same key
- 10% injected provider timeouts: zero double debits, all reconciled
- Budget decrement is atomic under concurrent workers
- **The adversarial test passes**: `failed → captured` concurrent with a firing retry, 100 iterations, zero double debits

**Artifact.** A chaos test report showing zero double debits under adversarial conditions.

---

## Phase 7 — Measurement plane

**Goal.** Any number the system produces is falsifiable.

**Build.**
- Deterministic customer-level arm assignment from a committed seed
- `experiment_config` with git commit hash, frozen before runs
- Baseline policy implementation (day 1/3/5) running through the same gate
- Incremental estimators with confidence intervals
- CUPED using pre-period recovery rate
- Guardrail metric computation
- SRM check
- Propensity logging wired into every decision

**Exit criteria.**
- A/A test: incremental lift CI contains zero
- SRM passes across 100 seeds
- CUPED demonstrably reduces variance on simulated data
- Every ledger record carries an arm and a propensity
- Guardrails compute correctly, including the ones that could look bad

**Artifact.** A batch report with a confidence interval, produced by machinery rather than by hand.

---

## Phase 8 — FIRST DEFENSIBLE NUMBER

**Goal.** Run the full loop on simulated data and report an honest result.

**Build.** Nothing new. Integrate, run, report.

**Exit criteria.**
- 10,000+ simulated cycles through the complete loop
- Incremental recovery reported with CI, CUPED-adjusted
- Attempts per recovery reported per arm
- Zero compliance violations in treatment; baseline violations counted in shadow
- Every decision replayable
- Robustness suite: lift survives every simulator perturbation

**Artifact — the sentence:**

> *"Across N simulated cycles, Prayas recovered ₹X, a +Y.Y pp incremental lift over a pre-registered, seed-committed 15% holdout running the industry-standard day-1/3/5 policy (95% CI [a, b], CUPED-adjusted), using 1.7 attempts per recovery versus the baseline's 3.4, with zero compliance-gate breaches across M gated actions — every one replayable from a hash-chained ledger."*

**If the lift is not there, stop and find out why before building further.** Phases 9 onward assume the core loop works. A negative result here is information, and it is much cheaper now than after eleven more phases.

---

## Phase 9 — Notification optimizer

**Goal.** Prevent failures instead of recovering them.

**Build.**
- Risk scoring for upcoming debits from the hazard model
- PDN timing optimisation: as late as legally permitted, aligned to attention and predicted funding eve
- Cutoff-aware scheduling (23:50 for T+1)
- Channel selection with DLT template, header series, DND, consent
- Content assembly: required fields plus one-tap pay path for high-risk
- **Prevention accounting**: high-risk cycles succeeding on first execution with zero retries consumed

**Exit criteria.**
- PDN never scheduled inside the cutoff
- Every notification passes the gate before sending
- Prevention rate computed separately from recovery, compared against control
- Fatigue cap enforced

**Artifact.** A prevention number — a metric no competitor reports, because none noticed the compliance requirement was also the best channel.

---

## Phase 10 — Retention subsystem

**Goal.** Manage the mandate, not the cycle.

**Build.**
- Revocation hazard model (Master Spec §22)
- Continuation value `W` computation
- **Upgrade the sequencer to the LTV objective** — replace the Phase 5 placeholder
- Nightly mandate-health sweep; `at_risk` state
- Intervention selector across six actions including **back off**
- Date-change proposal logic (Master Spec §24.3)
- Kaplan-Meier survival comparison in the measurement plane

**Exit criteria.**
- Revocation model calibrated on simulated mandate deaths
- LTV sequencer is measurably more conservative than the recovery-only version
- Survival curves computed per arm with a log-rank test
- Date-change proposals fire only when chronic and materially better
- Back-off decisions are recorded in the ledger with rationale

**Artifact.** A mandate that was going to die, kept alive by a date change — with the counterfactual visible in the replay.

---

## Phase 11 — V1 models

**Goal.** Replace heuristics with trained models where it pays.

**Build.**
- GBM discrete-time liquidity hazard replacing V0
- Isotonic calibration, weekly refit
- EM-trained code-05 cause inference
- Issuer nowcast: Wilson lower bound, CUSUM change-point, recovery ETA
- Model registry with pinned versions per decision
- Drift monitoring: ECE, PSI

**Exit criteria.**
- V1 beats V0 on held-out log-loss **and** on end-to-end incremental lift — the second is the one that counts
- **Cause inference confusion matrix against simulator ground truth**
- Nowcast detects injected outages within one window and recovery within three
- V0 fallback verified: kill V1, system degrades without firing anything illegal

**Artifact.** A confusion matrix for latent-cause inference — a claim that cannot be made on real data.

---

## Phase 12 — Memory subsystem

**Goal.** The hundredth cycle is smarter than the first.

**Build.**
- Customer Payment Profile with Bayesian payday posterior
- Recency-weighted updates, declared-hint TTL, fatigue decay
- Cross-tenant aggregate tier with k-anonymity floor
- **Strict tenant scoping of individual profiles** (Master Spec §27)
- Forgetting cascade with ledger pseudonymisation
- Online/offline feature parity job

**Exit criteria.**
- Profile-informed hazard beats segment-prior-only on customers with ≥3 cycles
- No individual profile data crosses a tenant boundary — asserted by test
- Aggregates enforce minimum cohort size
- Consent withdrawal deletes profile, pseudonymises ledger, suppresses contact — verified end to end
- Feature parity job detects injected skew

**Artifact.** A learning curve: model quality against number of observed cycles per customer.

---

## Phase 13 — LLM layer

**Goal.** Language where only language works. Nowhere else.

**Build.**
- **In-region** inbound reply parsing (residency constraint, Master Spec §41.3)
- Schema-validated structured output; unparseable replies to human queue
- Bounded influence: `declared_funding_day` shifts a prior with capped weight
- Fail-safe opt-out handling
- Grounded explanation over ledger records, non-PII payload
- Policy DSL: merchant natural language → proposed rules, human confirmation required

**Exit criteria.**
- **Prompt-injection test suite**: adversarial replies attempting to set state, mark paid, or stop collection produce no effect beyond a bounded prior shift
- Output schema violations are discarded, never partially applied
- Explanations contain no fact absent from the record
- LLM-proposed rules cannot activate without human confirmation
- No PII reaches any external provider — verified by payload inspection test

**Artifact.** A code-switched Hinglish reply becoming a calibrated feature — and an injection attempt visibly failing to do anything.

---

## Phase 14 — Multi-rail

**Goal.** Prove the abstraction with the hard rail.

**Build.**
- Card e-mandate adapter: network retry economics, expiry handling, card-updater signal
- **eNACH adapter: batch presentation windows, T+1 outcome latency**
- Rail-specific revocation baselines
- Rail migration intervention (Master Spec §24.4)
- Rail-aware rule filtering in the gate

**Exit criteria.**
- All three rails run the full loop end to end
- eNACH's outcome latency handled without the executor assuming synchronous results
- Rail-specific rules apply correctly and only to their rail
- Migration proposals fire on expiring cards with an available alternate rail

**Artifact.** One engine, three rails, one set of metrics — which is what turns a UPI trick into a platform capability.

---

## Phase 15 — Hardening

**Goal.** Operable by one person at 3am.

**Build.**
- Full chaos suite (Master Spec §40.7)
- Threat-model mitigations verified per row
- SLO instrumentation and error budgets
- All runbooks written and drilled
- Kill switches at three granularities
- Load test to 500 decisions/sec burst
- Backup, restore, and failover drills
- SAST, DAST, dependency scanning in CI

**Exit criteria.**
- Every chaos scenario passes
- Every threat-model row has a passing test or a documented accepted risk
- Emergency stop executes in under 60 seconds
- Restore from backup verified, not assumed
- Load test sustains burst without lag alarm

**Artifact.** A game-day report — deliberate breakage, recovery inside SLO.

---

## Phase 16 — Console

**Goal.** Three purposeful screens. Not a dashboard.

**Build.**
1. **Batch result** — headline pair (recovery + survival), CIs, guardrails, compliance counter with shadow baseline
2. **Decision replay** — search any mandate, see trigger → features → posteriors → candidates with EVs → rules with citations → outcome
3. **Policy simulator** — drag cost, `λ`, `μ`; watch policy shift live across thousands of cycles, because the DP costs 8 ms

**Exit criteria.**
- Replay renders any decision including denied ones
- Simulator recomputes 1,000 cycles in under 2 seconds
- Guardrails displayed with equal prominence to headline metrics
- RBAC enforced per screen

**Artifact.** Screen 2. Click one recovered rupee, see the entire causal chain.

---

## Phase 17 — Live integration

**Goal.** Wired to reality, not a notebook.

**Build.**
- Razorpay test-mode integration across all three rails
- Real webhook signature verification against real deliveries
- Contract tests against live API schemas
- Reconciliation against provider state
- Adoption stage machinery (observe → shadow → canary → ramp → full)

**Exit criteria.**
- Full lifecycle in test mode: mandate → PDN → debit → failure → decision → retry → success
- Contract tests green nightly
- Reconciliation finds zero discrepancies over 7 days
- Stage transitions gated by their exit criteria in code, not in a document

**Artifact.** A real webhook producing a real decision producing a real (test-mode) debit.

---

## Phase 18 — Pilot

**Goal.** One real merchant, one real holdout, one real number.

**Build.**
- Onboard a small merchant — a gym with a few hundred members, a SaaS with modest MRR
- Backfill their history (Master Spec §39)
- Run the adoption ramp properly, no stage skipping
- Weekly guardrail review with the merchant
- Post-pilot report

**Exit criteria.**
- Observe stage: event completeness ≥ 99.9% against their records
- Shadow stage: 10,000+ decisions, calibration holds on real data, **audit report generated on their actual baseline policy**
- Canary: 14 days, zero double debits, zero violations
- Ramp: incremental recovery CI excludes zero on real mandates
- Merchant would keep using it

**Artifact.** *"We recovered ₹X for a real business, measured against their own holdout."* This is the difference between a system and a product.

---

## Phase 19 — Publication

**Goal.** Distribute the part that costs no moat.

**Build.**
- Open-source the compliance rule pack with citations and `as_of` dates
- Rule schema, sandboxed evaluator, conformance test suite
- Rail constraint definitions
- The §8 audit report as a reproducible methodology
- Contribution guide for rule updates when regulations change

**Exit criteria.**
- Rule pack installable and runnable standalone
- Conformance suite passes on a clean install
- Audit report reproducible by a third party
- Documentation sufficient for someone with no context

**Artifact.** A public repository every Indian fintech building recurring payments has reason to depend on — and the best distribution channel this project has.

---

## Dependency graph

```
0 Foundations
└─► 1 Event spine
    └─► 2 Trust layer ─────────────────────┐
        ├─► 3 Simulator                    │
        │   └─► 4 V0 intelligence          │
        │       └─► 5 Sequencer            │
        │           └─► 6 Executor ◄───────┘
        │               └─► 7 Measurement
        │                   └─► 8 ★ FIRST NUMBER
        │                       ├─► 9  Notification
        │                       ├─► 10 Retention ──► upgrades 5
        │                       ├─► 11 V1 models
        │                       ├─► 12 Memory ─────► feeds 11
        │                       ├─► 13 LLM ────────► feeds 12
        │                       └─► 14 Multi-rail
        │                           └─► 15 Hardening
        │                               ├─► 16 Console
        │                               └─► 17 Live integration
        │                                   └─► 18 Pilot
        └───────────────────────────────────────► 19 Publication
```

Phases 9–14 are parallelisable after Phase 8. Phase 19 depends only on Phase 2 and can ship at any point after it — publishing the rule pack early is a reasonable strategy, since it builds credibility while the engine is still being built.

---

## What to cut under pressure

If time compresses, cut in this order. Each cut costs capability; none costs credibility.

| Cut first | Why it's safe |
|---|---|
| Phase 16 console screens 1 and 3 | Screen 2 (replay) carries the demo alone |
| Phase 14 eNACH | Card e-mandate proves the abstraction adequately |
| Phase 13 policy DSL | The other three LLM uses carry the argument |
| Phase 11 nowcast | V1 hazard and cause inference matter more |
| Phase 12 cross-tenant aggregates | Per-tenant memory is the bulk of the value |

**Never cut:** the ledger, the gate, fire-time revalidation, the holdout, propensity logging, or the tenant isolation tests. Each maps directly to something that would make the project not worth having built.

---

## Definition of done

The project is complete when a skeptical reviewer can:

1. Click any recovered rupee and see why it happened, with rule citations
2. Verify the ledger has not been altered
3. Read a recovery number with a confidence interval, measured against a seed-committed holdout
4. Read a survival number next to it, and see that recovery did not come at retention's expense
5. Watch the policy change live as the cost of an attempt is dragged upward
6. See the violation count the industry-standard baseline would have produced
7. Perturb the simulator and watch the lift survive
8. Attempt a prompt injection through a customer reply and observe nothing happen
9. Confirm no individual profile crossed a tenant boundary
10. Find the pilot merchant's real number, measured the same way as the simulated one

Ten checks. Each one is something a competing submission will not survive.

---

*PRAYAS Execution Playbook v1.0. Sequencing is deliberate; Rules 1 and 2 at the top of this document explain the two inversions most likely to be second-guessed.*
