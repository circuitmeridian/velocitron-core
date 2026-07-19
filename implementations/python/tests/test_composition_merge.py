r"""Composition merge-engine tests (``[impl] composition-merge-engine``).

The tests cover the structural merge contract in ``spec/composition.md``:
alias qualification, port-place fusion, arc-endpoint rewriting, and re-exposing
unwired ports. Each test constructs nets programmatically through the public
schema types (``Net``, ``Place``, ``Transition``, ``Arc``, etc.) and exercises
``merge_nets`` or ``merge_composition`` from ``velocitron.composition``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal


from velocitron.composition import merge_composition, merge_nets
from velocitron.engine import Engine
from velocitron.parser import _validate_net, parse_composition, parse_net  # pyright: ignore[reportPrivateUsage]
from velocitron.registry import HandlerRegistry
from velocitron.schema import (
    Arc,
    Composition,
    ConsumePattern,
    Marking,
    Net,
    NetRef,
    Place,
    Port,
    ProduceTemplate,
    Token,
    Transition,
    Wire,
)
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput


# ── Shared helpers ──────────────────────────────────────────────────────


def _tok(t: str = "task", **data: Any) -> Token:
    """A minimal token of type ``t`` with payload ``data``."""
    return Token(type=t, data=dict(data))


def _place(
    name: str,
    accepts: list[str] | None = None,
    port: Port | None = None,
) -> Place:
    """A place with the given name, accepts, and optional port facet."""
    return Place(name=name, accepts=accepts or ["task"], port=port)


def _transition(
    name: str,
    handler: str = "noop",
    guard: str | None = None,
    priority: int | None = None,
) -> Transition:
    """A transition with the given name, handler ref, and optional guard/priority."""
    return Transition(name=name, handler=handler, guard=guard, priority=priority)


def _arc_consume(
    from_place: str,
    to_transition: str,
    type: str = "task",
) -> Arc:
    """A consume arc: place → transition."""
    return Arc(
        from_place=from_place,
        from_transition=None,
        to_place=None,
        to_transition=to_transition,
        consume=ConsumePattern(type=type, predicate=None, mode="consume"),
        produce=None,
    )


def _arc_produce(
    from_transition: str,
    to_place: str,
    type: str = "task",
    destination: str | None = None,
) -> Arc:
    """A produce arc: transition → place."""
    return Arc(
        from_place=None,
        from_transition=from_transition,
        to_place=to_place,
        to_transition=None,
        consume=None,
        produce=ProduceTemplate(type=type, destination=destination or to_place),
    )


def _net(
    name: str,
    places: list[Place],
    transitions: list[Transition],
    arcs: list[Arc],
    initial_marking: Marking | None = None,
) -> Net:
    """A net with the given name, places, transitions, arcs, and optional marking."""
    return Net(
        name=name,
        places=places,
        transitions=transitions,
        arcs=arcs,
        initial_marking=initial_marking,
    )


# ── Module-level nets for M1–M10 ────────────────────────────────────────

# A minimal producer net: work → produce → out (output port).
_PRODUCER = _net(
    name="producer",
    places=[
        _place("work"),
        _place("out", port=Port(direction="output", type="task")),
    ],
    transitions=[_transition("produce", handler="produce_handler")],
    arcs=[
        _arc_consume("work", "produce"),
        _arc_produce("produce", "out"),
    ],
)

# A minimal consumer net: in (input port) → consume → done.
_CONSUMER = _net(
    name="consumer",
    places=[
        _place("in", port=Port(direction="input", type="task")),
        _place("done"),
    ],
    transitions=[_transition("consume", handler="consume_handler")],
    arcs=[
        _arc_consume("in", "consume"),
        _arc_produce("consume", "done"),
    ],
)

# A second consumer for fan-out tests (M3).
_CONSUMER2 = _net(
    name="consumer2",
    places=[
        _place("in", port=Port(direction="input", type="task")),
        _place("done"),
    ],
    transitions=[_transition("consume", handler="consume_handler")],
    arcs=[
        _arc_consume("in", "consume"),
        _arc_produce("consume", "done"),
    ],
)

# A second producer for fan-in tests (M4).
_PRODUCER2 = _net(
    name="producer2",
    places=[
        _place("work"),
        _place("out", port=Port(direction="output", type="task")),
    ],
    transitions=[_transition("produce", handler="produce_handler")],
    arcs=[
        _arc_consume("work", "produce"),
        _arc_produce("produce", "out"),
    ],
)

# A net with a guard for M6 (handler/guard refs NOT qualified).
_GUARDED_NET = _net(
    name="guarded",
    places=[
        _place("src"),
        _place("dst"),
    ],
    transitions=[_transition("t", handler="my_handler", guard="my_guard")],
    arcs=[
        _arc_consume("src", "t"),
        _arc_produce("t", "dst"),
    ],
)

# A net with an unwired port for M2 (unwired ports retained).
_UNWIRED_PORT_NET = _net(
    name="extra",
    places=[
        _place("internal"),
        _place("spare", port=Port(direction="output", type="task")),
    ],
    transitions=[_transition("noop", handler="noop")],
    arcs=[
        _arc_consume("internal", "noop"),
        _arc_produce("noop", "spare"),
    ],
)


# ── M1: single net with no wires → disjoint union ──────────────────────


class TestMergeNetsDisjointUnion:
    """M1 — a single net with no wires yields a disjoint union: every
    place/transition/arc qualified as ``<alias>.<name>``, ports retained
    as boundary."""

    def test_single_net_no_wires_qualifies_all_names_and_retains_ports(self):
        # given: a producer net with an output port
        # and: no wires
        alias_to_net = {"prod": _PRODUCER}
        wires: list[Wire] = []

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: places are qualified
        place_names = {p.name for p in result.places}
        assert "prod.work" in place_names
        assert "prod.out" in place_names
        # and: transitions are qualified
        transition_names = {t.name for t in result.transitions}
        assert "prod.produce" in transition_names
        # and: arcs are rewritten to qualified endpoints
        arc_endpoints: set[tuple[str | None, str | None]] = set()
        for a in result.arcs:
            arc_endpoints.add((a.from_place, a.to_transition))
            arc_endpoints.add((a.from_transition, a.to_place))
        assert ("prod.work", "prod.produce") in arc_endpoints
        assert ("prod.produce", "prod.out") in arc_endpoints
        # and: the output port is retained as a boundary port
        out_place = next(p for p in result.places if p.name == "prod.out")
        assert out_place.port is not None
        assert out_place.port.direction == "output"
        assert out_place.port.type == "task"


# ── M2: one wire → fused place ─────────────────────────────────────────


class TestMergeNetsSingleWire:
    """M2 — one wire ``prod.out → cons.in`` fuses the two port places into
    a single shared place; arcs are rewritten; the fused place has
    ``port=None``; unwired ports are retained."""

    def test_single_wire_fuses_ports_and_rewrites_arcs(self):
        # given: a producer with output port "out" and a consumer with input port "in"
        alias_to_net = {"prod": _PRODUCER, "cons": _CONSUMER}
        # and: one wire connecting them
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the two port places are replaced by one fused place
        place_names = {p.name for p in result.places}
        assert "cons.in" not in place_names
        # and: the fused place is named after the source port (prod.out)
        assert "prod.out" in place_names
        # and: the fused place has port=None (no longer a boundary)
        fused = next(p for p in result.places if p.name == "prod.out")
        assert fused.port is None
        # and: the producer's produce arc points at the fused place
        prod_produce = next(
            a for a in result.arcs if a.from_transition == "prod.produce"
        )
        assert prod_produce.to_place == "prod.out"
        # and: the consumer's consume arc reads from the fused place
        cons_consume = next(a for a in result.arcs if a.to_transition == "cons.consume")
        assert cons_consume.from_place == "prod.out"
        # and: non-port places are qualified
        assert "prod.work" in place_names
        assert "cons.done" in place_names

    def test_unwired_ports_retained_as_boundary(self):
        # given: a producer, a consumer, and an extra net with an unwired port
        alias_to_net = {
            "prod": _PRODUCER,
            "cons": _CONSUMER,
            "extra": _UNWIRED_PORT_NET,
        }
        # and: only the prod→cons wire (extra.spare is unwired)
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the unwired port is retained as a boundary port
        spare = next(p for p in result.places if p.name == "extra.spare")
        assert spare.port is not None
        assert spare.port.direction == "output"
        assert spare.port.type == "task"
        # and: the wired ports are fused (not boundary)
        fused = next(p for p in result.places if p.name == "prod.out")
        assert fused.port is None


# ── M3: fan-out ────────────────────────────────────────────────────────


class TestMergeNetsFanOut:
    """M3 — fan-out ``out → in1, in2`` collapses to one fused place; both
    consumers' consume arcs read from it."""

    def test_fan_out_collapses_to_one_fused_place(self):
        # given: one producer, two consumers
        alias_to_net = {"prod": _PRODUCER, "cons1": _CONSUMER, "cons2": _CONSUMER2}
        # and: fan-out wires
        wires = [
            Wire(from_net="prod", from_port="out", to_net="cons1", to_port="in"),
            Wire(from_net="prod", from_port="out", to_net="cons2", to_port="in"),
        ]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: one fused place (not three separate port places)
        place_names = {p.name for p in result.places}
        assert "prod.out" in place_names
        assert "cons1.in" not in place_names
        assert "cons2.in" not in place_names
        # and: both consumers' consume arcs read from the fused place
        cons1_consume = next(
            a for a in result.arcs if a.to_transition == "cons1.consume"
        )
        assert cons1_consume.from_place == "prod.out"
        cons2_consume = next(
            a for a in result.arcs if a.to_transition == "cons2.consume"
        )
        assert cons2_consume.from_place == "prod.out"


