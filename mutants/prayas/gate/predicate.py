"""Sandboxed predicate evaluation (Master Spec §30.3).

"`eval()` on text stored in a database is remote code execution with extra
steps." The rule predicates are data; this module is what keeps them data.

Three independent controls, none of them trusted alone:

1. **AST node whitelist.** Anything not explicitly permitted raises. Attribute
   access is absent, which alone kills ``().__class__.__bases__[0].__subclasses__()``
   and every variant of it.
2. **Empty builtins.** ``__import__``, ``open``, ``getattr`` are not in scope.
3. **Function whitelist.** Calls are permitted only to bare names present in the
   allowed-callable set, not to arbitrary callables that happen to be in context.

Never add a node type here without a Class A decision (ADR-025). Comprehensions,
lambdas, subscripts and attribute access are excluded deliberately: each one
re-opens a documented sandbox escape.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final


from mutmut.mutation.trampoline import wrap_in_trampoline as _mutmut_mutated, MutantDict


class PredicateError(RuntimeError):
    """The predicate is not evaluable. Always resolves to a denial (§30.2)."""


#: §30.3 verbatim. Note what is absent: ast.Attribute, ast.Subscript,
#: ast.ListComp, ast.Lambda, ast.JoinedStr, ast.NamedExpr, ast.Pow, ast.Starred.
ALLOWED_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Call,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
)
mutants_x__hours_since__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__hours_since__mutmut)
def _hours_since(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_orig(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_1(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is not None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_2(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float(None)
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_3(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("XX-infXX")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_4(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-INF")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_5(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is not None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_6(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError(None)
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_7(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("XXnaive datetime in predicate contextXX")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_8(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("NAIVE DATETIME IN PREDICATE CONTEXT")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_9(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() * 3600.0


def x__hours_since__mutmut_10(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) + moment).total_seconds() / 3600.0


def x__hours_since__mutmut_11(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=None) - moment).total_seconds() / 3600.0


def x__hours_since__mutmut_12(moment: datetime | None) -> float:
    """Hours elapsed since ``moment``. Absent timestamps deny by returning -inf.

    Returning negative infinity rather than raising means a rule like
    ``hours_since(pdn_sent_at) >= 24`` evaluates False for a missing PDN, which
    is the correct answer -- no notice was sent -- rather than an error the
    caller has to interpret.
    """
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:
        raise PredicateError("naive datetime in predicate context")
    return (datetime.now(tz=UTC) - moment).total_seconds() / 3601.0

mutants_x__hours_since__mutmut['_mutmut_orig'] = x__hours_since__mutmut_orig # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_1'] = x__hours_since__mutmut_1 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_2'] = x__hours_since__mutmut_2 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_3'] = x__hours_since__mutmut_3 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_4'] = x__hours_since__mutmut_4 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_5'] = x__hours_since__mutmut_5 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_6'] = x__hours_since__mutmut_6 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_7'] = x__hours_since__mutmut_7 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_8'] = x__hours_since__mutmut_8 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_9'] = x__hours_since__mutmut_9 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_10'] = x__hours_since__mutmut_10 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_11'] = x__hours_since__mutmut_11 # type: ignore # mutmut generated
mutants_x__hours_since__mutmut['x__hours_since__mutmut_12'] = x__hours_since__mutmut_12 # type: ignore # mutmut generated
mutants_x__in_window__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x__in_window__mutmut)
def _in_window(value: float, low: float, high: float) -> bool:
    """Half-open [low, high), so adjacent windows tile without overlapping."""
    return low <= value < high


def x__in_window__mutmut_orig(value: float, low: float, high: float) -> bool:
    """Half-open [low, high), so adjacent windows tile without overlapping."""
    return low <= value < high


def x__in_window__mutmut_1(value: float, low: float, high: float) -> bool:
    """Half-open [low, high), so adjacent windows tile without overlapping."""
    return low < value < high


def x__in_window__mutmut_2(value: float, low: float, high: float) -> bool:
    """Half-open [low, high), so adjacent windows tile without overlapping."""
    return low <= value <= high

mutants_x__in_window__mutmut['_mutmut_orig'] = x__in_window__mutmut_orig # type: ignore # mutmut generated
mutants_x__in_window__mutmut['x__in_window__mutmut_1'] = x__in_window__mutmut_1 # type: ignore # mutmut generated
mutants_x__in_window__mutmut['x__in_window__mutmut_2'] = x__in_window__mutmut_2 # type: ignore # mutmut generated


#: Statically bound helpers. ``afa_free_cap`` is injected per evaluation from
#: the regulatory_reference table (ADR-021) rather than fixed here.
ALLOWED_FUNCS: Final[Mapping[str, Callable[..., Any]]] = {
    "hours_since": _hours_since,
    "in_window": _in_window,
}

#: Names the engine may inject at evaluation time.
INJECTABLE_FUNCS: Final[frozenset[str]] = frozenset({"afa_free_cap"})

#: §30.1's predicates are written with `!= null`, which is not Python. Rules are
#: authored by compliance reviewers, not engineers, so the spec's spelling is
#: honoured by binding the name rather than by rewriting every rule to `None`.
LANGUAGE_BINDINGS: Final[dict[str, Any]] = {"null": None}
mutants_x_allowed_callables__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_allowed_callables__mutmut)
def allowed_callables() -> frozenset[str]:
    return frozenset(ALLOWED_FUNCS) | INJECTABLE_FUNCS


def x_allowed_callables__mutmut_orig() -> frozenset[str]:
    return frozenset(ALLOWED_FUNCS) | INJECTABLE_FUNCS


def x_allowed_callables__mutmut_1() -> frozenset[str]:
    return frozenset(ALLOWED_FUNCS) & INJECTABLE_FUNCS


def x_allowed_callables__mutmut_2() -> frozenset[str]:
    return frozenset(None) | INJECTABLE_FUNCS

mutants_x_allowed_callables__mutmut['_mutmut_orig'] = x_allowed_callables__mutmut_orig # type: ignore # mutmut generated
mutants_x_allowed_callables__mutmut['x_allowed_callables__mutmut_1'] = x_allowed_callables__mutmut_1 # type: ignore # mutmut generated
mutants_x_allowed_callables__mutmut['x_allowed_callables__mutmut_2'] = x_allowed_callables__mutmut_2 # type: ignore # mutmut generated
mutants_x_validate__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_validate__mutmut)
def validate(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_orig(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_1(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = None
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_2(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(None, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_3(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode=None)
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_4(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_5(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, )
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_6(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="XXevalXX")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_7(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="EVAL")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_8(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(None) from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_9(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = None
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_10(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(None):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_11(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_12(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(None)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_13(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(None).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_14(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) or (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_15(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) and node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_16(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_17(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id in permitted
        ):
            raise PredicateError("disallowed call")
    return tree


def x_validate__mutmut_18(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError(None)
    return tree


def x_validate__mutmut_19(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("XXdisallowed callXX")
    return tree


def x_validate__mutmut_20(expr: str) -> ast.Expression:
    """Parse and whitelist-check. Raises PredicateError on anything unexpected."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PredicateError(f"syntax error: {exc}") from exc

    permitted = allowed_callables()
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise PredicateError(type(node).__name__)
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in permitted
        ):
            raise PredicateError("DISALLOWED CALL")
    return tree

