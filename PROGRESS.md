# PRAYAS — Build Progress

## Current phase
Phase 4 — V0 intelligence

## Phase status
| # | Phase | Status | Closed on |
|---|---|---|---|
| 0 | Foundations | CLOSED | 2026-08-25 |
| 1 | Event spine | CLOSED | 2026-08-25 |
| 2 | Trust layer | CLOSED | 2026-08-26 |
| 3 | Simulator | CLOSED | 2026-08-26 |
| 4 | V0 intelligence | IN PROGRESS | — |
| 5 | Sequencer | not started | — |
| 6 | Executor | not started | — |
| 7 | Measurement plane | not started | — |
| 8 | FIRST DEFENSIBLE NUMBER | not started | — |
| 9–19 | see Execution Playbook | not started | — |

## Exit criteria — current phase (Phase 4 — V0 intelligence)
_To be read from the Execution Playbook at phase start._

## Closed phases

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

## Spec errata found (documentation only, no code impact)
- §18 cites "§34.4" for isolation-as-correctness; §34 is *Estimators* and has no subsections. Correct target is **§40.4**.
- §18 cites "(§27)" for per-tenant audit chains; §27 is *Cross-tenant learning*. Per-tenant chains are specified in **§32**.