# ── M4: fan-in ─────────────────────────────────────────────────────────


class TestMergeNetsFanIn:
    """M4 — fan-in ``out1, out2 → in`` collapses to one fused place; both
    producers' produce arcs deposit into it."""

    def test_fan_in_collapses_to_one_fused_place(self):
        # given: two producers, one consumer
        alias_to_net = {"prod1": _PRODUCER, "prod2": _PRODUCER2, "cons": _CONSUMER}
        # and: fan-in wires
        wires = [
            Wire(from_net="prod1", from_port="out", to_net="cons", to_port="in"),
            Wire(from_net="prod2", from_port="out", to_net="cons", to_port="in"),
        ]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: one fused place named after sorted source ports
        place_names = {p.name for p in result.places}
        assert "prod1.out__prod2.out" in place_names
        assert "prod1.out" not in place_names
        assert "prod2.out" not in place_names
        assert "cons.in" not in place_names
        # and: both producers' produce arcs deposit into the fused place
        prod1_produce = next(
            a for a in result.arcs if a.from_transition == "prod1.produce"
        )
        assert prod1_produce.to_place == "prod1.out__prod2.out"
        prod2_produce = next(
            a for a in result.arcs if a.from_transition == "prod2.produce"
        )
        assert prod2_produce.to_place == "prod1.out__prod2.out"
        # and: the consumer's consume arc reads from the fused place
        cons_consume = next(a for a in result.arcs if a.to_transition == "cons.consume")
        assert cons_consume.from_place == "prod1.out__prod2.out"


