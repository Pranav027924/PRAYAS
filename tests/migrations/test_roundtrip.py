"""Migrations run forward and backward cleanly (Phase 0 exit criterion).

"Cleanly" is asserted as *schema equality*, not merely as "the commands exited
zero". A downgrade that silently leaves a policy, a grant or a constraint behind
still exits zero, and would leave the next upgrade building on a schema nobody
described.

This test destroys and rebuilds the schema, so it is serialised away from the
isolation suite by leaving the database at ``head`` when it finishes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from prayas.db.tenancy import system_transaction
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

REPO_ROOT = Path(__file__).resolve().parents[2]

_COLUMNS = text(
    """
    SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND a.attnum > 0 AND NOT a.attisdropped
      AND c.relname <> 'alembic_version'
    """
)

_CONSTRAINTS = text(
    """
    SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = rel.relnamespace
    WHERE n.nspname = 'public' AND rel.relname <> 'alembic_version'
    """
)

_INDEXES = text(
    "SELECT tablename, indexname, indexdef FROM pg_indexes "
    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
)

_POLICIES = text(
    "SELECT tablename, policyname, coalesce(qual, ''), coalesce(with_check, '') "
    "FROM pg_policies WHERE schemaname = 'public'"
)

# alembic_version is Alembic's own bookkeeping and correctly survives
# `downgrade base` — it has to, in order to record that we are at base.
_RLS_FLAGS = text(
    """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND c.relname <> 'alembic_version'
    """
)

_GRANTS = text(
    "SELECT table_name, grantee, privilege_type FROM information_schema.table_privileges "
    "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
)


def _alembic(*args: str) -> None:
    """Invoke Alembic exactly as an operator would."""
    result = subprocess.run(
        ["alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(args)} failed (exit {result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


async def _snapshot(conn: AsyncConnection) -> list[str]:
    lines: list[str] = []

    for row in await conn.execute(_COLUMNS):
        lines.append(f"col {row[0]}.{row[1]} {row[2]} notnull={row[3]}")
    for row in await conn.execute(_CONSTRAINTS):
        lines.append(f"con {row[0]}.{row[1]} {row[2]}")
    for row in await conn.execute(_INDEXES):
        lines.append(f"idx {row[0]}.{row[1]} {row[2]}")
    for row in await conn.execute(_POLICIES):
        lines.append(f"pol {row[0]}.{row[1]} using={row[2]} check={row[3]}")
    for row in await conn.execute(_RLS_FLAGS):
        lines.append(f"rls {row[0]} enabled={row[1]} forced={row[2]}")
    for row in await conn.execute(_GRANTS):
        lines.append(f"grant {row[0]} {row[1]} {row[2]}")

    return sorted(lines)


async def test_downgrade_then_upgrade_restores_an_identical_schema(
    owner_engine: AsyncEngine,
) -> None:
    _alembic("upgrade", "head")
    async with system_transaction(owner_engine) as conn:
        before = await _snapshot(conn)

    assert before, "snapshot is empty — migrations did not create anything"

    _alembic("downgrade", "base")
    async with system_transaction(owner_engine) as conn:
        emptied = await _snapshot(conn)

    assert not emptied, f"downgrade left {len(emptied)} schema objects behind:\n" + "\n".join(
        emptied[:20]
    )

    _alembic("upgrade", "head")
    async with system_transaction(owner_engine) as conn:
        after = await _snapshot(conn)

    if before != after:
        only_before = [line for line in before if line not in after]
        only_after = [line for line in after if line not in before]
        pytest.fail(
            "schema differs after downgrade/upgrade roundtrip\n"
            f"lost ({len(only_before)}): {only_before[:10]}\n"
            f"gained ({len(only_after)}): {only_after[:10]}"
        )


async def test_ledger_partitions_exist(owner_engine: AsyncEngine) -> None:
    """ADR-006: §36 defines no partitions, so the first INSERT would otherwise fail."""
    async with system_transaction(owner_engine) as conn:
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_inherits i "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'decisions'"
            )
        )
        has_default = await conn.scalar(
            text("SELECT count(*) FROM pg_class WHERE relname = 'decisions_default'")
        )

    assert count and count >= 24, f"expected monthly partitions, found {count}"
    assert has_default == 1, "no DEFAULT partition — an audit write could be lost"
