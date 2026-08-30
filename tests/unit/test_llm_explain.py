"""Grounded explanation and residency containment (§41.3, §32, §6, T7).

Two Phase 13 exit criteria: "explanations contain no fact absent from the
record" and "no PII reaches any external provider — verified by payload
inspection test".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from prayas.llm.explain import (
    NON_PII_FIELDS,
    PII_FIELDS,
    ExplanationError,
    compose,
    payload_for_provider,
    ungrounded_claims,
)
from prayas.llm.provider import StubExternalProvider

TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _record(**overrides: Any) -> dict[str, Any]:
    record = {
        # Identifying — must never leave the region.
        "decision_id": "t_alpha_dec_7",
        "tenant_id": "t_alpha",
        "customer_id": "cust_9f3a",
        "mandate_id": "sub_0000144",
        "cycle_id": "inv_0000144",
        "consent_ref": "consent_abc",
        # Non-PII — may.
        "action_type": "retry",
        "verdict": "ALLOW",
        "amount_paise": 149_900,
        "decline_code": "51",
        "model_versions": {"hazard": "v1.2.0"},
        "compliance_checks": [{"rule_id": "NPCI-AUTOPAY-WINDOW", "verdict": "ALLOW"}],
        "recovered_paise": 149_900,
        "degraded": False,
        "ts": TS,
    }
    record.update(overrides)
    return record


# ── residency: the allowlist (§41.3, T7) ───────────────────────────────────


def test_no_identifying_field_reaches_the_payload() -> None:
    """**Phase 13 exit criterion**, by inspection of what would be sent."""
    payload = payload_for_provider(_record())
    for field in PII_FIELDS:
        assert field not in payload, f"{field} would have left the region"


def test_the_payload_is_built_by_allowlist_not_by_redaction() -> None:
    """A denylist answers "what did we remember to remove". Adding a column to
    `decisions` must not silently widen what gets sent."""
    payload = payload_for_provider(_record(some_new_column="a customer's phone number"))
    assert "some_new_column" not in payload
    assert set(payload) <= set(NON_PII_FIELDS)


def test_identifying_values_do_not_survive_anywhere_in_the_payload() -> None:
    """Not merely absent by key: the *values* must not appear either, in case a
    permitted field ever carried one."""
    record = _record()
    serialised = str(payload_for_provider(record))
    for field in PII_FIELDS:
        value = record.get(field)
        if isinstance(value, str):
            assert value not in serialised, f"{field}'s value leaked into the payload"


def test_what_the_external_provider_actually_receives() -> None:
    """The containment claim, checked against a provider that records its input
    rather than against the function that builds it."""
    provider = StubExternalProvider()
    provider.explain(payload_for_provider(_record()))

    assert len(provider.seen) == 1
    seen = str(provider.seen[0])
    for value in ("cust_9f3a", "sub_0000144", "inv_0000144", "consent_abc", "t_alpha"):
        assert value not in seen


def test_the_allowlist_and_the_pii_list_stay_disjoint() -> None:
    """A metatest: the two constants are the whole guarantee, so an edit that
    put a field in both must fail here rather than in production."""
    assert not set(NON_PII_FIELDS) & set(PII_FIELDS)


# ── grounding (§32, §6) ────────────────────────────────────────────────────


def test_a_composed_explanation_is_fully_grounded() -> None:
    """**Phase 13 exit criterion.**"""
    record = _record()
    explanation = compose(record)
    assert ungrounded_claims(explanation.text, record) == []
    assert "NPCI-AUTOPAY-WINDOW" in explanation.text
    assert "₹1,499.00" in explanation.text


def test_a_fabricated_fact_is_caught() -> None:
    """The check has to actually catch something, or it is decoration."""
    record = _record()
    fabricated = compose(record).text + " The customer was contacted 4 times."
    claims = ungrounded_claims(fabricated, record)
    assert "4" in claims


def test_a_fabricated_rule_citation_is_caught() -> None:
    """The most dangerous hallucination: an authoritative-looking citation for
    a rule that does not exist. Invariant 10 requires citations to be real."""
    record = _record()
    fabricated = compose(record).text + " Denied under RBI-EMANDATE-9999."
    assert "RBI-EMANDATE-9999" in ungrounded_claims(fabricated, record)


def test_an_explanation_asserts_only_what_the_record_holds() -> None:
    """Clauses appear only when their field does, so a sparse record produces a
    shorter explanation rather than an invented one."""
    sparse = {"action_type": "retry", "verdict": "DENY", "ts": TS}
    explanation = compose(sparse)
    assert ungrounded_claims(explanation.text, sparse) == []
    assert "₹" not in explanation.text
    assert "recovered" not in explanation.text
    assert "decline" not in explanation.text


def test_a_record_with_nothing_explainable_refuses() -> None:
    """Better to say nothing than to emit a confident empty sentence."""
    with pytest.raises(ExplanationError, match="nothing explainable"):
        compose({"tenant_id": "t_alpha"})


def test_grounding_tracks_which_fields_were_used() -> None:
    explanation = compose(_record())
    assert "amount_paise" in explanation.grounded_in
    assert "compliance_checks" in explanation.grounded_in
    assert all(field in NON_PII_FIELDS for field in explanation.grounded_in)


def test_a_denied_decision_reads_as_denied() -> None:
    """§6: "click any recovered rupee and see why". The verdict has to survive
    into the text rather than being softened."""
    assert "denied" in compose(_record(verdict="DENY")).text


def test_degradation_is_disclosed() -> None:
    """§43 alerts on degraded decision share; an explanation that hid it would
    make the ledger less honest than the metric."""
    assert "degraded" in compose(_record(degraded=True)).text
