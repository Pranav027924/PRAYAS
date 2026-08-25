"""Per-tenant webhook secret references, and the lookup path used before authentication.

ADR-014 and the SECURITY DEFINER decision that followed it.

§36 provides no webhook-secret storage anywhere. This table holds **references
only** — the secret material itself is resolved from the environment/secrets
manager at verification time, because Phase 0 requires "secrets via environment
injection from a manager, never files" and Invariant 7 treats credentials as
never-stored.

The lookup problem: §18 requires `app.tenant_id` be set from a verified token,
never a request parameter. A webhook carries no token — the HMAC *is* the
authentication — so the secret refs must be readable before any tenant context
exists. `prayas_active_webhook_secret_refs()` is that one narrow, named,
auditable path; tenant context is bound only after the signature verifies.

Why a second policy rather than dropping FORCE: FORCE ROW LEVEL SECURITY binds
the table owner too, so a SECURITY DEFINER function running as owner would
return zero rows. A role-scoped permissive policy restores the owner's read
without weakening `prayas_app`, which still matches only `tenant_isolation`.
Permissive policies OR together *within a role*, and `TO prayas_owner` keeps the
broad one away from the application role entirely.

Revision ID: 0005_webhook_secrets
Revises: 0004_tenants_rls
Create Date: 2026-08-25

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_webhook_secrets"
down_revision: str | None = "0004_tenants_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
OWNER_ROLE = "prayas_owner"
TENANT_EXPR = "current_setting('app.tenant_id', true)"
LOOKUP_FN = "prayas_active_webhook_secret_refs"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE webhook_secrets (
            tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),
            secret_ref   TEXT NOT NULL,   -- key in the secrets manager, NEVER the secret
            active_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
            active_until TIMESTAMPTZ,     -- NULL = open-ended; overlap gives rotation windows
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, secret_ref),
            CHECK (active_until IS NULL OR active_until > active_from)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_webhook_secrets_active ON webhook_secrets (tenant_id, active_from DESC)"
        " WHERE active_until IS NULL;"
    )

    op.execute("ALTER TABLE webhook_secrets ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE webhook_secrets FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY tenant_isolation ON webhook_secrets"
        f" USING (tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )
    # Scoped to the owner only. prayas_app never matches this policy.
    op.execute(
        f"CREATE POLICY owner_secret_lookup ON webhook_secrets"
        f" FOR SELECT TO {OWNER_ROLE} USING (true);"
    )

    # SECURITY DEFINER: runs as the owner, so it sees rows through the policy
    # above. `SET search_path` is mandatory here — without it a caller could
    # shadow `webhook_secrets` with a temp table and redirect the lookup.
    op.execute(
        f"""
        CREATE FUNCTION {LOOKUP_FN}(p_tenant_id TEXT)
        RETURNS TABLE (secret_ref TEXT)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        STABLE
        AS $$
            SELECT ws.secret_ref
            FROM webhook_secrets ws
            WHERE ws.tenant_id = p_tenant_id
              AND ws.active_from <= now()
              AND (ws.active_until IS NULL OR ws.active_until > now())
            ORDER BY ws.active_from DESC
        $$;
        """
    )

    # Default EXECUTE is granted to PUBLIC; revoke before granting narrowly.
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(TEXT) FROM PUBLIC;")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LOOKUP_FN}(TEXT) TO {APP_ROLE};")
    op.execute(f"GRANT SELECT ON webhook_secrets TO {APP_ROLE};")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FN}(TEXT) FROM {APP_ROLE};")
    op.execute(f"DROP FUNCTION IF EXISTS {LOOKUP_FN}(TEXT);")
    op.execute(f"REVOKE ALL ON webhook_secrets FROM {APP_ROLE};")
    op.execute("DROP POLICY IF EXISTS owner_secret_lookup ON webhook_secrets;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON webhook_secrets;")
    op.execute("DROP TABLE IF EXISTS webhook_secrets CASCADE;")
