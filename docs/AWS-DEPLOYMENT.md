# Deploying PRAYAS to AWS with GitHub CI/CD

Companion to [DEPLOYMENT.md](DEPLOYMENT.md), which covers running locally and
the pipeline in the abstract. This document is the concrete AWS path:
ap-south-1, ECS Fargate, RDS, Secrets Manager, and a GitHub Actions pipeline
that authenticates by OIDC and never holds an AWS key.

Read section 0 before spending money. Two things block a live deployment and
neither is fixed by having credentials.

---

## Contents

0. [Read this first](#0-read-this-first)
1. [What you are deploying](#1-what-you-are-deploying)
2. [Prerequisites](#2-prerequisites)
3. [Region and residency](#3-region-and-residency)
4. [The database](#4-the-database)
5. [Secrets](#5-secrets)
6. [Container registry and OIDC](#6-container-registry-and-oidc)
7. [ECS services](#7-ecs-services)
8. [The GitHub pipeline](#8-the-github-pipeline)
9. [First deploy, in order](#9-first-deploy-in-order)
10. [Verifying it actually works](#10-verifying-it-actually-works)
11. [Advancing the ramp](#11-advancing-the-ramp)
12. [Cost](#12-cost)
13. [Teardown](#13-teardown)

---

## 0. Read this first

### Test credentials unlock Tier 1, not Tier 2

The three tiers are defined in [DEPLOYMENT.md §1](DEPLOYMENT.md). Briefly:

| Tier | What runs | What it needs |
|---|---|---|
| **0** | Console, audit, simulator. No money path. | Nothing external. |
| **1** | Full stack against Razorpay **test mode**. Sandbox mandates, sandbox debits. | Test API keys. |
| **2** | Real money, real mandates, a live merchant. | A registered business, a live Razorpay account, and §44's 44-day ramp. |

Razorpay test keys get you **Tier 1**. Tier 2 is not a configuration setting —
it is Phase 18's pilot, and §44 requires the adoption ramp to walk
`OBSERVE → SHADOW → CANARY → RAMP → FULL` with evidence gates between stages.
That takes a real merchant with real mandates and a minimum of 44 days. The
software cannot shortcut it, and `prayas/adoption/stages.py` enforces the gates
in code rather than trusting an operator to be honest about them.

So: this guide deploys **Tier 1** to AWS. That is the correct next step, and
everything in it is reused unchanged when Tier 2 arrives — the only difference
is which key is in Secrets Manager and which stage the tenant is in.

> The full, current list is **[BLOCKERS.md](BLOCKERS.md)**. The two below are
> the ones that stop an AWS deploy specifically.

### Two blockers, both real

**(a) The Subscriptions product is not enabled on the Razorpay account.**
(FINDING-P17-03 in `PROGRESS.md`.)

Verified against the live test API with the supplied keys:

```
payments       200
customers      200
subscriptions  401  Unauthorized
plans          401  Unauthorized
```

`payments` and `customers` authenticate fine, so the keys are valid. The 401 on
`subscriptions` and `plans` is Razorpay reporting that the **product is not
activated on the account**, not that the credentials are wrong.

This matters because in Razorpay a recurring mandate *is* a subscription. Until
Subscriptions is enabled you can deploy the stack, run the gate, the sequencer,
the console and the audit, but you cannot create a mandate or fire a debit —
which is precisely the lifecycle Phase 17 must demonstrate.

**Fix:** Razorpay Dashboard → Settings → Configuration → request/enable
**Subscriptions**. On a test account this is usually self-serve. Re-run the
probe in [section 10](#10-verifying-it-actually-works); when all four endpoints
return 200 the mandate path is open.

**(b) `httpx` is a dev-only dependency and the Razorpay adapter needs it at runtime.**
(FINDING-P17-02 in `PROGRESS.md`.)

`prayas/executor/razorpay.py` imports `httpx`. In `pyproject.toml`, `httpx` sits
in `[dependency-groups] dev` — it was pulled in for `fastapi.testclient`, not for
production. The runtime image does not have it:

```console
$ docker run --rm --entrypoint python prayas-api:latest -c "import httpx"
ModuleNotFoundError: No module named 'httpx'
```

Tests pass locally because dev dependencies are installed there. The image would
fail on import the moment the executor tried to fire. This is the same failure
shape as the Dockerfile not copying `packages/`.

Promoting a dependency from dev to runtime is a **Class A decision** under the
project's build constitution and has not been made. Until it is, do not deploy a
Tier 1 executor.
The one-line change, once approved, is to move `httpx>=0.28` into the main
`dependencies` list with a comment recording that ADR-092 requires it.

---

## 1. What you are deploying

Six workloads, from `docker-compose.yml`. The pipeline is
`api → projector → planner → executor`, and **every link must run** — each is a
separate process, and a missing one fails silently: the stack stays green and
simply stops doing work (FINDING-P17-04).

| Workload | Shape on AWS | Why |
|---|---|---|
| `api` | ECS Fargate service behind an ALB | The console and webhook receiver. Needs inbound HTTPS. |
| `projector` | ECS Fargate service, **no** load balancer | Drains `events_raw` into projected state. `python -m prayas.ingest.worker`. Replicas are safe — it claims with `SKIP LOCKED`. |
| `planner` | ECS Fargate service, **no** load balancer | §17.2's decision service: solves §23.2 and writes `scheduled_actions`. `python -m prayas.planner.worker`. |
| `executor` | ECS Fargate service, **no** load balancer | Claims actions with `FOR UPDATE SKIP LOCKED` and fires them. Never receives traffic. |
| `chain-verifier` | ECS service, or EventBridge scheduled task | `python -m prayas.ledger.verify --interval 300`. Separate from the API so a multi-minute chain walk never competes with request handling. |
| `migrate` | One-off ECS task, run by the pipeline | Runs as the **owner** role. Not a service. |

The executor is a separate service and not a thread inside the API. That is
deliberate: firing money must not share a process with request handling, and it
scales and fails independently.

> **Do not run two executors against one database while testing.** They compete
> for the same actions through `SKIP LOCKED`. That is correct production
> behaviour, but it makes local integration tests non-deterministic — a hazard
> already documented in DEPLOYMENT.md and one that cost real debugging time.

---

## 2. Prerequisites

```bash
aws --version          # v2
docker --version
gh --version           # optional, for setting secrets from the CLI
```

You need:

- An AWS account with permission to create IAM roles, VPCs, RDS, and ECS.
- A GitHub repository with Actions enabled.
- A domain you control, if you want TLS on a real hostname (recommended —
  Razorpay will not send webhooks to plain HTTP).

Set these once for the commands below:

```bash
export AWS_REGION=ap-south-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT=prayas
echo "$AWS_ACCOUNT_ID in $AWS_REGION"
```

---

## 3. Region and residency

**Everything goes in `ap-south-1` (Mumbai).** This is not a latency preference.

RBI requires payment system data to be stored in India. The Master Spec treats
residency as an invariant, not a configuration knob, and the codebase carries a
residency boundary that PII must not cross. A replica in `us-east-1`, a backup
bucket outside India, or a log sink in another region all breach it.

Concretely, keep in `ap-south-1`:

- RDS and every snapshot
- ECR images
- CloudWatch log groups
- S3 buckets holding backups or exports
- Secrets Manager secrets

`ap-south-2` (Hyderabad) is also in India and acceptable. Anywhere else is not.

Guard against drift by making the region explicit everywhere rather than relying
on a default profile.

---

## 4. The database

### 4.1 Create the instance

```bash
aws rds create-db-instance \
  --region "$AWS_REGION" \
  --db-instance-identifier ${PROJECT}-db \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --engine-version 16 \
  --allocated-storage 20 \
  --storage-encrypted \
  --backup-retention-period 7 \
  --no-publicly-accessible \
  --master-username prayas_owner \
  --master-user-password "$(openssl rand -base64 24)" \
  --db-name prayas
```

Note the three choices that are not defaults:

- `--storage-encrypted` — encryption at rest. Cannot be enabled later without a
  snapshot-restore dance.
- `--no-publicly-accessible` — the database is reachable only from inside the
  VPC. The pipeline therefore runs migrations as an **ECS task in the VPC**, not
  from a GitHub runner. Making RDS public so a runner can reach it is the
  tempting shortcut and it is the wrong one.
- `--backup-retention-period 7` — automated backups on. The restore drill in
  `docs/runbooks/` depends on these existing.

Capture the password you generated; you will put it in Secrets Manager in the
next section and then forget it.

### 4.2 Create the application role

PRAYAS deliberately uses **two** database roles. Migrations run as the owner;
the application runs as `prayas_app`, a non-owner that Row-Level Security
applies to with `FORCE`. Running the app as the owner would silently bypass
every RLS policy and with it tenant isolation — invariant 6.

Connect from inside the VPC (a bastion, or an ECS exec session) and run:

```sql
CREATE ROLE prayas_app WITH LOGIN PASSWORD 'the-app-password';
GRANT CONNECT ON DATABASE prayas TO prayas_app;
GRANT USAGE ON SCHEMA public TO prayas_app;
-- Table grants are handled by the migrations; do not grant them by hand.
```

`prayas_app` must never be given `BYPASSRLS` and must never own a table.

### 4.3 Verify isolation before trusting it

After the first migration, confirm RLS is actually forced:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relname IN ('decisions', 'mandates', 'actions');
```

All three must show `t` for **both** columns. `relrowsecurity` alone is not
enough — without `FORCE`, the owner bypasses the policy.

---

## 5. Secrets

Never put secrets in a task definition's `environment` block; they show up in
`describe-task-definition` and in the console. Use `secrets` with ARNs.

```bash
aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/db-app-url \
  --secret-string "postgresql+asyncpg://prayas_app:PASSWORD@ENDPOINT:5432/prayas"

aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/db-owner-url \
  --secret-string "postgresql+asyncpg://prayas_owner:PASSWORD@ENDPOINT:5432/prayas"

aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/razorpay-key-id     --secret-string "rzp_test_..."
aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/razorpay-key-secret --secret-string "..."
# Inbound webhook signing material. The NAME must match the secret_ref stored
# in the `webhook_secrets` table for that tenant (see below).
aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/webhook-secret-WH_TEST_V1 --secret-string "..."

# Pseudonymisation pepper. Losing this makes existing pseudonyms
# unreproducible; rotating it silently breaks linkage. Generate once, back up.
aws secretsmanager create-secret --region "$AWS_REGION" \
  --name ${PROJECT}/pseudonym-pepper --secret-string "$(openssl rand -hex 32)"
```

### Inbound webhook secrets work differently, and this trips people up

Outbound API calls use `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`. Inbound
webhook verification does **not** use a single `RAZORPAY_WEBHOOK_SECRET`
variable — there is no such setting, and adding one would defeat rotation.

The mechanism is two-part (ADR-014):

1. A row in the `webhook_secrets` table naming a `secret_ref` for the tenant.
   The table stores the *reference*, never the material.
2. An environment variable `PRAYAS_WEBHOOK_SECRET_<REF>` holding the material.

So a tenant whose `secret_ref` is `WH_TEST_V1` needs
`PRAYAS_WEBHOOK_SECRET_WH_TEST_V1` injected into the API task:

```json
{ "name": "PRAYAS_WEBHOOK_SECRET_WH_TEST_V1",
  "valueFrom": "arn:...:prayas/webhook-secret-WH_TEST_V1" }
```

`secret_ref` must match `[A-Z0-9][A-Z0-9_]*` or resolution raises.

**Rotation** is why it is built this way. Insert a second row with an
overlapping active window, provision `PRAYAS_WEBHOOK_SECRET_WH_TEST_V2`, and
both secrets verify at once — so no events are dropped while Razorpay switches
over. Then close the old row's `active_until`. Verification with an empty
secret list returns false, so an unprovisioned deployment rejects every webhook
rather than accepting every webhook.

### Rotate the keys that were pasted into a chat

The test keys used during development were shared in a transcript. Test mode
moves no money, so nothing is at risk, but rotate them before this becomes
muscle memory: Dashboard → Settings → API Keys → Regenerate. Then update
`${PROJECT}/razorpay-key-id` and `${PROJECT}/razorpay-key-secret`.

---

## 6. Container registry and OIDC

### 6.1 ECR

```bash
for repo in api executor; do
  aws ecr create-repository --region "$AWS_REGION" \
    --repository-name ${PROJECT}/${repo} \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE
done
```

`IMMUTABLE` matters. Images are tagged by commit SHA, and an immutable tag means
the artifact that passed CI is provably the artifact that ran. A mutable `latest`
destroys that property and with it the ability to say what was deployed when —
which the audit trail depends on.

### 6.2 GitHub OIDC — no long-lived AWS keys

Do not create an IAM user and paste an access key into GitHub secrets. Let
Actions assume a role by OIDC instead.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Trust policy — **note the `sub` condition**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::AWS_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main"
      }
    }
  }]
}
```

The `sub` condition is the whole security of this arrangement. Without it —
or with `repo:YOUR_ORG/*` — **any** GitHub repository can assume your role.
Pin it to the exact repo, and to `main` if only `main` deploys.

```bash
aws iam create-role --role-name ${PROJECT}-github-deploy \
  --assume-role-policy-document file://trust-policy.json
```

Attach a policy allowing only what the pipeline needs: `ecr:*` on those two
repositories, `ecs:UpdateService`, `ecs:RunTask`, `ecs:DescribeTasks`,
`iam:PassRole` for the task roles, and `secretsmanager:GetSecretValue` on
`${PROJECT}/*`. Not `AdministratorAccess`.

Then, in the repo:

```bash
gh secret set AWS_DEPLOY_ROLE_ARN \
  --body "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PROJECT}-github-deploy"
```

That is the only AWS secret GitHub needs to hold, and it is not a credential.

---

## 7. ECS services

### 7.1 Two IAM roles per task, and they are different

- **Execution role** — used by the ECS *agent* to pull the image and read the
  secrets it injects. Needs `AmazonECSTaskExecutionRolePolicy` plus
  `secretsmanager:GetSecretValue` on `${PROJECT}/*`.
- **Task role** — assumed by *your process*. For PRAYAS this needs almost
  nothing; keep it empty rather than reusing the execution role.

Collapsing them into one role hands the application the ability to read every
secret in the account, including ones it should never see.

### 7.2 API task definition

```json
{
  "family": "prayas-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/prayas-ecs-execution",
  "taskRoleArn": "arn:aws:iam::ACCOUNT:role/prayas-ecs-task",
  "containerDefinitions": [{
    "name": "api",
    "image": "ACCOUNT.dkr.ecr.ap-south-1.amazonaws.com/prayas/api:GIT_SHA",
    "portMappings": [{ "containerPort": 8000 }],
    "environment": [
      { "name": "PRAYAS_ENV", "value": "staging" }
    ],
    "secrets": [
      { "name": "PRAYAS_DATABASE_URL_APP", "valueFrom": "arn:...:prayas/db-app-url" },
      { "name": "PRAYAS_PSEUDONYM_PEPPER", "valueFrom": "arn:...:prayas/pseudonym-pepper" },
      { "name": "RAZORPAY_KEY_ID",         "valueFrom": "arn:...:prayas/razorpay-key-id" },
      { "name": "RAZORPAY_KEY_SECRET",     "valueFrom": "arn:...:prayas/razorpay-key-secret" },
      { "name": "PRAYAS_WEBHOOK_SECRET_WH_TEST_V1",
        "valueFrom": "arn:...:prayas/webhook-secret-WH_TEST_V1" }
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/prayas-api",
        "awslogs-region": "ap-south-1",
        "awslogs-stream-prefix": "api"
      }
    },
    "healthCheck": {
      "command": ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"],
      "interval": 30, "timeout": 5, "retries": 3, "startPeriod": 30
    }
  }]
}
```

The API gets **`PRAYAS_DATABASE_URL_APP`** — the non-owner URL. It must never
receive the owner URL; that would bypass RLS.

### 7.3 Executor task definition

Same shape, with three differences:

- No `portMappings`. It serves nothing.
- No load balancer, and no public subnet.
- `"command": ["python", "-m", "prayas.executor.worker"]`, matching `docker-compose.yml`.

Run **one** executor task to begin with. `SKIP LOCKED` makes several safe, but
start with one so that behaviour under load is something you observe
deliberately rather than discover.

### 7.4 Networking

- API tasks in **private** subnets, reached through an ALB in public subnets.
- Executor tasks in private subnets with a NAT gateway for outbound calls to
  Razorpay.
- RDS security group allows 5432 **only** from the ECS task security groups.
- ALB terminates TLS with an ACM certificate. Razorpay will not deliver webhooks
  over plain HTTP.

---

## 8. The GitHub pipeline

`.github/workflows/deploy.yml` already exists and already does the right things:

- Triggers on `workflow_run` after **CI succeeds**, not on push. A red build
  cannot deploy.
- Tags images by commit SHA.
- Smoke-tests the image by asserting the gate resolves to the rule pack —
  `engine.safe_eval is prayas_rulepack.safe_eval` — which catches the packaging
  mistake where the image builds but the compliance gate is not the real one.
- Runs migrations as the owner and verifies the ledger chain afterwards.
- Currently **reports and exits without deploying**, by design.

To make it deploy to AWS, replace the placeholder `deploy` job. The parts that
matter:

```yaml
permissions:
  id-token: write      # required for OIDC
  contents: read

steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
      aws-region: ap-south-1

  # Migrations run INSIDE the VPC, because RDS is not public.
  - name: Run migrations as owner
    run: |
      TASK_ARN=$(aws ecs run-task \
        --cluster prayas \
        --task-definition prayas-migrate \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG]}" \
        --query 'tasks[0].taskArn' --output text)
      aws ecs wait tasks-stopped --cluster prayas --tasks "$TASK_ARN"
      CODE=$(aws ecs describe-tasks --cluster prayas --tasks "$TASK_ARN" \
        --query 'tasks[0].containers[0].exitCode' --output text)
      test "$CODE" = "0" || { echo "migration failed"; exit 1; }

  - name: Deploy services
    run: |
      aws ecs update-service --cluster prayas --service prayas-api \
        --task-definition prayas-api:$REVISION --force-new-deployment
      aws ecs update-service --cluster prayas --service prayas-executor \
        --task-definition prayas-executor:$REVISION --force-new-deployment
      aws ecs wait services-stable --cluster prayas \
        --services prayas-api prayas-executor
```

Three things that are easy to get wrong:

1. **`aws ecs wait tasks-stopped` does not check the exit code.** A migration
   that fails still "stops". Read `exitCode` explicitly, as above, or a broken
   migration deploys green.
2. **`aws ecs wait services-stable` is what makes a rollback possible.** Without
   it the job returns success while tasks are still crash-looping.
3. **Migrate before deploying, always.** The app expects the schema it was
   built against.

---

## 9. First deploy, in order

```
1.  Enable Subscriptions on the Razorpay account       (blocker (a), P17-03)
2.  Approve moving httpx to runtime deps               (blocker (b), P17-02, Class A)
3.  Create RDS, ECR, secrets, IAM roles, ECS cluster   (sections 4-7)
4.  Push to main -> CI runs
5.  CI green -> deploy workflow builds and pushes images
6.  Migrations run as owner, inside the VPC
7.  Ledger chain verified
8.  api + executor services updated, wait for stable
9.  Smoke test against the ALB
10. Register the webhook URL in the Razorpay dashboard
11. Confirm every tenant is in OBSERVE
```

Step 11 is not optional. A tenant defaults to `OBSERVE` and `may_fire()` returns
false there, so a fresh deployment fires nothing even if everything else is
wired. That is the intended behaviour, and it is why the first deploy is safe.

---

## 10. Verifying it actually works

A green pipeline is not evidence. Check these.

### The Razorpay account is actually usable

```bash
python3 - <<'EOF'
import base64, json, os, urllib.request, urllib.error
kid, sec = os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]
auth = base64.b64encode(f"{kid}:{sec}".encode()).decode()
for path in ("payments?count=1", "customers?count=1", "subscriptions?count=1", "plans?count=1"):
    req = urllib.request.Request(f"https://api.razorpay.com/v1/{path}",
                                 headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"  {path.split('?')[0]:<14} {r.status}")
    except urllib.error.HTTPError as e:
        print(f"  {path.split('?')[0]:<14} {e.code}  <- product not enabled" if e.code == 401
              else f"  {path.split('?')[0]:<14} {e.code}")
EOF
```

All four must return 200 before the mandate lifecycle can be exercised.

### The gate is the real gate

```bash
aws ecs execute-command --cluster prayas --task "$TASK" --container api \
  --interactive --command "python -c \"
from prayas.gate import engine
import prayas_rulepack
assert engine.safe_eval is prayas_rulepack.safe_eval
print('gate resolves to the rule pack')\""
```

### RLS is forced, not merely enabled

The query in [section 4.3](#43-verify-isolation-before-trusting-it). Both
columns `t`.

### The ledger chain verifies, and verifies something

```bash
# Must report a non-zero number of tenants checked.
# "0 tenants verified" is a pass that proves nothing - this exact vacuous
# success hid a broken verifier for several phases (FINDING-P15-01).
```

### Webhook signatures reject a tampered body

```bash
# Correct signature -> 200. Flip one byte of the body -> 401, and nothing
# is written to events_raw. A receiver that accepts everything looks
# identical to one that works, right up until it matters.
curl -si "https://$HOST/v1/webhooks/razorpay/$TENANT" \
  -H "X-Razorpay-Signature: deadbeef" \
  -H "Content-Type: application/json" \
  -d '{"event":"payment.failed"}' | head -1
# expect: HTTP/2 401
```

Then confirm the rejected payload was **not** persisted — `events_raw` must
have no row for it. §41.1 T6 requires that unverified payloads are never
stored, and a receiver that persists first and verifies second passes a naive
smoke test while failing the actual requirement.

---

## 11. Advancing the ramp

Stage lives in `tenants.config` and is read at fire time. Advance it with the
adoption CLI, never with SQL — the gates in `prayas/adoption/stages.py` are the
point, and an `UPDATE` bypasses them:

```
OBSERVE  -> SHADOW   requires event completeness >= 0.999
SHADOW   -> CANARY   requires shadow agreement and no gate regressions
CANARY   -> RAMP     requires canary outcomes within tolerance
RAMP     -> FULL     requires the full ramp window
```

A refusal is the system working:

```
AdoptionError: cannot advance from OBSERVE:
  event completeness 0.9980 below 0.999
```

`FULL` retains a 15% holdout permanently. It is not retired once things look
good — it is how uplift stays measurable.

---

## 12. Cost

Rough ap-south-1 monthly, staging-sized:

| Item | Approx |
|---|---|
| RDS db.t4g.micro, 20 GB, 7-day backups | $15–20 |
| ECS Fargate, api 0.5 vCPU always on | $15 |
| ECS Fargate, executor 0.25 vCPU always on | $8 |
| ALB | $18–20 |
| NAT gateway | $32 + data |
| ECR, Secrets Manager, CloudWatch | $5 |
| **Total** | **~$95–100/month** |

The NAT gateway and the ALB together are half of it. For a staging environment
you can drop the NAT by putting the executor in a public subnet with a public IP
— acceptable for test mode, not for Tier 2.

---

## 13. Teardown

```bash
aws ecs update-service --cluster $PROJECT --service ${PROJECT}-api      --desired-count 0
aws ecs update-service --cluster $PROJECT --service ${PROJECT}-executor --desired-count 0
aws rds delete-db-instance --db-instance-identifier ${PROJECT}-db \
  --final-db-snapshot-identifier ${PROJECT}-final-$(date +%Y%m%d)
```

Take the final snapshot. `--skip-final-snapshot` on a database holding an
append-only audit ledger destroys the only copy of the decision history, and
invariant 5 exists because that history is meant to be permanent.

Delete the NAT gateway and ALB explicitly — they bill whether or not anything
is running behind them.
