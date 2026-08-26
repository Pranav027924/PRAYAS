"""Simulator: determinism and distribution fidelity (Master Spec §38).

Covers two of Phase 3's four exit criteria — byte-identical output for a seed,
and generated distributions matching configured parameters. Both run without a
database, so they stay in the unit tier where mutmut can reach them (ADR-027).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from typing import Any

import pytest

from prayas.sim.config import (
    CHRONICALLY_DRY,
    DO_NOT_HONOR,
    GIG_IRREGULAR,
    ISSUER_DEGRADED,
    NO_FUNDS,
    SALARIED_1ST,
    ConfigError,
    SimConfig,
)
from prayas.sim.generate import SimulatedCycle, generate

TENANT = "t_sim"


def _digest(cycles: list[SimulatedCycle]) -> str:
    """A stable serialisation of a whole run, for byte-comparison."""
    payload = [
        {
            "cycle_id": c.cycle_id,
            "mandate_id": c.mandate_id,
            "rail": c.rail,
            "amount_paise": c.amount_paise,
            "due_at": c.due_at.isoformat(),
            "events": [(e["event_id"], e["body"]["event"]) for e in c.observables],
            "truth": [
                c.truth.true_cause,
                c.truth.issuer_state,
                c.truth.payday_archetype,
                c.truth.masked_as_05,
            ],
        }
        for c in cycles
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── exit criterion: same seed produces byte-identical output ────────────────


def test_same_seed_is_byte_identical() -> None:
    config = SimConfig()
    a = generate(config, seed=12345, tenant_id=TENANT, cycles=300)
    b = generate(config, seed=12345, tenant_id=TENANT, cycles=300)

    assert _digest(a) == _digest(b)


def test_different_seeds_diverge() -> None:
    """Otherwise byte-identity would be trivially satisfied by ignoring the seed."""
    config = SimConfig()
    a = generate(config, seed=1, tenant_id=TENANT, cycles=200)
    b = generate(config, seed=2, tenant_id=TENANT, cycles=200)

    assert _digest(a) != _digest(b)


def test_determinism_survives_a_process_boundary() -> None:
    """In-process repetition can hide dependence on global RNG state (ADR-029).

    Two runs in one interpreter share module state; two interpreters do not.
    """
    script = (
        "import json;"
        "from prayas.sim.config import SimConfig;"
        "from prayas.sim.generate import SimulatedCycle, generate;"
        "cs = generate(SimConfig(), seed=999, tenant_id='t_sim', cycles=100);"
        "print(json.dumps([[c.cycle_id, c.truth.true_cause, c.amount_paise] for c in cs]))"
    )
    first = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    second = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert first.stdout == second.stdout
    assert first.stdout.strip(), "the subprocess produced nothing"


def test_a_short_run_is_a_prefix_of_a_long_one() -> None:
    """Spawned per-cycle streams mean cycle N does not depend on the run length.

    Without this, a 100-cycle debugging run would not reproduce the first 100
    cycles of the 10,000-cycle run it was meant to explain.
    """
    config = SimConfig()
    short = generate(config, seed=7, tenant_id=TENANT, cycles=50)
    long = generate(config, seed=7, tenant_id=TENANT, cycles=500)

    assert _digest(short) == _digest(long[:50])


# ── exit criterion: distributions match configured parameters ───────────────


def test_payday_mix_is_reproduced_within_tolerance() -> None:
    config = SimConfig(payday_mix={SALARIED_1ST: 0.5, GIG_IRREGULAR: 0.3, CHRONICALLY_DRY: 0.2})
    cycles = generate(config, seed=42, tenant_id=TENANT, cycles=4000)
    observed = Counter(c.truth.payday_archetype for c in cycles)

    for archetype, expected in config.payday_mix.items():
        actual = observed[archetype] / len(cycles)
        assert actual == pytest.approx(expected, abs=0.03), (
            f"{archetype}: configured {expected}, generated {actual:.3f}"
        )


def test_rail_mix_is_reproduced_within_tolerance() -> None:
    config = SimConfig(rail_mix={"upi_autopay": 0.6, "card_emandate": 0.3, "enach": 0.1})
    cycles = generate(config, seed=43, tenant_id=TENANT, cycles=4000)
    observed = Counter(c.rail for c in cycles)

    for rail, expected in config.rail_mix.items():
        assert observed[rail] / len(cycles) == pytest.approx(expected, abs=0.03)


def test_mask_05_rate_is_reproduced_within_tolerance() -> None:
    """§20 — roughly half of no_funds surfacing as 05 is what makes cause
    inference necessary, so this parameter has to be faithful."""
    config = SimConfig(mask_05_rate=0.5)
    cycles = generate(config, seed=44, tenant_id=TENANT, cycles=4000)

    no_funds = [c for c in cycles if c.truth.true_cause == NO_FUNDS]
    assert len(no_funds) > 200, "too few no_funds cycles to measure masking"

    masked = sum(1 for c in no_funds if c.truth.masked_as_05) / len(no_funds)
    assert masked == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_mask_rate_extremes_are_honoured(rate: float) -> None:
    """Both ends of the boundary, per §40.2's discipline."""
    cycles = generate(SimConfig(mask_05_rate=rate), seed=45, tenant_id=TENANT, cycles=1500)
    no_funds = [c for c in cycles if c.truth.true_cause == NO_FUNDS]
    assert no_funds

    masked = sum(1 for c in no_funds if c.truth.masked_as_05)
    assert masked == (len(no_funds) if rate == 1.0 else 0)


