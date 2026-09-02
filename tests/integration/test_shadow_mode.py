"""Shadow mode and the §8 audit artifact (§30.4).

Phase 2's demonstrable artifact: "feed the day-1/3/5 baseline policy through the
gate in shadow mode and print a violation count."
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from prayas_rulepack.baseline import baseline_attempts
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction
from prayas.gate.shadow import (
    ShadowReport,
    evaluate_baseline_policy,
    hour_ist,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

AS_OF = date(2026, 8, 25)


# ── IST conversion, which every window rule depends on ──────────────────────


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 6, 1, 4, 30, tzinfo=UTC), 10.0),  # 04:30 UTC = 10:00 IST
        (datetime(2026, 6, 1, 0, 0, tzinfo=UTC), 5.5),  # midnight UTC = 05:30 IST
        (datetime(2026, 6, 1, 18, 30, tzinfo=UTC), 0.0),  # rolls past midnight IST
        (datetime(2026, 6, 1, 7, 30, tzinfo=UTC), 13.0),  # start of the 13:00 window
    ],
)
def test_ist_conversion(utc: datetime, expected: float) -> None:
    assert hour_ist(utc) == pytest.approx(expected)


# ── the baseline policy itself ──────────────────────────────────────────────


def test_baseline_is_day_1_3_5() -> None:
    first_failure = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    attempts = baseline_attempts(first_failure)

    assert len(attempts) == 3
    offsets = [(a["fire_at"].date() - first_failure.date()).days for a in attempts]
    assert offsets == [1, 3, 5]


def test_baseline_sends_no_pdn_by_default() -> None:
    """Tools built for card rails do not model the Indian PDN requirement."""
    assert all(a["pdn_sent_at"] is None for a in baseline_attempts(datetime.now(tz=UTC)))


def test_baseline_fires_at_10_ist_inside_the_peak_window() -> None:
    for attempt in baseline_attempts(datetime(2026, 6, 1, tzinfo=UTC)):
        assert hour_ist(attempt["fire_at"]) == pytest.approx(10.0)


# ── the audit finding ───────────────────────────────────────────────────────


async def test_baseline_policy_violates_the_framework(app_engine: AsyncEngine) -> None:
    """The §8 artifact. Every baseline attempt should violate something."""
    async with system_transaction(app_engine) as conn:
        report = await evaluate_baseline_policy(conn, cycles=200, as_of=AS_OF)

    assert report.cycles == 200
    assert report.attempts == 600, "day-1/3/5 is three attempts per cycle"
    assert report.allowed == 0, "the baseline should not produce a single lawful attempt"

    # Both headline findings §8 names.
    assert report.violations_by_rule["NPCI-AUTOPAY-WINDOW"] == 600
    assert report.violations_by_rule["RBI-EMANDATE-PDN-24H"] == 600

    assert report.per_10k_cycles("NPCI-AUTOPAY-WINDOW") == pytest.approx(30_000)
    assert report.per_10k_cycles("RBI-EMANDATE-PDN-24H") == pytest.approx(30_000)


async def test_sending_a_valid_pdn_removes_only_the_pdn_violation(
    app_engine: AsyncEngine,
) -> None:
    """Isolates the two findings — they are independent failures, not one."""
    async with system_transaction(app_engine) as conn:
        report = await evaluate_baseline_policy(conn, cycles=50, as_of=AS_OF, send_pdn=True)

    assert report.violations_by_rule.get("RBI-EMANDATE-PDN-24H", 0) == 0
    assert report.violations_by_rule["NPCI-AUTOPAY-WINDOW"] == 150
    assert report.allowed == 0, "the window violation alone still makes every attempt unlawful"


async def test_shadow_mode_fires_nothing(app_engine: AsyncEngine) -> None:
    """§30.4 — the same engine, evaluating without acting."""
    async with system_transaction(app_engine) as conn:
        report = await evaluate_baseline_policy(conn, cycles=10, as_of=AS_OF)

    assert report.attempts == 30, "sanity: the harness actually evaluated"

    async with system_transaction(app_engine) as conn:
        actions = await conn.scalar(text("SELECT count(*) FROM scheduled_actions"))
        outbox = await conn.scalar(text("SELECT count(*) FROM outbox"))

    assert actions == 0, "shadow mode scheduled something"
    assert outbox == 0, "shadow mode queued an external call"


def test_summary_states_the_finding_in_section_8_terms() -> None:
    report = ShadowReport(cycles=10_000, attempts=30_000, allowed=0)
    report.violations_by_rule = {
        "NPCI-AUTOPAY-WINDOW": 30_000,
        "RBI-EMANDATE-PDN-24H": 30_000,
    }
    summary = report.summary()

    assert "30000 debit attempts outside NPCI execution windows" in summary
    assert "30000 debits without valid 24-hour pre-debit notice" in summary


def test_per_10k_is_zero_for_an_empty_report() -> None:
    assert ShadowReport().per_10k_cycles("ANY") == 0.0


def test_an_allowed_attempt_records_no_violation() -> None:
    report = ShadowReport()
    report.record("ALLOW", [])

    assert report.attempts == 1
    assert report.allowed == 1
    assert report.violating_attempts == 0
    assert report.violations_by_rule == {}


def test_a_denied_attempt_is_attributed_to_every_failing_rule() -> None:
    report = ShadowReport()
    report.record("DENY", ["A", "B"])

    assert report.violating_attempts == 1
    assert report.violations_by_rule == {"A": 1, "B": 1}
    assert report.violations_by_verdict == {"DENY": 1}
