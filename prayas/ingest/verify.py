"""Webhook signature verification (Master Spec §41.1 T6, Playbook Phase 1).

T6 — "Webhook forgery → fabricated state. Mitigation: HMAC over raw bytes,
constant-time compare, unverified payloads never persisted."

Three properties this module exists to guarantee:

1. **Raw bytes, never re-serialised.** `json.loads` then `json.dumps` produces
   different bytes — key order, separators, unicode escaping — and the signature
   is over what the sender actually sent. The body must be verified before it is
   parsed, and the parsed form must never be re-encoded for verification.

2. **Constant-time comparison.** `hmac.compare_digest`, never `==`, so a
   forger cannot recover a signature byte-by-byte from response timing.

3. **Rotation windows.** Several secrets may be valid at once, so a rotation
   does not drop events. Every currently-valid secret is tried.

Secret *material* never lives in the database. `webhook_secrets` stores
references (ADR-014); this module resolves each reference against the
environment, matching Phase 0's "secrets via environment injection from a
manager, never files".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from collections.abc import Sequence
from typing import Final

log = logging.getLogger(__name__)

#: Environment variable naming for resolved secret material.
SECRET_ENV_PREFIX: Final = "PRAYAS_WEBHOOK_SECRET_"

#: Secret refs name environment variables, so constrain them to a safe grammar.
_SECRET_REF_RE: Final = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,63}$")


class SecretResolutionError(RuntimeError):
    """A secret reference could not be resolved to material."""


def is_valid_secret_ref(secret_ref: str) -> bool:
    return bool(_SECRET_REF_RE.match(secret_ref))


def resolve_secret(secret_ref: str) -> str | None:
    """Resolve a reference to secret material, or None if absent.

    Returns None rather than raising for a missing variable: during a rotation
    one reference may legitimately not be provisioned yet, and that must not
    take down verification against the others.
    """
    if not is_valid_secret_ref(secret_ref):
        raise SecretResolutionError(
            f"Malformed secret_ref {secret_ref!r}: expected [A-Z0-9][A-Z0-9_]*"
        )
    return os.environ.get(f"{SECRET_ENV_PREFIX}{secret_ref}") or None


def expected_signature(raw_body: bytes, secret: str) -> str:
    """Razorpay signs the raw request body with HMAC-SHA256, hex-encoded."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify(raw_body: bytes, provided_signature: str, secrets: Sequence[str]) -> bool:
    """True if `provided_signature` matches under any supplied secret.

    An empty secret list returns False — fail closed. A deployment with no
    provisioned secret must reject every webhook, not accept every webhook.
    """
    if not provided_signature or not secrets:
        return False

    verified = False
    for secret in secrets:
        if not secret:
            continue
        # No early exit: every candidate is compared, so total work does not
        # depend on which secret matched or on how many were tried.
        if hmac.compare_digest(expected_signature(raw_body, secret), provided_signature):
            verified = True
    return verified


def resolve_all(secret_refs: Sequence[str]) -> list[str]:
    """Resolve every reference, skipping and logging any that are unprovisioned."""
    resolved: list[str] = []
    for ref in secret_refs:
        material = resolve_secret(ref)
        if material is None:
            log.warning("webhook.secret_ref_unresolved", extra={"secret_ref": ref})
            continue
        resolved.append(material)
    return resolved
