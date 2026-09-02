#!/usr/bin/env bash
# Full reset and reseed of the demo fleet (Demo spec §R3).
#
# Everything the pipeline produces is produced by the pipeline: this posts
# signed webhooks and waits for the projector, planner and executor to work
# through them (N1). Onboarding state — tenants, secret refs, mandates — is
# written directly, because a mandate is not pipeline output and the projector
# requires the row to exist before it will fold events onto it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SEED="${SEED:-prayas-demo-2026}"
PORT="${API_PORT:-8010}"
export PRAYAS_DATABASE_URL_OWNER="${PRAYAS_DATABASE_URL_OWNER:-postgresql+asyncpg://prayas_owner:prayas_local_dev_only@localhost:5432/prayas}"
export PRAYAS_DATABASE_URL_APP="${PRAYAS_DATABASE_URL_APP:-postgresql+asyncpg://prayas_app:prayas_local_dev_only@localhost:5432/prayas}"
export PRAYAS_ENV="${PRAYAS_ENV:-local}"
export PRAYAS_PSEUDONYM_PEPPER="${PRAYAS_PSEUDONYM_PEPPER:-local_dev_pepper_not_a_secret}"

say() { printf '%s\n' "$*"; }
START=$(date +%s)

say "── reset ────────────────────────────────────────────────────────────"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 60); do
  [ "$(docker inspect prayas-api-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
say "  stack healthy"

say
say "── onboard ──────────────────────────────────────────────────────────"
# .env is gitignored and holds secrets; keep a copy before rewriting the refs.
cp -f .env .env.bak 2>/dev/null || true
OUT=$(uv run python scripts/seed_demo.py --seed "$SEED" --stage onboard 2>&1)
printf '%s\n' "$OUT"

# The API verifies signatures from its environment, so the secrets must be in
# place BEFORE a single webhook is posted — otherwise every one is rejected 401
# and the pipeline is fed nothing. Secrets are derived from the seed, so a
# reseed reuses them and this is idempotent.
grep -vE '^PRAYAS_WEBHOOK_SECRET_WH_(FITFIRST|STREAMLY|EDTECHCO)_V1=' .env > .env.tmp 2>/dev/null || cp .env .env.tmp
printf '%s\n' "$OUT" | grep -oE 'PRAYAS_WEBHOOK_SECRET_WH_[A-Z]+_V1=[A-Za-z0-9]+' >> .env.tmp
mv .env.tmp .env
docker compose up -d api >/dev/null 2>&1
for _ in $(seq 1 40); do
  [ "$(docker inspect prayas-api-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
say "  secrets installed, API restarted"

say
say "── ingest ───────────────────────────────────────────────────────────"
uv run python scripts/seed_demo.py --seed "$SEED" --stage events

say
say "── pipeline drain ───────────────────────────────────────────────────"
say "  waiting for the projector to fold every event…"
LAST=""
for _ in $(seq 1 200); do
  N=$(docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
        "SELECT count(*) FROM events_raw WHERE processed_at IS NULL;" 2>/dev/null | tr -d '\r ')
  [ "${N:-1}" = "0" ] && { say "  drained"; break; }
  [ "$N" != "$LAST" ] && { say "  $N unprocessed…"; LAST="$N"; }
  sleep 3
done

say
say "── plan ─────────────────────────────────────────────────────────────"
# The planner runs in-process here: it is the same `worker.tick`, but the
# service polls every 15 s, which would turn seeding into twenty minutes of
# waiting. The container keeps running for the demo itself.
docker compose stop planner >/dev/null 2>&1
uv run python scripts/seed_demo.py --stage plan

say
say "── settle ───────────────────────────────────────────────────────────"
say "  notices first, then debits 25 virtual hours later"
docker compose stop executor >/dev/null 2>&1
uv run python scripts/seed_demo.py --stage settle
docker compose up -d planner executor >/dev/null 2>&1

say
say "── fleet ────────────────────────────────────────────────────────────"
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc "
  SELECT '  '||rpad(t.tenant_id,10)
       ||' stage='||rpad((t.config->>'adoption_stage'),2)
       ||' mandates='||lpad((SELECT count(*) FROM mandates m WHERE m.tenant_id=t.tenant_id)::text,6)
       ||' cycles='||lpad((SELECT count(*) FROM cycles c WHERE c.tenant_id=t.tenant_id)::text,6)
       ||' events='||lpad((SELECT count(*) FROM events_raw e WHERE e.tenant_id=t.tenant_id)::text,7)
    FROM tenants t ORDER BY t.tenant_id;" 2>/dev/null

say
say "── ledger ───────────────────────────────────────────────────────────"
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc "
  SELECT '  '||rpad(action_type,14)||' '||rpad(verdict,6)||' '||count(*)
    FROM decisions GROUP BY action_type, verdict ORDER BY action_type, verdict;" 2>/dev/null
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc "
  SELECT '  refusals on '||rule||': '||n FROM (
    SELECT c->>'rule_id' AS rule, count(*) n FROM decisions d,
           LATERAL jsonb_array_elements(d.compliance_checks) c
     WHERE d.verdict <> 'ALLOW' AND c->>'verdict' <> 'ALLOW' GROUP BY 1) x;" 2>/dev/null
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc "
  SELECT '  interventions '||kind||': '||count(*) FROM interventions GROUP BY kind;" 2>/dev/null

say "  elapsed $(( $(date +%s) - START ))s"