def test_more_outages_produce_more_issuer_degraded_cycles() -> None:
    """§38's robustness perturbation: outage_lambda x 3."""
    quiet = generate(SimConfig(outage_lambda=0.01), seed=46, tenant_id=TENANT, cycles=2000)
    stormy = generate(SimConfig(outage_lambda=0.30), seed=46, tenant_id=TENANT, cycles=2000)

    quiet_rate = sum(1 for c in quiet if c.truth.issuer_state == ISSUER_DEGRADED)
    stormy_rate = sum(1 for c in stormy if c.truth.issuer_state == ISSUER_DEGRADED)

    assert stormy_rate > quiet_rate * 2


def test_chronically_dry_customers_mostly_fail_for_no_funds() -> None:
    """The population §1 says no retry schedule can help, and where stopping wins."""
    config = SimConfig(payday_mix={CHRONICALLY_DRY: 1.0}, outage_lambda=0.0)
    cycles = generate(config, seed=47, tenant_id=TENANT, cycles=1000)

    no_funds = sum(1 for c in cycles if c.truth.true_cause == NO_FUNDS)
    assert no_funds / len(cycles) > 0.6


def test_salaried_first_customers_fail_less_than_chronically_dry() -> None:
    dry = generate(
        SimConfig(payday_mix={CHRONICALLY_DRY: 1.0}, outage_lambda=0.0),
        seed=48,
        tenant_id=TENANT,
        cycles=1000,
    )
    salaried = generate(
        SimConfig(payday_mix={SALARIED_1ST: 1.0}, outage_lambda=0.0),
        seed=48,
        tenant_id=TENANT,
        cycles=1000,
    )

    dry_failures = sum(1 for c in dry if c.truth.true_cause != "none")
    salaried_failures = sum(1 for c in salaried if c.truth.true_cause != "none")
    assert salaried_failures < dry_failures


# ── ground truth is separated from observables (ADR-030) ───────────────────


def test_observables_never_carry_the_true_cause() -> None:
    """Leakage check at the source, before the database grant even matters."""
    cycles = generate(SimConfig(), seed=49, tenant_id=TENANT, cycles=300)

    for cycle in cycles:
        blob = json.dumps([e["body"] for e in cycle.observables])
        assert "true_cause" not in blob
        assert "payday_archetype" not in blob
        assert cycle.truth.payday_archetype not in blob
        if cycle.truth.true_cause not in ("none",):
            assert f'"{cycle.truth.true_cause}"' not in blob


def test_masked_failures_emit_05_not_the_real_code() -> None:
    """The whole point of §20: the observable code hides the cause."""
    cycles = generate(SimConfig(mask_05_rate=1.0), seed=50, tenant_id=TENANT, cycles=800)
    masked = [c for c in cycles if c.truth.masked_as_05]
    assert masked

    for cycle in masked:
        failure = [e for e in cycle.observables if e["body"]["event"] == "payment.failed"]
        assert failure
        code = failure[0]["body"]["payload"]["payment"]["entity"]["error_code"]
        assert code == DO_NOT_HONOR


def test_unmasked_no_funds_emits_51() -> None:
    cycles = generate(SimConfig(mask_05_rate=0.0), seed=51, tenant_id=TENANT, cycles=800)
    unmasked = [c for c in cycles if c.truth.true_cause == NO_FUNDS]
    assert unmasked

    for cycle in unmasked:
        failure = [e for e in cycle.observables if e["body"]["event"] == "payment.failed"]
        code = failure[0]["body"]["payload"]["payment"]["entity"]["error_code"]
        assert code == "51"


# ── config validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("mix does not sum to 1", {"payday_mix": {SALARIED_1ST: 0.5}}),
        ("unknown archetype", {"payday_mix": {"astrologer": 1.0}}),
        ("negative weight", {"payday_mix": {SALARIED_1ST: 1.5, GIG_IRREGULAR: -0.5}}),
        ("mask rate above 1", {"mask_05_rate": 1.5}),
        ("mask rate below 0", {"mask_05_rate": -0.1}),
        ("negative outage lambda", {"outage_lambda": -1.0}),
        ("zero sigma", {"outage_duration": (4.0, 0.0)}),
        ("negative revocation beta", {"revocation_beta": -0.1}),
        ("fatigue decay above 1", {"fatigue_decay": 1.5}),
        ("zero horizon", {"horizon_days": 0}),
        ("inverted amount range", {"amount_paise_range": (500, 100)}),
        ("zero amount floor", {"amount_paise_range": (0, 100)}),
    ],
)
def test_invalid_config_is_rejected(label: str, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        SimConfig(**kwargs)


def test_the_default_config_is_valid() -> None:
    config = SimConfig()
    assert sum(config.payday_mix.values()) == pytest.approx(1.0)
    assert sum(config.rail_mix.values()) == pytest.approx(1.0)


def test_config_is_frozen() -> None:
    """A config that drifts mid-run would invalidate the run's own labels."""
    config = SimConfig()
    with pytest.raises(AttributeError):
        config.mask_05_rate = 0.9  # type: ignore[misc]
