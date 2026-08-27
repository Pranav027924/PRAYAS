"""Money-safety criteria under concurrency and failure (Playbook Phase 6).

Three exit criteria live here: atomic budget decrement under concurrent
workers, zero double debits under 10% injected timeouts, and the adversarial
`failed -> captured` race.

§31 names four independent defences against a double debit — the unique index
on `idem_key`, the atomic `UPDATE ... WHERE attempts_used < attempt_budget`,
the provider's own idempotency, and the `CHECK` constraint. These tests attack
them together, and one of them attacks each in isolation so a passing suite
cannot be resting on a single one of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.executor.claiming import cancel_actions_for_cycle, claim_due_actions
from prayas.executor.firing import fire_action
from prayas.executor.idempotency import idem_key, jitter_seconds
from prayas.executor.outbox import reconcile_ambiguous, relay_once
from prayas.executor.provider import FakeProvider, Outcome
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_safe"
#: 09:00 IST, inside the NPCI pre-10:00 window. See test_executor_crash.
FIRE_AT = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)

_WIPE = (
    "TRUNCATE decisions, outbox, scheduled_actions, attempts, cycles,"
    " mandates, tenants RESTART IDENTITY CASCADE"
)


async def _seed(conn: object, n_cycles: int, *, budget: int = 4) -> list[str]:
    """One tenant, `n_cycles` unpaid cycles, each with one due action."""
    cycle_ids = []
    await conn.execute(text("INSERT INTO tenants (tenant_id, name) VALUES (:t,:t)"), {"t": TENANT})  # type: ignore[attr-defined]
    for i in range(n_cycles):
        mandate, cycle, action = f"mnd_{i}", f"cyc_{i}", f"act_{i}"
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO mandates (mandate_id,tenant_id,customer_id,rail,"
                "max_amount_paise,state,consent_ref,created_at,mcc)"
                " VALUES (:m,:t,:cu,'upi_autopay',5000000,'active','c1',:n,'5411')"
            ),
            {"m": mandate, "t": TENANT, "cu": f"cust_{i}", "n": FIRE_AT},
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO cycles (cycle_id,tenant_id,mandate_id,seq_no,amount_paise,"
                "due_at,deadline_at,attempt_budget,attempts_used,state,pdn_sent_at)"
                " VALUES (:c,:t,:m,1,49900,:n,:d,:b,0,'executing',:p)"
            ),
            {
                "c": cycle,
                "t": TENANT,
                "m": mandate,
                "n": FIRE_AT,
                "d": FIRE_AT + timedelta(days=20),
                "b": budget,
                "p": FIRE_AT - timedelta(hours=30),
            },
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
                "action_type,fire_at,state,payload)"
                " VALUES (:a,:t,:c,:m,'debit_attempt',:f,'pending','{}'::jsonb)"
            ),
            {
                "a": action,
                "t": TENANT,
                "c": cycle,
                "m": mandate,
                "f": FIRE_AT - timedelta(minutes=1),
            },
        )
        cycle_ids.append(cycle)
    return cycle_ids


@pytest.fixture
async def clean(owner_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
    yield owner_engine
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))


# ── Exit criterion: budget decrement atomic under concurrent workers ────────


async def test_budget_decrement_is_atomic_under_concurrent_workers(
    app_engine: AsyncEngine, clean: AsyncEngine
) -> None:
    """Ten workers race one cycle with a budget of four.

    Exactly four may consume budget; the rest must be refused. A read-then-write
    decrement would let all ten through — which is why §17.4 requires the
    rowcount of an atomic `UPDATE ... WHERE attempts_used < attempt_budget` be
    the check, never a prior SELECT.
    """
    budget, racers = 4, 10
    async with clean.begin() as conn:
        await _seed(conn, 1, budget=budget)
        # One action per racer, all due, all pointing at the same cycle.
        for i in range(1, racers):
            await conn.execute(
                text(
                    "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
                    "action_type,fire_at,state,payload)"
                    " VALUES (:a,:t,'cyc_0','mnd_0','debit_attempt',:f,'pending','{}'::jsonb)"
                ),
                {"a": f"race_{i}", "t": TENANT, "f": FIRE_AT - timedelta(minutes=1)},
            )

    async def worker() -> bool:
        async with tenant_transaction(app_engine, TENANT) as conn:
            claimed = await claim_due_actions(conn, TENANT, batch=1)
            if not claimed:
                return False
            outcome = await fire_action(conn, claimed[0], now=FIRE_AT)
            return outcome.fired

    results = await asyncio.gather(*(worker() for _ in range(racers)))

    async with tenant_transaction(app_engine, TENANT) as conn:
        used = await conn.scalar(text("SELECT attempts_used FROM cycles WHERE cycle_id = 'cyc_0'"))
        attempts = await conn.scalar(text("SELECT count(*) FROM attempts"))

    assert used == budget, f"attempts_used is {used}, expected exactly {budget}"
    assert sum(results) == budget, f"{sum(results)} workers fired, expected {budget}"
    assert attempts == budget, f"{attempts} attempt rows, expected {budget}"


async def test_the_check_constraint_makes_over_budget_a_database_error(
    app_engine: AsyncEngine, clean: AsyncEngine
) -> None:
    """§31's fourth defence, in isolation.

    "A fourth, the CHECK (attempts_used <= attempt_budget) constraint, makes
    budget violation a database error rather than a logic bug." Asserted
    directly, so the suite is not resting on the atomic UPDATE alone.
    """
    async with clean.begin() as conn:
        await _seed(conn, 1, budget=4)

    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await conn.execute(text("UPDATE cycles SET attempts_used = 5 WHERE cycle_id = 'cyc_0'"))
    assert "check constraint" in str(exc.value).lower()


async def test_the_unique_index_makes_a_repeated_key_a_database_error(
    app_engine: AsyncEngine, clean: AsyncEngine
) -> None:
    """§31's first defence, in isolation: `attempts.idem_key` is UNIQUE."""
    async with clean.begin() as conn:
        await _seed(conn, 1)

    key = idem_key("cyc_0", 1, "debit_attempt", 49900)
    insert = text(
        "INSERT INTO attempts (attempt_id,tenant_id,cycle_id,attempt_seq,idem_key,"
        "scheduled_for,state,decision_id)"
        " VALUES (:id,:t,'cyc_0',1,:k,:n,'fired','dec_x')"
    )
    async with tenant_transaction(app_engine, TENANT) as conn:
        await conn.execute(insert, {"id": "att_1", "t": TENANT, "k": key, "n": FIRE_AT})

    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await conn.execute(insert, {"id": "att_2", "t": TENANT, "k": key, "n": FIRE_AT})
    assert "unique" in str(exc.value).lower() or "duplicate" in str(exc.value).lower()


