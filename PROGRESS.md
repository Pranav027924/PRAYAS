# PRAYAS — Build Progress

## Current phase
Phase 7 — Measurement plane

## Phase status
| # | Phase | Status | Closed on |
|---|---|---|---|
| 0 | Foundations | CLOSED | 2026-08-25 |
| 1 | Event spine | CLOSED | 2026-08-25 |
| 2 | Trust layer | CLOSED | 2026-08-26 |
| 3 | Simulator | CLOSED | 2026-08-26 |
| 4 | V0 intelligence | CLOSED | 2026-08-26 |
| 5 | Sequencer | CLOSED | 2026-08-27 |
| 6 | Executor | CLOSED | 2026-08-27 |
| 7 | Measurement plane | IN PROGRESS | — |
| 8 | FIRST DEFENSIBLE NUMBER | not started | — |
| 9–19 | see Execution Playbook | not started | — |

## Exit criteria — current phase (Phase 7 — Measurement plane)
_Evidence from a run on 2026-08-27. Phase remains open pending confirmation._

- [x] **A/A test: incremental lift CI contains zero** — asserted as *coverage*, not a single replication: 600 A/A replications at n=4,000 per arm, intervals containing zero at the nominal ~95% rate. A single A/A passing would also pass for a badly miscalibrated interval. Paired with a guard test showing a real 6pp effect is still detected, so an estimator that always contained zero would fail
- [x] **SRM passes across 100 seeds** — 100 seeds × 6,000 customers through the real `arm()` function (not simulated counts), zero failures. Plus a deliberately broken assignment (30% actual vs 10% registered) asserted to **fail**, so the check cannot pass by always saying "fine"
- [x] **CUPED demonstrably reduces variance** — >5% reduction on simulated data with a genuine per-customer pre-period correlation, and the mean asserted unchanged to 1e-9, since a "reduction" that moved the estimate would be bias. Plus an unrelated covariate asserted to yield **<1%**, so CUPED cannot appear to help by fitting noise
- [x] **Every ledger record carries an arm and a propensity** — 40 fired decisions, zero NULL `holdout_arm`, zero NULL `propensity`, both arms present. Logged propensity asserted to equal the probability its recorded arm actually had. **Refusals carry an arm too** (40 STALE records, zero missing) — excluding them would bias every estimate toward cycles that happened to clear the gate. With no experiment running the arm is NULL, not invented
- [x] **Guardrails compute correctly, including unflattering ones** — each of §6's five asserted to breach on bad input *and* pass on good; a guardrail that never fires is indistinguishable from a broken one. The net-value guardrail breaches on a case where recovery alone looks strongly positive but induced churn cancels it
- Artifact: batch report rendered by machinery — SRM gate, the §6 matched pair, CUPED, efficiency, §35 net value, all five guardrails, and a verdict. **An SRM failure blocks it entirely**: no estimate is printed, asserted by checking the numbers do not leak into the output
- Pre-registration enforced by privilege: `prayas_app` holds SELECT only on `experiment_config`, so the running application cannot re-seed after seeing results — asserted with a permission-denied test, the same construction Invariant 5 uses for the ledger
- Statistics validated against **published reference data** (ADR-047): Freireich et al. (1963) — all 7 KM values to 3dp, median 8, log-rank χ² **16.79** and O=9/E=19.25, matching the published figures exactly; χ² tail against 5 standard table values
- Gates: 684 tests, coverage 88.90% (floor 85), mypy --strict clean (58 files), ruff + ruff format clean

## Closed phases

### Phase 6 — Executor · closed 2026-08-27
- [x] **Kill worker mid-transaction: no orphaned debits, no lost timers** — a real subprocess `SIGKILL`'d after the budget decrement, before COMMIT. `attempts_used` back to 0, zero attempt rows, zero outbox rows, timer immediately re-claimable. A patched exception would have exercised Python's `finally`; only a real kill exercises Postgres's rollback
- [x] **Kill after outbox insert, before provider call: relay resumes with same key** — `SIGKILL` in exactly that window; exactly one durable intent, still `pending`, attempt and outbox agreeing on the key. The relay then submitted **that same key**, one debit
- [x] **10% injected provider timeouts: zero double debits, all reconciled** — 40 cycles; ambiguous rows held their budget slots (`sum(attempts_used) == 40`, zero over budget), then all reconciled. Every key submitted exactly **once** — reconciliation *queries* by key rather than re-submitting
- [x] **Budget decrement atomic under concurrent workers** — 10 workers race one cycle with budget 4: exactly 4 fired, `attempts_used == 4`, 4 attempt rows. §31's other three defences each asserted in isolation too, so the suite is not resting on one
- [x] **The adversarial test passes** — 100 independent `failed → captured` races against a firing retry, head start alternated so both orderings are genuinely exercised. **Split 50/50, zero double debits.** Every cycle: ≤1 attempt, ≤1 budget consumed, attempt count equal to `attempts_used`
- Stack: `docker compose up --wait` brings postgres, migrate, api, chain-verifier **and the new `executor` service** to healthy; `/health` → 200
- Migration `0009_tenant_registry_fn` reverses and re-applies cleanly
- Gates: 621 tests, coverage 88.91% (floor 85), mypy --strict clean (49 files), ruff + ruff format clean

### Phase 5 — Sequencer · closed 2026-08-27
- [x] **DP matches brute-force enumeration for `B ≤ 3`, `H ≤ 40`** — 27 parametrised cases (B∈{1,2,3} × H∈{10,25,40} × 3 seeds), every state compared, plus 6 cases asserting the DP's *chosen slot* realises the value the objective predicts. The enumerator is transcribed from §23.1's equation, not from the DP module
- [x] **Value monotone non-decreasing in `B`, `A`, `W`** — B and A hold on `V` directly (Hypothesis, 60/40 examples). **W does not hold on `V`** — see ADR-040; asserted on `V + W`, which holds unconditionally, with the raw non-monotonicity pinned by its own regression test
- [x] **Solve under 15 ms at `B=4`, `H=720`** — **3.34 ms, 22% of budget**, 459 legal slots. The literal §23.2 loop measures **20.68 ms and misses the criterion**; see ADR-041
- [x] **Stopping rationale carries actual rupee figures** — asserts the real continuation value (`₹5,988.00`) and best-EV figures appear, not a zeroed template
- [x] **Legality mask verified against every rail constraint independently** — each of the four has its own function and its own tests, at §40.2's cliffs (09:59:59/10:00:00, 12:59:59/13:00:00, 16:59:59/17:00:00, 21:29:59/21:30:00, 23:49/23:50). Plus a test that each constraint excludes slots the others do not, so none can be a silent no-op
- Artifact: given a failed cycle, prints every candidate with its EV — chooses **slot 74 (05 Mar 15:30 IST, 33.3% funding)** over the first legal slot, i.e. the payday rather than the calendar, and names the runner-up and the margin
- Gates: 599 tests, coverage 89.57% (floor 85), mypy --strict clean (42 files), ruff + ruff format clean

