"""Phase 9 exit criteria (Playbook Phase 9; Master Spec §24.2, §30.1, §6)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pytest

from prayas.domain.rails import IST, hour_ist
from prayas.notify.channel import (
    HIGH_RISK_THRESHOLD,
    SMS,
    WHATSAPP,
    ChannelError,
    Content,
    assemble,
    build_context,
    choose_channel,
)
from prayas.notify.planner import (
    MIN_NOTICE_HOURS,
    NotificationPlan,
    is_before_cutoff,
    is_contact_window,
    is_legal_send,
    naive_plan,
    plan,
    risk_score,
)
from prayas.notify.prevention import (
    CycleAccounting,
    PreventionError,
    account,
    assert_disjoint,
)
from prayas.sim import pdn
from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate


def ist(y: int, m: int, d: int, hour: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=IST)


# ── Exit criterion: PDN never scheduled inside the cutoff ───────────────────


@pytest.mark.parametrize(
    ("label", "send", "debit", "allowed"),
    [
        ("23:49 for a next-day debit", ist(2026, 3, 2, 23, 49), ist(2026, 3, 3, 23, 49), True),
        ("23:50 for a next-day debit", ist(2026, 3, 2, 23, 50), ist(2026, 3, 3, 23, 50), False),
        ("23:59 for a next-day debit", ist(2026, 3, 2, 23, 59), ist(2026, 3, 3, 23, 59), False),
        ("23:55 for a debit three days out", ist(2026, 3, 2, 23, 55), ist(2026, 3, 5, 9, 0), True),
    ],
)
def test_cutoff_boundary(label: str, send: datetime, debit: datetime, allowed: bool) -> None:
    """§40.2's named cliff: 23:49 versus 23:50."""
    assert is_before_cutoff(send, debit) is allowed, label


def test_the_planner_never_proposes_a_send_inside_the_cutoff() -> None:
    """The criterion, over every debit hour of the day.

    Swept rather than spot-checked: a cutoff violation that only appears for
    debits at one particular hour is exactly what a single example misses.
    """
    decided = ist(2026, 3, 1, 8, 0)
    for hour in range(24):
        for minute in (0, 30, 55):
            debit = ist(2026, 3, 5, hour, minute)
            for funding in (None, debit + timedelta(hours=6), debit - timedelta(hours=2)):
                result = plan(
                    debit_at=debit,
                    decided_at=decided,
                    predicted_funding_at=funding,
                    risk=0.8,
                )
                if result.send_at is None:
                    continue
                assert is_before_cutoff(result.send_at, debit), (
                    f"debit {hour:02d}:{minute:02d} -> send "
                    f"{result.send_at.astimezone(IST)} is inside the cutoff"
                )


def test_every_planned_send_satisfies_all_three_constraints() -> None:
    """Notice lead, contact window and cutoff — not merely the cutoff."""
    decided = ist(2026, 3, 1, 8, 0)
    sent = 0
    for hour in range(24):
        debit = ist(2026, 3, 6, hour, 0)
        result = plan(debit_at=debit, decided_at=decided, predicted_funding_at=None, risk=0.9)
        if result.send_at is None:
            continue
        sent += 1
        assert is_legal_send(result.send_at, debit)
        assert is_contact_window(result.send_at), "sent outside 08:00-19:00 IST"
        assert result.hours_of_notice is not None
        assert result.hours_of_notice >= MIN_NOTICE_HOURS
    assert sent > 0, "no sends planned at all, so the assertion is vacuous"


@pytest.mark.parametrize(
    ("hour", "inside"),
    [(7, False), (8, True), (12, True), (18, True), (19, False), (22, False)],
)
def test_contact_window_boundaries(hour: int, inside: bool) -> None:
    """§30.1 RBI-FPC-CONTACT-WINDOW: `hour_ist >= 8 and hour_ist < 19`."""
    assert is_contact_window(ist(2026, 3, 2, hour, 0)) is inside


# ── Exit criterion: every notification passes the gate before sending ───────


