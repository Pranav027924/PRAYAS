"""Model registry with versions pinned per decision (Master Spec §43; ADR-070).

Every decision the system records must name the exact model that produced it.
Without that, §32's replay cannot reconstruct a decision, an incident cannot be
scoped to the model that caused it, and "the model was fixed last Tuesday" is an
unfalsifiable claim rather than a checkable one.

**Versions are content-addressed.** The id is a hash of what actually
determines the model's behaviour — its kind, its hyperparameters, its feature
names in order, and a fingerprint of the data it was fitted on. Two fits of the
same configuration on the same data get the same id; changing a hyperparameter
or reordering a feature gets a different one. An incrementing counter would let
two different models share a version number after a bad deploy, which is the
one thing a registry exists to prevent.

**Serving is a deliberate act.** Registering a model does not serve it.
`promote` does, and `rollback` undoes it — which is what makes the Phase 11 exit
criterion ("kill V1, the system degrades without firing anything illegal") a
single call rather than a code change under incident conditions.

**This registry does not decide anything.** It records which model answered, and
answers "which model should answer now". Compliance is unaffected: Invariant 1
and 2 hold whichever model is serving, and a rollback changes the quality of the
timing advice, never its legality.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final

#: The decision points a model can be pinned to.
LIQUIDITY_HAZARD: Final = "liquidity_hazard"
CAUSE_INFERENCE: Final = "cause_inference"
REVOCATION_HAZARD: Final = "revocation_hazard"

DECISION_POINTS: Final[tuple[str, ...]] = (
    LIQUIDITY_HAZARD,
    CAUSE_INFERENCE,
    REVOCATION_HAZARD,
)


class RegistryError(RuntimeError):
    """The registry was asked for something it cannot safely provide."""


def _canonical(value: Any) -> str:
    """Stable JSON, so a hash depends on content and never on dict ordering."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """One fitted model, identified by what determines its behaviour."""

    decision_point: str
    kind: str
    params: dict[str, Any]
    feature_names: tuple[str, ...]
    #: Fingerprint of the training data — row count and label sum are enough to
    #: distinguish refits without retaining anything about a customer.
    training_fingerprint: str
    fitted_at: datetime
    #: Held-out metrics, recorded at fit time so a promotion can be justified
    #: from the record rather than from memory.
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def version_id(self) -> str:
        """Content address. Deliberately excludes `fitted_at` and `metrics`.

        Two identical fits of the same configuration on the same data *are* the
        same model, whatever the clock said; including the timestamp would make
        every refit look like a change and drown a real one in noise.
        """
        payload = _canonical(
            {
                "decision_point": self.decision_point,
                "kind": self.kind,
                "params": self.params,
                "feature_names": list(self.feature_names),
                "training_fingerprint": self.training_fingerprint,
            }
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def describe(self) -> str:
        return f"{self.decision_point}:{self.kind}@{self.version_id}"


def fingerprint(*, rows: int, positives: int, seed: int | None = None) -> str:
    """A training-set fingerprint that retains nothing about any customer.

    Counts only. §27's k-anonymity floor and Invariant 8 both make it a bad
    idea for a registry entry to carry anything a person could be recovered
    from, and counts are sufficient to tell two refits apart.
    """
    if rows < 0 or positives < 0:
        raise ValueError("counts must be non-negative")
    if positives > rows:
        raise ValueError("positives cannot exceed rows")
    return hashlib.sha256(_canonical([rows, positives, seed]).encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Pin:
    """Which version serves a decision point, and why."""

    version_id: str
    promoted_at: datetime
    reason: str


class ModelRegistry:
    """Registered versions, and the pin that says which one serves."""

    def __init__(self) -> None:
        self._versions: dict[str, ModelVersion] = {}
        self._pins: dict[str, Pin] = {}
        self._history: list[tuple[datetime, str, str, str]] = []

    # ── registration ───────────────────────────────────────────────────────

    def register(self, version: ModelVersion) -> str:
        """Record a version. Does **not** promote it."""
        if version.decision_point not in DECISION_POINTS:
            raise RegistryError(f"unknown decision point: {version.decision_point}")

        # Same content address, different behaviour: either the fingerprint is
        # not capturing something that matters, or two models genuinely differ
        # in a way the address ignores. Both are bugs worth stopping for.
        existing = self._versions.get(version.version_id)
        if existing is not None and (
            existing.params != version.params
            or existing.feature_names != version.feature_names
            or existing.kind != version.kind
        ):
            raise RegistryError(
                f"version {version.version_id} already registered with different content"
            )
        self._versions[version.version_id] = version
        return version.version_id

    def get(self, version_id: str) -> ModelVersion:
        version = self._versions.get(version_id)
        if version is None:
            raise RegistryError(f"no such version: {version_id}")
        return version

    def versions_for(self, decision_point: str) -> list[ModelVersion]:
        return sorted(
            (v for v in self._versions.values() if v.decision_point == decision_point),
            key=lambda v: v.fitted_at,
        )

    # ── serving ────────────────────────────────────────────────────────────

    def promote(self, version_id: str, *, reason: str, at: datetime | None = None) -> Pin:
        """Make a version the one that serves its decision point."""
        if not reason.strip():
            raise RegistryError("a promotion must carry a reason")
        version = self.get(version_id)
        pin = Pin(version_id=version_id, promoted_at=at or datetime.now(UTC), reason=reason.strip())
        previous = self._pins.get(version.decision_point)
        self._pins[version.decision_point] = pin
        self._history.append(
            (
                pin.promoted_at,
                version.decision_point,
                previous.version_id if previous else "",
                version_id,
            )
        )
        return pin

    def serving(self, decision_point: str) -> ModelVersion | None:
        """The version currently serving, or None if nothing is promoted.

        `None` is a real answer, not an error: it is what a caller sees after a
        rollback with no earlier version to fall back to, and the caller is
        expected to have a heuristic that works without a model at all.
        """
        pin = self._pins.get(decision_point)
        return self._versions[pin.version_id] if pin else None

    def rollback(self, decision_point: str, *, reason: str) -> ModelVersion | None:
        """Fall back to the previously promoted version, or to nothing.

        The Phase 11 exit criterion in one call. Returns what now serves, which
        is `None` when there is nothing left to fall back to — the caller's
        heuristic then takes over, and no debit's legality changes either way.
        """
        if decision_point not in DECISION_POINTS:
            raise RegistryError(f"unknown decision point: {decision_point}")

        earlier = [h for h in self._history if h[1] == decision_point and h[2]]
        if not earlier:
            self._pins.pop(decision_point, None)
            self._history.append((datetime.now(UTC), decision_point, "", ""))
            return None

        target = earlier[-1][2]
        self.promote(target, reason=f"rollback: {reason}")
        return self.get(target)

    def history(self, decision_point: str | None = None) -> list[tuple[datetime, str, str, str]]:
        """`(at, decision_point, from_version, to_version)`, oldest first."""
        if decision_point is None:
            return list(self._history)
        return [h for h in self._history if h[1] == decision_point]

    # ── the record a decision carries ──────────────────────────────────────

    def stamp(self, decision_point: str) -> dict[str, str]:
        """What a decision records about the model that produced it.

        Always returns a stamp, including when nothing is promoted — a decision
        made by the heuristic is still a decision, and recording "no model" is
        materially different from recording nothing.
        """
        version = self.serving(decision_point)
        if version is None:
            return {"decision_point": decision_point, "model": "heuristic", "version_id": ""}
        return {
            "decision_point": decision_point,
            "model": version.kind,
            "version_id": version.version_id,
        }

    def stamp_all(self) -> dict[str, str]:
        """The `decisions.model_versions` payload — every decision point at once.

        §36 gives the ledger a `model_versions` column and §32's replay needs it
        populated: reconstructing a decision means knowing which models produced
        it, *including the ones that were not serving*. A decision point running
        on the heuristic is recorded as `"heuristic"` rather than omitted,
        because a missing key is indistinguishable from a key nobody thought to
        write, and only one of those is evidence.
        """
        return {
            point: (version.version_id if (version := self.serving(point)) else "heuristic")
            for point in DECISION_POINTS
        }


def version_of(
    decision_point: str,
    kind: str,
    *,
    params: dict[str, Any],
    feature_names: tuple[str, ...],
    rows: int,
    positives: int,
    seed: int | None = None,
    metrics: dict[str, float] | None = None,
    fitted_at: datetime | None = None,
) -> ModelVersion:
    """Convenience constructor — the shape every fit path produces."""
    return ModelVersion(
        decision_point=decision_point,
        kind=kind,
        params=dict(params),
        feature_names=tuple(feature_names),
        training_fingerprint=fingerprint(rows=rows, positives=positives, seed=seed),
        fitted_at=fitted_at or datetime.now(UTC),
        metrics=dict(metrics or {}),
    )


def with_metrics(version: ModelVersion, metrics: dict[str, float]) -> ModelVersion:
    """A copy carrying held-out metrics. The version id is unchanged."""
    return replace(version, metrics={**version.metrics, **metrics})
