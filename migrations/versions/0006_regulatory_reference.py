"""Regulatory reference data — AFA-free ceilings (ADR-021).

§30.1's `afa_free_cap(mcc)` needs the ₹15,000 default and the ₹1,00,000 exempt
ceiling. Those are regulatory facts, so Invariant 10 applies to them exactly as
it applies to the predicates: citation and `as_of`, or it is not a rule.

Global, not tenant-scoped — the ceiling is set by the RBI, not per merchant.
Sits alongside `compliance_rules` in GLOBAL_TABLES.

Revision ID: 0006_regulatory_reference
Revises: 0005_webhook_secrets
Create Date: 2026-08-25

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_regulatory_reference"
down_revision: str | None = "0005_webhook_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE regulatory_reference (
            mcc        TEXT   NOT NULL,   -- '*' is the default ceiling
            cap_paise  BIGINT NOT NULL,   -- integer paise, never rupees
            regulator  TEXT   NOT NULL,
            citation   TEXT   NOT NULL,   -- Invariant 10
            as_of      DATE   NOT NULL,   -- Invariant 10
            PRIMARY KEY (mcc, as_of),
            CHECK (cap_paise > 0)
        );
        """
    )
    # Lookups are always "the ceiling in force at this instant", so the index
    # matches that access pattern rather than the primary key order.
    op.execute("CREATE INDEX idx_regref_effective ON regulatory_reference (mcc, as_of DESC);")
    op.execute(f"GRANT SELECT ON regulatory_reference TO {APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON regulatory_reference FROM {APP_ROLE};")
    op.execute("DROP TABLE IF EXISTS regulatory_reference CASCADE;")
