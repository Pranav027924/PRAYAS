"""Predicate sandbox (Master Spec §30.3, §40.2; ADR-025).

"`eval()` on text stored in a database is remote code execution with extra
steps." Every test here is an attempt to make that true, and must fail.

The Phase 2 exit criterion names four forms specifically — `__import__`,
attribute access, comprehensions, lambdas — but a whitelist is only as good as
the escapes nobody thought of, so the known idioms are covered too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from prayas_rulepack.predicate import PredicateError, safe_eval, validate

# ── the four the exit criterion names ───────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "expr"),
    [
        ("__import__", "__import__('os')"),
        ("__import__ via builtins", "__import__('os').system('id')"),
        ("attribute access", "x.__class__"),
        ("dunder chain", "().__class__.__bases__[0].__subclasses__()"),
        ("list comprehension", "[i for i in range(10)]"),
        ("set comprehension", "{i for i in range(10)}"),
        ("dict comprehension", "{i: i for i in range(10)}"),
        ("generator expression", "(i for i in range(10))"),
        ("lambda", "(lambda: 1)()"),
    ],
)
def test_the_named_escapes_are_rejected(label: str, expr: str) -> None:
    with pytest.raises(PredicateError):
        safe_eval(expr, {"x": 1})


# ── the ones nobody names until they are exploited ──────────────────────────


@pytest.mark.parametrize(
    ("label", "expr"),
    [
        ("subscript", "x[0]"),
        ("slice", "x[0:1]"),
        ("f-string", "f'{x}'"),
        ("walrus", "(y := 1)"),
        ("starred", "max(*x)"),
        ("ternary", "1 if x else 2"),
        ("tuple literal", "(1, 2)"),
        ("list literal", "[1, 2]"),
        ("dict literal", "{'a': 1}"),
        ("set literal", "{1, 2}"),
        ("await", "await x"),
        ("power operator", "2 ** 64"),
        ("modulo", "5 % 2"),
        ("floor div", "5 // 2"),
        ("bitwise or", "1 | 2"),
        ("bitwise and", "1 & 2"),
        ("shift", "1 << 64"),
        ("matmul", "x @ x"),
        ("in operator", "'a' in x"),
        ("is operator", "x is None"),
        ("chained call on result", "hours_since(x)()"),
        ("call via attribute", "x.foo()"),
        ("named func not whitelisted", "eval('1')"),
        ("open", "open('/etc/passwd')"),
        ("getattr", "getattr(x, '__class__')"),
        ("globals", "globals()"),
        ("locals", "locals()"),
        ("vars", "vars()"),
        ("exec", "exec('1')"),
        ("compile", "compile('1','','eval')"),
    ],
)
def test_unlisted_constructs_are_rejected(label: str, expr: str) -> None:
    with pytest.raises(PredicateError):
        safe_eval(expr, {"x": 1})


def test_syntax_errors_deny_rather_than_crash() -> None:
    with pytest.raises(PredicateError, match="syntax error"):
        safe_eval("1 +", {})


# ── diagnostics: a denial nobody can interpret is a denial nobody can fix ────


def test_a_rejected_node_is_named_in_the_error() -> None:
    """The message must identify the offending construct, not just fail."""
    with pytest.raises(PredicateError) as exc:
        safe_eval("[i for i in x]", {"x": [1]})
    assert "ListComp" in str(exc.value)

    with pytest.raises(PredicateError) as exc:
        safe_eval("x.attr", {"x": 1})
    assert "Attribute" in str(exc.value)


def test_a_disallowed_call_says_so() -> None:
    with pytest.raises(PredicateError) as exc:
        safe_eval("eval('1')", {})
    assert str(exc.value) == "disallowed call"


def test_a_naive_datetime_is_named_in_the_error() -> None:
    with pytest.raises(PredicateError) as exc:
        safe_eval("hours_since(t) >= 1", {"t": datetime(2026, 1, 1)})  # noqa: DTZ001
    assert str(exc.value) == "naive datetime in predicate context"


def test_a_non_boolean_result_reports_the_actual_type() -> None:
    """ "expected bool" alone would not tell an author what their rule returned."""
    with pytest.raises(PredicateError) as exc:
        safe_eval("1 + 1", {})
    assert "int" in str(exc.value)

    with pytest.raises(PredicateError) as exc:
        safe_eval("a", {"a": "text"})
    assert "str" in str(exc.value)


def test_statements_are_rejected() -> None:
    """`mode="eval"` accepts only expressions, so assignment cannot smuggle state."""
    with pytest.raises(PredicateError):
        safe_eval("x = 1", {})


def test_multiple_statements_are_rejected() -> None:
    with pytest.raises(PredicateError):
        safe_eval("1; __import__('os')", {})


# ── the whitelist must still permit real rules ──────────────────────────────


@pytest.mark.parametrize(
    ("expr", "ctx", "expected"),
    [
        ("hour_ist < 10", {"hour_ist": 9.0}, True),
        ("hour_ist < 10", {"hour_ist": 10.0}, False),
        ("a and b", {"a": True, "b": True}, True),
        ("a or b", {"a": False, "b": True}, True),
        ("not a", {"a": False}, True),
        ("a != null", {"a": 1}, True),
        ("a != null", {"a": None}, False),
        ("a == '160'", {"a": "160"}, True),
        ("a >= 1 and a <= 10", {"a": 5}, True),
    ],
)
def test_legitimate_predicates_evaluate(expr: str, ctx: dict[str, object], expected: bool) -> None:
    assert safe_eval(expr, ctx) is expected


def test_validate_returns_a_tree_for_valid_input() -> None:
    assert validate("hour_ist < 10") is not None


# ── failure modes that must deny rather than raise something else ───────────


def test_missing_context_name_denies_rather_than_crashing() -> None:
    """A feature that was not computed must deny, not take the gate down."""
    with pytest.raises(PredicateError, match="NameError"):
        safe_eval("undefined_feature > 1", {})


def test_non_boolean_result_is_rejected() -> None:
    """Without this, Python truthiness would decide a compliance verdict."""
    with pytest.raises(PredicateError, match="expected bool"):
        safe_eval("1 + 1", {})

    with pytest.raises(PredicateError, match="expected bool"):
        safe_eval("a", {"a": "non-empty string"})


def test_builtins_are_not_reachable() -> None:
    with pytest.raises(PredicateError):
        safe_eval("len(x)", {"x": [1, 2]})


def test_the_builtins_namespace_is_genuinely_empty() -> None:
    """Isolates the empty-builtins control from the AST whitelist.

    `len(x)` is rejected by the *whitelist* (a call to a non-permitted name), so
    it passes whether or not builtins are actually empty — the first control
    masks the second. Mutation testing found four ways to defeat the builtins
    guard that every other test still passed:

        eval(code, None, bindings)          globals=None -> caller's globals
        eval(code, bindings)                bindings as globals -> auto-injected
        {"XX__builtins__XX": {}}            wrong key -> auto-injected
        {"__BUILTINS__": {}}                wrong key -> auto-injected

    A bare `Name` lookup passes the whitelist, so it reaches the namespace and
    can see what is really there. `not {}` is True; `not <module builtins>` is
    False.
    """
    assert safe_eval("not __builtins__", {}) is True


def test_a_failing_rule_is_attributable_to_the_rule_source() -> None:
    """The compiled pseudo-filename must identify the frame as a rule.

    Without it a stack trace from a bad predicate points at an anonymous string
    and an operator cannot tell rule evaluation from engine code. Mutation
    testing surfaced this: changing the filename broke nothing that was tested.
    """
    with pytest.raises(PredicateError) as exc:
        safe_eval("undefined_feature > 1", {})

    cause = exc.value.__cause__
    assert cause is not None

    frames = []
    tb = cause.__traceback__
    while tb is not None:
        frames.append(tb.tb_frame.f_code.co_filename)
        tb = tb.tb_next

    assert "<rule>" in frames, f"rule frame not identifiable; saw {frames}"


def test_context_cannot_smuggle_a_callable() -> None:
    """Only whitelisted *names* may be called, even if ctx holds a callable."""
    with pytest.raises(PredicateError, match="disallowed call"):
        safe_eval("evil()", {"evil": lambda: True})


# ── whitelisted helpers ─────────────────────────────────────────────────────


def test_hours_since_measures_elapsed_time() -> None:
    two_hours_ago = datetime.now(tz=UTC) - timedelta(hours=2)
    assert safe_eval("hours_since(t) >= 1", {"t": two_hours_ago}) is True
    assert safe_eval("hours_since(t) >= 3", {"t": two_hours_ago}) is False


def test_hours_since_none_denies_without_raising() -> None:
    """A missing PDN means no notice was sent — False, not an error (ADR-025)."""
    assert safe_eval("hours_since(t) >= 24", {"t": None}) is False


def test_hours_since_rejects_naive_datetimes() -> None:
    """Times are stored UTC and evaluated IST; a naive datetime is ambiguous."""
    with pytest.raises(PredicateError, match="naive datetime"):
        safe_eval("hours_since(t) >= 24", {"t": datetime(2026, 1, 1)})  # noqa: DTZ001


def test_in_window_is_half_open() -> None:
    """[low, high) so adjacent windows tile without overlapping at the boundary."""
    assert safe_eval("in_window(h, 13, 17)", {"h": 13.0}) is True
    assert safe_eval("in_window(h, 13, 17)", {"h": 16.999}) is True
    assert safe_eval("in_window(h, 13, 17)", {"h": 17.0}) is False
    assert safe_eval("in_window(h, 13, 17)", {"h": 12.999}) is False
