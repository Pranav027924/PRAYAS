# Deploying PRAYAS

End to end, with GitHub Actions. Written for someone who has not read the rest
of this repository.

Everything below has been run. Where a command's output is quoted, that is the
output it produced.

---

## Contents

1. [What is actually blocking a live deployment](#1-what-is-actually-blocking-a-live-deployment)
2. [Run it locally in ten minutes](#2-run-it-locally-in-ten-minutes)
3. [Before you deploy anywhere](#3-before-you-deploy-anywhere)
4. [The pipeline](#4-the-pipeline)
5. [Wiring a deploy target](#5-wiring-a-deploy-target)
6. [First deploy, in order](#6-first-deploy-in-order)
7. [Advancing the adoption ramp](#7-advancing-the-adoption-ramp)
8. [Operating it](#8-operating-it)
9. [What is still missing before real money](#9-what-is-still-missing-before-real-money)

---

## 1. What is actually blocking a live deployment

> **[BLOCKERS.md](BLOCKERS.md)** is the current, specific account: what needs an
> outside party (a customer authorising a mandate, DLT registration for the
> pre-debit notice, three tenants for cross-tenant priors, an AWS account) and
> what is simply unbuilt. It also carries the end-to-end proof that the pipeline
> runs. Read it before this section, which is the general shape rather than the
> live status.

Two things, and they are not equally hard.

| Blocker | Reality | Cost |
|---|---|---|
| **Razorpay credentials** (Phase 17) | **Test mode is free and self-serve.** Sign up, switch the dashboard to Test Mode, generate API keys. No business verification, no money movement, no merchant | An afternoon |
| **A real merchant** (Phase 18) | Needs someone who will let you collect their subscriptions, plus **44 days** — §44 requires 14 canary days and 30 days of green guardrails | Weeks, and a relationship |

So "we cannot deploy without credentials" is only half true. The system is
deployable **today** in a stage that fires nothing, and Phase 17 unblocks with a
free signup.

### The three tiers

**Tier 0 — deploy now, fire nothing.**
§44's `OBSERVE` ingests and decides nothing; `SHADOW` decides and logs but fires
nothing. This is enforced **in the executor** (ADR-089), checked at fire time
rather than only at scheduling — so an action scheduled before a stage change
still cannot fire. A tenant with no recorded stage reads as `OBSERVE`, which
means a fresh deployment is safe **by default rather than by discipline**.

You get a running service, real migrations, the console, the audit artifact, and
the whole pipeline exercised — with `FakeProvider` and no possibility of a debit.

**Tier 1 — Razorpay test mode.**
Free keys unlock the three blocked Phase 17 criteria: the full test-mode
lifecycle, contract tests against real payload shapes, and reconciliation
against provider state.

This is the tier that closes FINDING-P17-01. `prayas/ingest/envelope.py` has
carried a docstring since Phase 1 saying its extraction rules "need validation
against Razorpay test mode" — **they are still unvalidated**, and the contract
fixtures are marked `"_source": "constructed"` with a test asserting none claims
to be a real capture.

**Tier 2 — a pilot merchant.** Phase 18. No shortcut: "14 days, zero double
debits" cannot be compressed, and "merchant would keep using it" needs a
merchant.

---

## 2. Run it locally in ten minutes

```bash
git clone https://github.com/<owner>/PRAYAS.git && cd PRAYAS
cp .env.example .env          # defaults are safe for local; nothing real in them
docker compose up -d --build
```

Seven services start: `postgres`, `migrate` (runs once), `api`, `projector`,
`planner`, `executor`, `chain-verifier`.

The four that form the pipeline run in order — `api` receives the webhook,
`projector` turns it into state, `planner` decides and queues, `executor`
fires. Stopping any one of them stops the system doing work **without turning
anything red**: health stays `ok`, webhooks keep returning 200, and the queue
simply never fills. If nothing is happening, check that all four are up before
looking anywhere else.

> **Always pass `--build`.** Plain `docker compose up -d` reuses whatever image
> is already on the machine and will not rebuild when the source changes. The
> stack comes up green while running old code — which is how a verified fix can
> appear to fail, or a broken one appear to pass. If behaviour disagrees with
> the source, check the image before debugging the code:
>
> ```bash
> docker compose exec api sh -c "grep -c <a-string-you-just-added> /app/prayas/<file>.py"
> ```

> **Port 8000 is a common collision.** If another project holds it, the API's
> port publish silently does not take effect: the container runs, `docker ps`
> shows `8000/tcp` with no `->` mapping, and `curl localhost:8000/health` cheerfully
> answers *from the other application*. Set `API_PORT` in `.env` to something free
> and confirm with `docker compose port api 8000`. PRAYAS health returns
> `{"status":"ok","database":"ok"}` — a reply without the `database` key is not
> this service.

```bash
$ docker compose ps --format '{{.Service}}\t{{.Status}}'
api               Up (healthy)
chain-verifier    Up
executor          Up
postgres          Up (healthy)

$ curl -s localhost:8000/health
{"status":"ok","database":"ok"}
```

**If port 8000 is taken**, set `API_PORT`. The container always listens on 8000
internally:

```bash
API_PORT=8055 docker compose up -d
```

> **Do not leave this stack running against a database you also run the test
> suite against.** The executor claims work with `FOR UPDATE SKIP LOCKED`, so a
> live container and a test run compete for the same rows — and the test that
> notices is `test_injected_timeouts_produce_no_double_debits_and_all_reconcile`,
> which then reports 34 submissions where it expected 40. That looks exactly
> like a money-safety regression and is not one. `docker compose stop executor`
> before running the suite, or point the stack at its own database.
>
> The same applies to `scripts/backup-restore-drill.sh`: it drops the schema,
> and anything polling that database will error until migrations finish.

### Verify it properly

The health check only proves the API is up. These three prove the parts that
matter:

```bash
# 1. The compliance audit — no database, no credentials, no network.
$ docker compose exec api prayas-audit
The default recurring-retry behaviour widely deployed today — retries on
days 1, 3, 5 at 10:00 IST — produces 30,000 debit attempts outside NPCI
execution windows and 30,000 debits without valid 24-hour pre-debit notice,
per 10,000 cycles.

# 2. The console refuses unauthenticated requests.
$ curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/console/replay/anything
401

# 3. The ledger verifier.
$ docker compose logs chain-verifier --tail 1
{"level":"WARNING","message":"ledger.nothing_to_verify",
 "detail":"no tenants found — this is expected on a fresh deployment and is
           a problem on an established one. Nothing was verified."}
```

**Read that third one carefully.** On a fresh deployment "no tenants" is
correct. On an established one it means the verifier is checking nothing — which
is what it silently did here for fifteen phases (FINDING-P15-01) while
reporting `tenants: 0, breaks: 0` and exiting successfully. It now says so at
WARNING, because a verifier that passes vacuously is worse than none: it
produces evidence of a property it never tested.

---

## 3. Before you deploy anywhere

### Data residency is not optional

§41.3: *all primary stores, backups, and replicas in Indian regions.* Decide the
target on this basis first.

| Cloud | Indian regions | Typical shape |
|---|---|---|
| AWS | `ap-south-1` (Mumbai), `ap-south-2` (Hyderabad) | ECS/Fargate + RDS |
| GCP | `asia-south1` (Mumbai), `asia-south2` (Delhi) | Cloud Run + Cloud SQL |
| Azure | Central India, South India | Container Apps + Flexible Server |
| Fly.io | `bom` (Mumbai) | Use an external managed Postgres |

A US-region deployment of this system is a compliance problem, not a latency
one.

### Secrets

Five, all required. The application **fails closed** without them rather than
starting with a default — a default secret in a repository is a published
secret.

| Secret | Used by | If missing |
|---|---|---|
| `PRAYAS_DATABASE_URL_OWNER` | migrations only | Deploy fails |
| `PRAYAS_DATABASE_URL_APP` | the service | Will not start |
| `PRAYAS_APP_DB_PASSWORD` | role provisioning | Migrations fail |
| `PRAYAS_PSEUDONYM_PEPPER` | §28 forgetting | `forget()` refuses |
| `PRAYAS_CONSOLE_TOKEN_SECRET` | console auth | Every console request 401s |

Two of these deserve more than a table row.

**`PRAYAS_PSEUDONYM_PEPPER` is permanent per environment.** Rotating it breaks
every pseudonym already written, so erased customers stop matching their own
suppression rows and could be contacted again. Treat it like a key you cannot
re-key. Generate once: `openssl rand -hex 32`.

**The owner and app database URLs are different credentials on purpose**
(ADR-004). The app role owns nothing and cannot create tables; migrations need a
role that can. Giving the service the owner credential would discard the
separation that makes every tenant-isolation test meaningful — those tests run
as the non-owner role precisely so the RLS policies apply unconditionally.

Add them under **Settings → Secrets and variables → Actions**, scoped to an
Environment (`staging`, `production`) so production secrets are not readable
from a staging run.

---

## 4. The pipeline

```
push to main
   │
   ├─► CI  (.github/workflows/ci.yml)
   │     lint · format · mypy --strict · migrations · ~1,340 tests
   │     rule-pack conformance · bandit (SAST) · pip-audit
   │
   └─► Deploy  (.github/workflows/deploy.yml)     ← only if CI passed
         build   → image tagged by commit SHA → GHCR
                 → smoke-tests the image itself
         migrate → alembic upgrade head (as OWNER)
                 → verifies the ledger hash chain survived
         deploy  → your target
         smoke   → /health until it answers
```

Three properties worth understanding, because each exists for a reason:

**`workflow_run`, not `push`.** A red build cannot produce an image at all, and
the image is built from **the commit CI verified** rather than from whatever
`main` points at by the time the deploy starts.

**The image is smoke-tested before it ships.** The build job runs the image and
asserts the gate can load its evaluator:

```python
assert engine.safe_eval is prayas_rulepack.safe_eval
```

That check exists because it has already caught something. The Dockerfile copied
`prayas/` and `migrations/` but not `packages/`, so after the rule pack became a
workspace member the image **built cleanly and could not import the compliance
gate**. CI was green and the deploy was broken. Only running the artifact
revealed it.

**Migrations verify the ledger afterwards.** A migration that broke the hash
chain would otherwise be discovered by the nightly verifier hours later.

### Concurrency

`concurrency: deploy-${environment}` with `cancel-in-progress: false`. Two
concurrent `alembic upgrade head` runs against one database is the failure this
prevents; cancelling mid-migration would be worse than queueing.

---

## 5. Wiring a deploy target

The `deploy` job currently reports the image and exits **without deploying**.
That is deliberate — it does not pretend to have deployed something. Replace it
with one of these.

### Google Cloud Run (asia-south1)

```yaml
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}

      - uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: prayas-api
          region: asia-south1
          image: ghcr.io/${{ github.repository }}:${{ needs.build.outputs.tag }}
          flags: --min-instances=1 --port=8000
```

Use Workload Identity Federation, not a service-account JSON key: a key in a
GitHub secret is a long-lived credential with no expiry.

### AWS ECS (ap-south-1)

> **Full step-by-step AWS path: [AWS-DEPLOYMENT.md](AWS-DEPLOYMENT.md).** It
> covers RDS and the two database roles, Secrets Manager, GitHub OIDC, the ECS
> task definitions, cost, and teardown — plus the two blockers that must clear
> before a Tier 1 executor can run. The snippet below is only the deploy step.

```yaml
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE }}   # OIDC, not access keys
          aws-region: ap-south-1

      - uses: aws-actions/amazon-ecs-deploy-task-definition@v2
        with:
          task-definition: deploy/ecs-task.json
          service: prayas-api
          cluster: prayas
          wait-for-service-stability: true
```

### Fly.io (bom)

```yaml
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --image ghcr.io/${{ github.repository }}:${{ needs.build.outputs.tag }}
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

### The executor is a second service

`CMD` runs the API. The executor — durable timers, fire-time revalidation,
outbox relay — is a **separate long-running process**:

```bash
python -m prayas.executor.worker
```

Deploy it as its own service or task **from the same image**. A deployment with
the API but no executor ingests webhooks, decides nothing, and fires nothing —
which looks healthy and is not. Locally, compose already runs it; on a cloud
target you must define it.

The chain verifier is a third, on a schedule:

```bash
python -m prayas.ledger.verify --interval 3600
```

---

## 6. First deploy, in order

1. **Provision Postgres in an Indian region.** Create the owner role. The
   application role is created by migration `0001`, not by you.

2. **Generate and set the five secrets** on the GitHub Environment.

3. **Deploy at Tier 0.** No Razorpay account needed. Nothing can fire.

4. **Verify the deployment, not just the process:**

   ```bash
   curl https://<host>/health                     # {"status":"ok","database":"ok"}
   ```
   ```sql
   SELECT tenant_id, config->>'adoption_stage' FROM tenants;
   -- NULL or 0 = OBSERVE: ingesting, deciding nothing, firing nothing
   ```

5. **Confirm the verifier sees something.** On an established deployment,
   `ledger.nothing_to_verify` at WARNING means it is checking nothing.

6. **Run the audit** and keep the output. It needs no credentials and is the one
   artifact from this system with value independent of the rest of it (§8).

---

## 7. Advancing the adoption ramp

Stages are gated **in code, not in a document** (§44, ADR-089). You cannot
advance by editing a config value — `promote()` refuses without evidence and
names what is missing:

```python
from prayas.adoption.stages import Evidence
from prayas.adoption.store import promote

await promote(conn, tenant_id, Evidence(
    event_completeness=0.9995,
    projection_matches_days=7,
))
# AdoptionError: cannot advance from OBSERVE:
#   event completeness 0.9980 below 0.999
```

| Stage | Behaviour | To leave it |
|---|---|---|
| 0 Observe | Ingest only | completeness ≥ 99.9%, projection matches 7 days |
| 1 Shadow | Decide, **fire nothing** | ≥10,000 decisions, 0 gate errors, ECE < 0.05, audit report |
| 2 Canary | 1% of mandates, no holdout | 14 days, 0 double debits, 0 violations, revocation not above baseline |
| 3 Ramp | 10%, 15% holdout inside it | recovery CI excludes zero, survival CI not negative, guardrails green 30 days |
| 4 Full | Portfolio-wide | — holdout **never** retired |

`set_stage()` overrides without evidence. It exists for onboarding and rollback,
which are deliberate human acts — a different function from `promote` precisely
so that advancing cannot borrow its permissiveness.

**One subtlety.** Criteria that count bad things (`gate_errors`,
`double_debits`) cannot default to "unproven" — zero *is* the passing value.
What stops "we saw none because we never looked" is the volume criterion beside
each: `shadow_decisions=0` blocks before zero errors can be read as evidence.

---

## 8. Operating it

### Stopping collection immediately

Do **not** redeploy. Use the kill switch — under sixty seconds, three
granularities, failing closed:

```python
from prayas.executor.killswitch import stop, PLATFORM
await stop(conn, scope=PLATFORM, reason="incident 1234")
```

An unreadable switch stops firing, because you reach for this during an incident
— exactly when the database is least healthy. Restarting is deliberately **not**
symmetric: there is no `resume_all`, because an accidental un-stop should not
cost as little as the stop did.

See [runbooks/emergency-stop.md](runbooks/emergency-stop.md). The other eight
runbooks are in [runbooks/](runbooks/); the emergency stop is drilled on every
CI run rather than written and filed.

### Rollback

**The application** rolls back by redeploying an earlier SHA tag. Every image is
tagged by commit, so there is always a name for the thing to roll back to.

**Migrations do not roll back cleanly** and should not be assumed to. Prefer
rolling forward. If you must reverse one, restore from backup and replay:

```bash
scripts/backup-restore-drill.sh
```

That script refuses to run against an empty database — a restore that brings
back nothing satisfies every check vacuously — and verifies the hash chain
afterwards rather than assuming it survived.

### Changing a compliance rule

**Add a new version. Never edit one in place.** The ledger records which version
governed each past decision. See
[runbooks/rule-change-deployment.md](runbooks/rule-change-deployment.md) — the
failure mode is subtle enough that it happened here: bumping `version:` while
*replacing* the old entry passes review and destroys the replay property on
every fresh database.

---

## 9. What is still missing before real money

Deploying does not close Phases 17 or 18. Outstanding, in the order it matters:

- **`envelope.py`'s payload extraction is unvalidated against Razorpay.** Tier 1
  fixes this. Until then the contract fixtures are marked `"constructed"` and a
  test asserts none claims to be a recording.
- **Reconciliation over 7 days has never run.**
- **The Phase 18 pilot has not started** — no real merchant, no real number.
- **No DAST**, recorded as an accepted gap (ADR-085): it scans a running web
  application and this surface is one webhook endpoint.
- **T8 (credential compromise)** and **T9 (denial of wallet)** are *accepted*
  risks, not mitigated ones. Rotation, short-lived tokens and per-tenant quotas
  belong to infrastructure this repository does not provision.
- **FINDING-P11-02**: V1 beats V0 by 16.8% on held-out log-loss and that
  advantage **does not reach the money**. Do not quote a model improvement as a
  revenue improvement.
- **FINDING-P8-01**: the Phase 8 lift is still not attributable to the liquidity
  model in 8 of 9 perturbations.

The last two are not deployment blockers. They are things not to say in a pitch.
