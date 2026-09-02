# Contributing a regulatory change

## The one rule about changing rules

**Add a new version. Never edit an existing one.**

Anything consuming this pack records which rule version governed each past
decision. Rewriting a version retroactively falsifies that record: a decision
made in May would be reviewed against a rule written in September, and the
review would reach the wrong answer while looking rigorous.

This is easy to get wrong in a way that passes review. Bumping `version:` while
*replacing* the old entry looks correct — but then the previous version is gone
from a fresh install, and every earlier decision replays against a rule that
never governed it. **Keep both entries.**

## Steps

1. Add the new version to `src/prayas_rulepack/rulepack.yaml`, leaving the
   previous entry in place.

2. Set `as_of` to the date the new version **takes effect**.

   If you are correcting how a rule is *encoded* rather than tracking a change
   in the regulation, that date is today — not the regulation's date.
   Back-dating makes the new version govern decisions made before it existed.

3. Keep `citation` accurate. If the regulation did not change, the citation
   does not change either.

4. Add a conformance test covering **both sides** of the boundary. A rule
   tested only where it passes is a rule nobody has checked.

   ```python
   def test_the_window_boundary_is_a_cliff() -> None:
       assert _passes("NPCI-AUTOPAY-WINDOW", hour_ist=9.9999)
       assert not _passes("NPCI-AUTOPAY-WINDOW", hour_ist=10.0)
   ```

5. Run the suite, including the clean-install test:

   ```bash
   pytest packages/prayas-rulepack/tests
   ```

## What a rule needs

| Field | Why |
|---|---|
| `rule_id` | Stable across versions |
| `version` | Monotonic per `rule_id` |
| `regulator` | Who issued it |
| `citation` | Enough to find the source document |
| `as_of` | When this version takes effect |
| `applies_to` | Action types |
| `rails` | `null` for every rail — see below |
| `predicate` | An expression the sandbox accepts |
| `on_fail` | `DENY`, `ESCALATE_HUMAN`, or `DEFER` |

**On `rails`.** Use `null` unless the rule is genuinely rail-specific. A rule
listing every rail that exists today behaves identically to `null` right now
and silently exempts any rail added tomorrow — and the resulting debit looks
lawful, so nobody notices.

## What predicates may do

Comparisons, boolean operators, arithmetic, and calls to the helpers in
`ALLOWED_FUNCS`. No attribute access, no subscripting, no imports, no
comprehensions, no lambdas. The validator rejects the AST before evaluation
rather than sanitising the string.

If a rule needs something the sandbox does not allow, propose the helper
separately — widening the sandbox is a bigger change than adding a rule, and it
should be reviewed as one.
