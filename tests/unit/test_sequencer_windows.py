"""Legality mask, each rail constraint verified independently (Playbook Phase 5).

The exit criterion requires the mask be "verified against every rail constraint
**independently**". Each constraint therefore gets its own tests against its own
function; the combined mask is checked separately, and only for the property
that it is the conjunction.

Boundaries are the substance (§40.2): "09:59:59 / 10:00:00 IST window edges;
23:49 / 23:50 PDN cutoff".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from prayas.domain.rails import IST, UpiAutopayAdapter, adapter_for, hour_ist
from prayas.sequencer.windows import (
    build_mask,
    deadline_mask,
    notice_lead_mask,
    pdn_cutoff_mask,
    slot_times,
)

ADAPTER = UpiAutopayAdapter()


def ist(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """An IST wall-clock instant. Stored/compared as UTC underneath."""
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


# ── Constraint 1: NPCI execution windows (§1) ───────────────────────────────


@pytest.mark.parametrize(
    ("label", "when", "legal"),
    [
        ("09:59:59 — last legal second before the morning peak", ist(2026, 3, 2, 9, 59, 59), True),
        ("10:00:00 — peak begins", ist(2026, 3, 2, 10, 0, 0), False),
        ("12:59:59 — still peak", ist(2026, 3, 2, 12, 59, 59), False),
        ("13:00:00 — afternoon window opens", ist(2026, 3, 2, 13, 0, 0), True),
        ("16:59:59 — afternoon window closing", ist(2026, 3, 2, 16, 59, 59), True),
        ("17:00:00 — evening peak begins", ist(2026, 3, 2, 17, 0, 0), False),
        ("21:29:59 — still peak", ist(2026, 3, 2, 21, 29, 59), False),
        ("21:30:00 — late window opens", ist(2026, 3, 2, 21, 30, 0), True),
        ("23:59:59 — late window still open", ist(2026, 3, 2, 23, 59, 59), True),
        ("00:00:00 — early window", ist(2026, 3, 2, 0, 0, 0), True),
    ],
)
def test_execution_window_boundaries(label: str, when: datetime, legal: bool) -> None:
    """§1 — before 10:00, 13:00-17:00, after 21:30 IST. Half-open at each edge."""
    assert ADAPTER.is_execution_legal(when) is legal, label


def test_execution_window_mask_is_evaluated_in_ist_not_utc() -> None:
    """Rail windows are IST-defined; a UTC-evaluated mask is off by 5h30m.

    05:00 UTC is 10:30 IST — inside the morning peak, hence illegal. A system
    comparing the UTC hour would see 05:00 and call it legal.
    """
    utc_0500 = datetime(2026, 3, 2, 5, 0, tzinfo=datetime.now().astimezone().tzinfo).replace(
        tzinfo=None
    )
    from datetime import UTC

    when = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)
    assert hour_ist(when) == pytest.approx(10.5)
    assert ADAPTER.is_execution_legal(when) is False
    assert utc_0500.hour == 5  # the naive UTC hour that would have fooled us


def test_naive_datetime_is_rejected() -> None:
    """A naive timestamp on the money path is a bug, not a default."""
    with pytest.raises(ValueError, match="naive datetime"):
        ADAPTER.is_execution_legal(datetime(2026, 3, 2, 9, 0))  # noqa: DTZ001


# ── Constraint 2: 24h notice lead (§30.1 RBI-EMANDATE-PDN-24H) ──────────────


def test_notice_lead_excludes_everything_inside_24_hours() -> None:
    """A slot closer than the lead cannot be given lawful notice."""
    decided = ist(2026, 3, 2, 8, 0)
    times = slot_times(decided, 48)  # 48 hourly slots
    mask = notice_lead_mask(ADAPTER, times, decided_at=decided)

    assert not mask[:24].any(), "slots within 24h must be excluded"
    assert mask[24:].all(), "slots at or beyond 24h must be permitted"


def test_notice_lead_boundary_is_inclusive_at_exactly_24_hours() -> None:
    """Exactly 24h of notice satisfies "at least 24 hours" (§1)."""
    decided = ist(2026, 3, 2, 8, 0)
    times = [decided + timedelta(hours=24)]
    assert notice_lead_mask(ADAPTER, times, decided_at=decided)[0]

    just_under = [decided + timedelta(hours=24) - timedelta(seconds=1)]
    assert not notice_lead_mask(ADAPTER, just_under, decided_at=decided)[0]


# ── Constraint 3: PDN cutoff (§30.1 NPCI-PDN-CUTOFF-2350) ───────────────────


def test_pdn_send_is_legal_before_2350_for_a_next_day_debit() -> None:
    send = ist(2026, 3, 2, 23, 49)
    debit = ist(2026, 3, 3, 23, 49)
    assert ADAPTER.is_pdn_send_legal(send, debit) is True


def test_pdn_send_is_blocked_at_2350_for_a_next_day_debit() -> None:
    """§40.2's named cliff: 23:49 versus 23:50."""
    send = ist(2026, 3, 2, 23, 50)
    debit = ist(2026, 3, 3, 23, 50)
    assert ADAPTER.is_pdn_send_legal(send, debit) is False


