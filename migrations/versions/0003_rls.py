"""Row-level security, FORCE, and least-privilege grants.

Governing spec: Master Spec §18. Approved deviations:

* ADR-004 — ``FORCE ROW LEVEL SECURITY`` in addition to ``ENABLE``. §18 specifies
  only ENABLE, which does not apply to the table owner; without FORCE, an
  owner-connected session bypasses every policy silently.
* ADR-007 — ``current_setting('app.tenant_id', true)``. The bare form in §18
  raises when context is unbound; the ``missing_ok`` form yields NULL, which
  matches no row and therefore denies.
* ADR-012 — ``experiment_config`` admits ``tenant_id IS NULL`` as platform-scoped.

One strengthening beyond §18's literal text, flagged for review: policies carry
``WITH CHECK`` as well as ``USING``. §18 shows only ``USING``, which governs
reads. Without ``WITH CHECK`` a tenant could INSERT a row stamped with another
tenant's id — a cross-tenant *write*, which is strictly worse than the read the
section is written to prevent.

Revision ID: 0003_rls
Revises: 0002_schema
Create Date: 2026-08-24

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_rls"
down_revision: str | None = "0002_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
POLICY = "tenant_isolation"
TENANT_EXPR = "current_setting('app.tenant_id', true)"

#: Frozen at this revision. Deliberately not imported from prayas.db.schema —
#: a migration is a historical record and must not drift when that file changes.
STANDARD_TENANT_TABLES = (
    "events_raw",
    "mandates",
    "cycles",
    "attempts",
    "interventions",
    "customer_profiles",
    "decisions",
    "scheduled_actions",
    "outbox",
)

#: table -> privileges granted to the application role.
GRANTS: dict[str, str] = {
    # Global tables, read-only for the app.
    "tenants": "SELECT",
    "segment_priors": "SELECT",
    "compliance_rules": "SELECT",
    "issuer_health": "SELECT",
    "experiment_config": "SELECT",
    # Tenant-scoped.
    "events_raw": "SELECT, INSERT, UPDATE",
    "mandates": "SELECT, INSERT, UPDATE",
    "cycles": "SELECT, INSERT, UPDATE",
    "attempts": "SELECT, INSERT, UPDATE",
    "interventions": "SELECT, INSERT, UPDATE",
    # §28 Forgetting requires the app to be able to erase a profile.
    "customer_profiles": "SELECT, INSERT, UPDATE, DELETE",
    # Invariant 5 — append-only. This positive grant is what actually enforces
    # it; §36's `REVOKE UPDATE, DELETE ... FROM prayas_app` is a no-op, because
    # the privilege was never granted and the role did not previously exist.
    "decisions": "SELECT, INSERT",
    "scheduled_actions": "SELECT, INSERT, UPDATE, DELETE",
    "outbox": "SELECT, INSERT, UPDATE, DELETE",
}


def upgrade() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};")

    # Each statement is issued separately: asyncpg prepares every statement, and
    # a prepared statement may hold only one command.
    for table in STANDARD_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table}"
            f" USING (tenant_id = {TENANT_EXPR})"
            f" WITH CHECK (tenant_id = {TENANT_EXPR});"
        )

    # ADR-012: platform-scoped rows are readable by every tenant, because §33
    # randomises at customer level and a customer may span merchants. Writes are
    # still confined to the caller's own tenant.
    op.execute("ALTER TABLE experiment_config ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE experiment_config FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {POLICY} ON experiment_config"
        f" USING (tenant_id IS NULL OR tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )

    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE};")

    # outbox.outbox_id is BIGSERIAL; INSERT needs the sequence.
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE outbox_outbox_id_seq TO {APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE USAGE, SELECT ON SEQUENCE outbox_outbox_id_seq FROM {APP_ROLE};")

    for table in GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE};")

    for table in (*STANDARD_TENANT_TABLES, "experiment_config"):
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE};")
