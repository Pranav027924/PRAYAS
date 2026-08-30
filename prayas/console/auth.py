"""Console authentication and RBAC (Master Spec §5, §18; ADR-087).

§5 lists five people with different jobs and different surfaces. A merchant's
finance lead needs batch results and guardrails; a compliance reviewer needs to
"prove this action was lawful when it fired"; a data scientist needs
calibration. They are not the same audience and should not see the same
screens.

**Tokens carry the tenant, and that is the point.** §18 requires every
connection to bind `app.tenant_id` "from a verified token. NEVER from a request
parameter, query string, or header the client controls." A token that carries
the tenant makes that binding a property of authentication rather than of
whoever remembered to pass it along.

**Signed, not encrypted.** The contents are not secret — a tenant knows its own
id and role. What must be impossible is *forging* them, which an HMAC over the
payload gives. Encryption here would suggest a confidentiality property that
does not exist and is not needed.

**Fails closed at every step.** No secret configured, malformed token, bad
signature, expired, unknown role: all refuse. There is no path through this
module that grants access on an error, because the errors are exactly when
someone is probing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Final

#: Signing secret. Absent means refuse — see `_secret`.
SECRET_ENV: Final = "PRAYAS_CONSOLE_TOKEN_SECRET"

#: Domain separator, so a signature from this routine cannot be replayed
#: against another HMAC in the system that happens to share a secret.
_DOMAIN: Final = b"prayas.console.v1"

# ── roles (§5) ─────────────────────────────────────────────────────────────

MERCHANT_OPS: Final = "merchant_ops"
MERCHANT_ENGINEER: Final = "merchant_engineer"
PLATFORM_PM: Final = "platform_pm"
COMPLIANCE_REVIEWER: Final = "compliance_reviewer"
DATA_SCIENTIST: Final = "data_scientist"

ROLES: Final[frozenset[str]] = frozenset(
    {MERCHANT_OPS, MERCHANT_ENGINEER, PLATFORM_PM, COMPLIANCE_REVIEWER, DATA_SCIENTIST}
)

# ── screens ────────────────────────────────────────────────────────────────

BATCH_RESULT: Final = "batch_result"
DECISION_REPLAY: Final = "decision_replay"
POLICY_SIMULATOR: Final = "policy_simulator"

SCREENS: Final[frozenset[str]] = frozenset({BATCH_RESULT, DECISION_REPLAY, POLICY_SIMULATOR})

#: Who may see what, from §5's "Surface" column.
#:
#: A **deny-by-default** map: a role absent from a screen's set cannot open it,
#: and adding a screen without deciding who may see it locks everyone out
#: rather than admitting everyone. The failure direction is chosen.
PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    # "Collect more of what I'm owed without losing subscribers" — and §6 says
    # guardrails are reported unprompted, so ops sees them with the headline.
    BATCH_RESULT: frozenset({MERCHANT_OPS, PLATFORM_PM, DATA_SCIENTIST}),
    # "Prove this action was lawful when it fired." The engineer is here too:
    # §5 gives them "turn this on without risking a wrong debit", which is
    # answered by reading what actually fired.
    DECISION_REPLAY: frozenset({COMPLIANCE_REVIEWER, MERCHANT_ENGINEER, PLATFORM_PM}),
    # Changing cost, lambda and mu is a policy act. Ops owns the trade-off;
    # the data scientist needs it to reason about the model's effect.
    POLICY_SIMULATOR: frozenset({MERCHANT_OPS, DATA_SCIENTIST}),
}


class AuthError(RuntimeError):
    """Authentication or authorisation failed. Never carries a hint why."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking, and on whose data."""

    tenant_id: str
    role: str
    expires_at: int

    def may_open(self, screen: str) -> bool:
        return self.role in PERMISSIONS.get(screen, frozenset())


def _secret() -> bytes:
    """The signing secret. **Absent means refuse, not fall back.**

    A default secret in a repository is a published secret. Generating one at
    import time would be worse: tokens would silently stop verifying on every
    restart, and someone would "fix" it by disabling verification.
    """
    value = os.environ.get(SECRET_ENV)
    if not value:
        raise AuthError(
            f"{SECRET_ENV} is not set. Console tokens cannot be verified without it, "
            f"and a default signing key in source is a published one."
        )
    return value.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(
    tenant_id: str,
    role: str,
    *,
    ttl_seconds: int = 3600,
    secret: bytes | None = None,
    now: int | None = None,
) -> str:
    """Mint a token. Used by whatever issues credentials, and by tests."""
    if role not in ROLES:
        raise AuthError(f"unknown role {role!r}; expected one of {sorted(ROLES)}")
    if not tenant_id:
        raise AuthError("a token must name a tenant (§18)")
    if ttl_seconds <= 0:
        raise AuthError("ttl_seconds must be positive")

    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "role": role,
            "exp": (now if now is not None else int(time.time())) + ttl_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    key = secret if secret is not None else _secret()
    signature = hmac.new(key, _DOMAIN + b"|" + payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify(token: str, *, secret: bytes | None = None, now: int | None = None) -> Principal:
    """Establish who is asking. Raises `AuthError` on anything unexpected.

    Uses `compare_digest`, so a caller cannot learn the signature a byte at a
    time from response timing.
    """
    key = secret if secret is not None else _secret()

    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _unb64(encoded_payload)
        signature = _unb64(encoded_signature)
    except (ValueError, TypeError) as exc:
        raise AuthError("malformed token") from exc

    expected = hmac.new(key, _DOMAIN + b"|" + payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("bad signature")

    try:
        claims: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuthError("malformed token") from exc

    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    expires_at = claims.get("exp")

    if not isinstance(tenant_id, str) or not tenant_id:
        raise AuthError("token names no tenant")
    if role not in ROLES:
        raise AuthError("token names no known role")
    if not isinstance(expires_at, int):
        raise AuthError("token has no expiry")
    if expires_at <= (now if now is not None else int(time.time())):
        raise AuthError("token expired")

    return Principal(tenant_id=tenant_id, role=role, expires_at=expires_at)


def authorise(principal: Principal, screen: str) -> None:
    """Refuse unless this principal may open this screen.

    Raises rather than returning a bool: a caller that forgets to check a
    returned value is a caller that granted access, and this is the only
    function standing between a role and a screen.
    """
    if screen not in SCREENS:
        raise AuthError(f"unknown screen {screen!r}")
    if not principal.may_open(screen):
        raise AuthError(f"role {principal.role!r} may not open {screen!r}")
