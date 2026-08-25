"""Signature verification (§41.1 T6).

§40.2: "Boundary cases are the substance here." This is the code that decides
whether a forged event becomes real state, so both sides of every boundary.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from prayas.ingest.verify import (
    SecretResolutionError,
    expected_signature,
    is_valid_secret_ref,
    resolve_all,
    resolve_secret,
    verify,
)

BODY = b'{"event":"payment.failed","created_at":1767225600}'
SECRET = "s3cret_material"
GOOD = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()


def test_correct_signature_verifies() -> None:
    assert verify(BODY, GOOD, [SECRET]) is True


@pytest.mark.parametrize(
    ("label", "signature"),
    [
        ("empty", ""),
        ("wrong", "0" * 64),
        ("truncated", GOOD[:-1]),
        ("one char changed", ("0" if GOOD[0] != "0" else "1") + GOOD[1:]),
        ("uppercased", GOOD.upper()),
        ("with whitespace", f" {GOOD} "),
    ],
)
def test_bad_signatures_are_rejected(label: str, signature: str) -> None:
    assert verify(BODY, signature, [SECRET]) is False, f"{label} was accepted"


def test_no_secrets_fails_closed() -> None:
    """A deployment with nothing provisioned rejects everything, not accepts."""
    assert verify(BODY, GOOD, []) is False


def test_empty_secret_strings_are_skipped_not_used() -> None:
    """A blank env var must not be treated as a usable secret."""
    assert verify(BODY, GOOD, ["", ""]) is False


def test_rotation_window_accepts_either_secret() -> None:
    """Several secrets valid at once, so rotation drops no events."""
    other = "the_incoming_secret"
    other_sig = hmac.new(other.encode(), BODY, hashlib.sha256).hexdigest()

    assert verify(BODY, GOOD, [SECRET, other]) is True
    assert verify(BODY, other_sig, [SECRET, other]) is True
    assert verify(BODY, other_sig, [other, SECRET]) is True, "order must not matter"


def test_body_mutation_invalidates_the_signature() -> None:
    """The signature is over raw bytes — any change breaks it."""
    assert verify(BODY + b" ", GOOD, [SECRET]) is False
    assert verify(BODY.replace(b"failed", b"captured"), GOOD, [SECRET]) is False


def test_reserialised_json_does_not_verify() -> None:
    """Why the handler must never parse before verifying.

    `json.loads` then `json.dumps` produces different bytes — different key
    order and separators — so verifying the re-encoded form would reject
    legitimate events, or worse, invite verifying the parsed form instead.
    """
    import json

    reserialised = json.dumps(json.loads(BODY)).encode()
    assert reserialised != BODY
    assert verify(reserialised, GOOD, [SECRET]) is False


@pytest.mark.parametrize("ref", ["WH_V1", "A", "WH_TEST_V2", "X9_0"])
def test_valid_secret_refs(ref: str) -> None:
    assert is_valid_secret_ref(ref)


@pytest.mark.parametrize(
    "ref", ["", "lower_case", "_LEADING", "HAS SPACE", "A" * 65, "DASH-ED", "SEMI;"]
)
def test_malformed_secret_refs_are_rejected(ref: str) -> None:
    assert not is_valid_secret_ref(ref)
    with pytest.raises(SecretResolutionError):
        resolve_secret(ref)


def test_resolution_reads_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAYAS_WEBHOOK_SECRET_WH_X", "material")
    assert resolve_secret("WH_X") == "material"


def test_unprovisioned_ref_returns_none_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During rotation a ref may not be provisioned yet; that must not break others."""
    monkeypatch.delenv("PRAYAS_WEBHOOK_SECRET_WH_MISSING", raising=False)
    assert resolve_secret("WH_MISSING") is None


def test_resolve_all_skips_unprovisioned_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAYAS_WEBHOOK_SECRET_WH_PRESENT", "here")
    monkeypatch.delenv("PRAYAS_WEBHOOK_SECRET_WH_ABSENT", raising=False)

    assert resolve_all(["WH_PRESENT", "WH_ABSENT"]) == ["here"]


def test_expected_signature_matches_the_reference_construction() -> None:
    """Pinned against a hand-computed HMAC-SHA256, not against our own helper."""
    assert (
        expected_signature(BODY, SECRET)
        == hmac.new(SECRET.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    )
