"""Opt-in static lint checks over a parsed :class:`Net`.

Lint findings are advisory, never errors: every shape flagged here is a
*legitimate* net that :func:`velocitron.parser.parse_net` accepts — the lint
exists because some legitimate shapes are far more often bugs than intent
(e.g. a net-generator dropping produce arcs leaves a transition that parses
clean but can never advance the net). Consumers opt in by calling
:func:`lint_net` and deciding what to do with the findings; parsing and
firing behavior are untouched by this module.

Intentional occurrences are acknowledged per-transition through the ADR 0011
documentation-fields carve-out: ``annotations: {"lint": {"suppress":
["<rule-id>"]}}`` on the transition silences that rule for that transition.
The engine continues to ignore ``annotations`` entirely; only this opt-in
lint surface reads the ``lint`` key.

References: spec/net-schema.md (Lint rules, advisory, opt-in); ADR 0016;
ADR 0011.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .schema import Net, Transition

# Stable rule id: a transition with >=1 consume-mode arc and zero produce
# arcs. Read- and inhibit-mode arcs are not outputs and do not exempt a
# transition; a transition with ONLY read/inhibit inputs removes nothing and
# is a pure gate/observer, out of this rule's scope.
RULE_CONSUME_WITHOUT_PRODUCE = "consume-without-produce"


@dataclass(frozen=True)
class LintFinding:
    """A single advisory lint finding: a stable rule id, the flagged
    transition's name, and a human-readable message."""

    rule: str
    transition: str
    message: str


def _suppressed_rules(transition: Transition) -> list[str]:
    """The rule ids this transition's ``annotations.lint.suppress`` names.

    Anything other than the documented shape (a ``lint`` object carrying a
    ``suppress`` list) suppresses nothing — malformed suppression must fail
    open (the finding still fires) rather than silently silence a rule.
    """
    annotations = transition.annotations or {}
    lint_block = annotations.get("lint")
    if not isinstance(lint_block, dict):
        return []
    suppress = cast("dict[str, Any]", lint_block).get("suppress")
    if not isinstance(suppress, list):
        return []
    return [rule for rule in cast("list[Any]", suppress) if isinstance(rule, str)]


def lint_net(net: Net) -> list[LintFinding]:
    """Run all lint rules over a parsed net; return advisory findings.

    Findings follow transition declaration order. An empty list means the
    net is clean under every (non-suppressed) rule.
    """
    consuming_transitions: set[str] = set()
    producing_transitions: set[str] = set()
    for arc in net.arcs:
        if arc.consume is not None and arc.consume.mode == "consume":
            assert arc.to_transition is not None  # parser-enforced direction
            consuming_transitions.add(arc.to_transition)
        if arc.produce is not None:
            assert arc.from_transition is not None  # parser-enforced direction
            producing_transitions.add(arc.from_transition)

    findings: list[LintFinding] = []
    for transition in net.transitions:
        if (
            transition.name not in consuming_transitions
            or transition.name in producing_transitions
        ):
            continue
        if RULE_CONSUME_WITHOUT_PRODUCE in _suppressed_rules(transition):
            continue
        findings.append(
            LintFinding(
                rule=RULE_CONSUME_WITHOUT_PRODUCE,
                transition=transition.name,
                message=(
                    f"transition {transition.name!r} consumes but never "
                    f"produces — likely missing produce arc; if this sink is "
                    f"intentional, acknowledge it with annotations "
                    f'{{"lint": {{"suppress": '
                    f'["{RULE_CONSUME_WITHOUT_PRODUCE}"]}}}}'
                ),
            )
        )
    return findings
