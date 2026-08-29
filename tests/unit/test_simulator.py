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
    FRAUD_HOLD,
    GIG_IRREGULAR,
    ISSUER_DEGRADED,
    LIMIT_BREACH,
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


@pytest.mark.parametrize("cause", [NO_FUNDS, ISSUER_DEGRADED, FRAUD_HOLD, LIMIT_BREACH])
def test_each_cause_masks_at_its_own_declared_rate(cause: str) -> None:
    """ADR-065 — every cause that can hide behind 05 does so at its own rate.

    §20 makes 05 a *mixture*; the per-cause weight is what sets each
    component's share, so each has to be faithful independently.
    """
    config = SimConfig(mask_05_rate=0.5, outage_lambda=0.30)
    # `fraud_hold` is deliberately rare — well under 1% of cycles — so the
    # sample is sized for it rather than for `no_funds`, and the tolerance is
    # set at roughly 3.5 binomial standard errors at that count. Tighter would
    # flake rather than measure.
    cycles = generate(config, seed=44, tenant_id=TENANT, cycles=20_000)

    of_cause = [c for c in cycles if c.truth.true_cause == cause]
    assert len(of_cause) > 150, f"too few {cause} cycles to measure masking"

    masked = sum(1 for c in of_cause if c.truth.masked_as_05) / len(of_cause)
    assert masked == pytest.approx(config.mask_probability(cause), abs=0.08)


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_mask_rate_extremes_are_honoured(rate: float) -> None:
    """Both ends of the boundary, per §40.2's discipline.

    At 0.0 nothing hides, whatever a cause's weight. At 1.0 each cause masks at
    its full declared weight — which is not 1.0 for all of them, because a
    `no_funds` decline usually does arrive as the plain 51 that §20 describes.
    """
    config = SimConfig(mask_05_rate=rate, outage_lambda=0.30)
    cycles = generate(config, seed=45, tenant_id=TENANT, cycles=20_000)

    if rate == 0.0:
        assert not [c for c in cycles if c.truth.masked_as_05]
        return

    for cause in (NO_FUNDS, ISSUER_DEGRADED, FRAUD_HOLD, LIMIT_BREACH):
        of_cause = [c for c in cycles if c.truth.true_cause == cause]
        assert of_cause, cause
        masked = sum(1 for c in of_cause if c.truth.masked_as_05) / len(of_cause)
        assert masked == pytest.approx(config.mask_probability(cause), abs=0.08), cause


def test_code_05_is_a_genuine_mixture_about_half_no_funds() -> None:
    """ADR-065's evidence, and the precondition for Phase 11's EM item.

    §20: 05 is "the least informative signal in payments, with roughly half
    being insufficient funds in disguise". Before ADR-065 the bucket was 95%
    `no_funds` — a mixture with one component, from which EM can learn nothing
    and against which a confusion matrix proves nothing.
    """
    cycles = generate(SimConfig(), seed=99, tenant_id=TENANT, cycles=12000)

    seen = Counter(
        c.truth.true_cause
        for c in cycles
        for e in c.observables
        if _entity(e).get("error_code") == DO_NOT_HONOR
        or _entity(e).get("decline_code") == DO_NOT_HONOR
    )
    total = sum(seen.values())
    assert total > 500, "too few 05 declines to characterise the bucket"

    assert seen[NO_FUNDS] / total == pytest.approx(0.5, abs=0.10), (
        f"§20 wants roughly half insufficient funds; got {seen[NO_FUNDS] / total:.1%}"
    )
    # Every other component must be present, or EM has nothing to find.
    # `issuer_degraded` is held to a lower bar on purpose: ADR-067 gives outages
    # their real duration in minutes rather than rounding them up to whole days,
    # so an issuer is down rarely — but when it is, it fails everyone at once,
    # which is what makes it detectable at all (§20's `peer_success_rate`).
    for cause in (FRAUD_HOLD, LIMIT_BREACH):
        assert seen[cause] / total > 0.08, f"{cause} is too rare in 05 to be recoverable"
    assert seen[ISSUER_DEGRADED] / total > 0.015, "issuer_degraded absent from 05 entirely"


def _entity(event: dict[str, Any]) -> dict[str, Any]:
    entity = event.get("body", {}).get("payload", {}).get("payment", {}).get("entity", {})
    assert isinstance(entity, dict)
    return entity


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
