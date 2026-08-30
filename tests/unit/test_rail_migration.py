"""§24.4 rail migration — a proposal, never an action (Phase 14).

**Phase 14 exit criterion:** "Migration proposals fire on expiring cards with
an available alternate rail."

§24.4: "Card expiring or repeatedly fraud-held, with UPI Autopay available:
propose migration before the mandate lapses. Requires fresh AFA, so it is a
customer-consented flow, not an automatic action."

The load-bearing half is the second sentence. Auto-migrating would debit on a
mandate the customer never authorised, which is Invariant 1.
"""

from __future__ import annotations

import pytest

from prayas.retention.interventions import (
    EXPIRY_HORIZON_DAYS,
    FRAUD_HOLD_THRESHOLD,
    UNAVAILABLE,
    Intervention,
    RailMigrationProposal,
    propose_rail_migration,
)

ALL_RAILS = ["upi_autopay", "card_emandate", "enach"]


# ── the criterion ──────────────────────────────────────────────────────────


def test_an_expiring_card_with_an_alternate_proposes_migration() -> None:
    """**Phase 14 exit criterion.**"""
    proposal = propose_rail_migration(
        current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=20
    )
    assert proposal is not None
    assert proposal.from_rail == "card_emandate"
    assert proposal.to_rail == "upi_autopay", "§24.4 names UPI Autopay explicitly"
    assert proposal.days_to_expiry == 20
    assert "expiring" in proposal.rationale


def test_no_alternate_rail_means_no_proposal() -> None:
    """Without somewhere to go this is a lapse, not a migration."""
    assert (
        propose_rail_migration(
            current_rail="card_emandate",
            available_rails=["card_emandate"],
            days_to_expiry=10,
        )
        is None
    )


def test_a_healthy_card_is_left_alone() -> None:
    assert (
        propose_rail_migration(
            current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=300
        )
        is None
    )


def test_repeated_fraud_holds_propose_migration() -> None:
    """§24.4's second trigger. Two is a pattern; one is an incident."""
    assert (
        propose_rail_migration(
            current_rail="card_emandate",
            available_rails=ALL_RAILS,
            fraud_holds=FRAUD_HOLD_THRESHOLD - 1,
        )
        is None
    )
    proposal = propose_rail_migration(
        current_rail="card_emandate",
        available_rails=ALL_RAILS,
        fraud_holds=FRAUD_HOLD_THRESHOLD,
    )
    assert proposal is not None and "fraud holds" in proposal.reason


@pytest.mark.parametrize("rail", ["upi_autopay", "enach"])
def test_migration_is_card_specific(rail: str) -> None:
    """§24.4: "High value on the card e-mandate rail specifically." That is a
    scope limit — a UPI mandate does not expire and an eNACH mandate does not
    fraud-hold, so proposing migration off them would invent a reason."""
    assert (
        propose_rail_migration(
            current_rail=rail, available_rails=ALL_RAILS, days_to_expiry=5, fraud_holds=9
        )
        is None
    )


def test_the_expiry_horizon_boundary() -> None:
    """Asserted on both sides, per §40.2."""
    inside = propose_rail_migration(
        current_rail="card_emandate",
        available_rails=ALL_RAILS,
        days_to_expiry=EXPIRY_HORIZON_DAYS,
    )
    outside = propose_rail_migration(
        current_rail="card_emandate",
        available_rails=ALL_RAILS,
        days_to_expiry=EXPIRY_HORIZON_DAYS + 1,
    )
    assert inside is not None
    assert outside is None


def test_an_already_expired_card_still_proposes() -> None:
    """Zero days left is the most urgent case, not an out-of-range one."""
    assert (
        propose_rail_migration(
            current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=0
        )
        is not None
    )


def test_a_negative_expiry_is_not_treated_as_expiring() -> None:
    """A card whose expiry is in the past has lapsed; migration is a different
    flow from re-enrolment and should not silently claim it."""
    assert (
        propose_rail_migration(
            current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=-5
        )
        is None
    )


def test_enach_is_used_when_upi_is_not_available() -> None:
    proposal = propose_rail_migration(
        current_rail="card_emandate",
        available_rails=["card_emandate", "enach"],
        days_to_expiry=10,
    )
    assert proposal is not None and proposal.to_rail == "enach"


# ── the guarantee: a proposal cannot become a debit ────────────────────────


def test_a_proposal_always_requires_fresh_afa() -> None:
    """§24.4 — "customer-consented flow, not an automatic action". Stated as a
    property so a caller cannot forget to ask."""
    proposal = propose_rail_migration(
        current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=10
    )
    assert proposal is not None
    assert proposal.requires_fresh_afa


def test_a_proposal_carries_no_authority_to_debit() -> None:
    """Auto-migrating would debit on a mandate the customer never authorised
    (Invariant 1). The proposal has no consent reference and no way to make
    one — asserted as absence of capability, not as a check."""
    proposal = propose_rail_migration(
        current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=10
    )
    assert proposal is not None

    fields = set(RailMigrationProposal.__dataclass_fields__)
    assert fields == {"from_rail", "to_rail", "reason", "days_to_expiry"}
    for forbidden in ("consent_ref", "mandate_id", "amount_paise", "idem_key"):
        assert forbidden not in fields
    assert not hasattr(proposal, "apply")
    assert not hasattr(proposal, "migrate")


def test_the_rationale_says_it_needs_consent() -> None:
    """A merchant reading the proposal must see the constraint, not infer it."""
    proposal = propose_rail_migration(
        current_rail="card_emandate", available_rails=ALL_RAILS, days_to_expiry=10
    )
    assert proposal is not None
    assert "fresh AFA" in proposal.rationale
    assert "not automatic" in proposal.rationale


def test_rail_migration_is_no_longer_listed_unavailable() -> None:
    """It was deferred with a stated reason — "card e-mandate rail arrives in
    Phase 14". The rail arrived, so the deferral must go with it."""
    assert Intervention.RAIL_MIGRATION not in UNAVAILABLE
    assert Intervention.PARTIAL_COLLECTION in UNAVAILABLE, "§24.5 is still deferred"
