"""Read every figure `docs/DEMO-DAY-SCRIPT.md` quotes, off the live stack.

Run this after any reseed — and after any CI run, which truncates the tenant
tables as a fixture side effect and leaves the fleet empty. The numbers move
every time the fleet is regenerated, and the demo script is only as good as
its last verification against them.

    uv run python scripts/verify_demo_numbers.py

Goes through the same HTTP API the screens render from, so a figure here and
a figure on the page cannot disagree.

**The window matters.** The portfolio page defaults to 30 days; several
figures — FitFirst's survival interval among them — only clear zero over 90.
This prints both, and says which side of zero each interval falls on, because
that is the one claim in the script that would be actively false if read off
the wrong view.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

os.environ.setdefault("PRAYAS_CONSOLE_TOKEN_SECRET", "local_console_secret_not_for_production")

from prayas.console.auth import issue

BASE = os.environ.get("PRAYAS_CONSOLE_URL", "http://localhost:8010")
TENANTS = ("fitfirst", "streamly", "edtechco")


def _say(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _get(path: str, tenant: str, role: str = "platform_pm") -> Any:
    token = issue(tenant, role, ttl_seconds=3600)
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def _rupees(paise: int | None) -> str:
    """Indian grouping, matching `console.format.rupees` — the screen's own."""
    if paise is None:
        return "—"
    digits = str(paise // 100)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"Rs {digits}"


def _lakh(paise: int | None) -> str:
    return "—" if paise is None else f"{paise / 10_000_000:.2f}L"


def _side_of_zero(interval: list[float] | None) -> str:
    if not interval:
        return "suppressed"
    return "EXCLUDES zero" if interval[0] > 0 else "*** INCLUDES ZERO ***"


def _pair(label: str, path: str, tenant: str) -> None:
    data = _get(path, tenant)
    pair = data["matched_pair"]
    rec_ci = pair["incremental_recovery_ci"]
    sur_ci = pair["incremental_survival_ci"]
    _say(f"\n  {label}   window {data['window']['from']} -> {data['window']['to']}")
    _say(
        f"    recovery : {_rupees(pair['incremental_recovery_paise'])}"
        f"  ({_lakh(pair['incremental_recovery_paise'])})"
    )
    _say(
        f"      95% CI : {_lakh(rec_ci[0]) if rec_ci else '—'}"
        f" -> {_lakh(rec_ci[1]) if rec_ci else '—'}    {_side_of_zero(rec_ci)}"
    )
    _say(f"    survival : {pair['incremental_survival_pts']} pts")
    _say(f"      95% CI : {sur_ci}    {_side_of_zero(sur_ci)}")
    _say(
        f"    arms     : treatment {pair['treatment_n']}"
        f" / holdout {pair['holdout_n']} ({pair['holdout_pct']}%)"
    )
    _say(
        f"    rates    : treatment {pair.get('treatment_recovery_rate')}"
        f" / holdout {pair.get('holdout_recovery_rate')}"
    )


def main() -> int:
    try:
        return _report()
    except urllib.error.HTTPError as exc:
        # Most often an empty fleet: a CI run truncates the tenant tables as
        # a fixture side effect, so the API has nothing to summarise. Say the
        # remedy rather than a stack trace.
        _say(f"\n  the API answered {exc.code} for {exc.url}")
        _say("  if the fleet is empty (a CI run truncates it), reseed:")
        _say("      bash scripts/seed-demo.sh")
        return 1
    except urllib.error.URLError as exc:
        _say(f"\n  cannot reach {BASE}: {exc.reason}")
        _say("      docker compose up -d")
        return 1


def _report() -> int:
    _say("=" * 72)
    _say("MATCHED PAIR — both windows, per tenant")
    _say("=" * 72)
    for tenant in TENANTS:
        _pair(f"{tenant} 30d", "/v1/portfolio/summary", tenant)
        _pair(f"{tenant} 90d", "/v1/portfolio/summary?window=90d", tenant)

    _say()
    _say("=" * 72)
    _say("FLEET AGGREGATE")
    _say("=" * 72)
    _pair("all 30d", "/v1/portfolio/summary?tenant=all", "fitfirst")
    _pair("all 90d", "/v1/portfolio/summary?tenant=all&window=90d", "fitfirst")

    data = _get("/v1/portfolio/summary?tenant=all&window=90d", "fitfirst")
    _say(f"\n    permanent fixes : {data['efficiency']['permanent_fixes']}")
    for guard in data["guardrails"]:
        _say(f"    {guard['key']:24} {guard['value']!s:>14}  {guard['status']}")
    for rail in data["rails"]:
        _say(f"    {rail['rail']:16} {rail['share'] * 100:5.1f}%  ({rail['mandates']} mandates)")
    for service in data["health"]:
        _say(f"    {service['service']:12} up={service['up']}")

    _say()
    _say("=" * 72)
    _say("TENANT SWITCHER")
    _say("=" * 72)
    for row in _get("/v1/tenants", "fitfirst"):
        _say(
            f"  {row['tenant_id']:10} {row['name']:12} stage={row['stage']:8}"
            f" mandates={row['mandates']:6} rail={row['rail_mix']}"
        )

    _say()
    _say("=" * 72)
    _say("LEDGER + CHAIN")
    _say("=" * 72)
    ledger = _get("/v1/ledger?limit=5", "fitfirst", role="compliance_reviewer")
    chain = ledger["chain"]
    _say(f"  chain verified : {chain['verified']}   rows={chain['rows']}")
    _say(f"  breaks         : {chain.get('breaks')}")

    _say()
    _say("=" * 72)
    _say("HERO CYCLE  cyc_7f3a91")
    _say("=" * 72)
    timeline = _get("/v1/cycles/cyc_7f3a91/timeline", "fitfirst", role="compliance_reviewer")
    _say(f"  amount    : {_rupees(timeline['amount_paise'])}")
    _say(f"  recovered : {_rupees(timeline['recovered_paise'])}")
    _say(f"  attempts  : {timeline['attempts_used']}/{timeline['attempt_budget']}")
    rationale = timeline["rationale"]
    _say(f"  notice    : {rationale.get('hours_of_notice')}h — floor is 24")
    _say(f"  binding   : {rationale.get('binding_constraint')}")
    _say("  events:")
    for event in timeline["events"]:
        _say(f"    {event['at']}  {event['kind']:22} {event['label'][:48]}")

    if int(timeline["recovered_paise"]) == 0:
        _say("\n  *** hero cycle shows no recovery — do not demo beat 4 until reseeded ***")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
