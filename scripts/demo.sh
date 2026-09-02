#!/usr/bin/env bash
# One-command demonstration that PRAYAS recovers money.
#
# Brings up the stack, onboards a merchant, feeds it a real failed-payment
# webhook, and drives the result through to a lawful debit — showing the
# compliance decision at every step.
#
# Everything here is the production code path. The only stand-ins are the two
# that need an outside party: the payment rail (FakeProvider, as in any
# test-mode deployment) and the SMS channel (needs DLT registration — see
# docs/BLOCKERS.md §1.2).
set -euo pipefail

TENANT="${1:-demo$(date +%H%M%S)}"
PORT="${API_PORT:-8010}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PRAYAS_DATABASE_URL_OWNER="${PRAYAS_DATABASE_URL_OWNER:-postgresql+asyncpg://prayas_owner:prayas_local_dev_only@localhost:5432/prayas}"
export PRAYAS_DATABASE_URL_APP="${PRAYAS_DATABASE_URL_APP:-postgresql+asyncpg://prayas_app:prayas_local_dev_only@localhost:5432/prayas}"
export PRAYAS_ENV="${PRAYAS_ENV:-local}"
export PRAYAS_PSEUDONYM_PEPPER="${PRAYAS_PSEUDONYM_PEPPER:-local_dev_pepper_not_a_secret}"

say() { printf '%s\n' "$*"; }
rule() { printf '%s\n' "══════════════════════════════════════════════════════════════"; }

rule
say "PRAYAS — recovery demonstration"
say "merchant: $TENANT"
rule

say
say "[1/5] starting the stack"
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 40); do
  [ "$(docker inspect prayas-api-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 3
done
docker compose ps --format '{{.Name}}  {{.State}}' | sed 's/^/      /'

say
say "[2/5] onboarding the merchant"
say "      a new tenant starts in OBSERVE and fires nothing (§44); --stage FULL"
say "      is the deliberate act of turning collection on for this demo."

# FULL keeps a permanent 15% holdout — a control arm that is never acted on,
# which is how uplift stays measurable. A mandate landing there is correct
# behaviour and would make this demo do nothing, so pick a subject that is in
# the treatment arm and say so.
MANDATE=$(uv run python -c "
from prayas.adoption.cohort import Arm, arm_for
from prayas.adoption.stages import Stage
t = '$TENANT'
for i in range(1, 60):
    m = f'sub_{t}_{i}'
    if arm_for(Stage.FULL, tenant_id=t, mandate_id=m) is Arm.TREATMENT:
        print(m); break
")
say "      15% of mandates are held out permanently as a control arm; this demo"
say "      follows one in the treatment arm: $MANDATE"
OUT=$(uv run python scripts/bootstrap_tenant.py "$TENANT" --stage FULL --mandate-id "$MANDATE")
printf '%s\n' "$OUT" | sed 's/^/      /'
REF=$(printf '%s' "$OUT" | grep -oE 'PRAYAS_WEBHOOK_SECRET_[A-Z0-9_]+' | head -1)
SEC=$(printf '%s' "$OUT" | grep -oE 'PRAYAS_WEBHOOK_SECRET_[A-Z0-9_]+=.*' | cut -d= -f2)
# .env holds secrets and is gitignored, so keep a copy before touching it.
cp -f .env .env.bak 2>/dev/null || true
grep -q "^${REF}=" .env 2>/dev/null || printf '\n%s=%s\n' "$REF" "$SEC" >> .env
docker compose up -d api >/dev/null 2>&1
for _ in $(seq 1 40); do
  [ "$(docker inspect prayas-api-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 3
done

send() {
  local body="$1" eid="$2"
  local sig
  sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$SEC" -hex | sed 's/.*= *//')
  curl -s -o /dev/null -w '%{http_code}' -X POST "localhost:${PORT}/v1/webhooks/razorpay/${TENANT}" \
    -H "X-Razorpay-Signature: $sig" -H "X-Razorpay-Event-Id: $eid" \
    -H 'Content-Type: application/json' -d "$body"
}

NOW=$(date +%s); END=$((NOW + 20 * 86400))
SUB="{\"entity\":{\"id\":\"${MANDATE}\",\"status\":\"active\",\"current_end\":${END}}}"

say
say "[3/5] the customer authorises, then the monthly debit fails"
A=$(printf '{"event":"subscription.activated","payload":{"subscription":%s},"created_at":%d}' "$SUB" $((NOW - 7200)))
say "      subscription.activated  -> HTTP $(send "$A" "${TENANT}_act")"
F=$(printf '{"event":"payment.failed","payload":{"subscription":%s,"payment":{"entity":{"id":"pay_%s_1","amount":249900,"currency":"INR","status":"failed","invoice_id":"inv_%s_001","error_code":"BAD_REQUEST_ERROR","error_source":"bank","error_reason":"insufficient_funds"}}},"created_at":%d}' "$SUB" "$TENANT" "$TENANT" "$NOW")
say "      payment.failed          -> HTTP $(send "$F" "${TENANT}_fail")   (₹2,499, insufficient funds)"
say "      an unsigned or tampered webhook would return 401 and persist nothing."

say
say "[4/5] waiting for the projector (5s poll) and the planner (15s poll)"
say "      the planner reloads its priors each pass, so the first one can take"
say "      a little longer than its poll interval."
for _ in $(seq 1 40); do
  N=$(docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
        "SELECT count(*) FROM scheduled_actions WHERE tenant_id='${TENANT}' AND state='pending';" 2>/dev/null | tr -d '\r ')
  [ "${N:-0}" -ge 2 ] && break
  sleep 3
done
if [ "${N:-0}" -lt 2 ]; then
  say "      still nothing queued after 2 minutes. The planner logs why it"
  say "      declined — a stopped cycle is a decision, not a failure:"
  docker compose logs planner --tail=20 2>&1 \
    | grep -oE '"message":"planner\.[a-z_]+"|"reason":"[^"]*"' | tail -4 | sed 's/^/        /'
  exit 1
fi
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
  "SELECT '      '||rpad(action_type,13)||' fire_at='||to_char(fire_at,'DD Mon HH24:MI')||' UTC' \
     FROM scheduled_actions WHERE tenant_id='${TENANT}' ORDER BY fire_at;" 2>/dev/null

say
say "[5/5] driving it to a debit"
docker compose stop executor >/dev/null 2>&1   # the demo drives the executor itself
uv run python scripts/demo_recovery.py "$TENANT"
STATUS=$?
docker compose up -d executor >/dev/null 2>&1

# The debit was submitted; the rail confirms asynchronously by webhook, which
# is what turns "attempted" into money actually recovered (§11's terminal
# states). Without this the ledger shows an allowed debit and ₹0 recovered.
say
say "[6/6] the rail confirms the capture"
CAP=$(printf '{"event":"payment.captured","payload":{"subscription":%s,"payment":{"entity":{"id":"pay_%s_ok","amount":249900,"currency":"INR","status":"captured","invoice_id":"inv_%s_settled"}}},"created_at":%d}' "$SUB" "$TENANT" "$TENANT" "$(date +%s)")
say "      payment.captured        -> HTTP $(send "$CAP" "${TENANT}_cap")"
for _ in $(seq 1 15); do
  R=$(docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
        "SELECT coalesce(sum(recovered_paise),0) FROM cycles WHERE tenant_id='${TENANT}';" 2>/dev/null | tr -d '\r ')
  [ "${R:-0}" -gt 0 ] && break
  sleep 3
done
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
  "SELECT '      cycle '||cycle_id||'  state='||state||'  recovered='||recovered_paise||' paise' \
     FROM cycles WHERE tenant_id='${TENANT}' AND recovered_paise > 0;" 2>/dev/null

say
rule
say "Look at what it decided"
rule
TOKEN=$(docker compose exec -T api python -c "
from prayas.console.auth import issue
print(issue('${TENANT}', 'compliance_reviewer', ttl_seconds=86400))" 2>/dev/null | tr -d '\r')
DEC=$(docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
  "SELECT decision_id FROM decisions WHERE tenant_id='${TENANT}' ORDER BY chain_seq DESC LIMIT 1;" 2>/dev/null | tr -d '\r ')
say
say "  The ledger (append-only, hash-chained):"
docker compose exec -T postgres psql -U prayas_owner -d prayas -tAc \
  "SELECT '    #'||chain_seq||'  '||rpad(action_type,13)||' '||verdict \
     FROM decisions WHERE tenant_id='${TENANT}' ORDER BY chain_seq;" 2>/dev/null
say
say "  Open the decision in the console:"
say "    curl -H 'Authorization: Bearer \$TOKEN' \\"
say "      localhost:${PORT}/console/replay/${DEC}"
say
say "  TOKEN=$TOKEN"
say
say "  Or open the whole thing in a browser:"
say
say "      http://localhost:${PORT}/console/login"
say
say "  Paste the token above. You get the live pipeline — stage, queue, cycles,"
say "  mandates and the ledger — refreshing every 10 seconds."
say
say "  Unauthenticated, both return 401: the tenant comes from a verified token"
say "  and never from the URL (§18)."
exit $STATUS