# ── Exit criterion: 10% injected timeouts, zero double debits ───────────────


async def test_injected_timeouts_produce_no_double_debits_and_all_reconcile(
    app_engine: AsyncEngine, clean: AsyncEngine
) -> None:
    """Exit criterion, at 10% injected provider timeouts.

    A timeout means the debit MAY have happened. The relay must never re-issue
    it as a fresh charge; the row stays ambiguous, the budget stays consumed,
    and reconciliation resolves it under the same key.
    """
    n = 40
    async with clean.begin() as conn:
        await _seed(conn, n)

    async with tenant_transaction(app_engine, TENANT) as conn:
        claimed = await claim_due_actions(conn, TENANT, batch=n)
    assert len(claimed) == n

    for action in claimed:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await fire_action(conn, action, now=FIRE_AT)

    provider = FakeProvider(timeout_rate=0.10, seed="p6")
    await relay_once(app_engine, TENANT, provider, batch=n)

    async with tenant_transaction(app_engine, TENANT) as conn:
        ambiguous = await conn.scalar(text("SELECT count(*) FROM outbox WHERE state = 'ambiguous'"))
        held = await conn.scalar(text("SELECT count(*) FROM attempts WHERE state = 'ambiguous'"))

    assert ambiguous > 0, "10% of 40 should have timed out; the injection did nothing"
    assert held == ambiguous, "an ambiguous outbox row left its attempt unmarked"

    # Every key submitted at most once per outcome, and no key debited twice.
    assert len(provider.submissions) == n
    assert provider.distinct_debits == n - ambiguous

    # Budget is held pessimistically while unresolved (§31).
    async with tenant_transaction(app_engine, TENANT) as conn:
        over = await conn.scalar(
            text("SELECT count(*) FROM cycles WHERE attempts_used > attempt_budget")
        )
        used = await conn.scalar(text("SELECT sum(attempts_used) FROM cycles"))
    assert over == 0
    assert used == n, "an ambiguous attempt released its budget slot"

    # Reconciliation resolves them, by key, without a second charge.
    resolved = await reconcile_ambiguous(app_engine, TENANT, provider)
    assert resolved == ambiguous, f"{ambiguous} ambiguous rows, {resolved} reconciled"

    async with tenant_transaction(app_engine, TENANT) as conn:
        still_ambiguous = await conn.scalar(
            text("SELECT count(*) FROM outbox WHERE state = 'ambiguous'")
        )
    assert still_ambiguous == 0

    # The decisive assertion: reconciliation queried, it never re-submitted.
    for key in provider.debits_by_key:
        assert provider.submissions.count(key) == 1, (
            f"key {key} was submitted {provider.submissions.count(key)} times; "
            "reconciliation must query by key, never re-charge"
        )
    assert len(provider.fetches) == ambiguous, "reconciliation queried the wrong set"
    assert provider.distinct_debits <= n, "more debits than cycles"


