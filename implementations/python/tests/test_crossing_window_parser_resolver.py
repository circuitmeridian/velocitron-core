"""Red grammar, lowering, and canonical contracts for Slice 05 Crossing Window."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "05-crossing-window"
)
_JSON_PATH = _FIXTURE_ROOT / "crossing-window.json"
_CONFORMANCE_ROOT = (
    _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "05-crossing-window"
)
_IR_SCHEMA_PATH = _REPOSITORY_ROOT / "spec" / "petrinet-contribution-ir.schema.json"
_SOURCE_CASE_PATH = _CONFORMANCE_ROOT / "crossing-window.source.json"
_IR_CASE_PATH = _CONFORMANCE_ROOT / "crossing-window.contribution-ir.json"
_NET_CASE_PATH = _CONFORMANCE_ROOT / "crossing-window.net.json"

_SOURCE = """\
net crossing_window "Read a sample window unless an alert is already open"

@arrival: (arrival) -arrival-> [detect_crossing]
@latest: (latest_sample) -sample->? [detect_crossing]
@previous: (previous_sample) -sample->? [detect_crossing]
@no_open_alert: (open_alert) -alert->0 [detect_crossing]
[detect_crossing] -alert-> (alert_out)

[detect_crossing] handler "detect_crossing@ingest"

@arrival predicate cel "source == \\"co2mon\\""

marking initial (arrival) <- $arrival
marking initial (latest_sample) <- $latest
marking initial (previous_sample) <- $previous

$arrival: arrival {"source": "co2mon"}
$latest: sample {"source": "co2mon", "ppm": 1200}
$previous: sample {"source": "co2mon", "ppm": 900}
"""

_EXPECTED_CANONICAL = """\
net crossing_window "Read a sample window unless an alert is already open"

@arc_0: (arrival) -arrival-> [detect_crossing]
(latest_sample) -sample->? [detect_crossing]
(previous_sample) -sample->? [detect_crossing]
(open_alert) -alert->0 [detect_crossing]
[detect_crossing] -alert-> (alert_out)

[detect_crossing] handler "detect_crossing@ingest"

@arc_0 predicate cel "source == \\"co2mon\\""

