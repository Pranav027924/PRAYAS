"""A dedicated home for the demo's per-tenant virtual clock (Demo spec Phase 6, N2).

`tenants` is deliberately SELECT-only for `prayas_app` (ADR-013, `0004_tenants_rls`):
`config` carries rail preferences, fatigue caps and kill switches, and a table
that sensitive was locked to read-only on purpose. Phase 6 needs the app role to
write exactly one integer per demo tenant — how far its own clock has been
advanced — and the narrowest way to grant that is a table that holds nothing
else, rather than widening the grant on `tenants` itself. Even a total bug in
`prayas.demo.clock` can then corrupt only this table, never a fatigue cap or a
kill switch.

Tenant-scoped with RLS forced, like every other table carrying a tenant
discriminator (§18, Invariant 6). No DELETE granted: `reset` and `advance` both
write through an UPDATE (or the initial INSERT), so there is never a reason for
the application to remove a row.

Revision ID: 0014_demo_clock_state
Revises: 0013_pdn_rule_all_rails
Create Date: 2026-09-03

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_demo_clock_state"
down_revision: str | None = "0013_pdn_rule_all_rails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
POLICY = "tenant_isolation"
TENANT_EXPR = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE demo_clock_state (
            tenant_id       TEXT NOT NULL PRIMARY KEY REFERENCES tenants(tenant_id),
            offset_seconds  INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    op.execute(
        "COMMENT ON TABLE demo_clock_state IS "
        "'Demo spec Phase 6. Read by prayas.demo.clock.now_for; a tenant absent "
        "here has never been advanced and reads as offset 0 — the real clock.';"
    )

    op.execute("ALTER TABLE demo_clock_state ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE demo_clock_state FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {POLICY} ON demo_clock_state"
        f" USING (tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON demo_clock_state TO {APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON demo_clock_state FROM {APP_ROLE};")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON demo_clock_state;")
    op.execute("DROP TABLE IF EXISTS demo_clock_state;")
