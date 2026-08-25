# PRAYAS — Build Progress

## Current phase
Phase 1 — Event spine

## Phase status
| # | Phase | Status | Closed on |
|---|---|---|---|
| 0 | Foundations | CLOSED | 2026-08-25 |
| 1 | Event spine | IN PROGRESS | — |
| 2 | Trust layer | not started | — |
| 3 | Simulator | not started | — |
| 4 | V0 intelligence | not started | — |
| 5 | Sequencer | not started | — |
| 6 | Executor | not started | — |
| 7 | Measurement plane | not started | — |
| 8 | FIRST DEFENSIBLE NUMBER | not started | — |
| 9–19 | see Execution Playbook | not started | — |

## Exit criteria — current phase (Phase 1 — Event spine)
- [ ] Same event delivered 100× produces exactly one state transition
- [ ] Every permutation of a fixed event set converges to identical state (property test)
- [ ] `failed → captured` sequence leaves zero pending actions
- [ ] Unverified payloads are rejected and never persisted

## Closed phases

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

## Spec errata found (documentation only, no code impact)
- §18 cites "§34.4" for isolation-as-correctness; §34 is *Estimators* and has no subsections. Correct target is **§40.4**.
- §18 cites "(§27)" for per-tenant audit chains; §27 is *Cross-tenant learning*. Per-tenant chains are specified in **§32**.
