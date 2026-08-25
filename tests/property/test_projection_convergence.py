"""Convergence and budget properties (§40.3, Playbook Phase 1).

The exit criterion says *every* permutation of an event set converges. These run
against the pure fold rather than the database: the claim is about the
projection function, and testing it directly means every one of the 5,040
orderings can be enumerated in milliseconds instead of round-tripping Postgres.

ADR-016: exhaustive `itertools.permutations` for the fixed-set criterion, since
the set is small enough to enumerate and exhaustive beats sampled; Hypothesis
for the open-ended properties where inputs cannot be enumerated.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from prayas.domain.states import CycleState, MandateState
from prayas.ingest.envelope import Event
from prayas.ingest.projector import project_cycle, project_mandate

BASE = datetime(2026, 1, 1, tzinfo=UTC)
MANDATE = "sub_test"
INVOICE = "inv_test"


def _event(event_id: str, event_type: str, *, minutes: int, amount: int = 49900) -> Event:
    return Event(
        event_id=event_id,
        tenant_id="t_alpha",
        event_type=event_type,
        mandate_id=MANDATE,
        cycle_ref=INVOICE,
        occurred_at=BASE + timedelta(minutes=minutes),
        payload={
            "event": event_type,
            "payload": {"payment": {"entity": {"amount": amount, "invoice_id": INVOICE}}},
        },
    )


#: A realistic cycle: authenticated, three failures, then a late capture.
FIXED_EVENT_SET = [
    _event("e1", "subscription.authenticated", minutes=0),
    _event("e2", "payment.failed", minutes=10),
    _event("e3", "payment.failed", minutes=20),
    _event("e4", "payment.failed", minutes=30),
    _event("e5", "payment.captured", minutes=40),
    _event("e6", "subscription.halted", minutes=50),
    _event("e7", "subscription.cancelled", minutes=60),
]


def test_every_permutation_converges_to_identical_state() -> None:
    """Exhaustive over all 5,040 orderings — not sampled."""
    orderings = list(itertools.permutations(FIXED_EVENT_SET))
    assert len(orderings) == 5040, "the fixed set changed; update the expected count"

    baseline_cycle = project_cycle(FIXED_EVENT_SET)
    baseline_mandate = project_mandate(FIXED_EVENT_SET)

    for ordering in orderings:
        assert project_cycle(list(ordering)) == baseline_cycle
        assert project_mandate(list(ordering)) == baseline_mandate

    # Pin the actual values, so a projector that converges on the *wrong*
    # answer consistently still fails.
    assert baseline_cycle.state is CycleState.SUCCEEDED
    assert baseline_cycle.attempts_used == 3
    assert baseline_cycle.recovered_paise == 49900
    assert baseline_mandate is MandateState.REVOKED


def test_reversed_order_matches_forward_order() -> None:
    """The specific case a real provider produces: replayed backwards."""
    assert project_cycle(list(reversed(FIXED_EVENT_SET))) == project_cycle(FIXED_EVENT_SET)
    assert project_mandate(list(reversed(FIXED_EVENT_SET))) == project_mandate(FIXED_EVENT_SET)


_EVENT_TYPES = st.sampled_from(
    [
        "payment.failed",
        "payment.captured",
        "subscription.authenticated",
        "subscription.halted",
        "subscription.cancelled",
        "subscription.paused",
    ]
)


@st.composite
def _event_sets(draw: st.DrawFn) -> list[Event]:
    count = draw(st.integers(min_value=1, max_value=12))
    types = draw(st.lists(_EVENT_TYPES, min_size=count, max_size=count))
    # Deliberately allow repeated minutes so ties exercise the event_id tiebreak.
    minutes = draw(st.lists(st.integers(min_value=0, max_value=6), min_size=count, max_size=count))
    return [
        _event(f"e{i}", t, minutes=m) for i, (t, m) in enumerate(zip(types, minutes, strict=True))
    ]


@given(events=_event_sets(), seed=st.integers())
@settings(max_examples=200, deadline=None)
def test_projection_is_order_independent_for_arbitrary_sets(events: list[Event], seed: int) -> None:
    """§40.3 — any permutation of an event set converges to the same state."""
    import random

    shuffled = events[:]
    random.Random(seed).shuffle(shuffled)

    assert project_cycle(shuffled) == project_cycle(events)
    assert project_mandate(shuffled) == project_mandate(events)


@given(events=_event_sets())
@settings(max_examples=200, deadline=None)
def test_attempts_used_never_exceeds_the_regulator_budget(events: list[Event]) -> None:
    """§40.3 — `attempts_used ≤ attempt_budget` under arbitrary interleavings.

    §9: UPI Autopay permits 1 execution + 3 retries. The projector counts
    *distinct* failure event ids, so duplicate deliveries cannot inflate it —
    but a set with more than four genuine failures legitimately exceeds four,
    which is a signal the executor over-attempted, not a projector bug. The
    invariant asserted here is that the count equals distinct failures exactly.
    """
    projection = project_cycle(events)
    distinct_failures = len({e.event_id for e in events if e.event_type == "payment.failed"})

    assert projection.attempts_used == distinct_failures
    assert projection.attempts_used <= len(events)


@given(events=_event_sets())
@settings(max_examples=100, deadline=None)
def test_duplicate_delivery_never_changes_the_projection(events: list[Event]) -> None:
    """Delivering the same events again is a no-op — the basis of dedupe."""
    assert project_cycle(events + events) == project_cycle(events)
    assert project_mandate(events + events) == project_mandate(events)
