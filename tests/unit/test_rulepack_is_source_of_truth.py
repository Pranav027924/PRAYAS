"""The published pack is the one that runs (ADR-091).

Phase 14 recorded what happens when a compliance rule exists in two places: the
copies drift, and the ledger cites a version that no longer describes what ran.
So the rule pack was not *copied* into `packages/prayas-rulepack` — it was
moved there, and the gate loads from it.

These tests assert that arrangement holds, because the tempting future change
is to vendor a copy back for convenience.
"""

from __future__ import annotations

from pathlib import Path

import prayas_rulepack

from prayas.gate import engine

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "packages" / "prayas-rulepack" / "src" / "prayas_rulepack"


def test_there_is_exactly_one_rulepack_in_the_repository() -> None:
    """Two copies of a compliance rule is the Phase 14 failure. One file."""
    found = [
        p
        for p in REPO.rglob("rulepack.yaml")
        if ".venv" not in p.parts and "mutants" not in p.parts
    ]
    assert found == [PACKAGE / "rulepack.yaml"], found


def test_there_is_exactly_one_predicate_evaluator() -> None:
    """The sandbox is the most safety-critical file here. One implementation."""
    found = [
        p for p in REPO.rglob("predicate.py") if ".venv" not in p.parts and "mutants" not in p.parts
    ]
    assert found == [PACKAGE / "predicate.py"], found


def test_the_gate_evaluates_with_the_packaged_sandbox() -> None:
    """Not a re-export shim, not a fork — the same function object.

    Read from the module's namespace rather than as an attribute, because the
    gate does not re-export these and should not: they belong to the pack.
    """
    assert vars(engine)["safe_eval"] is prayas_rulepack.safe_eval
    assert vars(engine)["PredicateError"] is prayas_rulepack.PredicateError


def test_the_gate_does_not_carry_its_own_rules_directory() -> None:
    assert not (REPO / "prayas" / "gate" / "rules").exists()


def test_the_mutation_scope_follows_the_evaluator() -> None:
    """§40.10's criterion is "mutation testing on gate predicates: no surviving
    mutants". Moving the evaluator out of `prayas/gate/` without moving the
    mutmut scope would have quietly dropped that guarantee — the tests would
    still pass and the guarantee would be gone.
    """
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    mutmut = pyproject.split("[tool.mutmut]")[1]

    assert "packages/prayas-rulepack/src/" in mutmut, (
        "the evaluator moved but the mutation-testing scope did not follow it"
    )
    assert "prayas_rulepack.predicate" in pyproject
