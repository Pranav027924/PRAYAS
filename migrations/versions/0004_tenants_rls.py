"""Bring `tenants` under row-level security.

ADR-013. Found by the isolation metatest, not by review: `tenants.tenant_id` is a
genuine tenant discriminator (it is the primary key), but the table had been
classified as global alongside `segment_priors` and `compliance_rules`. The app
role holds SELECT on it, so any bound tenant could read every merchant's `name`
and `config` — and §18 defines `config` as policy weights, rail preferences,
fatigue caps, escalation ladder and kill switches. That is a cross-tenant read of
commercially sensitive data, which §18 classes as an incident.

Foreign keys from the seven referencing tables are unaffected: referential
integrity triggers execute as the table owner and are not subject to RLS.

Revision ID: 0004_tenants_rls
Revises: 0003_rls
Create Date: 2026-08-24

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_tenants_rls"
down_revision: str | None = "0003_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY = "tenant_isolation"
TENANT_EXPR = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {POLICY} ON tenants"
        f" USING (tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON tenants;")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;")
