"""`now_for` (Demo spec Phase 6). Pure — no database, no clock guard here.

The guard itself (`advance`/`reset`/`jump_to_next_action` refusing a tenant
without `config.demo_tenant: true`) needs a real connection and lives in
`tests/integration/test_demo_clock.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prayas.demo.clock import now_for

#: Generous enough that a slow CI runner cannot flake this, tight enough that
#: a broken offset (an hour, a day) cannot pass by accident.
TOLERANCE = timedelta(seconds=5)


def test_a_zero_offset_gets_the_wall_clock() -> None:
    before = datetime.now(UTC)
    result = now_for(0)
    after = datetime.now(UTC)
    assert before - TOLERANCE <= result <= after + TOLERANCE


def test_a_positive_offset_moves_the_clock_forward() -> None:
    result = now_for(3600)
    expected = datetime.now(UTC) + timedelta(hours=1)
    assert abs((result - expected).total_seconds()) < TOLERANCE.total_seconds()


def test_a_negative_offset_moves_the_clock_backward() -> None:
    result = now_for(-1800)
    expected = datetime.now(UTC) - timedelta(minutes=30)
    assert abs((result - expected).total_seconds()) < TOLERANCE.total_seconds()


def test_a_large_offset_moves_the_clock_by_days() -> None:
    result = now_for(2 * 86400)
    expected = datetime.now(UTC) + timedelta(days=2)
    assert abs((result - expected).total_seconds()) < TOLERANCE.total_seconds()
