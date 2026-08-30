"""Contract tests against Razorpay's payload shapes (Phase 17; ADR-090).

`prayas/ingest/envelope.py` says of its extraction rules: "**Payload shapes need
validation against Razorpay test mode in Phase 17.** They are documented here as
extraction rules in one place precisely so that correcting them later is a
small, local change."

**This is the harness for that validation, and it is not yet the validation.**
It runs against fixtures committed here, so it catches a regression in our own
extraction. It does *not* prove the fixtures match what Razorpay actually
sends, because this repository has no test-mode credentials and no captured
deliveries — see FINDING-P17-01.

The distinction matters and is the whole reason this file says so out loud. The
day credentials exist, `FIXTURES` is repointed at recorded deliveries and every
assertion below becomes a real contract test. Until then a green run here means
"our parser still parses what we think Razorpay sends", which is a weaker claim
than the phase's exit criterion and must not be reported as that criterion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prayas.ingest.envelope import (
    MalformedEventError,
    extract_amount_paise,
    extract_cycle_ref,
    extract_decline_code,
    extract_mandate_id,
    extract_next_billing_at,
    parse,
)

FIXTURES = Path(__file__).parent / "fixtures"

#: Every event the projector acts on. A shape absent here is a shape nobody is
#: checking, so the list is asserted complete against the fixture directory.
EXPECTED_EVENTS = {
    "subscription.authenticated",
    "payment.captured",
    "payment.failed",
    "subscription.cancelled",
}


def _fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return body


def _all_fixtures() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


# ── the harness covers every event we act on ───────────────────────────────


def test_every_event_the_projector_handles_has_a_fixture() -> None:
    """A shape with no fixture is a shape nobody is checking."""
    assert set(_all_fixtures()) == EXPECTED_EVENTS


@pytest.mark.parametrize("name", sorted(EXPECTED_EVENTS))
def test_a_fixture_parses_into_an_event(name: str) -> None:
    body = _fixture(name)
    event = parse(json.dumps(body).encode(), tenant_id="t_alpha", event_id=f"evt_{name}")
    assert event.event_type == name
    assert event.tenant_id == "t_alpha"


@pytest.mark.parametrize("name", sorted(EXPECTED_EVENTS))
def test_a_mandate_id_is_extractable_from_every_event(name: str) -> None:
    """Every event we act on must be attributable to a mandate, or the
    projector cannot apply it to anything."""
    assert extract_mandate_id(_fixture(name)) is not None, name


def test_a_captured_payment_yields_an_amount() -> None:
    assert extract_amount_paise(_fixture("payment.captured")) == 149_900


def test_a_failed_payment_yields_a_decline_code() -> None:
    """§20's cause inference is keyed on this. A failure whose code we cannot
    extract is a failure we can only guess about."""
    assert extract_decline_code(_fixture("payment.failed")) == "51"


def test_authentication_yields_the_next_billing_date() -> None:
    """The scheduler needs it to place the first cycle."""
    assert extract_next_billing_at(_fixture("subscription.authenticated")) is not None


def test_a_cycle_reference_is_extractable_where_one_exists() -> None:
    assert extract_cycle_ref(_fixture("payment.captured")) is not None


# ── the parser refuses what it cannot trust ────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json", b"[]", b"null", b'{"no_event": true}'],
)
def test_a_malformed_body_is_refused(raw: bytes) -> None:
    """§41.1's T6 is webhook forgery. A parser that can be crashed by a body is
    reachable by anyone who can guess the URL."""
    with pytest.raises(MalformedEventError):
        parse(raw, tenant_id="t_alpha", event_id="evt_1")


def test_extraction_returns_none_rather_than_inventing_a_value() -> None:
    """An absent field must read as absent. A default here would be a fact the
    provider never sent, flowing into a decision as though it had."""
    empty: dict[str, Any] = {"event": "payment.failed", "payload": {}}
    assert extract_mandate_id(empty) is None
    assert extract_amount_paise(empty) is None
    assert extract_decline_code(empty) is None
    assert extract_next_billing_at(empty) is None


# ── the honesty check ──────────────────────────────────────────────────────


def test_the_fixtures_are_marked_as_unverified() -> None:
    """**FINDING-P17-01, pinned.**

    Every fixture carries `"_source": "constructed"` until it is replaced by a
    recorded delivery. This test fails the moment someone quietly swaps in a
    real capture without updating the record — and, more importantly, it stops
    anyone reading a green contract suite as evidence that the shapes match
    Razorpay, which is the exit criterion this phase cannot yet meet.
    """
    for name in _all_fixtures():
        source = _fixture(name).get("_source")
        assert source in {"constructed", "recorded"}, f"{name} declares no provenance"

    sources = {_fixture(n).get("_source") for n in _all_fixtures()}
    assert sources == {"constructed"}, (
        "a fixture is now marked 'recorded' — if real deliveries have been "
        "captured, FINDING-P17-01 should be updated and this assertion inverted"
    )
