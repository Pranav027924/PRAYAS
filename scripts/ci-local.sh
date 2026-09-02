#!/usr/bin/env bash
# Run exactly what CI's `quality` job runs, in the same order.
#
# This exists because a narrower local command let five consecutive CI runs go
# red while local checks looked clean: CI type-checks `prayas` *and* `tests`
# under --strict, and only `prayas` was being checked here. Any divergence
# between this script and .github/workflows/ci.yml is a bug in one of them.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

: "${PRAYAS_DATABASE_URL_OWNER:=postgresql+asyncpg://prayas_owner:prayas_local_dev_only@localhost:5432/prayas}"
: "${PRAYAS_DATABASE_URL_APP:=postgresql+asyncpg://prayas_app:prayas_local_dev_only@localhost:5432/prayas}"
: "${PRAYAS_APP_DB_PASSWORD:=prayas_local_dev_only}"
: "${PRAYAS_ENV:=local}"
# ADR-078. Not a secret here: the value only has to be *present and stable*
# for the forgetting tests, and defaulting it is what keeps this mirror honest
# — the run must not pass merely because the caller happened to export one.
: "${PRAYAS_PSEUDONYM_PEPPER:=local_dev_pepper_not_a_secret}"
export PRAYAS_DATABASE_URL_OWNER PRAYAS_DATABASE_URL_APP PRAYAS_APP_DB_PASSWORD PRAYAS_ENV
export PRAYAS_PSEUDONYM_PEPPER

failed=0

step() {
  local name="$1"; shift
  printf '\n=== %s ===\n' "$name"
  if "$@"; then
    printf '    ok\n'
  else
    printf '    FAILED\n'
    failed=1
  fi
}

step "Lint"            uv run ruff check .
step "Format check"    uv run ruff format --check .
step "Type check"      uv run mypy --strict prayas tests
step "Apply migrations" uv run alembic upgrade head
step "Tests with coverage floor" uv run pytest --cov --cov-report=term-missing

# ADR-091. The published pack has its own suite, and it must pass without the
# parent package importable — a pack that only worked inside the repository it
# came from would fail on someone else's machine, not ours.
step "Rule pack conformance" uv run pytest packages/prayas-rulepack/tests -q

# ADR-086. Application code only; tests construct hostile payloads deliberately
# and flagging those trains people to ignore the report.
step "Static analysis (SAST)" uv run bandit -c pyproject.toml -r prayas -q

# Audits the resolved lockfile, exactly as CI does. `--no-emit-workspace` joins
# `--no-emit-project` (ADR-091): `prayas-rulepack` is an editable local path, and
# pip-audit cannot hash a directory. Excluding it loses nothing — it declares one
# dependency, PyYAML, which is audited here in its own right.
#
# `--no-emit-project` matters:
# the local `prayas` distribution is not on PyPI, and --strict treats an
# unauditable dependency as a failure.
#
# Needs network, so an offline sandbox skips rather than fails — a DNS error is
# not a vulnerability finding, and reporting it as one would train the reader to
# ignore this step.
printf '\n=== Dependency vulnerability scan ===\n'
uv export --no-emit-project --no-emit-workspace --format requirements-txt > /tmp/requirements.txt 2>/dev/null
if uv run pip-audit --strict -r /tmp/requirements.txt >/tmp/pip-audit.log 2>&1; then
  printf '    ok\n'
elif grep -qiE "Failed to resolve|NameResolutionError|Max retries exceeded|Temporary failure" /tmp/pip-audit.log; then
  printf '    SKIPPED (no network); CI runs this for real\n'
elif grep -qiE "Failed to install packages|Failed to upgrade .?pip" /tmp/pip-audit.log; then
  # pip-audit resolves into a throwaway venv, and in a sandbox that venv often
  # cannot reach the index — which surfaces as "No matching distribution found"
  # for a package that plainly exists. Matching on the *install* failure rather
  # than on that message keeps the discrimination honest: a genuine finding is
  # a vulnerability report, never an install error. GitHub CI has network and
  # runs this for real, which is where the guarantee actually lives.
  printf '    SKIPPED (audit venv could not install); CI runs this for real\n'
else
  tail -5 /tmp/pip-audit.log
  printf '    FAILED\n'
  failed=1
fi

printf '\n'
if [ "$failed" -eq 0 ]; then
  printf 'All CI checks passed locally.\n'
else
  printf 'At least one CI check FAILED. Do not push.\n'
fi
exit "$failed"
