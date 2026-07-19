"""Opt-in lint surface: static checks over a parsed net.

The lint surface is deliberately separate from parsing/validation: a sink
transition (consume without produce) is a legitimate Petri-net construct, so
it must NOT be a :class:`NetValidationError`. ``lint_net`` returns findings a
consumer may act on or ignore, and an ``annotations`` acknowledgement
(ADR 0011 documentation-fields carve-out) suppresses an intentional sink.
"""

from __future__ import annotations

from typing import Any

from velocitron.lint import RULE_CONSUME_WITHOUT_PRODUCE, LintFinding, lint_net
from velocitron.parser import parse_net

# ── Fixture nets ─────────────────────────────────────────────────────────
#
# Each net is minimal for the surface under test: every arc is either the
# consume/produce/read/inhibit shape the rule resolves or the structural
# minimum to make the net parse.


def _sink_net(annotations: dict[str, Any] | None = None) -> dict[str, Any]:
    """A sink net — one consume arc, zero produce arcs (the flagged shape) —
    with optional annotations on its ``clear_flag`` transition."""
    transition: dict[str, Any] = {"name": "clear_flag", "handler": "clear_flag"}
    if annotations is not None:
        transition["annotations"] = annotations
    return {
        "name": "sink",
        "places": [{"name": "flag", "accepts": ["flag"]}],
        "transitions": [transition],
        "arcs": [
            {
                "from": {"place": "flag"},
                "to": {"transition": "clear_flag"},
                "consume": {"type": "flag"},
            },
        ],
    }


SINK_NET: dict[str, Any] = _sink_net()

# A transition that consumes AND produces — the clean shape.
PRODUCER_NET: dict[str, Any] = {
    "name": "producer",
    "places": [
        {"name": "inbox", "accepts": ["job"]},
        {"name": "outbox", "accepts": ["job"]},
    ],
    "transitions": [{"name": "advance", "handler": "advance"}],
    "arcs": [
        {
            "from": {"place": "inbox"},
            "to": {"transition": "advance"},
            "consume": {"type": "job"},
        },
        {
            "from": {"transition": "advance"},
            "to": {"place": "outbox"},
            "produce": {"type": "job", "destination": "outbox"},
        },
    ],
}

# The motivating bug shape: an `advance_*` transition with consume+read inputs
# and zero produce arcs (a dropped produce arc), which parses clean but can
# never advance the net.
CONSUME_PLUS_READ_NET: dict[str, Any] = {
    "name": "advance-bug",
    "places": [
        {"name": "inbox", "accepts": ["job"]},
        {"name": "clock", "accepts": ["tick"]},
    ],
    "transitions": [{"name": "advance_stage", "handler": "advance_stage"}],
    "arcs": [
        {
            "from": {"place": "inbox"},
            "to": {"transition": "advance_stage"},
            "consume": {"type": "job"},
        },
        {
            "from": {"place": "clock"},
            "to": {"transition": "advance_stage"},
            "consume": {"type": "tick", "mode": "read"},
        },
    ],
}

# A transition whose only inputs are read + inhibit arcs (removes nothing) and
# no produce arc — a pure gate/observer, NOT a consumption sink.
READ_INHIBIT_ONLY_NET: dict[str, Any] = {
    "name": "observer",
    "places": [
        {"name": "config", "accepts": ["config"]},
        {"name": "lock", "accepts": ["lock"]},
    ],
    "transitions": [{"name": "observe", "handler": "observe"}],
    "arcs": [
        {
            "from": {"place": "config"},
            "to": {"transition": "observe"},
            "consume": {"type": "config", "mode": "read"},
        },
        {
            "from": {"place": "lock"},
            "to": {"transition": "observe"},
            "consume": {"type": "lock", "mode": "inhibit"},
        },
    ],
}

# Two sink transitions to pin finding order (declaration order).
TWO_SINKS_NET: dict[str, Any] = {
    "name": "two-sinks",
    "places": [
        {"name": "a", "accepts": ["t"]},
        {"name": "b", "accepts": ["t"]},
    ],
    "transitions": [
        {"name": "drop_a", "handler": "drop_a"},
        {"name": "drop_b", "handler": "drop_b"},
    ],
    "arcs": [
        {
            "from": {"place": "a"},
            "to": {"transition": "drop_a"},
            "consume": {"type": "t"},
        },
        {
            "from": {"place": "b"},
            "to": {"transition": "drop_b"},
            "consume": {"type": "t"},
        },
    ],
}


