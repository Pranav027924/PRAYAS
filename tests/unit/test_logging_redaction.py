"""PII redaction in structured logs (Execution Playbook, Phase 0; Invariant 7).

Invariant 7 means these values should never reach a log call at all. This is the
second line of defence, and it is tested as such — both sides of each boundary,
including the case that must NOT be redacted, since over-redaction that swallows
`mandate_id` would quietly destroy the audit trail's usefulness.
"""

from __future__ import annotations

import json
import logging

import pytest

from prayas.observability.logging import MASK, RedactingJsonFormatter, redact


def _format(record_kwargs: dict[str, object], message: str = "event") -> dict[str, object]:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    for key, value in record_kwargs.items():
        setattr(record, key, value)
    parsed: dict[str, object] = json.loads(RedactingJsonFormatter().format(record))
    return parsed


@pytest.mark.parametrize(
    "field",
    [
        "pan",
        "card_number",
        "cvv",
        "account_number",
        "ifsc",
        "balance",
        "email",
        "phone",
        "mobile",
        "vpa",
        "upi_id",
        "password",
        "secret",
        "token",
        "api_key",
        "signature",
    ],
)
def test_sensitive_field_names_are_masked(field: str) -> None:
    out = _format({field: "sensitive-value"})
    assert out[field] == MASK, f"{field} was not redacted"


@pytest.mark.parametrize(
    "field",
    ["mandate_id", "cycle_id", "tenant_id", "attempt_seq", "decision_id", "amount_paise"],
)
def test_operational_identifiers_survive(field: str) -> None:
    """Redaction must not eat the fields the audit trail depends on (§32)."""
    out = _format({field: "keep-me"})
    assert out[field] == "keep-me", f"{field} was redacted but must not be"


def test_matching_is_case_insensitive_and_substring() -> None:
    out = _format({"CustomerEmailAddress": "a@b.com", "raw_PAN_digits": "4111111111111111"})
    assert out["CustomerEmailAddress"] == MASK
    assert out["raw_PAN_digits"] == MASK


def test_nested_structures_are_redacted() -> None:
    payload = {"customer": {"vpa": "someone@upi", "id": "cust_1"}, "items": [{"token": "t"}]}
    result = redact(payload)
    assert result == {"customer": {"vpa": MASK, "id": "cust_1"}, "items": [{"token": MASK}]}


def test_free_text_values_are_scrubbed() -> None:
    """Fallback for values that arrive inside a message rather than a field."""
    out = _format({}, message="charge failed for 4111111111111111 / user@example.com")
    message = str(out["message"])
    assert "4111111111111111" not in message
    assert "user@example.com" not in message
    assert MASK in message


def test_recursion_is_depth_bounded() -> None:
    """The logging path sits in front of the money path and must not stall."""
    deep: dict[str, object] = {}
    node = deep
    for _ in range(50):
        child: dict[str, object] = {}
        node["next"] = child
        node = child

    assert redact(deep) is not None  # terminates rather than recursing without bound


def test_configure_replaces_existing_handlers() -> None:
    """A library's plain handler must not be able to emit an unredacted duplicate."""
    from prayas.observability.logging import configure

    root = logging.getLogger()
    root.addHandler(logging.StreamHandler())
    configure("INFO")

    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, RedactingJsonFormatter)
