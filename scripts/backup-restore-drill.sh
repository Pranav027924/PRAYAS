#!/usr/bin/env bash
# Backup and restore drill (Master Spec §45; Phase 15).
#
# "Restore from backup verified, not assumed." An untested backup is a belief,
# and the belief is usually wrong in a way nobody discovers until it matters.
#
# This dumps the database, destroys it, restores it, and then checks the thing
# that actually needs to survive: the ledger's hash chain still verifies and
# the row counts match. A restore that produced a syntactically valid database
# with a broken chain would satisfy pg_restore and fail the only test worth
# passing.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

: "${PGHOST:=localhost}"
: "${PGPORT:=5432}"
: "${PGUSER:=prayas_owner}"
: "${PGPASSWORD:=prayas_local_dev_only}"
: "${PGDATABASE:=prayas}"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

DUMP="${DUMP_PATH:-/tmp/prayas-drill.dump}"

# The client tools live on the CI runner but not necessarily on a developer's
# machine, where Postgres runs in Compose. Route through the container when
# they are absent, so the drill is runnable in both places rather than only
# where it happens to be convenient.
if command -v psql >/dev/null 2>&1; then
  PSQL() { psql "$@"; }
  PGDUMP() { pg_dump "$@"; }
  PGRESTORE() { pg_restore "$@"; }
else
  echo "    (psql not on PATH — routing through docker compose)"
  DC="docker compose exec -T -e PGPASSWORD=${PGPASSWORD} postgres"
  PSQL() { ${DC} psql -U "${PGUSER}" -d "${PGDATABASE}" "$@"; }
  PGDUMP() { ${DC} pg_dump -U "${PGUSER}" "$@"; }
  PGRESTORE() { ${DC} pg_restore -U "${PGUSER}" "$@"; }
  # A dump written inside the container must stay inside it.
  DUMP="/tmp/prayas-drill.dump"
fi

echo "=== 1. Counting rows before ==="
before=$(PSQL -tAc "SELECT count(*) FROM decisions" | tr -d "[:space:]")
echo "    decisions: ${before}"

# A restore that brings back nothing satisfies every check below vacuously.
# §45 asks for the restore to be *verified*, and there is nothing to verify in
# an empty database.
if [[ "${before}" == "0" ]]; then
  echo "    REFUSING: the database holds no decisions, so a restore would" >&2
  echo "    verify nothing. Seed the ledger and re-run." >&2
  exit 1
fi

echo "=== 2. Dumping ==="
PGDUMP --format=custom --file="${DUMP}" "${PGDATABASE}"
echo "    wrote ${DUMP}"

echo "=== 3. Destroying ==="
# The drill is worthless if it restores over a database that was never broken.
PSQL -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
remaining=$(PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d "[:space:]")
if [[ "${remaining}" != "0" ]]; then
  echo "    FAILED: schema not actually dropped" >&2
  exit 1
fi
echo "    schema dropped"

echo "=== 4. Restoring ==="
PGRESTORE --dbname="${PGDATABASE}" --no-owner --clean --if-exists "${DUMP}" 2>/dev/null || true

echo "=== 5. Verifying ==="
after=$(PSQL -tAc "SELECT count(*) FROM decisions" | tr -d "[:space:]")
echo "    decisions: ${after}"
if [[ "${before}" != "${after}" ]]; then
  echo "    FAILED: ${before} rows before, ${after} after" >&2
  exit 1
fi

# The chain is the point. A restore that loses ordering or content produces a
# database that opens and a ledger that cannot be trusted.
if ! uv run python -m prayas.ledger.verify; then
  echo "    FAILED: the hash chain does not verify after restore" >&2
  exit 1
fi

echo "=== Drill passed: ${after} decisions restored, chain verifies ==="
