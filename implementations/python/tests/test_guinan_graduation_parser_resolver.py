"""Slice 12 graduation contracts for existing parser, resolver, and merge behavior."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

from velocitron.composition import merge_composition
from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.parser import parse_composition, parse_net
from velocitron.schema import Arc, Composition, Net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT
    / "examples"
    / "capability-ladder"
    / "12-guinan-graduation-fragment"
)
_CADENCE_DSL = _FIXTURE_ROOT / "graduation_cadence.petrinet"
_CADENCE_JSON = _FIXTURE_ROOT / "graduation_cadence.json"
_CURATE_DSL = _FIXTURE_ROOT / "graduation_curate.petrinet"
_CURATE_JSON = _FIXTURE_ROOT / "graduation_curate.json"
_SPEAKS_DSL = _FIXTURE_ROOT / "graduation_speaks.petrinet"
_SPEAKS_JSON = _FIXTURE_ROOT / "graduation_speaks.json"
_COMPOSITION_DSL = _FIXTURE_ROOT / "guinan_graduation.petrinet"
_COMPOSITION_JSON = _FIXTURE_ROOT / "guinan_graduation.json"
_NET_PAIRS = [
    (_CADENCE_DSL, _CADENCE_JSON),
    (_CURATE_DSL, _CURATE_JSON),
    (_SPEAKS_DSL, _SPEAKS_JSON),
]


def _document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _compile(path: Path) -> dict[str, Any]:
    return compile_petrinet_text(path.read_text(encoding="utf-8"), str(path))


def _dsl_net_loader(ref: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / ref
    return _compile(path)


def _merged_from_dsl() -> Net:
    composition = cast(Any, parse_composition)(
        _compile(_COMPOSITION_DSL),
        origin=_FIXTURE_ROOT,
        net_loader=_dsl_net_loader,
    )
    return merge_composition(composition)


def _merged_from_json() -> Net:
    composition = cast(Any, parse_composition)(
        _document(_COMPOSITION_JSON), origin=_FIXTURE_ROOT
    )
    return merge_composition(composition)


def _arc_shape(arc: Arc) -> tuple[str, str, str, str]:
    if arc.consume is not None:
        assert arc.from_place is not None and arc.to_transition is not None
        return (
            arc.from_place,
            arc.to_transition,
            arc.consume.mode,
            arc.consume.type,
        )
    assert arc.produce is not None
    assert arc.from_transition is not None and arc.to_place is not None
    return (
        arc.from_transition,
        arc.to_place,
        "produce",
        arc.produce.type,
    )


def test_each_constituent_independently_compiles_and_parses_exactly() -> None:
    """Every standalone net is complete before composition supplies any context."""
    parsed: dict[str, Net] = {}
    for dsl_path, json_path in _NET_PAIRS:
        expected = _document(json_path)
        actual = _compile(dsl_path)
        assert actual == expected
        assert parse_net(actual) == parse_net(expected)
        parsed[dsl_path.stem] = parse_net(actual)

    cadence = parsed["graduation_cadence"]
    assert [place.name for place in cadence.places] == [
        "tick_latch",
        "clock",
        "refresh_due_out",
    ]
    assert [
        (transition.name, transition.handler) for transition in cadence.transitions
    ] == [("on_tick", "on_tick@cadence")]
    assert [_arc_shape(arc) for arc in cadence.arcs] == [
        ("tick_latch", "on_tick", "consume", "tick_latch"),
        ("on_tick", "tick_latch", "produce", "tick_latch"),
        ("clock", "on_tick", "read", "clock"),
        ("on_tick", "refresh_due_out", "produce", "refresh_due"),
    ]
    timer = cadence.transitions[0].timer
    assert timer is not None
    assert (timer.clock, timer.cel, timer.bind) == (
        "clock",
        "clock.now >= latch.fired_at + latch.cadence_s",
        {"latch": "tick_latch"},
    )
    assert cadence.initial_marking is not None
    expected_initial_marking = parse_net(_document(_CADENCE_JSON)).initial_marking
    assert expected_initial_marking is not None
    assert cadence.initial_marking == expected_initial_marking

    curate = parsed["graduation_curate"]
    assert [(place.name, place.port) for place in curate.places] == [
        ("refresh_due_in", curate.places[0].port),
        ("speak_request", curate.places[1].port),
    ]
    assert curate.places[0].port is not None
    assert (curate.places[0].port.direction, curate.places[0].port.type) == (
        "input",
        "refresh_due",
    )
    assert curate.places[1].port is not None
    assert (curate.places[1].port.direction, curate.places[1].port.type) == (
        "output",
        "speak_req",
    )
    assert [
        (transition.name, transition.handler) for transition in curate.transitions
    ] == [("curate", "curate@curate")]
    assert [_arc_shape(arc) for arc in curate.arcs] == [
        ("refresh_due_in", "curate", "consume", "refresh_due"),
        ("curate", "speak_request", "produce", "speak_req"),
    ]

    speaks = parsed["graduation_speaks"]
    assert [place.name for place in speaks.places] == [
        "request_in",
        "in_flight",
        "work",
        "utterance_out",
    ]
    assert [
        (transition.name, transition.handler) for transition in speaks.transitions
    ] == [
        ("start", "start@speaks"),
        ("finish", "finish@speaks"),
    ]
    assert [_arc_shape(arc) for arc in speaks.arcs] == [
        ("request_in", "start", "consume", "speak_req"),
        ("start", "in_flight", "produce", "speak_token"),
        ("in_flight", "start", "inhibit", "speak_token"),
        ("work", "start", "inhibit", "speak_work"),
        ("start", "work", "produce", "speak_work"),
        ("work", "finish", "consume", "speak_work"),
        ("finish", "utterance_out", "produce", "utterance"),
        ("in_flight", "finish", "consume", "speak_token"),
    ]


def test_composition_resolution_is_exact_and_refs_stay_relative() -> None:
    """The ordinary Slice 10 use/wire model resolves all exact typed endpoints."""
    actual = _compile(_COMPOSITION_DSL)
    expected = _document(_COMPOSITION_JSON)
    authored_expected = deepcopy(expected)
    for item in authored_expected["nets"]:
        item["ref"] = item["ref"].replace(".json", ".petrinet")

    assert actual == authored_expected
    assert set(actual) == {"nets", "wires"}
    assert actual["nets"] == [
        {"ref": "graduation_cadence.petrinet", "alias": "cadence"},
        {"ref": "graduation_curate.petrinet", "alias": "curate"},
        {"ref": "graduation_speaks.petrinet", "alias": "speaks"},
    ]
    assert actual["wires"] == [
        {
            "from": {"net": "cadence", "port": "refresh_due_out"},
            "to": {"net": "curate", "port": "refresh_due_in"},
        },
        {
            "from": {"net": "curate", "port": "speak_request"},
            "to": {"net": "speaks", "port": "request_in"},
        },
    ]
    resolved = cast(
        Composition,
        cast(Any, parse_composition)(
            actual, origin=_FIXTURE_ROOT, net_loader=_dsl_net_loader
        ),
    )
    parsed_nets = resolved.parsed_nets
    assert parsed_nets is not None
    assert list(parsed_nets) == ["cadence", "curate", "speaks"]


def test_merge_has_exact_fusions_topology_and_reexposed_output() -> None:
    """Two wires fuse places directly while the sole unwired output stays exposed."""
    merged = _merged_from_dsl()
    assert [place.name for place in merged.places] == [
        "cadence.tick_latch",
        "cadence.clock",
        "speaks.in_flight",
        "speaks.work",
        "speaks.utterance_out",
        "cadence.refresh_due_out",
        "curate.speak_request",
    ]
    assert [transition.name for transition in merged.transitions] == [
        "cadence.on_tick",
        "curate.curate",
        "speaks.start",
        "speaks.finish",
    ]
    assert len(merged.arcs) == 14
    assert [_arc_shape(arc) for arc in merged.arcs] == [
        ("cadence.tick_latch", "cadence.on_tick", "consume", "tick_latch"),
        ("cadence.on_tick", "cadence.tick_latch", "produce", "tick_latch"),
        ("cadence.clock", "cadence.on_tick", "read", "clock"),
        ("cadence.on_tick", "cadence.refresh_due_out", "produce", "refresh_due"),
        ("cadence.refresh_due_out", "curate.curate", "consume", "refresh_due"),
        ("curate.curate", "curate.speak_request", "produce", "speak_req"),
        ("curate.speak_request", "speaks.start", "consume", "speak_req"),
        ("speaks.start", "speaks.in_flight", "produce", "speak_token"),
        ("speaks.in_flight", "speaks.start", "inhibit", "speak_token"),
        ("speaks.work", "speaks.start", "inhibit", "speak_work"),
        ("speaks.start", "speaks.work", "produce", "speak_work"),
        ("speaks.work", "speaks.finish", "consume", "speak_work"),
        ("speaks.finish", "speaks.utterance_out", "produce", "utterance"),
        ("speaks.in_flight", "speaks.finish", "consume", "speak_token"),
    ]

    places = {place.name: place for place in merged.places}
    for fused_name in ["cadence.refresh_due_out", "curate.speak_request"]:
        assert places[fused_name].port is None
        assert (places[fused_name].annotations or {}).get("fusion") is True
    assert "curate.refresh_due_in" not in places
    assert "speaks.request_in" not in places
    assert places["speaks.utterance_out"].port is not None
    assert (
        places["speaks.utterance_out"].port.direction,
        places["speaks.utterance_out"].port.type,
    ) == ("output", "utterance")

    timer = merged.transitions[0].timer
    assert timer is not None
    assert (timer.clock, timer.bind) == (
        "cadence.clock",
        {"latch": "cadence.tick_latch"},
    )
    assert merged.initial_marking is not None
    assert set(merged.initial_marking) == {"cadence.clock", "cadence.tick_latch"}


def test_net_and_legacy_composition_json_are_canonical_fixed_points() -> None:
    """Canonical JSON-to-DSL emission reuses only established syntax and refs."""
    for dsl_path, json_path in _NET_PAIRS:
        expected = _document(json_path)
        emitted = emit_petrinet(expected)
        reparsed = compile_petrinet_text(emitted, str(dsl_path))
        assert reparsed == expected
        assert emit_petrinet(reparsed) == emitted

    speaks_text = emit_petrinet(_document(_SPEAKS_JSON))
    cadence_text = emit_petrinet(_document(_CADENCE_JSON))
    assert speaks_text.count("->0") == 2
    assert "->?" in cadence_text
    for handler in [
        "on_tick@cadence",
        "curate@curate",
        "start@speaks",
        "finish@speaks",
    ]:
        emitted = "\n".join(emit_petrinet(_document(path)) for _, path in _NET_PAIRS)
        assert f'handler "{handler}"' in emitted

    expected_composition = _document(_COMPOSITION_JSON)
    canonical_composition = emit_petrinet(expected_composition)
    assert canonical_composition.startswith("composition composition\n")
    assert [
        line for line in canonical_composition.splitlines() if line.startswith("use ")
    ] == [
        'use "graduation_cadence.json" as cadence',
        'use "graduation_curate.json" as curate',
        'use "graduation_speaks.json" as speaks',
    ]
    assert (
        compile_petrinet_text(canonical_composition, str(_COMPOSITION_DSL))
        == expected_composition
    )


def test_dsl_and_json_origins_merge_to_the_same_semantic_model() -> None:
    """Independent authoring forms preserve ports, modes, timers, and opaque refs."""
    dsl_merged = _merged_from_dsl()
    json_merged = _merged_from_json()
    assert dsl_merged == json_merged
    assert [transition.handler for transition in dsl_merged.transitions] == [
        "on_tick@cadence",
        "curate@curate",
        "start@speaks",
        "finish@speaks",
    ]
