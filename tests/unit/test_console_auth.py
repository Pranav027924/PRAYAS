"""Console authentication and per-screen RBAC (Master Spec §5, §18; ADR-087).

**Phase 16 exit criterion:** "RBAC enforced per screen."

Asserted in *both* directions for every role and screen. A test that only
checks the permitted case passes against a system that permits everything,
which is the failure this criterion exists to prevent.
"""

from __future__ import annotations

import pytest

from prayas.console.auth import (
    BATCH_RESULT,
    COMPLIANCE_REVIEWER,
    DATA_SCIENTIST,
    DECISION_REPLAY,
    MERCHANT_ENGINEER,
    MERCHANT_OPS,
    PERMISSIONS,
    PLATFORM_PM,
    POLICY_SIMULATOR,
    ROLES,
    SCREENS,
    SECRET_ENV,
    AuthError,
    authorise,
    issue,
    verify,
)

SECRET = b"test-console-secret"
TENANT = "t_alpha"
NOW = 1_800_000_000


def _token(role: str, tenant: str = TENANT, ttl: int = 3600) -> str:
    return issue(tenant, role, ttl_seconds=ttl, secret=SECRET, now=NOW)


# ── the criterion: every role against every screen ─────────────────────────


@pytest.mark.parametrize("role", sorted(ROLES))
@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_rbac_is_enforced_in_both_directions(role: str, screen: str) -> None:
    """**Phase 16 exit criterion.** A test that only checked the permitted case
    would pass against a system that permits everything."""
    principal = verify(_token(role), secret=SECRET, now=NOW)
    permitted = role in PERMISSIONS[screen]

    if permitted:
        authorise(principal, screen)  # must not raise
    else:
        with pytest.raises(AuthError, match="may not open"):
            authorise(principal, screen)


def test_every_screen_has_an_explicit_permission_set() -> None:
    """Deny by default: a screen added without deciding who may see it must
    lock everyone out rather than admit everyone."""
    assert set(PERMISSIONS) == SCREENS
    for screen, allowed in PERMISSIONS.items():
        assert allowed, f"{screen} admits nobody — probably an oversight"
        assert allowed <= ROLES, f"{screen} names a role that does not exist"


def test_an_unknown_screen_is_refused() -> None:
    principal = verify(_token(PLATFORM_PM), secret=SECRET, now=NOW)
    with pytest.raises(AuthError, match="unknown screen"):
        authorise(principal, "admin_everything")


def test_the_personas_map_to_what_section_5_gives_them() -> None:
    """§5's surface column, as assertions rather than as a comment."""
    # "Prove this action was lawful when it fired."
    assert COMPLIANCE_REVIEWER in PERMISSIONS[DECISION_REPLAY]
    # A compliance reviewer has no business changing policy weights.
    assert COMPLIANCE_REVIEWER not in PERMISSIONS[POLICY_SIMULATOR]
    # "Collect more of what I'm owed without losing subscribers."
    assert MERCHANT_OPS in PERMISSIONS[BATCH_RESULT]
    # "Turn this on without risking a wrong debit" — answered by reading what
    # actually fired, not by re-tuning the policy.
    assert MERCHANT_ENGINEER in PERMISSIONS[DECISION_REPLAY]
    assert MERCHANT_ENGINEER not in PERMISSIONS[POLICY_SIMULATOR]
    assert DATA_SCIENTIST in PERMISSIONS[POLICY_SIMULATOR]


# ── the token itself ───────────────────────────────────────────────────────


def test_a_token_round_trips() -> None:
    principal = verify(_token(MERCHANT_OPS), secret=SECRET, now=NOW)
    assert principal.tenant_id == TENANT
    assert principal.role == MERCHANT_OPS


def test_the_token_carries_the_tenant() -> None:
    """§18: bind `app.tenant_id` "from a verified token. NEVER from a request
    parameter, query string, or header the client controls"."""
    principal = verify(_token(MERCHANT_OPS, tenant="t_beta"), secret=SECRET, now=NOW)
    assert principal.tenant_id == "t_beta"


def test_a_tampered_payload_is_refused() -> None:
    """The whole purpose of signing. Editing the tenant must not work."""
    import base64
    import json

    token = _token(MERCHANT_OPS)
    payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["tenant_id"] = "t_victim"
    forged = (
        base64.urlsafe_b64encode(
            json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(AuthError, match="bad signature"):
        verify(f"{forged}.{signature}", secret=SECRET, now=NOW)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    other = issue(TENANT, MERCHANT_OPS, secret=b"someone-elses-key", now=NOW)
    with pytest.raises(AuthError, match="bad signature"):
        verify(other, secret=SECRET, now=NOW)


def test_an_expired_token_is_refused() -> None:
    token = _token(MERCHANT_OPS, ttl=60)
    verify(token, secret=SECRET, now=NOW + 59)
    with pytest.raises(AuthError, match="expired"):
        verify(token, secret=SECRET, now=NOW + 61)


@pytest.mark.parametrize("token", ["", "nonsense", "a.b.c", "....", "onlyonepart", "!!!.???"])
def test_malformed_tokens_are_refused_without_crashing(token: str) -> None:
    """Anyone can send anything to this endpoint. It must refuse, not raise
    something the framework turns into a 500 with a stack trace."""
    with pytest.raises(AuthError):
        verify(token, secret=SECRET, now=NOW)


def test_an_unknown_role_cannot_be_minted_or_verified() -> None:
    with pytest.raises(AuthError, match="unknown role"):
        issue(TENANT, "superuser", secret=SECRET, now=NOW)


def test_a_token_without_a_tenant_is_refused() -> None:
    with pytest.raises(AuthError, match="must name a tenant"):
        issue("", MERCHANT_OPS, secret=SECRET, now=NOW)


def test_a_missing_secret_refuses_rather_than_defaulting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default signing key in a repository is a published key, and generating
    one at import time would break every token on restart — which someone would
    then "fix" by disabling verification."""
    monkeypatch.delenv(SECRET_ENV, raising=False)
    with pytest.raises(AuthError, match="published one"):
        issue(TENANT, MERCHANT_OPS)
    with pytest.raises(AuthError, match="published one"):
        verify("anything")


def test_verification_is_constant_time() -> None:
    """`compare_digest`, so a caller cannot learn the signature a byte at a
    time from response timing."""
    import inspect

    from prayas.console import auth

    source = inspect.getsource(auth.verify)
    assert "compare_digest" in source
    assert "signature ==" not in source


def test_errors_do_not_leak_which_check_failed_to_a_caller() -> None:
    """Distinguishing "no such tenant" from "wrong signature" tells a prober
    which half to keep working on. The messages differ for operators reading
    logs, but every failure is the same exception type."""
    for bad in ("", "nonsense", issue(TENANT, MERCHANT_OPS, secret=b"other", now=NOW)):
        with pytest.raises(AuthError):
            verify(bad, secret=SECRET, now=NOW)