# ── The rule: consume without produce ────────────────────────────────────


class TestConsumeWithoutProduce:
    def test_sink_net_parses_without_error(self):
        # given: a sink net with NO suppression annotation
        # when: parsing it
        net = parse_net(SINK_NET)

        # then: parsing succeeds — a sink is a legitimate construct, and the
        # lint is opt-in, never a NetValidationError
        assert net.transitions[0].name == "clear_flag"

    def test_sink_transition_produces_a_finding(self):
        # given: a net with a transition that consumes but never produces
        net = parse_net(SINK_NET)

        # when: linting the net
        findings = lint_net(net)

        # then: exactly one finding is returned, naming the sink transition
        assert len(findings) == 1
        # and: the finding is a LintFinding carrying the stable rule id and
        # the transition name
        assert isinstance(findings[0], LintFinding)
        assert findings[0].rule == RULE_CONSUME_WITHOUT_PRODUCE
        assert findings[0].transition == "clear_flag"
        # and: the message names the likely cause
        assert "consumes but never produces" in findings[0].message
        assert "missing produce arc" in findings[0].message

    def test_transition_with_produce_arc_is_clean(self):
        # given: a transition that both consumes and produces
        net = parse_net(PRODUCER_NET)

        # when: linting
        findings = lint_net(net)

        # then: no finding — a produce arc is present
        assert findings == []

    def test_consume_plus_read_without_produce_is_flagged(self):
        # given: the motivating bug — consume+read inputs, zero produce arcs
        net = parse_net(CONSUME_PLUS_READ_NET)

        # when: linting
        findings = lint_net(net)

        # then: the consume-mode arc makes it a sink; the read arc is not an
        # output and does not exempt it
        assert len(findings) == 1
        assert findings[0].transition == "advance_stage"
        assert findings[0].rule == RULE_CONSUME_WITHOUT_PRODUCE

    def test_read_and_inhibit_only_transition_is_not_flagged(self):
        # given: a transition whose only inputs are read + inhibit arcs
        net = parse_net(READ_INHIBIT_ONLY_NET)

        # when: linting
        findings = lint_net(net)

        # then: no finding — read/inhibit remove nothing, so the transition is
        # not a consumption sink (a pure gate/observer, out of this rule's scope)
        assert findings == []

    def test_findings_follow_transition_declaration_order(self):
        # given: two sink transitions declared drop_a then drop_b
        net = parse_net(TWO_SINKS_NET)

        # when: linting
        findings = lint_net(net)

        # then: both are flagged, in declaration order
        assert [f.transition for f in findings] == ["drop_a", "drop_b"]


# ── Suppression via annotation (ADR 0011 carve-out) ──────────────────────


class TestSinkSuppression:
    def test_acknowledged_sink_is_suppressed(self):
        # given: a sink transition whose annotation acknowledges the rule
        net = parse_net(
            _sink_net({"lint": {"suppress": [RULE_CONSUME_WITHOUT_PRODUCE]}})
        )

        # when: linting
        findings = lint_net(net)

        # then: the intentional sink is silenced
        assert findings == []

    def test_suppression_of_another_rule_does_not_silence_this_one(self):
        # given: a suppress list naming a different (unrelated) rule id
        net = parse_net(_sink_net({"lint": {"suppress": ["some-other-rule"]}}))

        # when: linting
        findings = lint_net(net)

        # then: this rule still fires — suppression is rule-specific
        assert len(findings) == 1
        assert findings[0].rule == RULE_CONSUME_WITHOUT_PRODUCE

    def test_unrelated_annotations_do_not_suppress(self):
        # given: a sink transition with annotations that carry no lint block
        net = parse_net(_sink_net({"owner": "team-x", "note": "wip"}))

        # when: linting
        findings = lint_net(net)

        # then: the finding still fires — only a lint.suppress entry silences it
        assert len(findings) == 1