def test_the_gate_context_carries_every_predicate_the_rules_need() -> None:
    """Invariant 1 for notifications: nothing is sent without the gate ruling.

    The context must contain each field §30.1's notification rules reference,
    or the gate would evaluate against a missing name and — correctly — deny.
    """
    notification = assemble(
        send_at=ist(2026, 3, 4, 18, 0),
        debit_at=ist(2026, 3, 6, 9, 0),
        amount_paise=49_900,
        risk=0.8,
        consent_ref="consent_1",
    )
    ctx = build_context(notification)

    for field in (
        "hour_ist",  # NPCI-PDN-CUTOFF-2350, RBI-FPC-CONTACT-WINDOW
        "is_next_day_debit",  # NPCI-PDN-CUTOFF-2350
        "dlt_template_id",  # TRAI-DLT-TEMPLATE
        "header_series",  # TRAI-DLT-TEMPLATE
        "dnd_registered",  # TRAI-DLT-TEMPLATE
        "consent_ref",  # DPDP-CONSENT-VALID
        "consent_withdrawn",  # DPDP-CONSENT-VALID
        "messages_30d",  # PRAYAS-FATIGUE-CAP
        "tenant_fatigue_cap",  # PRAYAS-FATIGUE-CAP
    ):
        assert field in ctx, f"gate context is missing {field}"

    assert ctx["header_series"] == "160", "transactional header series required"
    assert hour_ist(notification.send_at) == pytest.approx(ctx["hour_ist"])


def test_a_withdrawn_consent_is_visible_to_the_gate() -> None:
    """DPDP-CONSENT-VALID must be able to see the absence."""
    ctx = build_context(
        assemble(
            send_at=ist(2026, 3, 4, 18, 0),
            debit_at=ist(2026, 3, 6, 9, 0),
            amount_paise=49_900,
            risk=0.2,
            consent_ref=None,
        )
    )
    assert ctx["consent_ref"] is None
    assert ctx["consent_withdrawn"] is True


def test_dnd_registration_moves_the_channel_off_sms() -> None:
    """TRAI-DLT-TEMPLATE denies SMS to a DND number."""
    assert choose_channel(preferred=SMS, dnd_registered=True) == WHATSAPP
    assert choose_channel(preferred=SMS, dnd_registered=False) == SMS


def test_a_one_tap_path_appears_only_when_risk_is_elevated() -> None:
    """§24.2: "plus a one-tap pay-now path when risk is elevated"."""

    def built(risk: float) -> bool:
        return assemble(
            send_at=ist(2026, 3, 4, 18, 0),
            debit_at=ist(2026, 3, 6, 9, 0),
            amount_paise=49_900,
            risk=risk,
            consent_ref="c1",
        ).includes_pay_now

    assert built(HIGH_RISK_THRESHOLD + 0.1)
    assert not built(HIGH_RISK_THRESHOLD - 0.1)


# ── Exit criterion: fatigue cap enforced ────────────────────────────────────


def test_the_fatigue_cap_suppresses_the_send_with_a_reason() -> None:
    """§24.6: a system that cannot select silence over-messages its portfolio.

    Suppression must carry a reason, because §24.6 also says the ledger entry
    for a back-off is as important as one for a debit.
    """
    result = plan(
        debit_at=ist(2026, 3, 6, 9, 0),
        decided_at=ist(2026, 3, 1, 8, 0),
        predicted_funding_at=None,
        risk=0.9,
        messages_sent_30d=3,
        fatigue_cap=3,
    )
    assert not result.will_send
    assert result.suppressed_reason is not None
    assert "fatigue cap" in result.suppressed_reason
    assert "3" in result.suppressed_reason


def test_one_below_the_cap_still_sends() -> None:
    """The boundary in the permissive direction."""
    result = plan(
        debit_at=ist(2026, 3, 6, 9, 0),
        decided_at=ist(2026, 3, 1, 8, 0),
        predicted_funding_at=None,
        risk=0.9,
        messages_sent_30d=2,
        fatigue_cap=3,
    )
    assert result.will_send


def test_fatigue_reduces_response_rather_than_only_blocking() -> None:
    """§38's `fatigue_decay`: each prior message makes the next less effective."""
    config = SimConfig()
    send = ist(2026, 3, 5, 18, 0)
    funding = ist(2026, 3, 5, 20, 0)

    fresh = pdn.prevention_probability(config, send_at=send, funding_at=funding)
    worn = pdn.prevention_probability(
        config, send_at=send, funding_at=funding, messages_already_sent=3
    )
    assert 0 < worn < fresh


# ── Exit criterion: prevention computed separately from recovery ────────────


def test_prevention_and_recovery_are_disjoint_by_construction() -> None:
    """Counting a cycle as both would double-count the same rupee."""
    with pytest.raises(PreventionError, match="both prevented and recovered"):
        CycleAccounting(
            cycle_id="c1",
            arm="treatment",
            high_risk=True,
            notification_sent=True,
            succeeded_first_execution=True,
            retries_consumed=0,
            recovered_after_retry=True,
        )


