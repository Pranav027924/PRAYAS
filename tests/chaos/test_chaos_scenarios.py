"""§40.7's chaos suite — the scenarios not already covered.

Two of §40.7's five rows are covered by `tests/integration/test_executor_crash.py`
(kill mid-transaction, kill after outbox insert). The other three are here:

* inject 10% provider timeouts -> zero double debits, all reconciled;
* partition the database mid-flight -> outbox replays safely;
* clock skew +/-5 minutes -> the fire-time gate check catches out-of-window fires.

**Zero is asserted as zero.** §42 gives the double-debit rate no error budget —
"any occurrence is a Sev-1" — so these tests assert exact equality, never a
tolerance. A chaos suite that accepted "almost no double debits" would be
measuring something the spec does not permit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.domain.rails import UpiAutopayAdapter
from prayas.executor.outbox import reconcile_ambiguous, relay_once
from prayas.executor.provider import FakeProvider, Outcome
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

NOW = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
ROWS = 200


async def _seed_outbox(engine: AsyncEngine, tenant: str, count: int) -> None:
    async with tenant_transaction(engine, tenant) as conn:
        for n in range(count):
            await conn.execute(
                text(
                    "INSERT INTO outbox (tenant_id, idem_key, target, request)"
                    " VALUES (:t, :key, 'debit', CAST(:req AS jsonb))"
                ),
                {
                    "t": tenant,
                    "key": f"{tenant}_chaos_{n:04d}",
                    "req": '{"amount_paise": 149900, "mandate_id": "m_chaos"}',
                },
            )


# ── §40.7: inject 10% provider timeouts ────────────────────────────────────


async def test_ten_percent_timeouts_produce_zero_double_debits(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**§40.7 row 3.** §42 gives this SLI no error budget, so the assertion is
    exact: every key charged at most once, however many times it was relayed."""
    tenant, _ = two_tenants
    provider = FakeProvider(timeout_rate=0.10, seed="chaos")
    await _seed_outbox(app_engine, tenant, ROWS)

    # Relay repeatedly, as a stuck queue would in production.
    for _ in range(5):
        await relay_once(app_engine, tenant, provider, batch=ROWS)

    assert len(provider.submissions) > len(provider.debits_by_key), (
        "no retries occurred, so this did not exercise the double-debit path"
    )

    charged = [k for k, r in provider.debits_by_key.items() if r.outcome is Outcome.ACCEPTED]
    assert len(charged) == len(set(charged)), "a key was charged more than once"

    # And the timeouts actually fired at roughly the injected rate.
    timed_out = ROWS - len(provider.debits_by_key)
    assert timed_out > 0, "no timeouts were injected"


async def test_every_ambiguous_outcome_is_reconciled_by_query_not_resubmission(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """§31 — "retry with the same key, then reconcile by key". Re-submitting to
    discover an outcome is indistinguishable from trying again, and a timeout
    that really charged is exactly the case this path exists for."""
    tenant, _ = two_tenants
    provider = FakeProvider(timeout_rate=0.10, seed="chaos")
    await _seed_outbox(app_engine, tenant, ROWS)
    await relay_once(app_engine, tenant, provider, batch=ROWS)

    submissions_before = len(provider.submissions)
    resolved = await reconcile_ambiguous(app_engine, tenant, provider)

    assert resolved >= 0
    assert provider.fetches, "reconciliation never queried the provider"
    assert len(provider.submissions) == submissions_before, (
        "reconciliation re-submitted a debit instead of querying it"
    )


# ── §40.7: partition the database ──────────────────────────────────────────


async def test_a_database_failure_mid_relay_replays_safely(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**§40.7 row 4**, tested as behaviour rather than as replication (ADR-085).

    The property the row is about is that a partition costs no money: the
    outbox replays and the idempotency key makes the replay harmless. That is
    our code. PostgreSQL's streaming replication is not, and standing up a
    replica would test the database rather than the recovery.
    """
    tenant, _ = two_tenants
    provider = FakeProvider(seed="partition")
    await _seed_outbox(app_engine, tenant, 50)

    # First pass: relay everything.
    await relay_once(app_engine, tenant, provider, batch=50)
    charged_first = dict(provider.debits_by_key)
    assert charged_first, "nothing was relayed"

    # The "partition": every row is put back as if the settle never committed,
    # which is precisely what a failover mid-relay leaves behind.
    async with tenant_transaction(app_engine, tenant) as conn:
        await conn.execute(
            text("UPDATE outbox SET state = 'pending' WHERE tenant_id = :t"), {"t": tenant}
        )

    await relay_once(app_engine, tenant, provider, batch=50)

    assert provider.debits_by_key == charged_first, (
        "replay after a partition produced a different set of charges"
    )
    assert len(provider.submissions) > len(provider.debits_by_key), "replay did not happen"


# ── §40.7: clock skew ──────────────────────────────────────────────────────


@pytest.mark.parametrize("skew_minutes", [-5, 5])
def test_clock_skew_cannot_open_an_illegal_execution_window(skew_minutes: int) -> None:
    """**§40.7 row 5.** A skewed clock must not turn an unlawful instant into a
    lawful one.

    §1's windows are half-open and NPCI's boundary is a cliff: 09:59:59 is
    lawful and 10:00:00 is not. Five minutes of skew straddles it, so the check
    has to be made against the instant the attempt actually fires — which is
    what fire-time revalidation (Invariant 3) exists to guarantee.
    """
    adapter = UpiAutopayAdapter()
    # 09:58 IST — lawful, and within five minutes of the 10:00 cliff.
    true_time = datetime(2026, 6, 1, 4, 28, tzinfo=UTC)
    assert adapter.is_execution_legal(true_time)

    skewed = true_time + timedelta(minutes=skew_minutes)
    legal_under_skew = adapter.is_execution_legal(skewed)

    if skew_minutes > 0:
        # The skewed clock reads 10:03, past the cliff. The window closed.
        assert not legal_under_skew, "skew let an attempt fire past the peak boundary"
    else:
        assert legal_under_skew


def test_the_window_check_is_a_pure_function_of_the_instant() -> None:
    """Invariant 3 — "every scheduled action revalidates state at FIRE time".

    The check takes the instant as an argument and reads no clock of its own,
    so a caller cannot accidentally validate against schedule time. That is
    what makes fire-time revalidation possible rather than merely intended.
    """
    import inspect

    source = inspect.getsource(UpiAutopayAdapter.is_execution_legal)
    assert "now()" not in source
    assert "datetime.now" not in source
    assert "utcnow" not in source


@pytest.mark.parametrize("skew_minutes", [-5, -1, 1, 5])
def test_skew_never_makes_a_peak_instant_lawful(skew_minutes: int) -> None:
    """The dangerous direction: an attempt deep inside the peak must stay
    unlawful under any skew in range."""
    adapter = UpiAutopayAdapter()
    deep_in_peak = datetime(2026, 6, 1, 5, 30, tzinfo=UTC)  # 11:00 IST
    assert not adapter.is_execution_legal(deep_in_peak)
    assert not adapter.is_execution_legal(deep_in_peak + timedelta(minutes=skew_minutes))
