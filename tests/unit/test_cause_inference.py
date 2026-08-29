"""Cause inference V0, against simulator ground truth (Master Spec §20).

§20: "The simulator emits ground-truth causes, so cause inference is reported as
an actual confusion matrix — a claim impossible to make on real data, and the
strongest honest use of synthetic data in this project."

So the tests here do not merely check the heuristic's internals; they score it
against labels, which is the only honest way to say whether it works.
"""

from __future__ import annotations

from collections import Counter

import pytest

from prayas.inference.cause import (
    DETERMINISTIC,
    CauseContext,
    infer,
    is_terminal,
)
from prayas.sim.config import (
    CREDENTIAL_DEAD,
    ISSUER_DEGRADED,
    LIMIT_BREACH,
    MANDATE_DEAD,
    NO_FUNDS,
    SimConfig,
)
from prayas.sim.generate import SimulatedCycle, generate

TENANT = "t_alpha"


# ── the deterministic layer: "no model needed, no model wanted" ────────────


@pytest.mark.parametrize(("code", "cause"), sorted(DETERMINISTIC.items()))
def test_hard_codes_resolve_with_certainty(code: str, cause: str) -> None:
    """§20 — codes 41, 43, 14, 54 and revocation are terminal, not probabilistic."""
    result = infer(code)

    assert result.deterministic is True
    assert result.most_likely == cause
    assert result.confidence == 1.0
    assert result.posterior == {cause: 1.0}


def test_code_51_is_near_deterministic_no_funds() -> None:
    """§20 — the issuer said "insufficient funds" plainly. Near, not absolute."""
    result = infer("51")

    assert result.most_likely == NO_FUNDS
    assert result.confidence > 0.9
    assert result.deterministic is False, "51 is near-deterministic, not certain"
    assert sum(result.posterior.values()) == pytest.approx(1.0)


def test_terminal_causes_are_identified() -> None:
    """§20 — these are not recoverable; the right action is to stop."""
    assert is_terminal(MANDATE_DEAD)
    assert is_terminal(CREDENTIAL_DEAD)
    assert not is_terminal(NO_FUNDS)
    assert not is_terminal(ISSUER_DEGRADED)


def test_every_posterior_is_a_distribution() -> None:
    for code in [*DETERMINISTIC, "51", "91", "61", "05", "99", None]:
        posterior = infer(code).posterior
        assert sum(posterior.values()) == pytest.approx(1.0)
        assert all(0.0 <= p <= 1.0 for p in posterior.values())


def test_an_unknown_code_spreads_rather_than_guessing() -> None:
    """Guessing the most common cause would flatter the confusion matrix."""
    result = infer("XX")
    assert result.confidence < 0.5, "an uninformative code produced a confident answer"


# ── code 05: the contextual case (§20) ─────────────────────────────────────


def test_bare_05_defaults_toward_no_funds() -> None:
    """§20 — "roughly half being insufficient funds in disguise"."""
    assert infer("05").most_likely == NO_FUNDS


def test_unhealthy_issuer_shifts_05_toward_issuer_degraded() -> None:
    """§20's `issuer_wilson_lower` — a degraded issuer fails everyone."""
    ctx = CauseContext(issuer_wilson_lower=0.40, peer_success_rate=0.30)
    assert infer("05", ctx).most_likely == ISSUER_DEGRADED


def test_healthy_issuer_keeps_05_on_the_customer() -> None:
    ctx = CauseContext(issuer_wilson_lower=0.99, peer_success_rate=0.98)
    assert infer("05", ctx).most_likely == NO_FUNDS


def test_sibling_failures_rule_the_customer_in() -> None:
    """§20 — this customer's other mandates failing points at the account."""
    ctx = CauseContext(sibling_failure_rate=0.9, issuer_wilson_lower=0.99)
    result = infer("05", ctx)

    assert result.most_likely == NO_FUNDS
    assert result.posterior[NO_FUNDS] > infer("05").posterior[NO_FUNDS]


def test_a_large_amount_shifts_toward_limit_breach() -> None:
    """§20's `amount / p75(customer successful debits)`."""
    ctx = CauseContext(amount_ratio=4.0)
    baseline = infer("05").posterior[LIMIT_BREACH]
    assert infer("05", ctx).posterior[LIMIT_BREACH] > baseline


def test_distance_from_payday_raises_no_funds() -> None:
    """§20's `days_since_inferred_payday` — the strongest no_funds signal."""
    far = infer("05", CauseContext(days_since_inferred_payday=25))
    near = infer("05", CauseContext(days_since_inferred_payday=1))

    assert far.posterior[NO_FUNDS] > near.posterior[NO_FUNDS]


