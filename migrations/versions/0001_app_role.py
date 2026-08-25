"""Create the non-owner application role.

ADR-004: the application connects as a role that owns nothing, so row-level
security applies to it unconditionally. Migrations run as the owner. This is
what makes the Phase 0 isolation test meaningful rather than vacuous — an
owner-connected application bypasses every policy.

Revision ID: 0001_app_role
Revises:
Create Date: 2026-08-24

"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_app_role"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"


def upgrade() -> None:
    password = os.environ.get("PRAYAS_APP_DB_PASSWORD", "")

    # The password reaches SQL through a bind parameter into a transaction-local
    # setting, then through format(%L) which quotes it as a literal. CREATE ROLE
    # is a utility statement and cannot take a bind parameter directly, so the
    # alternative would be interpolating a secret into a SQL string.
    op.get_bind().execute(
        sa.text("SELECT set_config('prayas.app_password', :pw, true)").bindparams(pw=password)
    )

    op.execute(
        f"""
        DO $$
        DECLARE
            pw text := current_setting('prayas.app_password', true);
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                -- Already provisioned, most likely out-of-band by infrastructure.
                -- Never overwrite an existing credential from a migration.
                RAISE NOTICE 'role {APP_ROLE} already exists; leaving it untouched';
            ELSIF pw IS NULL OR pw = '' THEN
                RAISE EXCEPTION
                    'PRAYAS_APP_DB_PASSWORD must be set to create role {APP_ROLE}';
            ELSE
                EXECUTE format('CREATE ROLE {APP_ROLE} LOGIN PASSWORD %L', pw);
            END IF;

            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO {APP_ROLE}', current_database()
            );
        END
        $$;
        """
    )

    # Do not leave the secret readable for the rest of the transaction.
    op.execute("SELECT set_config('prayas.app_password', '', true)")


def downgrade() -> None:
    # DROP OWNED clears privileges granted to the role; the role owns no objects
    # by construction, which is the entire point of ADR-004.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'DROP OWNED BY {APP_ROLE}';
                EXECUTE 'DROP ROLE {APP_ROLE}';
            END IF;
        END
        $$;
        """
    )