# ── Fused-place annotations: fusion tag + member carry-through ──────────


def _port_only_net(
    net_name: str,
    port_name: str,
    direction: Literal["input", "output"],
    annotations: dict[str, Any] | None = None,
) -> Net:
    """A net that is nothing but one (optionally annotated) port place —
    the minimal fixture for the annotation-merge constraint, which resolves
    only the wired ports' names, directions, and annotations."""
    return _net(
        name=net_name,
        places=[
            Place(
                name=port_name,
                accepts=["task"],
                port=Port(direction=direction, type="task"),
                annotations=annotations,
            )
        ],
        transitions=[],
        arcs=[],
    )


class TestMergeNetsFusedPlaceAnnotations:
    """Consolidated-backlog item #11 — every fused place the merge creates
    carries ``annotations.fusion = true`` (so the viz fusion-place styling
    triggers on a merged net), and the member ports' own annotations carry
    through onto the fused place: output (source) ports merge before input
    ports, each group in sorted qualified-name order, the earliest member
    wins on conflicting keys, and the merge sets ``fusion: true`` last
    (overriding any member value)."""

    def test_fused_place_is_tagged_with_fusion_annotation(self):
        # given: a producer and consumer with un-annotated ports
        alias_to_net = {"prod": _PRODUCER, "cons": _CONSUMER}
        # and: one wire fusing prod.out with cons.in
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the fused place carries the fusion tag
        fused = next(p for p in result.places if p.name == "prod.out")
        assert fused.annotations is not None
        assert fused.annotations["fusion"] is True
        # and: non-fused places gain no annotations
        work = next(p for p in result.places if p.name == "prod.work")
        assert work.annotations is None

    def test_member_port_annotations_carry_through_source_wins(self):
        # given: an annotated output port and an input port carrying a
        # conflicting "team" key
        alias_to_net = {
            "prod": _port_only_net(
                "producer", "out", "output", {"team": "prod-team", "tier": 1}
            ),
            "cons": _port_only_net(
                "consumer", "in", "input", {"team": "cons-team", "sla": "1h"}
            ),
        }
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the fused place merges both members' annotations; the
        # output (source) port wins the conflicting "team" key even though
        # "cons.in" sorts before "prod.out", and the fusion tag is set
        fused = next(p for p in result.places if p.name == "prod.out")
        assert fused.annotations == {
            "team": "prod-team",
            "tier": 1,
            "sla": "1h",
            "fusion": True,
        }

    def test_fusion_tag_overrides_a_member_fusion_annotation(self):
        # given: an output port perversely annotated fusion=False
        alias_to_net = {
            "prod": _port_only_net("producer", "out", "output", {"fusion": False}),
            "cons": _port_only_net("consumer", "in", "input"),
        }
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the merge's fusion tag wins over the member's value
        fused = next(p for p in result.places if p.name == "prod.out")
        assert fused.annotations is not None
        assert fused.annotations["fusion"] is True