# ── Exit criterion: the adversarial test ───────────────────────────────────


async def test_late_capture_racing_a_firing_retry_never_double_debits(
    app_engine: AsyncEngine, clean: AsyncEngine
) -> None:
    """The adversarial case, 100 independent races.

    Phase 1's late-capture guard cancels scheduled actions when a cycle is
    paid. Here that cancellation runs *concurrently* with the retry firing.
    Whichever wins, the outcome must be safe: either the retry fires before the
    capture lands, or the cancellation wins and nothing fires. What must never
    happen is more than one debit against a cycle.

    Run as one test over 100 distinct cycles rather than 100 parametrised
    tests: repeating the fixture's TRUNCATE between iterations deadlocks
    against still-open pooled connections, which measures the harness rather
    than the executor.
    """
    iterations = 100
    async with clean.begin() as conn:
        await _seed(conn, iterations)

    async def capture(cycle: str, delay: float) -> None:
        """The late capture: mark paid, then cancel what is scheduled."""
        await asyncio.sleep(delay)
        async with tenant_transaction(app_engine, TENANT) as conn:
            await conn.execute(
                text(
                    "UPDATE cycles SET state = 'succeeded', recovered_paise = 49900"
                    " WHERE tenant_id = :t AND cycle_id = :c"
                ),
                {"t": TENANT, "c": cycle},
            )
            await cancel_actions_for_cycle(conn, TENANT, cycle)

    # Claim every action once, up front. The criterion is a late capture
    # concurrent with a *firing* retry, so the claim is not part of the race —
    # and claiming inside the loop would lease all remaining actions on the
    # first pass, leaving nothing to fire afterwards.
    async with tenant_transaction(app_engine, TENANT) as conn:
        claimed = {
            a.action_id: a
            for a in await claim_due_actions(conn, TENANT, batch=iterations, lease_seconds=600)
        }
    assert len(claimed) == iterations, f"claimed {len(claimed)} of {iterations}"

    async def fire_claimed(action_id: str, delay: float) -> bool:
        """Fire an already-claimed action, holding lock order: cycle first."""
        await asyncio.sleep(delay)
        async with tenant_transaction(app_engine, TENANT) as conn:
            outcome = await fire_action(conn, claimed[action_id], now=FIRE_AT)
        return outcome.fired

    fired_count = 0
    for i in range(iterations):
        # Alternate the head start so both orderings are genuinely exercised.
        # Relying on the scheduler to vary would let the test pass while only
        # ever testing one side of the race.
        fire_delay, capture_delay = (0.0, 0.002) if i % 2 else (0.002, 0.0)
        fired, _ = await asyncio.gather(
            fire_claimed(f"act_{i}", fire_delay), capture(f"cyc_{i}", capture_delay)
        )
        fired_count += bool(fired)

    async with tenant_transaction(app_engine, TENANT) as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT c.cycle_id, c.state, c.attempts_used,"
                    "       (SELECT count(*) FROM attempts a"
                    "          WHERE a.cycle_id = c.cycle_id) AS n_attempts"
                    "  FROM cycles c WHERE c.tenant_id = :t"
                ),
                {"t": TENANT},
            )
        )

    assert len(rows) == iterations
    for row in rows:
        assert row.state == "succeeded", f"{row.cycle_id}: capture lost the state"
        # The decisive assertion: never two debits, whichever side won.
        assert row.n_attempts <= 1, f"{row.cycle_id}: {row.n_attempts} attempts - double debit"
        assert row.attempts_used <= 1, f"{row.cycle_id}: budget consumed {row.attempts_used} times"
        assert row.n_attempts == row.attempts_used, (
            f"{row.cycle_id}: {row.n_attempts} attempt rows but "
            f"attempts_used={row.attempts_used} - budget and record disagree"
        )

    # Both outcomes must actually occur, or the race is not being exercised
    # and the zero-double-debit result would be trivially true.
    assert 0 < fired_count < iterations, (
        f"{fired_count}/{iterations} fired - the race never went both ways, "
        "so this is not testing a race"
    )


