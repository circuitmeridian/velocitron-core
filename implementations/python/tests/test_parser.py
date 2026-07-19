"""Tests for the Petri net JSON schema parser and validator.

These tests exercise the current public-alpha parser and validator contract:

* Parse a valid planning-slice net JSON into frozen dataclasses.
* Parse consume patterns (type + optional predicate + mode), produce
  templates, CEL/named predicates, ports, initial markings, and
  composition documents with wires.
* Reject structurally invalid nets with a ``NetValidationError``.

The normative behavior covered here is defined by ``spec/net-schema.md`` and
``spec/composition.md``: arcs are top-level and arc-centric; consume mode
distinguishes consume, inhibit, and read semantics; produce templates are
routing contracts; and composition is represented by a separate document with
ports in the net schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter
import velocitron.parser as parser_module
from velocitron.schema_resources import COMPOSITION_SCHEMA, NET_SCHEMA

from velocitron.parser import NetValidationError, parse_composition, parse_net
from velocitron.schema import (
    Arc,
    Composition,
    Marking,
    Net,
    Token,
)
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.registry import HandlerRegistry

# ── Fixture data: a planning slice covering the current schema ──────────
#
# Covers: regular places, accepted token types, consume arcs (default +
# inhibit), produce arcs with destination, handler refs, and the git-diff
# gating pattern (inhibit for edits, consume for commits).

PLANNING_SLICE: dict[str, Any] = {
    "name": "planning-slice",
    "places": [
        {"name": "backlog", "accepts": ["feature"]},
        {"name": "plan_needed", "accepts": ["feature"]},
        {"name": "plan_drafted", "accepts": ["feature"]},
        {"name": "qa_check", "accepts": ["feature"]},
        {"name": "done", "accepts": ["feature"]},
        {"name": "git_tree_diff", "accepts": ["git_status"]},
    ],
    "transitions": [
        {"name": "start_feature", "handler": "start_feature"},
        {"name": "write_plan", "handler": "write_plan"},
        {"name": "commit_plan", "handler": "commit_plan"},
    ],
    "arcs": [
        # start_feature: consume backlog, inhibit done, produce plan_needed
        {
            "from": {"place": "backlog"},
            "to": {"transition": "start_feature"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "done"},
            "to": {"transition": "start_feature"},
            "consume": {"type": "feature", "mode": "inhibit"},
        },
        {
            "from": {"transition": "start_feature"},
            "to": {"place": "plan_needed"},
            "produce": {"type": "feature", "destination": "plan_needed"},
        },
        # write_plan: consume plan_needed, inhibit git_tree_diff,
        #             produce plan_drafted + git_tree_diff (dirty)
        {
            "from": {"place": "plan_needed"},
            "to": {"transition": "write_plan"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "git_tree_diff"},
            "to": {"transition": "write_plan"},
            "consume": {"type": "git_status", "mode": "inhibit"},
        },
        {
            "from": {"transition": "write_plan"},
            "to": {"place": "plan_drafted"},
            "produce": {"type": "feature", "destination": "plan_drafted"},
        },
        {
            "from": {"transition": "write_plan"},
            "to": {"place": "git_tree_diff"},
            "produce": {"type": "git_status", "destination": "git_tree_diff"},
        },
        # commit_plan: consume plan_drafted + git_tree_diff (dirty),
        #              produce qa_check (clean)
        {
            "from": {"place": "plan_drafted"},
            "to": {"transition": "commit_plan"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "git_tree_diff"},
            "to": {"transition": "commit_plan"},
            "consume": {"type": "git_status"},
        },
        {
            "from": {"transition": "commit_plan"},
            "to": {"place": "qa_check"},
            "produce": {"type": "feature", "destination": "qa_check"},
        },
    ],
}


# ── Parse a valid net ────────────────────────────────────────────────────


class TestParseNet:
    def test_parse_from_dict_returns_net(self):
        # given: the canonical planning slice fixture
        # when: parsing the slice from a dict
        net = parse_net(PLANNING_SLICE)
        # then: a Net is returned with the slice name
        assert isinstance(net, Net)
        assert net.name == "planning-slice"

    def test_parse_from_path_returns_net(self, tmp_path: Path):
        # given: the planning slice written to a JSON file
        net_file = tmp_path / "net.json"
        net_file.write_text(json.dumps(PLANNING_SLICE))
        # when: parsing the net from that file path
        net = parse_net(net_file)
        # then: a Net is returned with the slice name
        assert isinstance(net, Net)
        assert net.name == "planning-slice"

    def test_places_parsed(self):
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # when: inspecting the parsed places
        # then: six places are present with their accepted token types
        assert len(net.places) == 6
        by_name = {p.name: p for p in net.places}
        assert by_name["backlog"].accepts == ["feature"]
        assert by_name["git_tree_diff"].accepts == ["git_status"]

    def test_transitions_parsed(self):
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # when: inspecting the parsed transitions
        # then: three transitions are present with their handler refs
        assert len(net.transitions) == 3
        by_name = {t.name: t for t in net.transitions}
        assert by_name["start_feature"].handler == "start_feature"
        assert by_name["write_plan"].handler == "write_plan"

    def test_transition_handler_is_optional_but_nonempty_when_present(self):
        """An absent handler is ``None``; explicit refs are preserved verbatim."""
        # given: otherwise-minimal transitions with absent and explicit handlers
        document = {
            "name": "traditional",
            "places": [],
            "transitions": [
                {"name": "handlerless"},
                {"name": "handled", "handler": "handled@demo"},
            ],
            "arcs": [],
        }
        # when: parsing the core JSON document
        net = parse_net(document)
        # then: absence remains None and no fallback is invented
        assert net.transitions[0].handler is None
        assert net.transitions[1].handler == "handled@demo"

    @pytest.mark.parametrize("handler", [None, ""])
    def test_transition_handler_rejects_null_and_empty_string(
        self, handler: str | None
    ):
        """A present handler must be a nonempty string."""
        # given: a transition with an invalid present handler value
        document = {
            "name": "invalid-handler",
            "places": [],
            "transitions": [{"name": "transition", "handler": handler}],
            "arcs": [],
        }
        # when: parsing the invalid core JSON document
        # then: schema validation rejects null and empty string
        with pytest.raises(NetValidationError):
            parse_net(document)

    def test_arcs_count(self):
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # when: counting the parsed arcs
        # then: ten arcs are present
        assert len(net.arcs) == 10

    def test_consume_arc_default_mode_is_consume(self):
        """A consume arc without explicit ``mode`` defaults to ``"consume"``."""
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the backlog → start_feature consume arc
        arc = _find_consume_arc(
            net, from_place="backlog", to_transition="start_feature"
        )
        # then: the arc is a consume of type feature with default mode
        assert arc.consume is not None
        assert arc.consume.type == "feature"
        assert arc.consume.mode == "consume"

    def test_inhibit_mode_parsed(self):
        """A consume arc with ``mode: "inhibit"`` is parsed as an inhibitor."""
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the done → start_feature consume arc
        arc = _find_consume_arc(net, from_place="done", to_transition="start_feature")
        # then: the arc is a consume with inhibit mode
        assert arc.consume is not None
        assert arc.consume.mode == "inhibit"

    def test_produce_template_parsed(self):
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the start_feature → plan_needed produce arc
        arc = _find_produce_arc(
            net, from_transition="start_feature", to_place="plan_needed"
        )
        # then: the arc is a produce of type feature bound to plan_needed
        assert arc.produce is not None
        assert arc.produce.type == "feature"
        assert arc.produce.destination == "plan_needed"

    def test_absent_predicate_is_none(self):
        """A consume pattern with no predicate field yields ``predicate=None``."""
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the backlog → start_feature consume arc
        arc = _find_consume_arc(
            net, from_place="backlog", to_transition="start_feature"
        )
        # then: the consume pattern has no predicate
        assert arc.consume is not None
        assert arc.consume.predicate is None

    def test_optional_guard_and_priority_default_none(self):
        """Transitions without guard/priority parse to ``None`` for both."""
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the start_feature transition
        t = next(t for t in net.transitions if t.name == "start_feature")
        # then: guard and priority are both None
        assert t.guard is None
        assert t.priority is None

    def test_net_with_initial_marking(self):
        # given: a net dict with an initial marking on backlog
        net_dict = {
            "name": "marked-net",
            "places": [{"name": "backlog", "accepts": ["feature"]}],
            "transitions": [
                {"name": "start", "handler": "start"},
            ],
            "arcs": [
                {
                    "from": {"place": "backlog"},
                    "to": {"transition": "start"},
                    "consume": {"type": "feature"},
                },
            ],
            "initialMarking": {
                "backlog": [{"type": "feature", "data": {"id": "f1"}}],
            },
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: the initial marking is populated with the backlog token
        assert net.initial_marking is not None
        tokens = net.initial_marking["backlog"]
        assert len(tokens) == 1
        assert tokens[0].type == "feature"
        assert tokens[0].data == {"id": "f1"}


# ── Predicates ───────────────────────────────────────────────────────────


class TestPredicates:
    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_predicate_parsed(self, adapter: CelAdapter) -> None:
        """An inline CEL expression is parsed into ``Predicate(cel=...)``."""
        # given: a net with a consume arc carrying an inline CEL predicate
        net_dict = {
            "name": "cel-net",
            "places": [
                {"name": "decision", "accepts": ["feature"]},
                {"name": "out", "accepts": ["feature"]},
            ],
            "transitions": [{"name": "route", "handler": "passthrough"}],
            "arcs": [
                {
                    "from": {"place": "decision"},
                    "to": {"transition": "route"},
                    "consume": {
                        "type": "feature",
                        "predicate": {"cel": "data.more_questions == true"},
                    },
                },
                {
                    "from": {"transition": "route"},
                    "to": {"place": "out"},
                    "produce": {"type": "feature", "destination": "out"},
                },
            ],
        }
        # when: parsing the net and locating the consume arc
        net = parse_net(net_dict, cel_adapter=adapter)
        arc = _find_consume_arc(net, from_place="decision", to_transition="route")
        # then: the predicate is a CEL predicate with no handler
        assert arc.consume is not None
        assert arc.consume.predicate is not None
        assert arc.consume.predicate.cel == "data.more_questions == true"
        assert arc.consume.predicate.handler is None

    def test_named_predicate_handler_parsed(self):
        """A named predicate handler ref is parsed into ``Predicate(handler=...)``."""
        # given: a net with a consume arc carrying a named predicate handler
        net_dict = {
            "name": "handler-net",
            "places": [
                {"name": "inbox", "accepts": ["email"]},
                {"name": "out", "accepts": ["email"]},
            ],
            "transitions": [{"name": "filter", "handler": "filter_handler"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "filter"},
                    "consume": {
                        "type": "email",
                        "predicate": {"handler": "pred.is_authorized"},
                    },
                },
                {
                    "from": {"transition": "filter"},
                    "to": {"place": "out"},
                    "produce": {"type": "email", "destination": "out"},
                },
            ],
        }
        # when: parsing the net and locating the consume arc
        net = parse_net(net_dict)
        arc = _find_consume_arc(net, from_place="inbox", to_transition="filter")
        # then: the predicate is a handler predicate with no CEL
        assert arc.consume is not None
        assert arc.consume.predicate is not None
        assert arc.consume.predicate.handler == "pred.is_authorized"
        assert arc.consume.predicate.cel is None

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_and_handler_mutually_exclusive(self, adapter: CelAdapter) -> None:
        """A predicate with both ``cel`` and ``handler`` is rejected."""
        # given: a net with a predicate specifying both cel and handler
        net_dict = {
            "name": "bad-pred",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {
                        "type": "t",
                        "predicate": {
                            "cel": "data.x",
                            "handler": "pred.x",
                        },
                    },
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict, cel_adapter=adapter)

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_valid_cel_compiles_at_parse(self, adapter: CelAdapter) -> None:
        """A syntactically valid CEL expression parses cleanly (D6).

        Compile-at-parse validates syntax only; the expression's free
        variables resolve against token ``data`` at fire time, so a valid
        expression with no binding context still parses.
        """
        # given: a net with a syntactically valid inline CEL predicate
        net_dict = {
            "name": "valid-cel",
            "places": [
                {"name": "p", "accepts": ["t"]},
                {"name": "out", "accepts": ["t"]},
            ],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {"type": "t", "predicate": {"cel": "priority > 5"}},
                },
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "out"},
                    "produce": {"type": "t", "destination": "out"},
                },
            ],
        }
        # when: parsing the net and locating the consume arc
        net = parse_net(net_dict, cel_adapter=adapter)
        arc = _find_consume_arc(net, from_place="p", to_transition="tr")
        # then: the CEL predicate is preserved
        assert arc.consume is not None
        assert arc.consume.predicate is not None
        assert arc.consume.predicate.cel == "priority > 5"

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_invalid_cel_rejected_at_parse(self, adapter: CelAdapter) -> None:
        """A syntactically invalid CEL expression fails parsing (D6).

        A parse/compile error is a malformed net, surfaced as a
        NetValidationError at parse time — not silently degraded to
        predicate-false at fire time.
        """
        # given: a net with a syntactically invalid inline CEL predicate
        net_dict = {
            "name": "bad-cel",
            "places": [
                {"name": "p", "accepts": ["t"]},
                {"name": "out", "accepts": ["t"]},
            ],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {
                        "type": "t",
                        "predicate": {"cel": "priority > > 5"},
                    },
                },
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "out"},
                    "produce": {"type": "t", "destination": "out"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict, cel_adapter=adapter)


# ── Ports ────────────────────────────────────────────────────────────────


class TestPorts:
    def test_port_parsed(self):
        """A place with a ``port`` facet is parsed as a Port on the Place."""
        # given: a net with input and output port facets on two places
        net_dict = {
            "name": "port-net",
            "places": [
                {
                    "name": "inbox",
                    "accepts": ["email"],
                    "port": {"direction": "input", "type": "email"},
                },
                {
                    "name": "outbox",
                    "accepts": ["email"],
                    "port": {"direction": "output", "type": "email"},
                },
            ],
            "transitions": [{"name": "process", "handler": "process"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "process"},
                    "consume": {"type": "email"},
                },
                {
                    "from": {"transition": "process"},
                    "to": {"place": "outbox"},
                    "produce": {"type": "email", "destination": "outbox"},
                },
            ],
        }
        # when: parsing the net and locating the port places
        net = parse_net(net_dict)
        inbox = next(p for p in net.places if p.name == "inbox")
        # then: the inbox place has an input port of type email
        assert inbox.port is not None
        assert inbox.port.direction == "input"
        assert inbox.port.type == "email"
        # and: the outbox place has an output port
        outbox = next(p for p in net.places if p.name == "outbox")
        assert outbox.port is not None
        assert outbox.port.direction == "output"

    def test_non_port_place_has_none(self):
        # given: the parsed planning slice
        net = parse_net(PLANNING_SLICE)
        # and: the backlog place
        backlog = next(p for p in net.places if p.name == "backlog")
        # then: the place has no port
        assert backlog.port is None

    def test_port_type_not_accepted_rejected(self):
        """A port whose ``type`` is not in the place's ``accepts`` is rejected."""
        # given: a net with a port whose type is not in the place's accepts
        net_dict = {
            "name": "bad-port",
            "places": [
                {
                    "name": "inbox",
                    "accepts": ["email"],
                    "port": {"direction": "input", "type": "task"},
                },
            ],
            "transitions": [{"name": "process", "handler": "process"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "process"},
                    "consume": {"type": "email"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)


# ── Composition ──────────────────────────────────────────────────────────


class TestComposition:
    def test_parse_composition(self, tmp_path: Path):
        """A composition document references nets by path and wires their ports."""
        # given: a producer net with an output port and a consumer net with an input port
        net_a = {
            "name": "producer",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [
                {
                    "from": {"place": "work"},
                    "to": {"transition": "produce"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "produce"},
                    "to": {"place": "out"},
                    "produce": {"type": "task", "destination": "out"},
                },
            ],
        }
        net_b = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {"name": "done", "accepts": ["task"]},
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [
                {
                    "from": {"place": "in"},
                    "to": {"transition": "consume"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "consume"},
                    "to": {"place": "done"},
                    "produce": {"type": "task", "destination": "done"},
                },
            ],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(net_a))
        path_b.write_text(json.dumps(net_b))

        # and: a composition dict wiring producer out → consumer in
        comp_dict = {
            "nets": [
                {"ref": str(path_a), "alias": "prod"},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }
        # when: parsing the composition
        comp = parse_composition(comp_dict)
        # then: a Composition is returned with two nets and one wire
        assert isinstance(comp, Composition)
        assert len(comp.nets) == 2
        assert len(comp.wires) == 1
        # and: the wire connects prod.out to cons.in
        wire = comp.wires[0]
        assert wire.from_net == "prod"
        assert wire.from_port == "out"
        assert wire.to_net == "cons"
        assert wire.to_port == "in"

    def test_alias_defaults_to_net_name(self, tmp_path: Path):
        """A net ref without ``alias`` defaults to the referenced net's ``name``."""
        # given: a producer net and a consumer net (no explicit aliases)
        net_a = {
            "name": "producer",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [
                {
                    "from": {"place": "work"},
                    "to": {"transition": "produce"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "produce"},
                    "to": {"place": "out"},
                    "produce": {"type": "task", "destination": "out"},
                },
            ],
        }
        net_b = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {"name": "done", "accepts": ["task"]},
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [
                {
                    "from": {"place": "in"},
                    "to": {"transition": "consume"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "consume"},
                    "to": {"place": "done"},
                    "produce": {"type": "task", "destination": "done"},
                },
            ],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(net_a))
        path_b.write_text(json.dumps(net_b))
        # and: a composition dict with refs but no aliases
        comp_dict = {
            "nets": [
                {"ref": str(path_a)},
                {"ref": str(path_b)},
            ],
            "wires": [
                {
                    "from": {"net": "producer", "port": "out"},
                    "to": {"net": "consumer", "port": "in"},
                },
            ],
        }
        # when: parsing the composition
        comp = parse_composition(comp_dict)
        # then: aliases default to each net's name
        assert comp.nets[0].alias == "producer"
        assert comp.nets[1].alias == "consumer"
        # and: the wire uses the defaulted aliases
        assert comp.wires[0].from_net == "producer"
        assert comp.wires[0].to_net == "consumer"


# ── Validation errors ────────────────────────────────────────────────────


class TestValidationErrors:
    def test_duplicate_place_names(self):
        # given: a net with two places sharing a name
        net_dict = {
            "name": "dup",
            "places": [
                {"name": "p", "accepts": ["t"]},
                {"name": "p", "accepts": ["t"]},
            ],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_duplicate_transition_names(self):
        """Two transitions sharing a name are rejected."""
        # given: a net with two transitions sharing a name
        net_dict = {
            "name": "dup",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [
                {"name": "tr", "handler": "h"},
                {"name": "tr", "handler": "h"},
            ],
            "arcs": [],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_dangling_arc_place(self):
        """An arc referencing a non-existent place is rejected."""
        # given: a net with an arc referencing a non-existent place
        net_dict = {
            "name": "dangling",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "nonexistent"},
                    "to": {"transition": "tr"},
                    "consume": {"type": "t"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_dangling_arc_transition(self):
        """An arc referencing a non-existent transition is rejected."""
        # given: a net with an arc referencing a non-existent transition
        net_dict = {
            "name": "dangling",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "nonexistent"},
                    "consume": {"type": "t"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_consume_arc_must_be_place_to_transition(self):
        """A consume arc on a transition→place edge is rejected."""
        # given: a net with a consume arc oriented transition → place
        net_dict = {
            "name": "bad-dir",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "p"},
                    "consume": {"type": "t"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_produce_arc_must_be_transition_to_place(self):
        """A produce arc on a place→transition edge is rejected."""
        # given: a net with a produce arc oriented place → transition
        net_dict = {
            "name": "bad-dir",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "produce": {"type": "t", "destination": "p"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_produce_destination_must_match_to_place(self):
        """The produce template's ``destination`` must equal the arc's ``to`` place."""
        # given: a net with a produce destination that mismatches the arc's to place
        net_dict = {
            "name": "mismatch",
            "places": [
                {"name": "p1", "accepts": ["t"]},
                {"name": "p2", "accepts": ["t"]},
            ],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "p1"},
                    "produce": {"type": "t", "destination": "p2"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_consume_type_must_be_accepted_by_place(self):
        """A consume arc whose type is not in the place's ``accepts`` is rejected."""
        # given: a net with a consume arc whose type is not accepted by the place
        net_dict = {
            "name": "type-mismatch",
            "places": [{"name": "p", "accepts": ["feature"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {"type": "email"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_produce_type_must_be_accepted_by_place(self):
        """A produce arc whose type is not in the destination place's ``accepts`` is rejected."""
        # given: a net with a produce arc whose type is not accepted by the destination place
        net_dict = {
            "name": "type-mismatch",
            "places": [{"name": "p", "accepts": ["feature"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "p"},
                    "produce": {"type": "email", "destination": "p"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)


# ── Wire validation (composition) ─────────────────────────────────────────


def _write_prod_cons(tmp_path: Path) -> tuple[Path, Path]:
    """Write a canonical producer (output port ``out``, type ``task``) and a
    consumer (input port ``in``, type ``task``) to ``tmp_path``; return
    ``(path_a, path_b)``. Shapes mirror TestComposition.test_parse_composition.
    """
    net_a = {
        "name": "producer",
        "places": [
            {"name": "work", "accepts": ["task"]},
            {
                "name": "out",
                "accepts": ["task"],
                "port": {"direction": "output", "type": "task"},
            },
        ],
        "transitions": [{"name": "produce", "handler": "produce_handler"}],
        "arcs": [
            {
                "from": {"place": "work"},
                "to": {"transition": "produce"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "produce"},
                "to": {"place": "out"},
                "produce": {"type": "task", "destination": "out"},
            },
        ],
    }
    net_b = {
        "name": "consumer",
        "places": [
            {
                "name": "in",
                "accepts": ["task"],
                "port": {"direction": "input", "type": "task"},
            },
            {"name": "done", "accepts": ["task"]},
        ],
        "transitions": [{"name": "consume", "handler": "consume_handler"}],
        "arcs": [
            {
                "from": {"place": "in"},
                "to": {"transition": "consume"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "consume"},
                "to": {"place": "done"},
                "produce": {"type": "task", "destination": "done"},
            },
        ],
    }
    path_a = tmp_path / "producer.json"
    path_b = tmp_path / "consumer.json"
    path_a.write_text(json.dumps(net_a))
    path_b.write_text(json.dumps(net_b))
    return path_a, path_b


class TestWireValidation:
    """Composition wire validation: dangling ports, direction, type, alias uniqueness."""

    def test_wire_dangling_port(self, tmp_path: Path):
        """A wire referencing a port that does not exist on the net is rejected."""
        # given: canonical producer and consumer nets
        path_a, path_b = _write_prod_cons(tmp_path)
        # and: a composition wiring a non-existent source port
        comp = {
            "nets": [
                {"ref": str(path_a), "alias": "prod"},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "nonexistent"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_wire_source_not_output_port(self, tmp_path: Path):
        """A wire whose source port is an input port (not output) is rejected."""
        # given: a producer with an input control port and a consumer with an input port
        producer = {
            "name": "producer",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "ctrl",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [],
        }
        consumer = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {"name": "done", "accepts": ["task"]},
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(producer))
        path_b.write_text(json.dumps(consumer))
        # and: a composition wiring the input control port as a source
        comp = {
            "nets": [
                {"ref": str(path_a), "alias": "prod"},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "ctrl"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_wire_target_not_input_port(self, tmp_path: Path):
        """A wire whose target port is an output port (not input) is rejected."""
        # given: a producer with an output port and a consumer with an output ack port
        producer = {
            "name": "producer",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [],
        }
        consumer = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {
                    "name": "ack",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(producer))
        path_b.write_text(json.dumps(consumer))
        # and: a composition wiring to the consumer's output ack port
        comp = {
            "nets": [
                {"ref": str(path_a), "alias": "prod"},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "out"},
                    "to": {"net": "cons", "port": "ack"},
                },
            ],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_wire_type_mismatch(self, tmp_path: Path):
        """A wire joining an output port and input port of different types is rejected."""
        # given: a producer with a task output port and a consumer with an email input port
        producer = {
            "name": "producer",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [],
        }
        consumer = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["email"],
                    "port": {"direction": "input", "type": "email"},
                },
                {"name": "done", "accepts": ["email"]},
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(producer))
        path_b.write_text(json.dumps(consumer))
        # and: a composition wiring the mismatched ports
        comp = {
            "nets": [
                {"ref": str(path_a), "alias": "prod"},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_duplicate_alias_explicit(self, tmp_path: Path):
        """Two nets given the same explicit alias are rejected."""
        # given: canonical producer and consumer nets
        path_a, path_b = _write_prod_cons(tmp_path)
        # and: a composition giving both nets the same explicit alias
        comp = {
            "nets": [
                {"ref": str(path_a), "alias": "dup"},
                {"ref": str(path_b), "alias": "dup"},
            ],
            "wires": [],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_duplicate_alias_derived_default(self, tmp_path: Path):
        """Two nets with the same ``name`` and no explicit alias collide on the
        derived default alias and are rejected."""
        # given: two nets that share the same name and no explicit alias
        net_a = {
            "name": "same",
            "places": [{"name": "p", "accepts": ["task"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        net_b = {
            "name": "same",
            "places": [{"name": "p", "accepts": ["task"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        path_a.write_text(json.dumps(net_a))
        path_b.write_text(json.dumps(net_b))
        # and: a composition referencing both without aliases
        comp = {
            "nets": [
                {"ref": str(path_a)},
                {"ref": str(path_b)},
            ],
            "wires": [],
        }
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)

    def test_derived_alias_from_non_identifier_net_name_rejected(self, tmp_path: Path):
        """A net whose ``name`` is not a valid identifier and which relies on the
        default alias is rejected at parse time. The derived alias would break the
        spec's ``<alias>.<placeName>`` invariant: aliases are restricted to simple
        identifiers (schema ``pattern``) so the ``.`` is an unambiguous delimiter
        (spec/composition.md, Aliasing — Why dotted). The schema enforces this only
        for *explicit* aliases; the derived alias bypasses the schema, so the parser
        is the authoritative enforcer (D4). A name like ``prod.line`` makes
        ``prod.line.out`` ambiguous (alias ``prod.line`` + port ``out`` vs alias
        ``prod`` + port ``line.out``) and must fail at parse, not pass silently."""
        # given: a producer net whose name contains a dot (a valid net name, but
        # not a valid composition alias)
        producer = {
            "name": "prod.line",
            "places": [
                {"name": "work", "accepts": ["task"]},
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
            ],
            "transitions": [{"name": "produce", "handler": "produce_handler"}],
            "arcs": [],
        }
        consumer = {
            "name": "consumer",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
            ],
            "transitions": [{"name": "consume", "handler": "consume_handler"}],
            "arcs": [],
        }
        # and: the nets written to JSON files
        path_a = tmp_path / "producer.json"
        path_b = tmp_path / "consumer.json"
        path_a.write_text(json.dumps(producer))
        path_b.write_text(json.dumps(consumer))
        # and: a composition that omits the producer's alias (so it defaults to
        # the net name "prod.line") and wires the ambiguous alias through
        comp = {
            "nets": [
                {"ref": str(path_a)},
                {"ref": str(path_b), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod.line", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }
        # when: parsing the composition
        # then: validation raises NetValidationError at parse time — the derived
        # alias "prod.line" is not a simple identifier, so "<alias>.<placeName>"
        # would be ambiguous and must be rejected, not silently accepted
        with pytest.raises(NetValidationError):
            parse_composition(comp)


# ── Schema strictness (packaged schema matches the canonical artifact) ──


class TestSchemaStrictness:
    """The packaged JSON Schema is tight, not a loose approximation."""

    def test_rejects_additional_top_level_property(self):
        # given: the planning slice with an unexpected top-level property
        net = dict(PLANNING_SLICE)
        net["unexpected"] = "x"
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net)

    def test_rejects_empty_string_name(self):
        # given: the planning slice with an empty name
        net = {**PLANNING_SLICE, "name": ""}
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net)

    def test_rejects_empty_accepts(self):
        # given: a net with a place whose accepts list is empty
        net_dict: dict[str, Any] = {
            "name": "empty-accepts",
            "places": [{"name": "p", "accepts": []}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_produce_data_not_object(self):
        # given: a net with a produce template whose data is not an object
        net_dict = {
            "name": "bad-data",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"transition": "tr"},
                    "to": {"place": "p"},
                    "produce": {
                        "type": "t",
                        "destination": "p",
                        "data": "not-an-object",
                    },
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_endpoint_with_both_place_and_transition(self):
        # given: a net with an arc endpoint specifying both place and transition
        net_dict = {
            "name": "bad-endpoint",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p", "transition": "tr"},
                    "to": {"transition": "tr"},
                    "consume": {"type": "t"},
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_arc_with_neither_consume_nor_produce(self):
        # given: a net with an arc specifying neither consume nor produce
        net_dict = {
            "name": "no-kind",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {"from": {"place": "p"}, "to": {"transition": "tr"}},
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_predicate_with_third_property(self):
        # given: a net with a predicate carrying a third, unsupported property
        net_dict = {
            "name": "bad-pred",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {
                        "type": "t",
                        "predicate": {"cel": "data.x", "bogus": 1},
                    },
                },
            ],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_composition_with_no_nets(self):
        # given: a composition document with no nets
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition({"nets": [], "wires": []})

    def test_rejects_composition_alias_with_dot(self):
        # given: a composition with a net alias containing a dot
        comp = {"nets": [{"ref": "x", "alias": "has.dot"}], "wires": []}
        # when: parsing the invalid composition
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_composition(comp)


# ── Shared schema resources ─────────────────────────────────────────────


class TestSharedSchemaResources:
    """The parser validates with the package-owned schema documents."""

    def test_net_parser_uses_shared_resource_and_preserves_validation(self):
        # given: the schema object loaded once from the packaged resource
        assert parser_module.NET_SCHEMA is NET_SCHEMA
        invalid_net = {**PLANNING_SLICE, "unexpected": True}
        # when/then: parser validation still rejects the same strict-schema violation
        with pytest.raises(NetValidationError, match="Additional properties"):
            parse_net(invalid_net)

    def test_composition_parser_uses_shared_resource_and_preserves_validation(self):
        # given: the schema object loaded once from the packaged resource
        assert parser_module.COMPOSITION_SCHEMA is COMPOSITION_SCHEMA
        # when/then: parser validation still rejects the same minItems violation
        with pytest.raises(NetValidationError, match="should be non-empty"):
            parse_composition({"nets": [], "wires": []})


# ── Documentation fields ────────────────────────────────────────────────


class TestDocumentationFields:
    """description + annotations on place, transition, arc, and net.

    Asserts the two documentation fields parse, are preserved on the
    dataclasses, default to None when absent, do not affect firing (ADR 0001
    preserved), and that strictness against unknown properties still holds.
    """

    # ── Parse + preserve ─────────────────────────────────────────────

    def test_place_description_and_annotations_parse_and_preserve(self):
        # given: a minimal net whose single place carries description + annotations
        net_dict: dict[str, Any] = {
            "name": "doc-place",
            "places": [
                {
                    "name": "p",
                    "accepts": ["t"],
                    "description": "the entry point",
                    "annotations": {"group": "input", "priority": 1},
                }
            ],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: the place carries its description and annotations
        assert net.places[0].description == "the entry point"
        assert net.places[0].annotations == {"group": "input", "priority": 1}

    def test_transition_description_and_annotations_parse_and_preserve(self):
        # given: a minimal net whose single transition carries description + annotations
        net_dict: dict[str, Any] = {
            "name": "doc-transition",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [
                {
                    "name": "tr",
                    "handler": "h",
                    "description": "moves tokens",
                    "annotations": {"cost": 5, "tags": ["slow"]},
                }
            ],
            "arcs": [],
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: the transition carries its description and annotations
        assert net.transitions[0].description == "moves tokens"
        assert net.transitions[0].annotations == {"cost": 5, "tags": ["slow"]}

    def test_arc_description_and_annotations_parse_and_preserve(self):
        # given: a minimal net whose consume arc carries description + annotations
        net_dict: dict[str, Any] = {
            "name": "doc-arc",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [
                {
                    "from": {"place": "p"},
                    "to": {"transition": "tr"},
                    "consume": {"type": "t"},
                    "description": "the only arc",
                    "annotations": {"label": "a1"},
                }
            ],
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: the arc carries its description and annotations
        assert net.arcs[0].description == "the only arc"
        assert net.arcs[0].annotations == {"label": "a1"}

    def test_net_description_and_annotations_parse_and_preserve(self):
        # given: a minimal net with top-level description + annotations
        net_dict: dict[str, Any] = {
            "name": "doc-net",
            "description": "a documented net",
            "annotations": {"version": "1.0", "author": "test"},
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: the net carries its description and annotations
        assert net.description == "a documented net"
        assert net.annotations == {"version": "1.0", "author": "test"}

    # ── Optional / defaults ──────────────────────────────────────────

    def test_documentation_fields_default_to_none(self):
        # given: a net with no documentation fields anywhere
        net_dict: dict[str, Any] = {
            "name": "no-docs",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the net
        net = parse_net(net_dict)
        # then: every documentation field is None
        assert net.description is None
        assert net.annotations is None
        assert net.places[0].description is None
        assert net.places[0].annotations is None
        assert net.transitions[0].description is None
        assert net.transitions[0].annotations is None

    # ── Net purity ───────────────────────────────────────────────────

    def test_documentation_fields_do_not_affect_firing(self):
        # given: a minimal net with a single transition and a passthrough handler
        base: dict[str, Any] = {
            "name": "purity",
            "places": [
                {"name": "src", "accepts": ["task"]},
                {"name": "dst", "accepts": ["task"]},
            ],
            "transitions": [{"name": "move", "handler": "move"}],
            "arcs": [
                {
                    "from": {"place": "src"},
                    "to": {"transition": "move"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "move"},
                    "to": {"place": "dst"},
                    "produce": {"type": "task", "destination": "dst"},
                },
            ],
        }
        # and: the same net with documentation fields on every element
        doc_net_dict: dict[str, Any] = {
            "name": "purity",
            "description": "purity test net",
            "annotations": {"test": True},
            "places": [
                {
                    "name": "src",
                    "accepts": ["task"],
                    "description": "source",
                    "annotations": {"role": "input"},
                },
                {
                    "name": "dst",
                    "accepts": ["task"],
                    "description": "destination",
                    "annotations": {"role": "output"},
                },
            ],
            "transitions": [
                {
                    "name": "move",
                    "handler": "move",
                    "description": "mover",
                    "annotations": {"cost": 1},
                }
            ],
            "arcs": [
                {
                    "from": {"place": "src"},
                    "to": {"transition": "move"},
                    "consume": {"type": "task"},
                    "description": "consume arc",
                    "annotations": {"kind": "consume"},
                },
                {
                    "from": {"transition": "move"},
                    "to": {"place": "dst"},
                    "produce": {"type": "task", "destination": "dst"},
                    "description": "produce arc",
                    "annotations": {"kind": "produce"},
                },
            ],
        }
        # when: parsing both nets
        base_net = parse_net(base)
        doc_net = parse_net(doc_net_dict)
        # and: firing each with the same initial marking and handler

        def _passthrough(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            return {
                "status": "completed",
                "outputTokens": {"dst": inp["inputTokens"].get("src", [])},
                "error": None,
                "metadata": {},
            }

        reg = HandlerRegistry()
        reg.register_transition("move", _passthrough)
        engine = Engine(reg)

        tok = Token(type="task", data={"id": 1})
        marking = Marking({"src": [tok]})

        base_result, _ = engine.fire(base_net, marking, "move", attempt=1)
        doc_result, _ = engine.fire(doc_net, marking, "move", attempt=1)

        # then: both firings produce identical markings
        assert dict(base_result) == dict(doc_result)

    # ── Strictness preserved ─────────────────────────────────────────

    def test_rejects_unknown_element_property(self):
        # given: a net with an unknown property on a place
        net_dict: dict[str, Any] = {
            "name": "bad-prop",
            "places": [{"name": "p", "accepts": ["t"], "bogus": 1}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)

    def test_rejects_unknown_top_level_property_still(self):
        # given: a net with an unknown top-level property (not description/annotations)
        net_dict: dict[str, Any] = {
            "name": "bad-top",
            "places": [{"name": "p", "accepts": ["t"]}],
            "transitions": [{"name": "tr", "handler": "h"}],
            "arcs": [],
            "bogus": "x",
        }
        # when: parsing the invalid net
        # then: validation raises NetValidationError
        with pytest.raises(NetValidationError):
            parse_net(net_dict)


# ── Helpers ──────────────────────────────────────────────────────────────


def _find_consume_arc(net: Net, *, from_place: str, to_transition: str) -> Arc:
    """Find a consume arc (place → transition) by its endpoints."""
    for arc in net.arcs:
        if (
            arc.from_place == from_place
            and arc.to_transition == to_transition
            and arc.consume is not None
        ):
            return arc
    raise AssertionError(
        f"No consume arc from place '{from_place}' to transition '{to_transition}'"
    )


def _find_produce_arc(net: Net, *, from_transition: str, to_place: str) -> Arc:
    """Find a produce arc (transition → place) by its endpoints."""
    for arc in net.arcs:
        if (
            arc.from_transition == from_transition
            and arc.to_place == to_place
            and arc.produce is not None
        ):
            return arc
    raise AssertionError(
        f"No produce arc from transition '{from_transition}' to place '{to_place}'"
    )