# ── M5: produce template destination rewritten ─────────────────────────


class TestMergeNetsProduceTemplate:
    """M5 — the produce template ``destination`` is rewritten to the fused
    name, not the original port name."""

    def test_produce_template_destination_rewritten_to_fused_name(self):
        # given: a producer and consumer with one wire
        alias_to_net = {"prod": _PRODUCER, "cons": _CONSUMER}
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the producer's produce arc destination is the fused name
        prod_produce = next(
            a for a in result.arcs if a.from_transition == "prod.produce"
        )
        assert prod_produce.produce is not None
        assert prod_produce.produce.destination == "prod.out"
        # and: the consumer's produce arc destination is still qualified (not a port)
        cons_produce = next(
            a for a in result.arcs if a.from_transition == "cons.consume"
        )
        assert cons_produce.produce is not None
        assert cons_produce.produce.destination == "cons.done"


# ── M6: handler/guard refs NOT qualified ────────────────────────────────


class TestMergeNetsHandlerGuardRefs:
    """M6 — handler and guard refs pass through unchanged (handlers are
    global registry names, not net-local; ADR 0003)."""

    def test_handler_and_guard_refs_not_qualified(self):
        # given: a net with a handler and guard
        alias_to_net = {"g": _GUARDED_NET}
        wires: list[Wire] = []

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the transition name is qualified
        t = next(t for t in result.transitions if t.name == "g.t")
        # and: the handler ref is unchanged
        assert t.handler == "my_handler"
        # and: the guard ref is unchanged
        assert t.guard == "my_guard"


# ── M7: composed Net passes _validate_net ───────────────────────────────


class TestMergeNetsValidation:
    """M7 — the composed ``Net`` passes ``_validate_net`` (verifiable as
    one net)."""

    def test_composed_net_passes_validate_net(self):
        # given: a producer and consumer with one wire
        alias_to_net = {"prod": _PRODUCER, "cons": _CONSUMER}
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the composed net passes structural validation
        _validate_net(result)  # does not raise


# ── M8: composed net is runnable via Engine ─────────────────────────────


class TestMergeNetsRunnable:
    """M8 — the composed net is runnable end-to-end via ``Engine``: a token
    deposited by the producer's transition is consumable by the consumer's
    transition through the fused place."""

    def test_composed_net_runnable_end_to_end(self):
        # given: a producer and consumer with one wire
        alias_to_net = {"prod": _PRODUCER, "cons": _CONSUMER}
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        merged = merge_nets(alias_to_net, wires)

        # and: an engine with handlers that pass tokens through
        registry = HandlerRegistry()

        def produce_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            # Deposit a task token into the output port (fused place).
            return {
                "status": "completed",
                "outputTokens": {"prod.out": [_tok("task", src="producer")]},
                "error": None,
                "metadata": {},
            }

        def consume_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            # Consume from the fused place, deposit into done.
            return {
                "status": "completed",
                "outputTokens": {"cons.done": [_tok("task", src="consumer")]},
                "error": None,
                "metadata": {},
            }

        registry.register_transition("produce_handler", produce_handler)
        registry.register_transition("consume_handler", consume_handler)

        engine = Engine(registry)

        # and: an initial marking with a token in prod.work
        initial = Marking({"prod.work": [_tok("task")]})

        # when: running the engine
        final = engine.run(merged, initial, max_steps=10)

        # then: the token flowed through the fused place to cons.done
        assert len(final.get("cons.done", [])) == 1
        assert final["cons.done"][0].data.get("src") == "consumer"
        # and: the fused place is empty (token was consumed)
        assert len(final.get("prod.out", [])) == 0


# ── M9: initial markings compose ────────────────────────────────────────