marking initial (arrival) <- $token_0
marking initial (latest_sample) <- $token_1
marking initial (previous_sample) <- $token_2
$token_0: arrival {"source":"co2mon"}
$token_1: sample {"ppm":1200,"source":"co2mon"}
$token_2: sample {"ppm":900,"source":"co2mon"}
"""


def _fixture_document() -> dict[str, object]:
    return json.loads(_JSON_PATH.read_text(encoding="utf-8"))


def _conformance_document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_fresh_crossing_ir_matches_schema_and_frozen_portable_contract() -> None:
    """Given the conformance source, lowering reproduces the schema-valid frozen IR."""
    # given: the portable source tuple and independently frozen IR/schema artifacts
    source_case = _conformance_document(_SOURCE_CASE_PATH)
    expected = _conformance_document(_IR_CASE_PATH)
    schema = _conformance_document(_IR_SCHEMA_PATH)

    # when: the source is freshly lowered rather than loading the expected artifact
    actual = lower_petrinet_text(source_case["text"], source_case["sourceId"])

    # then: it is valid portable IR and exactly reproduces the frozen corpus
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(actual)  # pyright: ignore[reportUnknownMemberType]
    assert actual == expected

    # and: only ordinary consume carries transition-name provenance
    declarations = [
        cast(dict[str, Any], contribution["value"])
        for contribution in actual["contributions"]
        if contribution["kind"] == "arc.declare"
    ]
    assert [
        (declaration["mode"], set(declaration)) for declaration in declarations
    ] == [
        (
            "consume",
            {"color", "from", "mode", "to", "transitionNameSpan"},
        ),
        ("read", {"color", "from", "mode", "to"}),
        ("read", {"color", "from", "mode", "to"}),
        ("inhibit", {"color", "from", "mode", "to"}),
        ("produce", {"color", "from", "mode", "to"}),
    ]


def test_frozen_crossing_ir_resolves_to_exact_frozen_net() -> None:
    """Given schema-valid frozen IR, resolution accepts it without source re-lowering."""
    # given: the portable contribution artifact and its exact expected core net
    portable = _conformance_document(_IR_CASE_PATH)
    expected = _conformance_document(_NET_CASE_PATH)

    # when: a consumer imports and resolves the frozen portable artifact
    actual = resolve_contribution_ir(portable)

    # then: all topology, modes, facts, and ordering match the frozen net
    assert actual == expected


@pytest.mark.parametrize(("mode", "line"), [("read", 4), ("inhibit", 6)])
def test_frozen_ir_rejects_transition_name_span_on_exceptional_input(
    mode: str, line: int
) -> None:
    """Given read/inhibit IR, transitionNameSpan remains forbidden and strict."""
    # given: a frozen exceptional declaration extended with consume-only provenance
    portable = deepcopy(_conformance_document(_IR_CASE_PATH))
    declarations = [
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "arc.declare"
    ]
    consume = next(
        contribution
        for contribution in declarations
        if contribution["value"]["mode"] == "consume"
    )
    exceptional = next(
        contribution
        for contribution in declarations
        if contribution["value"]["mode"] == mode
    )
    exceptional["value"]["transitionNameSpan"] = deepcopy(
        consume["value"]["transitionNameSpan"]
    )

    # when: schema and resolver inspect the extended imported artifact
    schema = _conformance_document(_IR_SCHEMA_PATH)
    assert not Draft202012Validator(schema).is_valid(portable)  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    # then: resolution rejects the extra field at the exceptional declaration
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid consumed arc declaration"
    assert diagnostic.span.start.line == line


def test_frozen_ir_rejects_consume_without_transition_name_span() -> None:
    """Given ordinary consume IR, transitionNameSpan remains required and strict."""
    # given: the frozen consume declaration with its required provenance removed
    portable = deepcopy(_conformance_document(_IR_CASE_PATH))
    consume = next(
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "arc.declare"
        and contribution["value"]["mode"] == "consume"
    )
    del consume["value"]["transitionNameSpan"]

    # when: schema and resolver inspect the incomplete imported artifact
    schema = _conformance_document(_IR_SCHEMA_PATH)
    assert not Draft202012Validator(schema).is_valid(portable)  # pyright: ignore[reportUnknownMemberType]
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    # then: resolution rejects the missing field at the consume declaration
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid consumed arc declaration"
    assert diagnostic.span.start.line == 3


def test_exceptional_operators_longest_match_and_lower_in_source_order() -> None:
    """Given shared arrow prefixes, exceptional operators lower as single input modes."""
    # given: ordinary consume, two reads, one inhibit, and one produce in exact order

    # when: ANTLR lexing and portable lowering process the Crossing Window source
    portable = lower_petrinet_text(_SOURCE, "crossing-window.petrinet")
    declarations = [
        cast(dict[str, Any], contribution["value"])
        for contribution in portable["contributions"]
        if contribution["kind"] == "arc.declare"
    ]

    # then: ->? and ->0 are longest-matched operators, not -> plus punctuation;
    # and: the default consume mode and all five arc positions remain explicit in IR
    assert [declaration["mode"] for declaration in declarations] == [
        "consume",
        "read",
        "read",
        "inhibit",
        "produce",
    ]
    assert [
        (declaration["from"]["name"], declaration["to"]["name"])
        for declaration in declarations
    ] == [
        ("arrival", "detect_crossing"),
        ("latest_sample", "detect_crossing"),
        ("previous_sample", "detect_crossing"),
        ("open_alert", "detect_crossing"),
        ("detect_crossing", "alert_out"),
    ]


def test_crossing_window_resolution_pins_modes_omissions_and_binding_order() -> None:
    """Given exceptional inputs, resolution emits exact core modes without defaults."""
    # given: the authoritative Crossing Window source

    # when: public compilation resolves it to core JSON
    actual = compile_petrinet_text(_SOURCE, "crossing-window.petrinet")
    arcs = cast(list[dict[str, Any]], actual["arcs"])

    # then: source order controls the deterministic binding and produce order
    assert [(arc["from"], arc["to"]) for arc in arcs] == [
        ({"place": "arrival"}, {"transition": "detect_crossing"}),
        ({"place": "latest_sample"}, {"transition": "detect_crossing"}),
        ({"place": "previous_sample"}, {"transition": "detect_crossing"}),
        ({"place": "open_alert"}, {"transition": "detect_crossing"}),
        ({"transition": "detect_crossing"}, {"place": "alert_out"}),
    ]
    # and: default consume/weight are omitted, while exceptional modes are explicit
    assert arcs[0]["consume"] == {
        "type": "arrival",
        "predicate": {"cel": 'source == "co2mon"'},
    }
    assert arcs[1]["consume"] == {"type": "sample", "mode": "read"}
    assert arcs[2]["consume"] == {"type": "sample", "mode": "read"}
    assert arcs[3]["consume"] == {"type": "alert", "mode": "inhibit"}
    assert set(arcs[3]["consume"]) == {"type", "mode"}
    assert arcs[4]["produce"] == {"type": "alert", "destination": "alert_out"}
    assert actual["transitions"] == [
        {"name": "detect_crossing", "handler": "detect_crossing@ingest"}
    ]


@pytest.mark.parametrize(
    ("operator", "color", "place", "column"),
    [
        ("->?", "sample", "snapshot", 26),
        ("->0", "alert", "alert_out", 25),
    ],
)
def test_exceptional_input_operators_reject_transition_to_place_at_operator_span(
    operator: str, color: str, place: str, column: int
) -> None:
    """Given an output use of an input mode, PN101 targets the authored operator."""
    # given: an exceptional consume-mode operator authored from transition to place
    kind = "read" if operator == "->?" else "inhibit"
    source_name = f"crossing_window_invalid_{kind}.petrinet"
    source = (
        f"net crossing_window_invalid_{kind}\n\n"
        f"[detect_crossing] -{color}{operator} ({place})\n"
    )

    # when: syntax parsing sees the invalid direction before semantic resolution
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, source_name)

    # then: the diagnostic pins the complete longest-matched operator and exact location
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN101"
    assert diagnostic.message == (
        f"{operator} is only allowed on place-to-transition arcs"
    )
    assert diagnostic.span.source == source_name
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (3, column)
    assert (diagnostic.span.end.line, diagnostic.span.end.column) == (
        3,
        column + len(operator),
    )


def test_crossing_json_emits_topology_native_modes_as_a_canonical_fixed_point() -> None:
    """Given paired JSON, canonical DSL uses ->? and ->0 with no mode facts."""
    # given: the exact five-place, one-transition, five-arc fixture document
    expected = _fixture_document()

    # when: JSON is emitted, compiled, and emitted a second time
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(
        canonical, "crossing-window.canonical.petrinet"
    )
    second = emit_petrinet(round_tripped)

    # then: exceptional modes are topology-native and the canonical form is fixed
    assert canonical == _EXPECTED_CANONICAL
    assert "-sample->?" in canonical
    assert "-alert->0" in canonical
    assert " mode " not in canonical
    assert round_tripped == expected
    assert second == canonical
