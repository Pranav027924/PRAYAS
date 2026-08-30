"""The k-anonymity floor at the database (Master Spec §27, §36; ADR-079).

ADR-079 claims the floor is enforced twice: `CHECK (n_obs >= 50)` in the DDL,
and a pre-filter in `prayas.memory.aggregate`. The application-level filter is
tested in `test_memory_aggregate_parity.py`. This asserts the other half —
that the database would refuse a thin cell **even if this module had a bug** —
because a "belt and braces" claim with only one of them tested is a claim about
the belt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction
from prayas.memory.aggregate import (
    MIN_COHORT,
    AggregateError,
    SegmentKey,
    SegmentPrior,
    publish,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

KEY = SegmentKey(mcc="5411", ticket_band="small", rail="upi_autopay", day_of_month=3, hour_band=1)


def _prior(n_obs: int) -> SegmentPrior:
    return SegmentPrior(
        key=KEY, hazard=0.42, n_obs=n_obs, contributors=3, updated_at=datetime.now(UTC)
    )


async def test_a_dense_cell_publishes(owner_engine: AsyncEngine) -> None:
    async with system_transaction(owner_engine) as conn:
        await conn.execute(text("DELETE FROM segment_priors WHERE mcc = :m"), {"m": KEY.mcc})
        assert await publish(conn, [_prior(MIN_COHORT)]) == 1

        stored = await conn.scalar(
            text("SELECT n_obs FROM segment_priors WHERE mcc = :m AND day_of_month = :d"),
            {"m": KEY.mcc, "d": KEY.day_of_month},
        )
        assert stored == MIN_COHORT


async def test_the_module_refuses_a_thin_cell_before_the_database_sees_it(
    owner_engine: AsyncEngine,
) -> None:
    """The legible failure: a named error rather than an integrity violation
    surfacing three layers up."""
    async with system_transaction(owner_engine) as conn:
        with pytest.raises(AggregateError, match="minimum cohort"):
            await publish(conn, [_prior(MIN_COHORT - 1)])


async def test_the_database_refuses_a_thin_cell_even_if_the_module_does_not(
    owner_engine: AsyncEngine,
) -> None:
    """**The other half of the belt and braces.** This bypasses `publish`
    entirely, standing in for a bug in it. §27's floor has to survive that."""
    async with system_transaction(owner_engine) as conn:
        with pytest.raises(Exception, match=r"check constraint|violates"):
            await conn.execute(
                text(
                    "INSERT INTO segment_priors"
                    " (mcc, ticket_band, rail, day_of_month, hour_band, hazard, n_obs, updated_at)"
                    " VALUES ('9999', 'small', 'enach', 9, 0, 0.5, :n, now())"
                ),
                {"n": MIN_COHORT - 1},
            )


async def test_the_app_role_cannot_write_a_cross_tenant_aggregate(
    app_engine: AsyncEngine,
) -> None:
    """`segment_priors` is read by every tenant. A tenant-bound session must not
    be able to write something all the others will consume."""
    from prayas.db.tenancy import tenant_transaction

    async with tenant_transaction(app_engine, "t_alpha") as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(
                text(
                    "INSERT INTO segment_priors"
                    " (mcc, ticket_band, rail, day_of_month, hour_band, hazard, n_obs, updated_at)"
                    " VALUES ('8888', 'small', 'enach', 9, 0, 0.5, 100, now())"
                )
            )
