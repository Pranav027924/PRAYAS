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


def _in_window(value: float, low: float, high: float) -> bool:
    """Half-open [low, high), so adjacent windows tile without overlapping."""
    return low <= value < high


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


def allowed_callables() -> frozenset[str]:
    return frozenset(ALLOWED_FUNCS) | INJECTABLE_FUNCS


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