**Carried into Phase 10 (ADR-040):** §23.2's STOP baseline of `0` contradicts §23.1's `A + W` success payout. Changing STOP to `W` is the economically correct fix but changes stopping behaviour, so it was deferred to when §22's revocation model makes `W` a real output.

### Phase 4 — V0 intelligence · closed 2026-08-26 · tag `phase-4-complete`
- [x] V0 hazard beats a uniform prior by log-loss — measured on a held-out fold, with the margin pinned so a regression that stays merely "better" still fails
- [x] ECE below 0.05 on held-out data — plus a deliberately miscalibrated model asserted to *exceed* the threshold, so the metric cannot pass vacuously by returning ~0 for everything
- [x] **Conditional `p(t|t_last)` verified against simulator ground truth** — checked at four `t_last` values against the empirically observed frequency among genuine survivors, and separately shown that using the **marginal** is *further from reality*, not merely different (§21's "systematically wrong stopping points")
- Artifact: salaried-1st hazard curve peaks at `h(0) > 0.5`, more than 5× any later slot — a curve that visibly peaks on payday
- §20 confusion matrix against ground truth: 91.1% accuracy on specifically-coded failures
- Gates: 501 tests, coverage 89.65% (floor 85), mypy --strict clean, ruff clean

**Recorded weakness (not a blocker):** V0 cannot separate causes hiding behind code 05 — `fraud_hold` surfacing as 05 is classified `no_funds` 100% of the time. This is the documented reason §20 wants EM in Phase 11.

**Recorded simulator gap (see ADR-035):** the 05 population is ~98% `no_funds`, not the ~50% §20 describes, because `issuer_degraded` deterministically emits 91 and never masquerades as 05. Overall accuracy is therefore *identical* at `mask_05_rate` 0.0 and 1.0 (0.911 both). V0 looks better on 05 than a realistic mix would allow.

### Phase 3 — Simulator · closed 2026-08-26 · tag `phase-3-complete`
- [x] Distributions match configured parameters — payday mix, rail mix and `mask_05_rate` each within ±0.03 over 4,000 cycles, plus both boundary extremes (0.0 and 1.0) asserted exactly
- [x] Same seed → byte-identical output — verified in-process, **and across a process boundary** (a subprocess pair), since in-process repetition can hide dependence on global RNG state. Also asserts a 50-cycle run is a byte-exact prefix of a 500-cycle run, which spawned per-cycle streams guarantee
- [x] Simulated events project to valid state — through the real `project_tenant`; every projected cycle and mandate state is a member of the §11 state machines, `attempts_used ≤ attempt_budget` holds, and zero events left unconsumed
- [x] 10,000 cycles under 60s — **0.55s, 0.9% of budget**
- Leakage control (ADR-030): app role verified to hold *no* SELECT/INSERT/UPDATE/DELETE on `sim_ground_truth`, asserted both structurally and behaviourally
- No-drift (ADR-031): the same events HMAC-signed through `/v1/webhooks/razorpay/{tenant}` project to state **identical** to direct insertion
- Gates: 409 tests, coverage 89.31% (floor 85), mypy --strict clean, ruff clean

### Phase 2 — Trust layer · closed 2026-08-26 · tag `phase-2-complete`
- [x] Tampering with **any** ledger field detected — parametrised across all **25** hashed columns individually, plus `record_hash` itself, record deletion (sequence gap), and single-break localisation
- [x] Every rule has a both-sides boundary test — all 8 rules at §40.2's cliffs, plus a metatest that fails if a rule ships untested, and assertions that every rule carries citation/`as_of`/`regulator` (Invariant 10)
- [x] `safe_eval` rejects the named forms — `__import__`, attribute access, comprehensions, lambdas, plus ~30 further idioms including `().__class__.__bases__[0].__subclasses__()`
- [x] Gate DENYs when the rule store is unreachable — with `degraded=True`; also denies on unknown action type and unrecognised `on_fail`
- [x] **Mutation testing: 62 mutants on `prayas/gate/predicate.py`, 62 killed, zero survivors** (measured from the run output; see ADR-027 on why `mutmut results` is not the source of truth)
- Artifact (§8): shadow-mode harness reports the baseline day-1/3/5 policy producing **30,000 attempts outside NPCI windows and 30,000 without valid 24h notice, per 10,000 cycles** — every baseline attempt unlawful
- Gates: 369 tests, coverage 86.84% (floor 85), mypy --strict clean, ruff clean

### Phase 1 — Event spine · closed 2026-08-25 · tag `phase-1-complete`
- [x] Same event 100× → exactly one transition — 100 deliveries produced 1 `events_raw` row and `attempts_used == 1`; asserts projected state, not just row count
- [x] Every permutation converges — all **5,040** orderings of the fixed event set enumerated exhaustively, plus 200 Hypothesis-generated interleavings; converged values pinned so consistent-but-wrong fails
- [x] `failed → captured` leaves zero pending actions — pending = 0, cycle state `succeeded`
- [x] Unverified payloads rejected and never persisted — 4 forgery variants (wrong, empty, truncated, foreign-secret) → 401 with `events_raw` count 0; body tampering after signing also rejected
- Gates: 161 tests, coverage 90.35% (floor 85), mypy --strict clean, ruff clean

### Phase 0 — Foundations · closed 2026-08-25 · tag `phase-0-complete`
- [x] `docker compose up` from a clean clone — postgres healthy, migrate exited 0, api healthy; `/health` → 200 `{"status":"ok","database":"ok"}`; 40 tables; revision `0004_tenants_rls`
- [x] Migrations forward and backward — 4 applied → 4 reversed → 4 applied; schema snapshot identical across both `head` states
- [x] Tenant isolation on every tenant-scoped table — 35 isolation tests pass, including an `information_schema` metatest that fails when a table carrying `tenant_id` is unregistered or unpoliced. App role verified `rolsuper=f`, `rolbypassrls=f`, owns nothing
- [x] CI green on an empty feature set — ruff, ruff format, mypy --strict, pip-audit (no known vulnerabilities), 88 tests, coverage 91.94% against an 85% floor

## Decisions taken
_Appended as ADRs. Format: date · decision · options considered · rationale._

### ADR-001 · 2026-08-24 · Dependency manager and Python version
**Decision:** uv, Python 3.12.
**Options:** uv+3.12; Poetry+3.12; uv+3.11; pip-tools+3.12.
**Rationale:** Lockfile-first with interpreter pinning, so the toolchain is reproducible from a clean clone — directly serving the Phase 0 exit criterion. Poetry's resolver is the historic CI breakage source. 3.12 clears the project's 3.11+ floor.

### ADR-002 · 2026-08-24 · PostgreSQL version
**Decision:** PostgreSQL 16, via Docker Compose.
**Options:** 16; 17; 15.
**Rationale:** Mature declarative partitioning (§36 `decisions`), stable RLS/FORCE semantics (§18), `pg_advisory_xact_lock` for per-tenant chains (§32). Broadest managed availability in Indian regions for §41.3 residency. Runtime was not a real choice — Phase 0's exit criterion mandates `docker compose up`.

### ADR-003 · 2026-08-24 · Database access layer
**Decision:** SQLAlchemy 2.x **Core** (not ORM) + asyncpg.
**Options:** SQLAlchemy Core+asyncpg; raw asyncpg; psycopg3+Core; SQLAlchemy ORM+asyncpg.
**Rationale:** Governs where `SET LOCAL app.tenant_id` binds on a pooled connection. Core's transaction event hooks give exactly one enforceable place to bind tenant context, keeping Invariant 6 in machinery rather than call-site discipline (§18). ORM rejected because it emits queries nobody wrote, making "does every tenant-scoped query filter by tenant" unauditable.

### ADR-004 · 2026-08-24 · RLS enforcement strategy
**Decision:** Non-owner `prayas_app` role + `FORCE ROW LEVEL SECURITY` on every tenant-scoped table. Migrations run as a separate owner/DDL role.
**Options:** non-owner+FORCE; owner+FORCE; ENABLE only (as §18 is written); app-layer filtering with RLS backstop.
**Rationale:** `ENABLE ROW LEVEL SECURITY` does not apply to the table owner. Under the literal §18 text, an owner-connected app bypasses every policy and the Phase 0 isolation test passes while proving nothing. Also creates the `prayas_app` role that §36's REVOKE references but never defines.

### ADR-005 · 2026-08-24 · `decisions` primary key under partitioning
**Decision:** `PRIMARY KEY (decision_id, ts)`, `UNIQUE (tenant_id, chain_seq, ts)`. Partitioning retained.
**Options:** composite keys; drop partitioning until Phase 15; composite keys + uniqueness trigger.
**Rationale:** §36 as written **cannot be created** — PostgreSQL requires every unique constraint on a partitioned table to include all partition key columns. Smallest deviation preserving §36's intent. **Accepted cost:** `(tenant_id, chain_seq)` is now unique only within a partition; §32's per-tenant advisory lock serialises appends and the chain-verifier walk detects duplicates, so this is a loss of defence-in-depth, not of correctness.

### ADR-006 · 2026-08-24 · Partition management
**Decision:** Alembic-managed monthly partitions over a rolling window, plus a `DEFAULT` partition that alerts.
**Options:** migration-managed+DEFAULT; pg_partman; monthly with no DEFAULT.
**Rationale:** §36 defines no partitions, so the first INSERT would fail. Keeping partitions in migrations preserves explicit `downgrade()` paths for the reversibility exit criterion. DEFAULT ensures an audit write is never lost to a missing range; it must alert, since rows landing there block later creation of an overlapping partition. pg_partman rejected as an infrastructure dependency outside Alembic's control.

### ADR-007 · 2026-08-24 · Unset tenant context
**Decision:** `current_setting('app.tenant_id', true)` in policies (NULL → matches nothing → deny), plus an application-level assertion that tenant context is bound before any tenant-scoped query.
**Options:** missing_ok+app guard; missing_ok alone; §18 as written (raises).
**Rationale:** Two layers, two jobs — the database refuses silently and fail-closed, the application fails loudly and legibly. `missing_ok` alone risks an unset context reading as "no rows exist" on the money path; the raw §18 form aborts with an opaque error naming neither tenant nor table.

### ADR-008 · 2026-08-24 · Referential integrity
**Decision:** `REFERENCES tenants(tenant_id)` on cycles, attempts, interventions, customer_profiles, decisions, scheduled_actions, outbox. **Not** on `events_raw`. `attempts.decision_id` / `interventions.decision_id` left as unenforced references.
**Options:** FKs except events_raw; §36 as written (mandates only); FKs everywhere.
**Rationale:** §36's single FK on `mandates` reads as oversight; making it deliberate protects the ledger from orphaning. `events_raw` is excluded because it is the raw landing zone — a webhook for an unprovisioned tenant must land and be flagged, not be rejected, or evidence is lost during onboarding races. `decision_id` left unenforced because ADR-005's composite PK would force a `ts` column into `attempts` purely to satisfy the FK.

### ADR-009 · 2026-08-24 · CI platform and coverage floor
**Decision:** GitHub Actions, 85% coverage floor, `pip-audit` for dependency scanning.
**Options:** Actions+85%; Actions+90%; GitLab CI+85%.
**Rationale:** Service-container support for a real PostgreSQL 16 instance, which the isolation and migration tests require. 85% functions as a ratchet to raise, not a bar to fight; 90% tends to produce tests written for the number rather than for defects.

### ADR-010 · 2026-08-24 · Terraform target
**Decision:** AWS `ap-south-1` (Mumbai). Provider and backend config only, **zero resources, never applied**.
**Options:** AWS ap-south-1; GCP asia-south1; Azure Central India; defer to Phase 17.
**Rationale:** Satisfies RBI payment-data localisation and §41.3 residency, with the largest Indian fintech compliance precedent and RDS support for PostgreSQL 16 + pg_partman should ADR-006 need revisiting at scale. Zero resources keeps this outside the "incurs cost" Class A trigger.

### ADR-011 · 2026-08-24 · Carried forward without a separate ask
**Alembic** as migration tool — already fixed by the project engineering standards; the Playbook's "or equivalent" does not reopen it. Note that autogenerate handles none of RLS, FORCE, partitioning, roles or REVOKE, so all of that is hand-written `op.execute()` with explicit `downgrade()`.
**Test layout** `tests/{unit,integration,isolation,migrations}/` — Class B. `isolation/` is deliberately separate because §18 and §40.4 treat isolation as a correctness property, not a test category.

## Open questions for the human
_Appended here when blocked on a Class A decision._

## Deviations from spec
_Any approved divergence, with the reason and the approving message._

| Deviation | Spec | Approved in |
|---|---|---|
| `decisions` PK/UNIQUE extended to include `ts` | §36 | ADR-005 |
| Monthly partitions + DEFAULT added to `decisions` | §36 | ADR-006 |
| `FORCE ROW LEVEL SECURITY` + non-owner role added | §18, §36 | ADR-004 |
| `current_setting(..., true)` replaces bare `current_setting(...)` | §18 | ADR-007 |
| `experiment_config` policy admits `tenant_id IS NULL` as platform-scoped | §18, §36 | ADR-012 below |
| FKs to `tenants` added on seven tables | §36 | ADR-008 |

### ADR-012 · 2026-08-24 · `experiment_config` tenant policy
**Decision:** Policy is `tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)`.
**Options:** admit NULL as platform-scoped; make `tenant_id NOT NULL`; exclude from RLS.
**Rationale:** §36's nullable column contradicts §18's equality policy, which would hide platform-scoped rows from every tenant. §33 randomises **customer-level, not cycle-level**, and notes "one customer may hold mandates with several merchants" — so a customer spans tenants and platform-scoped experiments are required, not accidental. The shared rows hold only seed, control_pct, git_commit and timestamps: no PII, so Invariant 8 is untouched.

### ADR-013 · 2026-08-25 · `tenants` is tenant-scoped, not global
**Decision:** `tenants` moves from `GLOBAL_TABLES` to `TENANT_SCOPED_TABLES`, with RLS enabled, forced, and a self-row policy (migration `0004_tenants_rls`).
**Options:** self-row RLS policy; revoke SELECT from the app role entirely; leave global and narrow the metatest.
**Rationale:** Found by the isolation metatest, not by review. `tenants.tenant_id` is a genuine discriminator (it is the primary key), but the table had been classified global alongside `segment_priors`. Demonstrated leak: with `probe_a` bound, the app role read a second merchant's `name` and `config` — and §18 defines `config` as policy weights (λ, μ), rail preferences, fatigue caps, escalation ladder and kill switches. §18: "Cross-tenant reads are an incident, not a bug."
**Verified:** FK checks from the seven referencing tables still resolve, because referential-integrity triggers execute as the table owner and are not subject to RLS. Pinned by `test_foreign_keys_to_tenants_resolve_under_rls` rather than left as reasoning.
**Follow-on:** any future path needing to enumerate all tenants (a cross-tenant scheduler, the Phase 16 console) must use a privileged role, not the app role.

### ADR-014 · 2026-08-25 · Tenant resolution and webhook secret storage
**Decision:** `POST /v1/webhooks/razorpay/{tenant_id}` resolves the tenant from the path. A new `webhook_secrets` table holds `(tenant_id, secret_ref, active_from, active_until)` — **references only, never secret material**.
**Options:** per-tenant path + secrets table; secrets in `tenants.config` JSONB; single shared secret.
**Rationale:** §36 provides no webhook-secret storage anywhere, and `events_raw.tenant_id` is NOT NULL while a Razorpay webhook carries no tenant id of ours. The tenant must be known *before* the body is parsed, because §41.1 T6 requires unverified payloads never be persisted — you cannot decide where to reject without knowing whose secret to check. Overlapping validity windows give rotation.
**Constraint honoured:** the table stores a `secret_ref`, resolved against the environment/secrets manager at verification time. Storing the secret itself would contradict Phase 0's "secrets via environment injection from a manager, never files" and Invariant 7's treatment of credentials.

### ADR-015 · 2026-08-25 · Event log transport
**Decision:** Postgres `events_raw` is the log. An in-process projector claims unprocessed rows with `SELECT ... FOR UPDATE SKIP LOCKED` and stamps `processed_at`. No broker in Phase 1.
**Options:** Postgres as log; Redpanda/Kafka; NATS JetStream.
**Rationale:** §36 already gives `events_raw` a `processed_at TIMESTAMPTZ` column, which only makes sense as a projector watermark — the schema anticipates this design. Keeps the event log and the projected state in one transaction, so an event cannot be marked processed while its projection rolls back; the late-capture guard depends on that atomicity. §43's "consumer lag (projector) > 30s" becomes `now() - min(received_at) WHERE processed_at IS NULL`.
**Revisit:** §15 gives the projector "consumer offsets" and "consumer lag", implying a broker at scale. Phase 15 Hardening, or when capacity demands.

### ADR-016 · 2026-08-25 · Property-based testing
**Decision:** Add `hypothesis`. Use it for open-ended §40.3 properties; use exhaustive `itertools.permutations` for the fixed-set convergence exit criterion.
**Options:** Hypothesis + exhaustive; Hypothesis alone; itertools only.
**Rationale:** The exit criterion says *every* permutation converges. Hypothesis samples rather than enumerates, so a green Hypothesis run would not establish that claim; the fixed set is small enough to enumerate. Hypothesis still earns its place for `attempts_used ≤ attempt_budget` under arbitrary interleavings, and later for ledger and DP monotonicity properties — plus shrinking, which reduces a failing 12-event interleaving to a minimal repro.

### ADR-017 · 2026-08-25 · Metrics
**Decision:** `stale_transition` and projector lag emit as structured-log fields behind a thin internal counter interface. No metrics backend in Phase 1.
**Options:** structured-log counters; prometheus-client; OpenTelemetry.
**Rationale:** §43 defines thresholds but names no technology, and §42's SLOs are defined in Phase 15 Hardening. Choosing a backend now would foreclose it before the SLOs that should inform it exist, and a `/metrics` endpoint with no scraper is scaffolding ahead. The seam keeps call sites stable when a backend is chosen.

### ADR-014a · 2026-08-25 · Amendment — pre-authentication secret lookup
**Decision:** `prayas_active_webhook_secret_refs(tenant_id)`, a `SECURITY DEFINER` function owned by the migration role with `EXECUTE` granted only to `prayas_app`. Tenant context is bound **only after** the HMAC verifies.
**Problem it solves:** §18 requires `app.tenant_id` come from a verified token, never a request parameter — but a webhook carries no token, and the secret refs must be readable before the signature can be checked. Binding context from the URL path would have made the isolation guarantee depend on reasoning about reachability rather than on the database refusing.
**Implementation note:** `FORCE ROW LEVEL SECURITY` binds the owner too, so a SECURITY DEFINER function would have returned zero rows. Rather than dropping FORCE, `webhook_secrets` carries a second policy scoped `TO prayas_owner`. Permissive policies OR together within a role, and the role scope keeps the broad policy away from `prayas_app`, which still matches only `tenant_isolation`. `SET search_path = public, pg_temp` is mandatory on the function — without it a caller could shadow the table with a temp table.

### ADR-018 · 2026-08-25 · Cycle deadline
**Decision:** `cycles.deadline_at` = the next billing date, taken from the subscription's `current_end`.
**Options:** next billing date; provider `expire_by` with fallback; fixed window from `due_at`.
**Rationale:** No section defines how `deadline_at` is computed, yet §23.4 uses `now() > c.deadline_at` as a hard override to stop a cycle — a wrong value stops collection at the wrong time. §1 says "one execution plus up to three retries per cycle. Then the cycle is over", so the cycle boundary and the attempt budget describe the same thing rather than two independent notions of expiry.
**Fail-closed behaviour:** when no billing date can be resolved from the payload, the projector emits `cycle_deadline_unresolved` and **skips the write** rather than inventing a deadline.

### ADR-019 · 2026-08-25 · Carried without a separate ask
`httpx` added as a dev dependency: `fastapi.testclient` requires it, and there is no way to test an HTTP endpoint on the already-chosen framework without it. Same "mechanical consequence" reasoning as `uvicorn`, `pytest-asyncio` and `pytest-cov` in ADR-011. Note the tests use `httpx.AsyncClient` + `ASGITransport` rather than `TestClient` — `TestClient` runs its own event loop, which strands the asyncpg pool created on pytest-asyncio's loop, and Starlette now deprecates the `TestClient`/httpx pairing anyway.

### ADR-020 · 2026-08-25 · Rule pack storage
**Decision:** `prayas/gate/rules/*.yaml` is the source of truth; an Alembic migration loads it into `compliance_rules`; the gate reads the table at runtime. A test asserts YAML and table agree.
**Options:** YAML→table by migration; table-only seeded by migration; YAML-only at runtime.
**Rationale:** Makes §30.1's claim literally true — "a regulatory change is a data migration reviewable by a non-engineer". D7 open-sources the rule pack, and a YAML diff is what a compliance reviewer can actually read. The exit criterion "gate returns DENY when the rule store is unreachable" presumes a store that *can* be unreachable, which a local file is not.

### ADR-021 · 2026-08-25 · AFA cap reference data
**Decision:** New `regulatory_reference` table (mcc, cap_paise, regulator, citation, as_of), loaded from the same rule pack. Global, not tenant-scoped.
**Options:** reference table from rule pack; extra rules in `compliance_rules`; hardcoded in `ALLOWED_FUNCS`.
**Rationale:** The ₹15,000 / ₹1,00,000 thresholds are regulatory facts, so Invariant 10 demands a citation and `as_of` exactly as the predicates do. Encoding them as rules is blocked by the sandbox itself — §30.3's whitelist has no `ast.In`, so a predicate cannot express `mcc in exempt_list`, and one rule per exempt MCC would bury the audit record in near-duplicates. Hardcoding would make a regulator's change a deploy rather than a data migration.

### ADR-022 · 2026-08-25 · Chain verifier execution
**Decision:** Verification as a pure library function, a `python -m prayas.ledger.verify` entrypoint, and a Compose service looping on an interval.
**Options:** library+CLI+Compose; in-process asyncio task; library plus on-demand endpoint only.
**Rationale:** §15 catalogues `chain-verifier` as its own component; the Playbook wants it running continuously. An in-process task would compete with request handling on the API's event loop and duplicate work across replicas. Production scheduling stays a Phase 15 decision.

### ADR-023 · 2026-08-25 · Mutation testing
**Decision:** `mutmut`, scoped to `prayas/gate/`, as a separate nightly CI job.
**Options:** mutmut; cosmic-ray; mutatest.
**Rationale:** Scope matches the exit criterion's wording and keeps runtime tractable — mutation testing is far too slow to point at the whole codebase per commit, and §40.1 already puts slow tiers on a nightly cadence. mutatest's smaller mutation catalogue risks missing boundary and comparison-operator mutations, which are precisely the class this criterion exists to catch.

### ADR-024 · 2026-08-25 · Ledger genesis constant
**Decision:** `GENESIS = "0" * 64`, defined once in `prayas/ledger/chain.py` and never changed.
**Rationale:** §32 references `GENESIS` as the first record's `prev_hash` but never defines it. Any fixed value works; what matters is that it is fixed, since changing it would invalidate every existing chain. Recorded as an ADR rather than left as a constant precisely so nobody "tidies" it later.
**Not asked:** no alternative changes behaviour, so this was a conventional default rather than a decision.

### ADR-025 · 2026-08-25 · Predicate sandbox whitelist
**Decision:** §30.3's 22-node whitelist verbatim, plus three hardening additions. Surfaced by the `guard.sh` PreToolUse hook, which blocked the write until approved — the control working as designed.
**Additions beyond §30.3's sketch:**
1. `isinstance(result, bool)` check. Without it a predicate returning `"yes"` or `[]` would let Python truthiness decide a compliance verdict.
2. `afa_free_cap` injected per evaluation from `regulatory_reference` rather than a static dict (follows ADR-021).
3. `hours_since(None)` returns `-inf` rather than raising, so `hours_since(pdn_sent_at) >= 24` cleanly evaluates False for a missing PDN.
**Deliberately absent:** `ast.Attribute` (kills `().__class__.__bases__[0].__subclasses__()`), `Subscript`, `ListComp`, `Lambda`, `JoinedStr`, `NamedExpr`, `Pow`, `Starred`.
**Known residual:** `Mult` is permitted per §30.3, so `'x' * 999999999` inside a stored predicate could allocate before any comparison. Rules are reviewed data, so exposure is low; narrowing was offered and declined in favour of spec fidelity.
**Note:** the hook has no approved-state mechanism, so this one file was written via shell after approval. The hook remains armed and was re-verified blocking afterwards.

### ADR-026 · 2026-08-25 · Carried without a separate ask
`PyYAML` promoted to a declared runtime dependency. ADR-020 chose YAML as the rule-pack format and migrations must parse it, so this is a mechanical consequence rather than a choice — same reasoning as `uvicorn` (ADR-011) and `httpx` (ADR-019). It was previously present only transitively, which is not something to rely on.

### ADR-027 · 2026-08-26 · Making mutation testing actually run
**Problem:** `mutmut run` aborted with `BadTestExecutionCommandsException`. The real cause took three wrong hypotheses to find — the visible error was only "pytest exit code 4".
**Root cause:** mutmut copies `source_paths` into a `mutants/` directory and runs pytest there. With `source_paths = ["prayas/gate/"]`, `mutants/prayas/` contained only `gate/`, so the root conftest's `import prayas.db` raised `ModuleNotFoundError`, pytest exited 4 (usage error), and mutmut aborted.
**Resolution:** copy the whole package (`source_paths = ["prayas/"]`) so `mutants/` stays importable, and scope at run time instead: `mutmut run "prayas.gate.predicate.*"`.
**Second finding, more important:** `evaluate_rules` and `make_afa_free_cap` reported **"no tests"** — every mutant survived unexamined — because their pure-logic tests sat in `tests/integration/` while mutmut is scoped to `tests/unit/`. Moved to `tests/unit/test_gate_logic.py`. A pure function whose tests live in the wrong tier is worse than untested, because it looks covered.
**Also noted:** `mutmut results` reads a different store than `mutmut run` and reported all mutants "not checked" after a successful run. Outcomes are measured from the run's own output, not from `results`.

### ADR-028 · 2026-08-26 · Four sandbox escapes found by mutation testing
Mutation testing found four ways to defeat the empty-builtins control that **every existing test still passed**:
`eval(code, None, bindings)` · `eval(code, bindings)` · `{"XX__builtins__XX": {}}` · `{"__BUILTINS__": {}}`
Each leaves Python to auto-inject the real builtins module. They survived because `test_builtins_are_not_reachable` used `len(x)`, which the **AST whitelist** rejects before builtins are ever consulted — the first control masked the second entirely.
**Fix:** `test_the_builtins_namespace_is_genuinely_empty` asserts `safe_eval("not __builtins__", {}) is True`. A bare `Name` passes the whitelist and reaches the namespace, so it can see what is actually there: `not {}` is True, `not <module builtins>` is False.
**Lesson worth keeping:** layered controls hide each other from tests. Each layer needs a test that isolates it.

### ADR-029 · 2026-08-26 · Simulator RNG and determinism
**Decision:** `numpy.random.default_rng(seed)`, Generator threaded explicitly through every call, with `SeedSequence.spawn()` for independent per-entity streams. numpy added as a runtime dependency.
**Options:** numpy Generator threaded; stdlib `random.Random` instances; numpy with module-level global seeding.
**Rationale:** The exit criterion is "same seed produces byte-identical output". numpy explicitly guarantees stream reproducibility for a given bit generator; CPython guarantees the Mersenne Twister core but *not* that distribution algorithms stay fixed across versions, so a Python upgrade could break byte-identity for a reason unrelated to the simulator. Spawned streams mean adding a customer never shifts another's draws. Global seeding was rejected outright: test ordering would change output.

### ADR-030 · 2026-08-26 · Ground-truth storage
**Decision:** `sim_ground_truth` table keyed by cycle, RLS-enabled, **SELECT granted to the owner/evaluation role only — never to `prayas_app`**.
**Options:** separate table with no app grant; separate table with normal grants; file artifact outside the database.
**Rationale:** §38 states "models see only the observables". Withholding the grant makes leakage structurally impossible rather than a matter of discipline — a feature query cannot join labels it has no privilege to read, the same move ADR-004 used to make tenant isolation real. Keeps the labels joinable in SQL for §20's confusion matrix. Training-serving leakage is how a model posts excellent offline numbers and fails in production; §43 already pages on "online/offline feature parity".

### ADR-031 · 2026-08-26 · Simulator ingest path
**Decision:** Bulk generation inserts into `events_raw` and calls the production `project_tenant`. Separately, a small sample is HMAC-signed and POSTed through `/v1/webhooks/razorpay/{tenant}` and asserted to yield identical projected state.
**Options:** direct + webhook fidelity test; direct only; full webhook path for every event.
**Rationale:** The build list requires "the same projector — one code path, no drift", and the projector is shared either way. The fidelity test turns "no drift" from an assumption into an assertion, catching a divergence between the simulator's event construction and the webhook envelope parsing (e.g. how `cycle_ref` or `occurred_at` is derived). Full-webhook for all events would very likely miss the 60-second budget: ~30,000 in-process HTTP round-trips is a minute before any generation work.

### ADR-032 · 2026-08-26 · Band definitions
**Decision:** `hour_band` cut at the §1 NPCI execution windows — 5 bands: `<10:00`, `10:00–13:00`, `13:00–17:00`, `17:00–21:30`, `>=21:30`. `ticket_band` cut at the regulatory ceilings: `<₹1,000`, `₹1k–5k`, `₹5k–15k` (AFA cap), `₹15k–₹1,00,000`, `>=₹1,00,000`.
**Options:** NPCI-aligned 5 bands; hourly 24 bands; three-hour 8 bands.
**Rationale:** §36 keys `segment_priors` on both and defines neither. NPCI alignment spends resolution only where the system can act — hazard detail inside a peak window is unusable because the gate would DENY the attempt anyway. It also keeps cells dense enough to clear §27's `n_obs >= 50` floor: ~775 cells per mcc-rail versus ~3,720 for hourly, which would need ~186,000 observations and leave most cells falling back to the global prior. Three-hour bins were rejected because they straddle NPCI boundaries, blending times the system can use with times it cannot.
**Ticket edges carry citations** already (ADR-021's AFA ceilings), satisfying Invariant 10 without inventing thresholds.

### ADR-033 · 2026-08-26 · Per-customer observed hazard
**Decision:** Derived on demand from `cycles` joined to `mandates`, filtered `due_at <= as_of`. No materialised counter.
**Options:** derive point-in-time; new `customer_hazard` counts table; `customer_profiles.payday_posterior` JSONB.
**Rationale:** §37 calls point-in-time correctness non-negotiable, and deriving from immutable history gives it by construction. A running aggregate has no as-of semantics — replaying a three-month-old decision would read today's counts, which is precisely the training-serving skew §37 exists to kill. Cheap at this scale (tens of cycles per customer). Materialisation belongs to Phase 12, where §25/§26 actually specify the memory subsystem.

### ADR-034 · 2026-08-26 · Calibration numerics
**Decision:** numpy only. Log-loss, equal-width ECE binning and reliability curves implemented in-repo and validated against hand-computed closed-form values.
**Options:** numpy only; scikit-learn; scipy only.
**Rationale:** ~40 lines, and project standards forbid adding a dependency to avoid writing twenty. Keeping the definitions in-repo makes them auditable — equal-width versus equal-frequency binning changes ECE materially, and §21 says a miscalibrated model "computes the wrong money". §40.2 already sets this pattern by requiring the Wilson bound and CUSUM be checked against reference implementations rather than imported. sklearn would also not pay forward: Phase 11's GBM will likely want LightGBM or XGBoost.

### ADR-035 · 2026-08-26 · OPEN — simulator's code-05 composition
**Status:** recorded, not yet resolved. Surfaced by a Phase 4 test whose premise turned out to be false.
**Finding:** §20 characterises code 05 as "30-40% of all declines... roughly half being insufficient funds in disguise". The simulator produces an 05 population that is **~98% `no_funds`**, because `issuer_degraded` deterministically emits 91 and `limit_breach` emits 61 — neither ever masquerades as 05. §38 defines `mask_05_rate` solely as `P(05 | no_funds)`, so the simulator is faithful to §38 while not reproducing §20's account of the real signal.
**Measured consequence:** overall V0 cause accuracy is identical at `mask_05_rate` 0.0 and 1.0 (0.911 both), because masking moves `no_funds` from 51 to 05 and V0's default answer for 05 is already `no_funds`. Masking, as modelled, creates no difficulty at all.
**Why it matters:** Phase 11's EM model would be scored against a flattering V0 baseline. The 05 population is where §20 says the work is, and here it is nearly pure.
**Options when addressed:** extend masking to `issuer_degraded` and `limit_breach` (a deviation beyond §38's stated parameter, so Class A); or accept the gap and score Phase 11 only on the sub-population where causes genuinely compete.
**Pinned by:** `test_masking_does_not_change_overall_accuracy_here`, which fails if the composition changes — so this cannot be silently fixed or silently worsened.

### ADR-036 · 2026-08-27 · Mandate continuation value `W` placeholder
**Decision:** `W = 12 × A` until §22's revocation model lands in Phase 10.
**Options:** 12×A derived from Appendix C; W = 0; flat rupee constant from `tenants.config`.
**Rationale:** Appendix C fixes `discount_factor: 0.98` and `ltv_horizon_cycles: 24`. Holding `A_k` constant with a modest per-cycle survival decay and ~0.9 collection rate, §23.1's `Σ δ^k · P(alive) · A · P(collect)` lands near 12×A — derived from published config rather than invented. `W = 0` was rejected because it collapses §23.1's thesis entirely: the failure branch loses its penalty and Phase 5 would ship the exact single-cycle formulation the section says was the wrong design. A flat constant was rejected because continuation value is intrinsically per-mandate.

### ADR-037 · 2026-08-27 · Marginal revocation hazard `Δr` placeholder
**Decision:** Constant `Δr = 0.04` per attempt, matching Phase 3's `SimConfig.revocation_beta`.
**Options:** constant 0.04 matching the simulator; convex in attempts used; time-varying per §23.1's literal signature.
**Rationale:** The simulator already generates ground truth with 0.04 hazard added per consecutive failure. Reusing that exact value keeps the DP's assumed revocation model identical to the one the labelled data actually exhibits — so Phase 8 measures the sequencer rather than a mismatch between two disagreeing models. Convex-in-attempts is likely more realistic but would optimise against a world the ground truth does not describe. Time-varying was rejected because no spec section supplies a shape for that curve, so the values would be invented onto the money path.

### ADR-038 · 2026-08-27 · Cost model shape — deviation from §23.2
**Decision:** Cost becomes **two-dimensional**, `cost[b][t]`: flat fee + `β_fraud · k²` (k = attempts already spent, scaled to amount at stake) + `λ_annoyance` per attempt. Coefficients from Appendix C (`beta_fraud: 0.4`, `lambda_annoyance: 1.0`).
**Options:** extend to `cost[b][t]`; keep `cost[t]` flat; keep `cost[t]` convex in time-to-deadline.
**Rationale:** The Phase 5 build list requires "fees, **convex fraud risk**, annoyance", but §23.2 types cost as `cost[t]` — one dimension over slots. Convexity in attempt count is not expressible in that shape, since attempts live in `b`. Fraud exposure comes from repetition against a single mandate, so convexity belongs in `b`, not `t`. Preserves the DP's `O(B·H²)` complexity exactly — the array gains a dimension the loop already iterates. Keeping `cost[t]` flat would price the fourth attempt identically to the first, removing a real reason to stop early and leaving `Δr·W` as the sole brake.
**Deviation:** §23.2's `cost[t]` signature → `cost[b][t]`.

### ADR-039 · 2026-08-27 · Issuer health multiplier placeholder
**Decision:** `health[t] = 1.0` everywhere, behind a marked seam. No read of `issuer_health`.
**Options:** constant 1.0; read `issuer_health` with 1.0 fallback; crude proxy from recent attempts.
**Rationale:** §21's nowcast is Phase 11 and `issuer_health` is unpopulated. A neutral multiplier leaves the hazard exactly as the Phase 4 model estimated it, so Phase 8's number is attributable to something that exists. Wiring a read path against an empty table would ship code exercised by nothing for six phases — the scaffolding-ahead pattern the build rules forbid. A naive success-rate proxy was rejected because §21 specifies a Wilson lower bound and CUSUM precisely because the naive version misfires on sparse data, and a wrong multiplier corrupts every EV the sequencer computes.

### ADR-040 · 2026-08-27 · W-monotonicity — §23.2 STOP baseline contradicts §23.1
**Decision:** Keep §23.2 verbatim (STOP = 0). Assert §40.3's monotonicity property on **`V + W`**, the total position value, rather than on `V` alone.
**Options:** keep §23.2 and assert on V+W; change STOP's value to W; keep §23.2 and narrow the exit criterion to B and A only.
**Rationale:** §23.2 gives STOP a value of `0` while §23.1 pays `A + W` on success. Those baselines are incompatible — if stopping yields nothing, stopping loses the mandate, which contradicts §24.6 (back-off *preserves* the mandate) and the thesis that the mandate is the asset. The success branch effectively counts `W` as a gain although the mandate was already held.
**Consequence, measured:** `dV/dW = p − (1−p)·Δr`, negative whenever `p < Δr/(1+Δr)` ≈ **3.85%** at ADR-037's Δr = 0.04. Demonstrated with EV falling ₹2,894 → ₹2,789 → ₹2,578 as W doubles, staying positive throughout so `max(0, ·)` does not rescue it. **§40.3's "value monotone non-decreasing in W" is therefore false as §23.2 is written.**
**Resolution:** `d(V+W)/dW = 1 + p − (1−p)·Δr > 0` unconditionally, so the total position is monotone. That is the economically meaningful quantity and a real, falsifiable test — not a weakened one. The raw non-monotonicity is pinned by its own regression test so the boundary cannot drift unnoticed.
**Flagged for Phase 10:** changing STOP's value to `W` is the economically correct fix (attempt iff `pA − cost > W(1−p)Δr`) but materially changes stopping behaviour. It should be decided when §22's revocation model makes `W` a real output rather than a placeholder.

### ADR-041 · 2026-08-27 · DP vectorisation
**Decision:** Ship a layer-vectorised `solve`; keep `solve_reference`, a literal §23.2 transcription, and assert the two agree exactly.
**Options:** vectorise; ship the literal loop; loosen the 15 ms criterion.
**Rationale:** Not premature optimisation — **measured, the literal §23.2 loop takes 20.68 ms at B=4, H=720, missing the 15 ms exit criterion outright.** §23.2's "~8 ms vectorised" estimate does not survive a per-`(b,t)` numpy call at this size, where per-call overhead dominates. The rearrangement `ev(t,t') = U[t'] − Z[t']·(1/S[t])` makes each layer one outer product: **3.34 ms, 22% of budget, a 6.2× speedup.** Keeping the reference implementation means the shipped code has something independent to be checked against and a reader can still compare against the spec directly.

### ADR-042 · 2026-08-27 · Executor worker runtime
**Decision:** A separate Compose service (`executor`) alongside `migrate` and `api`, running a database-backed poll loop.
**Options:** separate service; thread inside the API process; separate service with APScheduler.
**Rationale:** Matches §15's component catalogue, where `executor` is its own deployable scaling on pending-timer depth. Decisive for the exit criteria: two of them require killing the worker mid-transaction, and only a real `SIGKILL` against a real process exercises Postgres's rollback — the mechanism actually being relied on. A thread cannot be killed independently of the API. APScheduler was rejected as a dependency duplicating what `FOR UPDATE SKIP LOCKED` over `scheduled_actions` already specifies.
**Consequence:** the worker drains **per tenant**, binding `app.tenant_id` for each, because RLS scopes every query to one tenant. This is not a workaround — §18 specifies "decision work is drained with weighted fair queuing keyed on tenant, so one merchant's month-start burst cannot starve another's".

### ADR-043 · 2026-08-27 · Provider seam and Phase 6 stand-in
**Decision:** A `RailProvider` protocol the executor codes against, plus an in-repo fake supporting deterministic success / 5xx / timeout / ambiguous outcomes by seed.
**Options:** protocol + in-repo fake; local HTTP stub server; per-test mocks with no protocol.
**Rationale:** Calling Razorpay is Class A on cost grounds and Phase 17 owns live integration, so Phase 6 needs a stand-in. A protocol gives Phase 17 a seam to drop the real adapter into and keeps provider specifics out of the executor. Making fault injection a first-class capability of the fake — rather than mocks stitched into each test — is what lets "10% injected timeouts, zero double debits" measure the executor rather than the mocks. An HTTP stub is more realistic but §40.7 places chaos testing in Phase 15.

### ADR-044 · 2026-08-27 · Executor timings
**Decision:** Lease 60s, poll interval 1s, claim batch 50.
**Options:** 60s/1s/50; 300s/5s/200; 15s/500ms/20.
**Rationale:** Appendix C specifies none of these. The lease must exceed the worst-case fire transaction, or two workers can hold one action — the atomic budget decrement and unique `idem_key` would still block a double debit, but that spends §31's defence-in-depth rather than keeping it. 60s is far above any plausible transaction time while returning a crashed worker's timers within a minute, well inside the sequencer's notice-lead margins. A 300s lease strands up to 200 timers per crash; a 15s lease risks stealing a live-but-slow worker's claim.

### ADR-045 · 2026-08-27 · Deterministic jitter source
**Decision:** Derive jitter from the idempotency key.
**Options:** from `idem_key`; from `action_id`; random per fire.
**Rationale:** §31's key is already a deterministic function of `(cycle_id, attempt_seq, action_type, amount_paise)`, so jitter becomes a pure function of the attempt's identity: a retry of the same logical attempt lands at the same instant instead of drifting, and §32's replay reconstructs the fire time from stored artifacts alone, which §40.9's replay-determinism requirement needs. `action_id` is not part of the decision record. Random jitter spreads load best but breaks replay outright.

### ADR-046 · 2026-08-27 · Tenant enumeration for platform-level workers
**Decision:** A `SECURITY DEFINER` function `prayas_tenant_ids()` returning **only** `tenant_id`, executable by `prayas_app`. Migration `0009_tenant_registry_fn`.
**Options:** SECURITY DEFINER function returning only ids; policy letting the app role read all of `tenants`; a separate executor role bypassing RLS.
**Rationale:** Phase 6 exposed a gap Phase 0 did not: §18 requires the executor drain per tenant, but `tenants` is under RLS (ADR-013) keyed on `app.tenant_id`, so a worker with no tenant bound sees zero rows and cannot discover which tenants to bind. The function is the narrowest opening — tenant *identifiers* are platform metadata, while `config` (policy weights, fatigue caps, kill switches) stays behind the policy, so a bug reading another tenant's config is still stopped by the database. Follows ADR-014a (`webhook_secrets`) and ADR-030 (`sim_ground_truth`) in carving out a specific owner-scoped read rather than weakening a table's policy. `search_path` is pinned inside the function, since a SECURITY DEFINER function with a caller-controlled search path is a privilege-escalation vector.

### ADR-047 · 2026-08-27 · Claim and fire are separate transactions
**Decision:** The worker claims in one transaction and fires each action in its own, rather than doing both in one.
**Rationale:** Recorded because it looks like a granularity choice and is actually a **correctness** one. Claiming locks `scheduled_actions`; firing locks `cycles` then `scheduled_actions`. Combining them acquires the two tables actions-first, while the late-capture guard acquires them cycle-first — opposite orders, which deadlocks under exactly the race Phase 6's adversarial test covers (observed as `DeadlockDetectedError` before the split). Separating them makes every path lock the cycle first. It also keeps a crash's blast radius to one action and stops one tenant's failure rolling back another's committed work.

### ADR-047 · 2026-08-27 · Survival and test statistics
**Decision:** Hand-roll Kaplan-Meier, the log-rank test and the SRM χ², validated against published reference data. No new dependency.
**Options:** hand-rolled with reference tests; add `lifelines`; add `scipy` only.
**Rationale:** Both comparisons here are two-arm, so the only distribution needed is χ² with **1 degree of freedom**, which is exactly `math.erfc(sqrt(x/2))` from the standard library — no approximation, no scipy. §40.2 already establishes the pattern of checking "Wilson bound and CUSUM against reference implementations". `lifelines` would pull scipy, pandas and matplotlib for two estimators; `scipy` alone solves the easy half (χ² tail) while leaving KM and log-rank to be written anyway. Keeping the estimators in-repo also keeps them auditable, which matters for a system whose claim is defensibility.

### ADR-048 · 2026-08-27 · What `decisions.propensity` records
**Decision:** The **arm-assignment probability** — P(this cycle received the arm it received), i.e. `control_pct` or its complement.
**Options:** arm-assignment probability; add exploration to the sequencer; record 1.0 and defer.
**Rationale:** §34 requires propensity logged at decision time, but the Phase 5 DP is deterministic — it argmaxes — so the chosen *action* has propensity 1.0 and action-level IPS degenerates. The genuine randomisation in this system is §33's arm assignment, so logging that makes IPS and doubly-robust valid at the arm level, which is what the A/B comparison actually needs. Adding exploration would mean deliberately firing slots the DP priced as worse, spending real attempt budget, and reopening Phase 5's closed money path — a product decision, not a measurement one. Recording a bare 1.0 would satisfy the exit criterion in letter while carrying no information.
**Recorded limitation:** action-level off-policy evaluation is unavailable until the sequencer explores. Stated rather than implied.

### ADR-049 · 2026-08-27 · CUPED pre-period
**Decision:** Per-customer recovery rate over the **3 cycles immediately preceding assignment**; customers with fewer than 3 prior cycles take the cohort mean.
**Options:** 3 prior cycles per customer; fixed 90-day calendar window; all prior cycles per mandate.
**Rationale:** §34 names the covariate but not the window. Appendix C already sets `memory.min_cycles_for_profile: 3`, so the window comes from published config rather than being invented, and it matches the unit of randomisation (§33 randomises per customer). A calendar window mixes weekly and monthly billing, so covariate reliability varies across the population. Using all prior cycles makes precision a function of mandate age, which correlates with survival — the outcome being measured — risking a tenure artefact in the adjusted estimate. Falling back to the cohort mean prevents θ being fitted on missing data, which would reintroduce the bias CUPED exists to remove.

### ADR-050 · 2026-08-27 · Baseline policy location
**Decision:** Extract the day-1/3/5 schedule into one shared policy module, cited by both §8's shadow audit and the experiment's control arm.
**Options:** shared module; experiment imports the Phase 2 shadow implementation; separate implementations.
**Rationale:** If the audit's baseline and the control arm ever diverge, the published claim that "the default retry behaviour produces N violations per 10,000 cycles" would describe a policy the experiment never ran, and the two numbers become quietly incomparable. Importing Phase 2's shadow harness directly would guarantee they match but couple the measurement plane to the gate's audit tooling, so a change made for the report silently alters the control arm. One definition both cite keeps the coupling explicit.

## Spec errata found (documentation only, no code impact)
- §18 cites "§34.4" for isolation-as-correctness; §34 is *Estimators* and has no subsections. Correct target is **§40.4**.
- §18 cites "(§27)" for per-tenant audit chains; §27 is *Cross-tenant learning*. Per-tenant chains are specified in **§32**.