class TestMergeNetsInitialMarking:
    """M9 — initial markings compose: qualified and fused keys merged."""

    def test_initial_markings_qualified_and_fused(self):
        # given: a producer with an initial marking on "work"
        prod = _net(
            name="producer",
            places=[
                _place("work"),
                _place("out", port=Port(direction="output", type="task")),
            ],
            transitions=[_transition("produce", handler="produce_handler")],
            arcs=[
                _arc_consume("work", "produce"),
                _arc_produce("produce", "out"),
            ],
            initial_marking=Marking({"work": [_tok("task", id=1)]}),
        )
        # and: a consumer with an initial marking on "done"
        cons = _net(
            name="consumer",
            places=[
                _place("in", port=Port(direction="input", type="task")),
                _place("done"),
            ],
            transitions=[_transition("consume", handler="consume_handler")],
            arcs=[
                _arc_consume("in", "consume"),
                _arc_produce("consume", "done"),
            ],
            initial_marking=Marking({"done": [_tok("task", id=2)]}),
        )
        alias_to_net = {"prod": prod, "cons": cons}
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the composed initial marking has qualified keys
        assert result.initial_marking is not None
        marking = result.initial_marking
        assert "prod.work" in marking
        assert marking["prod.work"][0].data.get("id") == 1
        # and: the consumer's non-port marking is qualified
        assert "cons.done" in marking
        assert marking["cons.done"][0].data.get("id") == 2
        # and: the fused place has no initial tokens (neither side marked it)
        assert len(marking.get("prod.out", [])) == 0

    def test_fused_place_merges_tokens_from_both_sides(self):
        # given: a producer with initial tokens on its output port
        prod = _net(
            name="producer",
            places=[
                _place("work"),
                _place("out", port=Port(direction="output", type="task")),
            ],
            transitions=[_transition("produce", handler="produce_handler")],
            arcs=[
                _arc_consume("work", "produce"),
                _arc_produce("produce", "out"),
            ],
            initial_marking=Marking({"out": [_tok("task", side="prod")]}),
        )
        # and: a consumer with initial tokens on its input port
        cons = _net(
            name="consumer",
            places=[
                _place("in", port=Port(direction="input", type="task")),
                _place("done"),
            ],
            transitions=[_transition("consume", handler="consume_handler")],
            arcs=[
                _arc_consume("in", "consume"),
                _arc_produce("consume", "done"),
            ],
            initial_marking=Marking({"in": [_tok("task", side="cons")]}),
        )
        alias_to_net = {"prod": prod, "cons": cons}
        wires = [Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")]

        # when: merging
        result = merge_nets(alias_to_net, wires)

        # then: the fused place has tokens from both sides
        assert result.initial_marking is not None
        fused_tokens = result.initial_marking.get("prod.out", [])
        assert len(fused_tokens) == 2
        sides = {t.data.get("side") for t in fused_tokens}
        assert sides == {"prod", "cons"}


# ── M10: merge_composition wrapper ─────────────────────────────────────


class TestMergeComposition:
    """M10 — ``merge_composition`` uses ``parsed_nets`` when present and
    matches ``merge_nets`` on the same alias_to_net; re-parse fallback
    path also matches."""

    def test_merge_composition_uses_parsed_nets(self, tmp_path: Path):
        # given: net files on disk
        prod_path = tmp_path / "producer.json"
        cons_path = tmp_path / "consumer.json"
        prod_path.write_text(
            json.dumps(
                {
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
            )
        )
        cons_path.write_text(
            json.dumps(
                {
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
            )
        )

        # and: a composition document
        comp_dict = {
            "nets": [
                {"ref": str(prod_path), "alias": "prod"},
                {"ref": str(cons_path), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                },
            ],
        }

        # when: parsing and merging via merge_composition
        composition = parse_composition(comp_dict)
        result = merge_composition(composition)

        # then: the result matches merge_nets on the same alias_to_net
        alias_to_net = {
            "prod": parse_net(str(prod_path)),
            "cons": parse_net(str(cons_path)),
        }
        expected = merge_nets(alias_to_net, composition.wires)
        assert result == expected

    def test_merge_composition_reparse_fallback(self, tmp_path: Path):
        # given: net files on disk
        prod_path = tmp_path / "producer.json"
        cons_path = tmp_path / "consumer.json"
        prod_path.write_text(
            json.dumps(
                {
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
            )
        )
        cons_path.write_text(
            json.dumps(
                {
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
            )
        )

        # and: a directly-constructed Composition (no parsed_nets)
        composition = Composition(
            nets=[
                NetRef(ref=str(prod_path), alias="prod"),
                NetRef(ref=str(cons_path), alias="cons"),
            ],
            wires=[Wire(from_net="prod", from_port="out", to_net="cons", to_port="in")],
        )

        # when: merging via merge_composition (re-parse fallback)
        result = merge_composition(composition)

        # then: the result matches merge_nets on the same alias_to_net
        alias_to_net = {
            "prod": parse_net(str(prod_path)),
            "cons": parse_net(str(cons_path)),
        }
        expected = merge_nets(alias_to_net, composition.wires)
        assert result == expected