# ── Idempotency and jitter (§31, ADR-045) ──────────────────────────────────


def test_idem_key_matches_the_specified_derivation() -> None:
    """§31's formula, transcribed. Pinned so a refactor cannot drift it."""
    import hashlib

    raw = "cyc_1:2:debit_attempt:49900"
    expected = "prayas_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
    assert idem_key("cyc_1", 2, "debit_attempt", 49900) == expected


def test_amount_is_part_of_the_key() -> None:
    """§31: "so a mid-cycle amount change cannot silently reuse one"."""
    a = idem_key("cyc_1", 1, "debit_attempt", 49900)
    b = idem_key("cyc_1", 1, "debit_attempt", 59900)
    assert a != b, "an amount change reused the key — a different debit, same idempotency"


def test_the_key_is_stable_across_calls() -> None:
    """Deterministic, so a logical retry always produces the same key."""
    keys = {idem_key("cyc_1", 1, "debit_attempt", 49900) for _ in range(50)}
    assert len(keys) == 1


def test_jitter_is_deterministic_in_the_key_and_within_the_spread() -> None:
    """ADR-045 — the same attempt always lands at the same instant."""
    key = idem_key("cyc_1", 1, "debit_attempt", 49900)
    values = {jitter_seconds(key, spread_seconds=3600) for _ in range(50)}
    assert len(values) == 1
    assert 0 <= values.pop() < 3600


def test_jitter_spreads_across_different_attempts() -> None:
    """Determinism must not collapse into every attempt firing at once."""
    values = {
        jitter_seconds(idem_key(f"cyc_{i}", 1, "debit_attempt", 49900), spread_seconds=3600)
        for i in range(200)
    }
    assert len(values) > 150, f"only {len(values)} distinct offsets from 200 keys"


async def test_provider_is_idempotent_on_the_key() -> None:
    """The provider's half of §31's defence in depth."""
    provider = FakeProvider()
    first = await provider.submit_debit(
        idem_key="prayas_k", amount_paise=49900, mandate_id="m", request={}
    )
    second = await provider.submit_debit(
        idem_key="prayas_k", amount_paise=49900, mandate_id="m", request={}
    )
    assert first == second
    assert provider.distinct_debits == 1, "the same key produced two debits"
    assert len(provider.submissions) == 2, "both submissions should still be recorded"


async def test_reconciliation_reveals_a_timeout_that_actually_charged() -> None:
    """The case the ambiguous path exists for.

    A timeout that really did charge must be discovered by querying, and must
    never be re-submitted — that would be the double debit itself.
    """
    provider = FakeProvider(timeout_rate=1.0)
    charged_key = next(
        k for k in (f"prayas_k{i}" for i in range(200)) if provider._roll(f"truth:{k}") < 0.5
    )

    first = await provider.submit_debit(
        idem_key=charged_key, amount_paise=49900, mandate_id="m", request={}
    )
    assert first.outcome is Outcome.AMBIGUOUS
    assert provider.distinct_debits == 0, "an unknown outcome was counted as a debit"

    revealed = await provider.fetch_by_key(charged_key)
    assert revealed is not None and revealed.outcome is Outcome.ACCEPTED
    assert provider.distinct_debits == 1, "the real charge was not surfaced"
    assert provider.submissions.count(charged_key) == 1, "reconciliation re-submitted"


async def test_ambiguous_outcome_is_not_recorded_as_a_debit() -> None:
    """An ambiguous result must not be assumed either way.

    Recording it as a debit would hide a real charge; recording it as no-debit
    would invite a second one. It stays unknown until reconciliation says.
    """
    provider = FakeProvider(timeout_rate=1.0)
    response = await provider.submit_debit(
        idem_key="prayas_t", amount_paise=49900, mandate_id="m", request={}
    )
    assert response.outcome is Outcome.AMBIGUOUS
    assert provider.distinct_debits == 0
    assert "prayas_t" not in provider.debits_by_key
