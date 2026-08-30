"""Durable contact suppression that survives forgetting (Master Spec §28; ADR-078).

§28's cascade both deletes the profile and suppresses all future contact. Those
two cannot live in the same row: `customer_profiles` is the thing being deleted,
so a `consent_withdrawn` flag on it is destroyed by the very operation that is
supposed to set it. A suppression that can be forgotten is not a suppression.

This table is therefore separate, insert-only for the application, and keyed on
the **pseudonym** rather than the original customer id — because §28 severs the
link to the natural person, and a suppression list keyed on an identifier that
no longer exists anywhere would match nothing.

Tenant-scoped with RLS forced, like every other table carrying a tenant
discriminator (§18, Invariant 6).

Revision ID: 0010_contact_suppressions
Revises: 0009_tenant_registry_fn
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_contact_suppressions"
down_revision: str | None = "0009_tenant_registry_fn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
POLICY = "tenant_isolation"
TENANT_EXPR = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE contact_suppressions (
            tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id),
            customer_ref   TEXT NOT NULL,
            reason         TEXT NOT NULL,
            suppressed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, customer_ref)
        );
        """
    )
    op.execute(
        "COMMENT ON COLUMN contact_suppressions.customer_ref IS "
        "'Pseudonym, not the original customer_id — §28 severs the person link.';"
    )

    op.execute("ALTER TABLE contact_suppressions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE contact_suppressions FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {POLICY} ON contact_suppressions"
        f" USING (tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )

    # No UPDATE, no DELETE. Lifting a suppression is a new consent event, not an
    # edit — and an erasure that can be quietly undone is not one.
    op.execute(f"GRANT SELECT, INSERT ON contact_suppressions TO {APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON contact_suppressions FROM {APP_ROLE};")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON contact_suppressions;")
    op.execute("DROP TABLE IF EXISTS contact_suppressions;")
