"""Event normalisation (D2 rail abstraction) and §11 state machines."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from prayas.domain.states import (
    ATTEMPT_TERMINAL,
    CYCLE_PAID,
    CYCLE_TERMINAL,
    MANDATE_TERMINAL,
    AttemptState,
    CycleState,
    MandateState,
    can_transition_attempt,
    can_transition_cycle,
    can_transition_mandate,
)
from prayas.ingest.envelope import (
    MalformedEventError,
    extract_amount_paise,
    extract_cycle_ref,
    extract_mandate_id,
    extract_next_billing_at,
    parse,
)

# ── envelope ────────────────────────────────────────────────────────────────

BODY = {
    "entity": "event",
    "event": "payment.failed",
    "created_at": 1_767_225_600,
    "payload": {
        "subscription": {"entity": {"id": "sub_1", "current_end": 1_769_904_000}},
        "payment": {"entity": {"id": "pay_1", "amount": 49900, "invoice_id": "inv_1"}},
    },
}


def _raw(body: dict[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode()


def test_parse_extracts_the_envelope() -> None:
    event = parse(_raw(BODY), tenant_id="t_alpha", event_id="evt_1")

    assert event.event_id == "evt_1"
    assert event.tenant_id == "t_alpha"
    assert event.event_type == "payment.failed"
    assert event.mandate_id == "sub_1"
    assert event.cycle_ref == "inv_1"
    assert event.occurred_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_body_id_is_used_when_the_header_is_absent() -> None:
    event = parse(_raw({**BODY, "id": "evt_from_body"}), tenant_id="t", event_id=None)
    assert event.event_id == "evt_from_body"


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("not json", b"{not json"),
        ("not an object", b'["a","list"]'),
        ("no event type", b'{"created_at":1}'),
        ("empty event type", b'{"event":""}'),
        ("invalid utf-8", b'{"event":"\xff\xfe"}'),
    ],
)
def test_malformed_bodies_are_rejected(label: str, raw: bytes) -> None:
    with pytest.raises(MalformedEventError):
        parse(raw, tenant_id="t", event_id="e1")


def test_missing_event_id_everywhere_is_rejected() -> None:
    with pytest.raises(MalformedEventError, match="no event id"):
        parse(_raw(BODY), tenant_id="t", event_id=None)


def test_ordering_key_is_a_total_order() -> None:
    """Ties on timestamp must still order deterministically, or convergence fails."""
    a = parse(_raw(BODY), tenant_id="t", event_id="evt_a")
    b = parse(_raw(BODY), tenant_id="t", event_id="evt_b")

    assert a.occurred_at == b.occurred_at
    assert a.ordering_key < b.ordering_key


def test_extractors_return_none_rather_than_raising_on_absent_fields() -> None:
    empty: dict[str, object] = {}
    assert extract_mandate_id(empty) is None
    assert extract_cycle_ref(empty) is None
    assert extract_amount_paise(empty) is None
    assert extract_next_billing_at(empty) is None


def test_amount_is_integer_paise_never_float() -> None:
    """Project standard: money is integer paise. A float amount is not an amount."""
    assert extract_amount_paise(BODY) == 49900
    assert isinstance(extract_amount_paise(BODY), int)

    floaty = {"payload": {"payment": {"entity": {"amount": 499.00}}}}
    assert extract_amount_paise(floaty) is None, "a float amount must not be accepted"


def test_next_billing_date_becomes_the_cycle_deadline() -> None:
    """ADR-018 — deadline is the next billing date."""
    assert extract_next_billing_at(BODY) == datetime(2026, 2, 1, tzinfo=UTC)


# ── state machines (§11) ────────────────────────────────────────────────────


def test_terminal_sets_are_derived_not_hardcoded() -> None:
    assert MandateState.EXPIRED in MANDATE_TERMINAL
    assert MandateState.ACTIVE not in MANDATE_TERMINAL
    assert CycleState.SUCCEEDED in CYCLE_TERMINAL
    assert CycleState.SUPERSEDED in CYCLE_TERMINAL
    assert CycleState.EXECUTING not in CYCLE_TERMINAL
    assert AttemptState.RECONCILED in ATTEMPT_TERMINAL


def test_terminal_states_have_no_outgoing_transitions() -> None:
    """Absorption is what makes a late event unable to reanimate a settled entity."""
    for cycle_state in CYCLE_TERMINAL:
        assert not any(can_transition_cycle(cycle_state, other) for other in CycleState)
    for mandate_state in MANDATE_TERMINAL:
        assert not any(can_transition_mandate(mandate_state, other) for other in MandateState)
    for attempt_state in ATTEMPT_TERMINAL:
        assert not any(can_transition_attempt(attempt_state, other) for other in AttemptState)


def test_paid_states_are_a_subset_of_terminal() -> None:
    assert CYCLE_PAID <= CYCLE_TERMINAL


@pytest.mark.parametrize(
    ("current", "proposed", "legal"),
    [
        (MandateState.CREATED, MandateState.ACTIVE, True),
        (MandateState.ACTIVE, MandateState.AT_RISK, True),
        (MandateState.AT_RISK, MandateState.REVOKED, True),
        (MandateState.REVOKED, MandateState.RE_ENROLLING, True),
        (MandateState.RE_ENROLLING, MandateState.ACTIVE, True),
        # Both sides of the boundary — these must NOT be legal.
        (MandateState.CREATED, MandateState.REVOKED, False),
        (MandateState.REVOKED, MandateState.ACTIVE, False),
        (MandateState.EXPIRED, MandateState.ACTIVE, False),
    ],
)
def test_mandate_transition_legality(
    current: MandateState, proposed: MandateState, legal: bool
) -> None:
    assert can_transition_mandate(current, proposed) is legal


@pytest.mark.parametrize(
    ("current", "proposed", "legal"),
    [
        (CycleState.SCHEDULED, CycleState.PDN_SENT, True),
        (CycleState.PDN_SENT, CycleState.EXECUTING, True),
        (CycleState.EXECUTING, CycleState.SUCCEEDED, True),
        (CycleState.EXECUTING, CycleState.BUDGET_EXHAUSTED, True),
        (CycleState.EXECUTING, CycleState.SUPERSEDED, True),
        (CycleState.SCHEDULED, CycleState.SUCCEEDED, False),
        (CycleState.SUCCEEDED, CycleState.EXECUTING, False),
    ],
)
def test_cycle_transition_legality(current: CycleState, proposed: CycleState, legal: bool) -> None:
    assert can_transition_cycle(current, proposed) is legal


def test_every_state_appears_in_its_transition_table() -> None:
    """A state absent from the table would raise KeyError at projection time."""
    for mandate_state in MandateState:
        assert can_transition_mandate(mandate_state, mandate_state) in (True, False)
    for cycle_state in CycleState:
        assert can_transition_cycle(cycle_state, cycle_state) in (True, False)
    for attempt_state in AttemptState:
        assert can_transition_attempt(attempt_state, attempt_state) in (True, False)
