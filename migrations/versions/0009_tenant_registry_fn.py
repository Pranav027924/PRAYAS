"""Tenant enumeration for platform-level workers (ADR-046; Master Spec §18).

The executor drains per tenant, which §18 requires ("weighted fair queuing
keyed on tenant"). But `tenants` is under RLS (ADR-013) with a policy keyed on
`app.tenant_id`, so a worker with no tenant bound sees nothing — it cannot
discover which tenants exist in order to bind them.

This adds the narrowest opening that resolves it: a SECURITY DEFINER function
returning **only** `tenant_id`. Tenant identifiers are platform metadata; the
tenant *data* the policy protects — `config`, holding policy weights, fatigue
caps and kill switches — stays behind it, and a bug that read another tenant's
config while serving a request would still be stopped by the database.

Follows the precedent of ADR-014a (`webhook_secrets`) and ADR-030
(`sim_ground_truth`): carve out a specific, owner-scoped read rather than
weaken the table's own policy.

Revision ID: 0009_tenant_registry_fn
Revises: 0008_sim_ground_truth
Create Date: 2026-08-27

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_tenant_registry_fn"
down_revision: str | None = "0008_sim_ground_truth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"


def upgrade() -> None:
    # SECURITY DEFINER runs as the owner, which is not subject to the policy.
    # `search_path` is pinned inside the function: a SECURITY DEFINER function
    # with a caller-controlled search_path is a privilege-escalation vector.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prayas_tenant_ids()
        RETURNS TABLE (tenant_id TEXT)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        STABLE
        AS $$
            SELECT t.tenant_id FROM public.tenants t ORDER BY t.tenant_id
        $$;
        """
    )

    # Nobody but the application role needs it.
    op.execute("REVOKE ALL ON FUNCTION prayas_tenant_ids() FROM PUBLIC;")
    op.execute(f"GRANT EXECUTE ON FUNCTION prayas_tenant_ids() TO {APP_ROLE};")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS prayas_tenant_ids();")
