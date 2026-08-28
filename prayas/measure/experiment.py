"""Frozen experiment configuration and arm resolution (Master Spec §33).

"Pre-registration is a frozen `experiment_config` row holding the seed and the
git commit hash, written before the run. The commit timestamp in git history is
the proof."

Freezing matters because an experiment whose seed can change after the fact is
not pre-registered — it is a result someone chose. `frozen` is checked before
any assignment is resolved, and a frozen row cannot be edited (enforced here on
the way in; the ledger's own append-only guarantee covers what was decided).

Reads span tenants: an experiment row may be platform-scoped, with
`tenant_id IS NULL` (ADR-012), because §33 randomises at customer level and one
customer may hold mandates with several merchants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.measure.assignment import arm, propensity

log = logging.getLogger(__name__)


class ExperimentError(RuntimeError):
    """The experiment is missing, unfrozen, or malformed."""


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: str
    seed: str
    control_pct: float
    git_commit: str
    frozen: bool

    def arm_for(self, customer_id: str) -> str:
        """The arm this customer is in. Pure function of seed and id (§33)."""
        if not self.frozen:
            raise ExperimentError(
                f"experiment {self.experiment_id} is not frozen; assigning from an "
                "editable seed is not pre-registration"
            )
        return arm(customer_id, self.seed, self.control_pct)

    def propensity_for(self, customer_id: str) -> float:
        """P(this customer received the arm they received) — ADR-048."""
        return propensity(self.arm_for(customer_id), self.control_pct)


async def active_experiment(conn: AsyncConnection) -> Experiment | None:
    """The frozen experiment governing this tenant, or None.

    Returns None rather than raising when no experiment is running: most of the
    system's life is spent outside an experiment, and that is not an error. The
    caller records a null arm in that case, which is honest.

    A platform-scoped row (`tenant_id IS NULL`) is visible to every tenant under
    ADR-012's policy, and is preferred over a tenant-specific one only if no
    tenant-specific row exists — a merchant's own experiment overrides the
    platform default.
    """
    row = (
        await conn.execute(
            text(
                "SELECT experiment_id, seed, control_pct, git_commit, frozen, tenant_id"
                "  FROM experiment_config"
                " WHERE frozen = true"
                " ORDER BY (tenant_id IS NULL), committed_at DESC"
                " LIMIT 1"
            )
        )
    ).one_or_none()

    if row is None:
        return None

    return Experiment(
        experiment_id=row.experiment_id,
        seed=row.seed,
        control_pct=float(row.control_pct),
        git_commit=row.git_commit,
        frozen=bool(row.frozen),
    )


async def freeze(
    conn: AsyncConnection,
    *,
    experiment_id: str,
    seed: str,
    control_pct: float,
    git_commit: str,
    tenant_id: str | None = None,
) -> Experiment:
    """Pre-register an experiment. Refuses to overwrite a frozen one.

    §33 makes the git commit part of the record, so that "written before the
    run" is checkable against history rather than asserted.

    **Requires the owner role.** `prayas_app` holds SELECT on
    `experiment_config` and nothing more, so the running application physically
    cannot re-seed an experiment after seeing results. Pre-registration is
    enforced by privilege rather than by the `frozen` flag alone — the same
    move Invariant 5 uses to make the ledger append-only. Freezing is a
    deliberate operator action, not routine application work.
    """
    if not 0.0 < control_pct < 1.0:
        raise ExperimentError(f"control_pct must be strictly between 0 and 1, got {control_pct}")
    if not seed or not git_commit:
        raise ExperimentError("seed and git_commit are both required to pre-register")

    existing = (
        await conn.execute(
            text("SELECT frozen FROM experiment_config WHERE experiment_id = :eid"),
            {"eid": experiment_id},
        )
    ).one_or_none()
    if existing is not None and existing.frozen:
        raise ExperimentError(
            f"experiment {experiment_id} is already frozen; re-registering would "
            "let the seed be chosen after seeing results"
        )

    await conn.execute(
        text(
            "INSERT INTO experiment_config"
            " (experiment_id, tenant_id, seed, control_pct, git_commit, committed_at, frozen)"
            " VALUES (:eid, :tid, :seed, :pct, :commit, now(), true)"
            " ON CONFLICT (experiment_id) DO UPDATE SET"
            "   seed = EXCLUDED.seed, control_pct = EXCLUDED.control_pct,"
            "   git_commit = EXCLUDED.git_commit, committed_at = now(), frozen = true"
        ),
        {
            "eid": experiment_id,
            "tid": tenant_id,
            "seed": seed,
            "pct": control_pct,
            "commit": git_commit,
        },
    )

    return Experiment(
        experiment_id=experiment_id,
        seed=seed,
        control_pct=control_pct,
        git_commit=git_commit,
        frozen=True,
    )
