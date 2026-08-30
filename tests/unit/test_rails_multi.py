"""All three rails, and the one that proves the abstraction (§9, §24.4).

§9: "eNACH is the hardest rail and therefore the one that proves the
abstraction. Batch semantics mean 'attempt at 09:10 on the 5th' is meaningless;
you present into a clearing cycle and learn the outcome T+1. The rail adapter
must express budget, windows, *and outcome latency*, which a UPI-only design
would never surface."

These tests are written so a UPI-shaped assumption fails loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prayas.domain.rails import (
    ADAPTERS,
    ENACH_PRESENTATION_CUTOFF_HOUR,
    CardEmandateAdapter,
    EnachAdapter,
    RailAdapter,
    UpiAutopayAdapter,
    adapter_for,
    hour_ist,
)

RAILS = sorted(ADAPTERS)


# 2026-06-01 is a Monday; 2026-06-05 a Friday; 2026-06-06 a Saturday.
MONDAY = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)  # 09:30 IST
FRIDAY = datetime(2026, 6, 5, 4, 0, tzinfo=UTC)  # 09:30 IST
SATURDAY = datetime(2026, 6, 6, 4, 0, tzinfo=UTC)  # 09:30 IST


# ── every rail is present and coherent ─────────────────────────────────────


def test_all_three_rails_have_adapters() -> None:
    """§9 names three. A missing one is a rail the sequencer cannot act on."""
    assert RAILS == ["card_emandate", "enach", "upi_autopay"]


@pytest.mark.parametrize("rail", RAILS)
def test_each_adapter_satisfies_the_protocol(rail: str) -> None:
    adapter = adapter_for(rail)
    assert isinstance(adapter, RailAdapter)
    assert adapter.rail == rail
    assert adapter.attempt_budget >= 1
    assert adapter.notice_lead_hours >= 24, "§9 — >=24h PDN on every rail"


def test_an_unknown_rail_fails_loudly() -> None:
    with pytest.raises(NotImplementedError, match="known rails"):
        adapter_for("carrier_billing")


# ── the rails differ where §9 says they differ ─────────────────────────────


def test_execution_windows_differ_by_rail() -> None:
    """11:00 IST on a Monday: inside NPCI's peak, so illegal for UPI; legal for
    card, which is continuous; illegal for eNACH only because it is past the
    presentation cut-off is *not* the reason — 11:00 is before 13:00, so eNACH
    accepts it. Three rails, three different answers to one instant."""
    at_1100 = datetime(2026, 6, 1, 5, 30, tzinfo=UTC)  # 11:00 IST, Monday

    assert not UpiAutopayAdapter().is_execution_legal(at_1100)
    assert CardEmandateAdapter().is_execution_legal(at_1100)
    assert EnachAdapter().is_execution_legal(at_1100)


def test_card_is_continuous() -> None:
    """§9 — "continuous". No hour is illegal on window grounds."""
    adapter = CardEmandateAdapter()
    for hour in range(24):
        assert adapter.is_execution_legal(datetime(2026, 6, 1, hour, tzinfo=UTC))


def test_attempt_budgets_differ() -> None:
    """§9 gives each rail a different budget rule. Equal budgets would mean the
    adapter is not carrying the constraint it exists to carry."""
    assert UpiAutopayAdapter().attempt_budget == 4
    assert EnachAdapter().attempt_budget == 3
    assert EnachAdapter().attempt_budget < UpiAutopayAdapter().attempt_budget


# ── outcome latency: the member eNACH forced onto the protocol ─────────────


def test_only_enach_has_outcome_latency() -> None:
    assert UpiAutopayAdapter().outcome_latency == timedelta(0)
    assert CardEmandateAdapter().outcome_latency == timedelta(0)
    assert EnachAdapter().outcome_latency > timedelta(0)


def test_a_realtime_rail_resolves_when_it_fires() -> None:
    for adapter in (UpiAutopayAdapter(), CardEmandateAdapter()):
        assert adapter.outcome_due_at(MONDAY) == MONDAY


def test_enach_resolves_on_the_next_clearing_day() -> None:
    """T+1, in working days."""
    due = EnachAdapter().outcome_due_at(MONDAY)
    assert due.date() == datetime(2026, 6, 2, tzinfo=UTC).date()


def test_a_friday_presentation_settles_on_monday() -> None:
    """Calendar arithmetic, not `+ 24h`. A system expecting Saturday would
    raise an overdue alert every weekend."""
    due = EnachAdapter().outcome_due_at(FRIDAY)
    assert due.weekday() == 0, "Friday should settle Monday, not Saturday"
    assert (due.date() - FRIDAY.date()).days == 3


def test_enach_does_not_clear_at_the_weekend() -> None:
    """§9 — "batch, ~1 working day". Saturday is not a working day."""
    adapter = EnachAdapter()
    assert not adapter.is_execution_legal(SATURDAY)
    assert adapter.outcome_due_at(SATURDAY).weekday() == 0


def test_enach_refuses_presentation_after_the_cutoff() -> None:
    """Past the cut-off the presentation would sit until the next cycle.
    Accepting it would make the scheduler lie about when money moves."""
    adapter = EnachAdapter()
    before = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)  # 11:30 IST
    after = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)  # 14:30 IST

    assert ENACH_PRESENTATION_CUTOFF_HOUR == 13.0
    assert adapter.is_execution_legal(before)
    assert not adapter.is_execution_legal(after)


@pytest.mark.parametrize("rail", RAILS)
def test_outcome_is_never_due_before_presentation(rail: str) -> None:
    """A guard against a latency model that runs backwards."""
    adapter = adapter_for(rail)
    assert adapter.outcome_due_at(MONDAY) >= MONDAY


# ── the notice lead is the gate's rule, not the rail's (ADR-020) ──────────


@pytest.mark.parametrize("rail", RAILS)
def test_an_adapter_does_not_re_implement_the_24h_lead(rail: str) -> None:
    """`RBI-EMANDATE-PDN-24H` applies to all three rails and lives in the
    rulepack, which ADR-020 makes the source of truth. An adapter that also
    enforced it would put one compliance rule in two places, where the copies
    drift and the ledger cites a version that no longer describes what ran.

    So a send one hour before the debit is *not* refused here — the gate
    refuses it, and `test_gate_rails.py` asserts that.
    """
    adapter = adapter_for(rail)
    debit = MONDAY + timedelta(days=3)
    one_hour_before = debit - timedelta(hours=1)

    # Only UPI refuses, and only because of NPCI's 23:50 submission cutoff,
    # which is genuinely particular to that rail.
    refused = not adapter.is_pdn_send_legal(one_hour_before, debit)
    assert refused == (rail == "upi_autopay" and hour_ist(one_hour_before) >= 23.0)


def test_upi_carries_the_npci_submission_cutoff() -> None:
    """The one send-time constraint that *is* rail-specific (§30.1)."""
    adapter = UpiAutopayAdapter()
    debit = datetime(2026, 6, 2, 6, 0, tzinfo=UTC)  # 11:30 IST, next day
    after_cutoff = datetime(2026, 6, 1, 18, 25, tzinfo=UTC)  # 23:55 IST
    before_cutoff = datetime(2026, 6, 1, 17, 0, tzinfo=UTC)  # 22:30 IST

    assert not adapter.is_pdn_send_legal(after_cutoff, debit)
    assert adapter.is_pdn_send_legal(before_cutoff, debit)


@pytest.mark.parametrize("rail", ["card_emandate", "enach"])
def test_the_npci_cutoff_does_not_leak_onto_other_rails(rail: str) -> None:
    """It is an NPCI submission rule, not a universal one."""
    adapter = adapter_for(rail)
    debit = datetime(2026, 6, 2, 6, 0, tzinfo=UTC)
    after_cutoff = datetime(2026, 6, 1, 18, 25, tzinfo=UTC)
    assert adapter.is_pdn_send_legal(after_cutoff, debit)