def test_a_first_execution_success_cannot_have_consumed_retries() -> None:
    with pytest.raises(PreventionError, match="consumed"):
        CycleAccounting(
            cycle_id="c2",
            arm="control",
            high_risk=True,
            notification_sent=True,
            succeeded_first_execution=True,
            retries_consumed=2,
            recovered_after_retry=False,
        )


def _cycle(i: int, arm: str, *, prevented: bool, recovered: bool = False) -> CycleAccounting:
    return CycleAccounting(
        cycle_id=f"c{i}",
        arm=arm,
        high_risk=True,
        notification_sent=True,
        succeeded_first_execution=prevented,
        retries_consumed=0 if prevented else 2,
        recovered_after_retry=recovered,
    )


def test_prevention_is_reported_against_control_not_in_isolation() -> None:
    """§6 lists prevention with its own denominator, compared to control."""
    cycles = [_cycle(i, "treatment", prevented=i < 30) for i in range(100)] + [
        _cycle(100 + i, "control", prevented=i < 10) for i in range(100)
    ]
    assert_disjoint(cycles)
    report = account(cycles)

    assert report.treatment.recovered == 30
    assert report.control.recovered == 10
    assert report.prevention_lift.point == pytest.approx(0.20)
    assert report.prevention_lift.excludes_zero


def test_prevention_is_measured_on_high_risk_cycles_only() -> None:
    """Otherwise the metric rises by enrolling easier customers.

    A low-risk cycle that succeeds first time would have done so without a
    notice, so including it measures the population, not the intervention.
    """
    easy = [
        CycleAccounting(
            cycle_id=f"easy{i}",
            arm="treatment",
            high_risk=False,
            notification_sent=False,
            succeeded_first_execution=True,
            retries_consumed=0,
            recovered_after_retry=False,
        )
        for i in range(500)
    ]
    hard = [_cycle(i, "treatment", prevented=i < 5) for i in range(50)] + [
        _cycle(100 + i, "control", prevented=i < 5) for i in range(50)
    ]

    report = account(easy + hard, high_risk_only=True)
    assert report.treatment.n == 50, "low-risk cycles leaked into the denominator"
    assert report.prevention_lift.contains_zero


# ── The mechanism §24.2 claims, measured ────────────────────────────────────


def test_late_notice_beats_a_72_hour_notice() -> None:
    """§24.2: "A notice sent 72 hours early is forgotten."

    The claim the whole section rests on, measured against the simulator's
    published response model (ADR-059) rather than asserted.
    """
    config = SimConfig()
    population = generate(config, seed=20260829, tenant_id="t_p9x", cycles=3000)

    optimised = naive = scored = 0
    for cycle in population:
        decided = cycle.due_at - timedelta(days=5)
        late = plan(debit_at=cycle.due_at, decided_at=decided, predicted_funding_at=None, risk=0.8)
        early = naive_plan(debit_at=cycle.due_at, decided_at=decided, risk=0.8)
        if not (late.will_send and early.will_send):
            continue
        scored += 1
        truth = cycle.truth.true_funding_time
        optimised += pdn.responds(
            config, cycle_id=cycle.cycle_id, send_at=late.send_at, funding_at=truth
        )
        naive += pdn.responds(
            config, cycle_id=cycle.cycle_id, send_at=early.send_at, funding_at=truth
        )

    assert scored > 500, f"only {scored} comparable cycles"
    assert optimised > naive * 3, (
        f"late notice prevented {optimised}, 72h notice {naive} — §24.2's "
        "central claim is not reproduced"
    )


