"""Load and select compliance rules from the pack.

The engine that ships inside Prayas reads these rules from PostgreSQL, because
a running system wants them cached and joined. A third party auditing the rules
wants neither, so this loader reads the YAML directly and applies the same
selection semantics.

**Selection is by `as_of`, and that is the whole point of versioning them.**
Asking "which rules applied on 2026-05-01" must return the versions that were
in force *then*, not the ones in force now. A compliance artifact that could
only answer "what are the rules today" cannot be used to review a decision made
last quarter — which is the main thing anyone would want it for.

**A rule without a citation and an `as_of` will not load.** That is not
strictness for its own sake: an uncited rule in a compliance pack is worse than
a missing one, because it looks authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import yaml

PACK_PATH: Final = Path(__file__).parent / "rulepack.yaml"

#: The jurisdiction these rules come from. Kept local rather than importing
#: `zoneinfo`, so the pack has no dependency beyond PyYAML.
_IST: Final = timezone(timedelta(hours=5, minutes=30))

#: §30.2's verdict vocabulary, ordered by severity — higher wins when several
#: rules disagree. Four values, not two: some failures are neither "proceed"
#: nor "refuse" but "a person decides" or "not yet", and collapsing those into
#: DENY would lose the distinction a merchant most needs to act on.
VERDICT_PRECEDENCE: Final[dict[str, int]] = {
    "DENY": 3,
    "ESCALATE_HUMAN": 2,
    "DEFER": 1,
    "ALLOW": 0,
}

ALLOW: Final = "ALLOW"
DENY: Final = "DENY"
ESCALATE_HUMAN: Final = "ESCALATE_HUMAN"
DEFER: Final = "DEFER"


class RulePackError(ValueError):
    """The pack is not a usable set of rules."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One versioned rule."""

    rule_id: str
    version: int
    regulator: str
    citation: str
    as_of: date
    applies_to: tuple[str, ...]
    rails: tuple[str, ...] | None
    predicate: str
    on_fail: str

    def applies(self, *, action_type: str, rail: str | None) -> bool:
        """Whether this rule governs this action on this rail.

        `rails = null` means every rail, including one that does not exist yet.
        That is the safe reading: a rule enumerating the rails it knows about
        silently exempts any rail added later, and the resulting debit looks
        lawful. (Prayas hit exactly this; see FINDING-P14-01 in its history.)
        """
        if action_type not in self.applies_to:
            return False
        return self.rails is None or (rail is not None and rail in self.rails)


def _parse_rule(raw: dict[str, Any]) -> Rule:
    missing = [
        k for k in ("rule_id", "version", "regulator", "citation", "as_of") if not raw.get(k)
    ]
    if missing:
        raise RulePackError(
            f"rule {raw.get('rule_id', '<unnamed>')!r} is missing {missing}; "
            f"a rule without a citation and an as_of date is an opinion, not a rule"
        )

    as_of = raw["as_of"]
    if not isinstance(as_of, date):
        raise RulePackError(f"{raw['rule_id']}: as_of must be a date, got {type(as_of).__name__}")

    # Defaults to DENY: a rule whose author forgot to say what failing it means
    # should refuse, not permit.
    on_fail = raw.get("on_fail", DENY)
    if on_fail not in VERDICT_PRECEDENCE:
        raise RulePackError(
            f"{raw['rule_id']}: on_fail must be one of "
            f"{sorted(VERDICT_PRECEDENCE)}, got {on_fail!r}"
        )

    rails = raw.get("rails")
    return Rule(
        rule_id=str(raw["rule_id"]),
        version=int(raw["version"]),
        regulator=str(raw["regulator"]),
        citation=str(raw["citation"]),
        as_of=as_of,
        applies_to=tuple(raw.get("applies_to") or ()),
        rails=tuple(rails) if rails else None,
        predicate=str(raw["predicate"]),
        on_fail=on_fail,
    )


def _load_pack(path: Path | None = None) -> dict[str, Any]:
    source = path or PACK_PATH
    if not source.is_file():
        raise RulePackError(f"no rule pack at {source}")
    data: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "rules" not in data:
        raise RulePackError(f"{source} does not contain a `rules` list")
    return data


def all_rules(path: Path | None = None) -> list[Rule]:
    """Every rule at every version, in file order."""
    return [_parse_rule(raw) for raw in _load_pack(path)["rules"]]


def load_rules(
    *,
    action_type: str,
    rail: str | None = None,
    as_of: date | None = None,
    path: Path | None = None,
) -> list[Rule]:
    """The rules in force for this action, on this rail, at this moment.

    One version per `rule_id`: the newest whose `as_of` is not in the future.
    A rule with no version yet in force is absent rather than defaulted — it
    did not exist then, and pretending otherwise would apply a rule
    retroactively.
    """
    # Timezone-explicit: "which rules are in force" has a different answer
    # either side of midnight, and a naive local date makes that depend on
    # where the auditor happens to be sitting. India is the jurisdiction these
    # rules come from, so it is the clock they are read against.
    when = as_of or datetime.now(_IST).date()

    newest: dict[str, Rule] = {}
    for rule in all_rules(path):
        if not rule.applies(action_type=action_type, rail=rail):
            continue
        if rule.as_of > when:
            continue
        current = newest.get(rule.rule_id)
        if current is None or (rule.as_of, rule.version) > (current.as_of, current.version):
            newest[rule.rule_id] = rule

    return sorted(newest.values(), key=lambda r: r.rule_id)


def afa_caps(path: Path | None = None) -> list[dict[str, Any]]:
    """Additional-factor-authentication ceilings by merchant category."""
    caps = _load_pack(path).get("afa_caps") or []
    return [dict(cap) for cap in caps]