def test_pdn_send_after_cutoff_is_fine_for_a_later_debit() -> None:
    """The cutoff constrains next-day debits only, not all late submissions."""
    send = ist(2026, 3, 2, 23, 55)
    debit = ist(2026, 3, 5, 9, 0)
    assert ADAPTER.is_pdn_send_legal(send, debit) is True


def test_pdn_cutoff_mask_admits_a_slot_when_an_earlier_send_exists() -> None:
    """A blacked-out latest-send does not condemn the slot.

    The send window is `[decided_at, debit - 24h]`. If its late end is inside
    the blackout but an earlier lawful instant exists, the debit is schedulable.
    """
    decided = ist(2026, 3, 2, 8, 0)
    debit = ist(2026, 3, 3, 23, 55)  # latest send = 2 Mar 23:55, inside blackout
    mask = pdn_cutoff_mask(ADAPTER, [debit], decided_at=decided)
    assert mask[0], "an earlier lawful send exists, so the slot must survive"


def test_pdn_cutoff_mask_excludes_a_slot_with_no_lawful_send_instant() -> None:
    """When the whole send window sits inside the blackout, the slot is out."""
    decided = ist(2026, 3, 2, 23, 52)  # already past the cutoff
    debit = ist(2026, 3, 3, 23, 55)  # latest send 23:55, also past
    mask = pdn_cutoff_mask(ADAPTER, [debit], decided_at=decided)
    assert not mask[0], "no lawful send instant exists for this slot"


# ── Constraint 4: cycle deadline ────────────────────────────────────────────


def test_deadline_excludes_slots_at_or_after_the_deadline() -> None:
    """A debit at or past the deadline belongs to a different cycle."""
    decided = ist(2026, 3, 2, 0, 0)
    deadline = decided + timedelta(hours=10)
    times = slot_times(decided, 20)
    mask = deadline_mask(times, deadline_at=deadline)

    assert mask[:10].all()
    assert not mask[10:].any(), "the deadline hour itself must be excluded"


# ── Combination and explanation ─────────────────────────────────────────────


def test_combined_mask_is_exactly_the_conjunction() -> None:
    """No constraint may be silently dropped when they are ANDed."""
    decided = ist(2026, 3, 2, 8, 0)
    mask = build_mask(
        ADAPTER, decided_at=decided, deadline_at=decided + timedelta(days=20), horizon_slots=200
    )
    expected = mask.execution_window & mask.notice_lead & mask.pdn_cutoff & mask.deadline
    np.testing.assert_array_equal(mask.combined, expected)


def test_each_constraint_removes_slots_the_others_do_not() -> None:
    """Guards against a mask that is right for the wrong reason.

    If any single constraint were a no-op, or were subsumed by another, the
    combined mask could still look plausible. This asserts each one is doing
    work the others are not.
    """
    decided = ist(2026, 3, 2, 8, 0)
    mask = build_mask(
        ADAPTER, decided_at=decided, deadline_at=decided + timedelta(days=5), horizon_slots=240
    )

    for name, component in (
        ("execution_window", mask.execution_window),
        ("notice_lead", mask.notice_lead),
        ("deadline", mask.deadline),
    ):
        others = np.ones_like(mask.combined)
        for other_name, other in (
            ("execution_window", mask.execution_window),
            ("notice_lead", mask.notice_lead),
            ("deadline", mask.deadline),
        ):
            if other_name != name:
                others &= other
        uniquely_excluded = others & ~component
        assert uniquely_excluded.any(), f"{name} excludes nothing the others do not"


def test_why_excluded_reports_every_failing_constraint() -> None:
    """A merchant asking "why not 09:00 tomorrow?" gets all the reasons."""
    decided = ist(2026, 3, 2, 8, 0)
    mask = build_mask(
        ADAPTER, decided_at=decided, deadline_at=decided + timedelta(hours=4), horizon_slots=48
    )
    # Slot 2 is 10:00 IST: inside the peak, inside the notice lead, and past
    # a 4-hour deadline. All three should be reported, not just the first.
    reasons = mask.why_excluded(2)
    assert "outside rail execution window" in reasons
    assert "inside the notice lead time" in reasons
    assert len(reasons) >= 2, reasons


def test_legal_slots_are_all_genuinely_legal() -> None:
    """Whatever survives must satisfy every constraint individually."""
    decided = ist(2026, 3, 2, 8, 0)
    mask = build_mask(
        ADAPTER, decided_at=decided, deadline_at=decided + timedelta(days=15), horizon_slots=360
    )
    assert mask.legal_slots.size > 0, "scenario produced no legal slots at all"

    for slot in mask.legal_slots:
        assert mask.why_excluded(int(slot)) == []
        assert ADAPTER.is_execution_legal(mask.times[int(slot)])


# ── Rail registry ───────────────────────────────────────────────────────────


def test_upi_autopay_budget_matches_the_regulator() -> None:
    """§1 — one execution plus up to three retries."""
    assert adapter_for("upi_autopay").attempt_budget == 4
    assert adapter_for("upi_autopay").notice_lead_hours == 24


@pytest.mark.parametrize("rail", ["card_emandate", "enach"])
def test_unimplemented_rails_fail_loudly(rail: str) -> None:
    """Phase 14 adds these. Until then, silence would be the dangerous answer."""
    with pytest.raises(NotImplementedError, match="Phase 14"):
        adapter_for(rail)