def test_concurrent_mandates_raise_no_funds() -> None:
    """§20 — competition for the same rupees."""
    many = infer("05", CauseContext(n_concurrent_mandates=6))
    one = infer("05", CauseContext(n_concurrent_mandates=1))

    assert many.posterior[NO_FUNDS] > one.posterior[NO_FUNDS]


def test_issuer_and_customer_evidence_can_oppose_each_other() -> None:
    """Both signals present: the heuristic must resolve, not crash or tie."""
    ctx = CauseContext(issuer_wilson_lower=0.4, peer_success_rate=0.3, sibling_failure_rate=0.9)
    result = infer("05", ctx)

    assert result.most_likely in {NO_FUNDS, ISSUER_DEGRADED}
    assert sum(result.posterior.values()) == pytest.approx(1.0)


# ── the confusion matrix (§20's stated validation) ─────────────────────────


def _observed_code(cycle: SimulatedCycle) -> str | None:
    for event in cycle.observables:
        if event["body"]["event"] == "payment.failed":
            entity = event["body"]["payload"]["payment"]["entity"]
            code: str | None = entity.get("error_code")
            return code
    return None


def test_confusion_matrix_against_ground_truth() -> None:
    """The claim §20 says is impossible on real data.

    Scored on unmasked failures, where the decline code is informative. The
    masked-05 population is scored separately below, because that is where V0 is
    *expected* to be weak — the whole reason §20 wants a model in Phase 11.
    """
    cycles = generate(
        SimConfig(mask_05_rate=0.0, outage_lambda=0.05),
        seed=2001,
        tenant_id=TENANT,
        cycles=3000,
    )
    failures = [c for c in cycles if c.truth.true_cause != "none"]
    assert len(failures) > 500

    matrix: Counter[tuple[str, str]] = Counter()
    for cycle in failures:
        predicted = infer(_observed_code(cycle)).most_likely
        matrix[(cycle.truth.true_cause, predicted)] += 1

    correct = sum(count for (true, pred), count in matrix.items() if true == pred)
    accuracy = correct / len(failures)

    assert accuracy > 0.85, f"unmasked accuracy {accuracy:.3f} is too low; matrix={dict(matrix)}"


def test_v0_cannot_separate_the_causes_hiding_behind_05() -> None:
    """V0's documented weakness, measured rather than asserted.

    Every cause surfacing as 05 collapses to the same answer, because the code
    alone carries no information to separate them. Measured on this population:
    `fraud_hold` cycles that surface as 05 are classified `no_funds` every time.

    This is exactly why §20 wants EM over the latent variable in Phase 11, and
    why the Phase 11 comparison must be scored on the 05 population rather than
    on overall accuracy, which hides the failure entirely.
    """
    cycles = generate(
        SimConfig(mask_05_rate=1.0, outage_lambda=0.05),
        seed=2002,
        tenant_id=TENANT,
        cycles=3000,
    )
    hidden = [
        c
        for c in cycles
        if c.truth.true_cause not in ("none", NO_FUNDS) and _observed_code(c) == "05"
    ]
    assert hidden, "no non-no_funds causes surfaced as 05 — the premise is untestable"

    correct = sum(1 for c in hidden if infer("05").most_likely == c.truth.true_cause)
    assert correct == 0, (
        "V0 separated a cause hiding behind 05 without context — if that is now "
        "possible, the Phase 11 baseline needs re-deriving"
    )


def test_masking_degrades_v0_accuracy() -> None:
    """ADR-065 closed the gap this test used to record.

    §20 describes 05 as "30-40% of all declines... the least informative signal
    in payments". Until ADR-065 the simulator's 05 bucket was ~95% `no_funds`,
    and V0's default answer for 05 is `no_funds` — so masking moved cycles from
    51 to 05 and V0 kept getting them right. Accuracy was *flat* in the masking
    rate, which meant 05 cost the model nothing and Phase 11 would have been
    measured against a flattering baseline.

    Now that four causes hide behind 05, masking has to hurt. If this test ever
    goes flat again, the mixture has collapsed back to one component and every
    cause-inference number above is measuring something easier than §20.
    """

    def accuracy(mask_rate: float) -> float:
        cycles = generate(
            SimConfig(mask_05_rate=mask_rate, outage_lambda=0.05),
            seed=2002,
            tenant_id=TENANT,
            cycles=3000,
        )
        failures = [c for c in cycles if c.truth.true_cause != "none"]
        correct = sum(
            1 for c in failures if infer(_observed_code(c)).most_likely == c.truth.true_cause
        )
        return correct / len(failures)

    clear, obscured = accuracy(0.0), accuracy(1.0)
    assert obscured < clear - 0.02, (
        f"masking costs V0 nothing ({clear:.1%} -> {obscured:.1%}); the 05 bucket "
        "has collapsed back to a single component and ADR-035 has regressed"
    )