mutants_x_validate__mutmut['_mutmut_orig'] = x_validate__mutmut_orig # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_1'] = x_validate__mutmut_1 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_2'] = x_validate__mutmut_2 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_3'] = x_validate__mutmut_3 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_4'] = x_validate__mutmut_4 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_5'] = x_validate__mutmut_5 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_6'] = x_validate__mutmut_6 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_7'] = x_validate__mutmut_7 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_8'] = x_validate__mutmut_8 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_9'] = x_validate__mutmut_9 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_10'] = x_validate__mutmut_10 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_11'] = x_validate__mutmut_11 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_12'] = x_validate__mutmut_12 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_13'] = x_validate__mutmut_13 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_14'] = x_validate__mutmut_14 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_15'] = x_validate__mutmut_15 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_16'] = x_validate__mutmut_16 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_17'] = x_validate__mutmut_17 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_18'] = x_validate__mutmut_18 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_19'] = x_validate__mutmut_19 # type: ignore # mutmut generated
mutants_x_validate__mutmut['x_validate__mutmut_20'] = x_validate__mutmut_20 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut: MutantDict = {}  # type: ignore


@_mutmut_mutated(mutants_x_safe_eval__mutmut)
def safe_eval(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_orig(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_1(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = None
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_2(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(None)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_3(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = None

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_4(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs and {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_5(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = None
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_6(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(None, {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_7(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), None, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_8(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, None)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_9(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval({"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_10(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_11(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, )
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_12(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(None, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_13(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, None, "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_14(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", None), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_15(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile("<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_16(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_17(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", ), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_18(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "XX<rule>XX", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_19(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<RULE>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_20(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "XXevalXX"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_21(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "EVAL"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_22(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"XX__builtins__XX": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_23(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__BUILTINS__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_24(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(None) from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_25(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(None).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_26(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(result).__name__}, expected bool")
    return result


def x_safe_eval__mutmut_27(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(None)
    return result


def x_safe_eval__mutmut_28(
    expr: str,
    ctx: Mapping[str, Any],
    funcs: Mapping[str, Callable[..., Any]] | None = None,
) -> bool:
    """Evaluate a rule predicate. Raises only PredicateError.

    The result is type-checked rather than coerced: a predicate returning a
    non-bool would otherwise let Python truthiness decide a compliance verdict.
    """
    tree = validate(expr)
    bindings: dict[str, Any] = {
        **LANGUAGE_BINDINGS,
        **ctx,
        **ALLOWED_FUNCS,
        **(funcs or {}),
    }

    try:
        result = eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, bindings)
    except PredicateError:
        raise
    except Exception as exc:
        # A NameError for a context key the rule expects is a real condition --
        # a feature that was not computed. It must deny, not crash the gate.
        raise PredicateError(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(result, bool):
        raise PredicateError(f"predicate returned {type(None).__name__}, expected bool")
    return result

mutants_x_safe_eval__mutmut['_mutmut_orig'] = x_safe_eval__mutmut_orig # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_1'] = x_safe_eval__mutmut_1 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_2'] = x_safe_eval__mutmut_2 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_3'] = x_safe_eval__mutmut_3 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_4'] = x_safe_eval__mutmut_4 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_5'] = x_safe_eval__mutmut_5 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_6'] = x_safe_eval__mutmut_6 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_7'] = x_safe_eval__mutmut_7 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_8'] = x_safe_eval__mutmut_8 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_9'] = x_safe_eval__mutmut_9 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_10'] = x_safe_eval__mutmut_10 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_11'] = x_safe_eval__mutmut_11 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_12'] = x_safe_eval__mutmut_12 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_13'] = x_safe_eval__mutmut_13 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_14'] = x_safe_eval__mutmut_14 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_15'] = x_safe_eval__mutmut_15 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_16'] = x_safe_eval__mutmut_16 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_17'] = x_safe_eval__mutmut_17 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_18'] = x_safe_eval__mutmut_18 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_19'] = x_safe_eval__mutmut_19 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_20'] = x_safe_eval__mutmut_20 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_21'] = x_safe_eval__mutmut_21 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_22'] = x_safe_eval__mutmut_22 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_23'] = x_safe_eval__mutmut_23 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_24'] = x_safe_eval__mutmut_24 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_25'] = x_safe_eval__mutmut_25 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_26'] = x_safe_eval__mutmut_26 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_27'] = x_safe_eval__mutmut_27 # type: ignore # mutmut generated
mutants_x_safe_eval__mutmut['x_safe_eval__mutmut_28'] = x_safe_eval__mutmut_28 # type: ignore # mutmut generated