def test_per_customer_history_predicts_funding_better_than_a_constant() -> None:
    """ADR-058's payoff: customers recur, and their own history is informative.

    Before archetypes were made stable per customer, per-customer prediction
    measured *worse* than a population constant — there was nothing consistent
    to learn. This pins the fix.
    """
    population = generate(SimConfig(), seed=20260829, tenant_id="t_p9y", cycles=6000)

    def offset(cycle: SimulatedCycle) -> float | None:
        funding = cycle.truth.true_funding_time
        if funding is None:
            return None
        return (funding - cycle.due_at).total_seconds() / 3600.0

    by_customer: dict[str, list[SimulatedCycle]] = defaultdict(list)
    for cycle in population:
        by_customer[cycle.customer_id].append(cycle)

    population_median = float(np.median([o for c in population if (o := offset(c)) is not None]))

    per_customer_errors, constant_errors = [], []
    for cycles in by_customer.values():
        if len(cycles) < 2:
            continue
        history = [o for c in cycles[:-1] if (o := offset(c)) is not None]
        truth = offset(cycles[-1])
        if truth is None or not history:
            continue
        per_customer_errors.append(abs(float(np.median(history)) - truth))
        constant_errors.append(abs(population_median - truth))

    assert per_customer_errors, "no customers with usable history"
    improvement = 1.0 - float(np.mean(per_customer_errors)) / float(np.mean(constant_errors))
    assert improvement > 0.3, (
        f"per-customer history only {improvement:.1%} better than a constant — "
        "archetypes may no longer be stable per customer"
    )


def test_risk_score_reads_the_hazard_not_a_constant() -> None:
    """§24.2's first build item: score upcoming debits from the hazard model."""
    survival = np.clip(np.cumprod(np.full(100, 0.97)), 1e-9, 1.0)
    early, late = risk_score(survival, 1), risk_score(survival, 60)
    assert early > late, "risk should fall as funding accumulates"
    assert 0.0 < late < early <= 1.0


def test_a_plan_that_cannot_be_sent_lawfully_is_suppressed_not_forced() -> None:
    """Deciding too late must produce silence, never an unlawful send."""
    debit = ist(2026, 3, 5, 9, 0)
    result: NotificationPlan = plan(
        debit_at=debit,
        decided_at=debit - timedelta(hours=2),  # inside the notice lead already
        predicted_funding_at=None,
        risk=0.9,
    )
    assert not result.will_send
    assert result.suppressed_reason is not None


# ── §24.2 content assembly: RBI-required fields ─────────────────────────────


def test_the_notice_always_carries_an_opt_out() -> None:
    """§16's obligations table: "≥24h before every debit, **with opt-out**".

    The opt-out is part of what makes the notice lawful, not a courtesy — so it
    is present regardless of risk, channel, or whether a pay-now link is.
    """
    for risk in (0.0, 0.49, 0.5, 1.0):
        notice = assemble(
            send_at=ist(2026, 3, 4, 18, 0),
            debit_at=ist(2026, 3, 6, 9, 0),
            amount_paise=49_900,
            risk=risk,
            consent_ref="c1",
            mandate_ref="sub_42",
        )
        assert notice.content.opt_out_path, f"no opt-out at risk={risk}"
        assert notice.content.required_fields_present


def test_a_body_without_an_opt_out_cannot_be_built() -> None:
    """Refused at construction, not left for the gate to catch.

    Building it would be planning an action the gate must deny.
    """
    with pytest.raises(ChannelError, match="opt-out"):
        Content(
            amount_paise=49_900,
            debit_at=ist(2026, 3, 6, 9, 0),
            mandate_ref="sub_42",
            opt_out_path="",
        )


def test_the_pay_now_link_tracks_risk_but_the_opt_out_does_not() -> None:
    """§24.2: the one-tap path is conditional; the opt-out never is."""

    def built(risk: float) -> Content:
        return assemble(
            send_at=ist(2026, 3, 4, 18, 0),
            debit_at=ist(2026, 3, 6, 9, 0),
            amount_paise=49_900,
            risk=risk,
            consent_ref="c1",
            mandate_ref="sub_9",
        ).content

    high, low = built(0.9), built(0.1)

    assert high.pay_now_link is not None
    assert low.pay_now_link is None
    assert high.opt_out_path == low.opt_out_path


def test_the_notice_identifies_the_mandate_and_the_amount() -> None:
    """A notice a customer cannot tie to a specific debit is not notice."""
    notice = assemble(
        send_at=ist(2026, 3, 4, 18, 0),
        debit_at=ist(2026, 3, 6, 9, 0),
        amount_paise=49_900,
        risk=0.8,
        consent_ref="c1",
        mandate_ref="sub_77",
    )
    assert notice.content.mandate_ref == "sub_77"
    assert notice.content.amount_paise == 49_900
    assert notice.content.debit_at == ist(2026, 3, 6, 9, 0)

    with pytest.raises(ChannelError, match="identify the mandate"):
        Content(
            amount_paise=49_900,
            debit_at=ist(2026, 3, 6, 9, 0),
            mandate_ref="",
            opt_out_path="/opt-out",
        )
