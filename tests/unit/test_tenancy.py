"""Tenant identifier validation at the boundary (Master Spec §18)."""

from __future__ import annotations

import pytest

from prayas.db.tenancy import TENANT_SETTING, TenantContextError, validate_tenant_id


@pytest.mark.parametrize("tenant_id", ["t_alpha", "acme", "A1", "a" * 64, "tenant-01"])
def test_valid_identifiers_are_accepted(tenant_id: str) -> None:
    assert validate_tenant_id(tenant_id) == tenant_id


@pytest.mark.parametrize(
    "tenant_id",
    [
        "",
        "_leading",  # must start alphanumeric
        "a" * 65,  # over length
        "has space",
        "quote'injection",
        "semi;colon",
        "unicodeé",
        "new\nline",
    ],
)
def test_malformed_identifiers_are_rejected(tenant_id: str) -> None:
    with pytest.raises(TenantContextError):
        validate_tenant_id(tenant_id)


def test_sql_comment_syntax_is_not_special() -> None:
    """`drop--comment` is a well-formed identifier, and deliberately accepted.

    Hyphens are valid (`tenant-01` is a realistic slug), and the value never
    reaches Postgres as SQL text — it is bound as a parameter to `set_config`,
    so comment syntax inside it is inert. Rejecting it would be cargo-cult
    escaping standing in for the parameterisation that actually does the work.
    """
    assert validate_tenant_id("drop--comment") == "drop--comment"


def test_setting_name_matches_the_spec() -> None:
    """§18 names this setting explicitly; a rename would silently break every policy."""
    assert TENANT_SETTING == "app.tenant_id"
