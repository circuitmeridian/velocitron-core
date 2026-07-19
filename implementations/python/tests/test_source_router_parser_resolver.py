"""Red parser, resolver, and emitter contracts for Slice 04 Source Router."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text
from velocitron.dsl.diagnostics import PetrinetDslError


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "04-source-router"
_CEL_PATH = _FIXTURE_ROOT / "source-router.petrinet"
_HANDLER_PATH = _FIXTURE_ROOT / "source-router-predicate-handlers.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "source-router.json"

_TWO_ROUTES = """\
net "source-router"
@co2_route: (sample_in) -sample-> [route_co2mon] -sample-> (co2mon_samples)
@weather_route: (sample_in) -sample-> [route_weather] -sample-> (weather_samples)
[route_co2mon] handler "route_co2mon"
[route_weather] handler "route_weather"
@co2_route predicate cel "source == \\"co2mon\\""
@weather_route predicate cel "source == \\"weather\\""
"""


def _fixture_document() -> dict[str, object]:
    return json.loads(_JSON_PATH.read_text(encoding="utf-8"))


def test_progressive_cel_predicates_select_the_unique_consume_arc_in_each_chain() -> (
    None
):
    """Given handled two-arc runs, predicates decorate only their consume arcs."""
    # given: each route handle owns a complete consume-then-produce declaration run
    portable = lower_petrinet_text(_TWO_ROUTES, "source-router.petrinet")

    # when: the progressive CEL predicate facts are lowered and resolved
    resolved = compile_petrinet_text(_TWO_ROUTES, "source-router.petrinet")

    # then: handles keep both ordered arc identities while each predicate selects one consume
    handles = {
        item["target"]["name"]: item["value"]["arcIds"]
        for item in portable["contributions"]
        if item["kind"] == "arc.handle"
    }
    assert handles == {
        "co2_route": [
            {"document": "source-router.petrinet", "statement": 1, "part": 1},
            {"document": "source-router.petrinet", "statement": 1, "part": 2},
        ],
        "weather_route": [
            {"document": "source-router.petrinet", "statement": 2, "part": 1},
            {"document": "source-router.petrinet", "statement": 2, "part": 2},
        ],
    }
    assert [arc.get("consume", {}).get("predicate") for arc in resolved["arcs"]] == [
        {"cel": 'source == "co2mon"'},
        None,
        {"cel": 'source == "weather"'},
        None,
    ]
    assert all("guard" not in transition for transition in resolved["transitions"])


def test_progressive_handler_predicates_lower_to_the_same_route_topology() -> None:
    """Given named predicate facts, lowering changes predicates but not topology."""
    # given: the ladder's alternative source uses named single-token predicates
    source = _HANDLER_PATH.read_text(encoding="utf-8")

    # when: the alternative is compiled
    actual = compile_petrinet_text(source, str(_HANDLER_PATH))
    actual_arcs = cast(list[dict[str, Any]], actual["arcs"])
    expected_arcs = cast(list[dict[str, Any]], _fixture_document()["arcs"])

    # then: both predicate refs belong to consume inscriptions on the same four arcs
    assert actual["places"] == _fixture_document()["places"]
    assert actual["transitions"] == _fixture_document()["transitions"]
    assert [(arc["from"], arc["to"]) for arc in actual_arcs] == [
        (arc["from"], arc["to"]) for arc in expected_arcs
    ]
    assert [arc.get("consume", {}).get("predicate") for arc in actual_arcs] == [
        {"handler": "is_co2mon"},
        None,
        {"handler": "is_weather"},
        None,
    ]


def test_conflicting_predicate_facts_report_later_primary_and_first_related_span() -> (
    None
):
    """Given two unequal predicates, PN202 points at both progressive facts."""
    # given: one chain receives a CEL predicate and then a contradictory handler predicate
    source = """\
net "source-router"
@route: (sample_in) -sample-> [route_co2mon] -sample-> (co2mon_samples)
[route_co2mon] handler "route_co2mon"
@route predicate cel "source == \\"co2mon\\""
@route predicate handler "is_co2mon"
"""

    # when: the contradictory facts are resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "predicate-conflict.petrinet")

    # then: the later fact is primary and the original fact is related context
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == "conflicting predicate facts for arc @route"
    assert (
        diagnostic.help
        == "remove one declaration or make both predicate values identical"
    )
    assert diagnostic.span.source == "predicate-conflict.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (5, 1)
    assert len(diagnostic.related) == 1
    assert diagnostic.related[0].message == "first predicate was declared here"
    assert diagnostic.related[0].span.source == "predicate-conflict.petrinet"
    assert (
        diagnostic.related[0].span.start.line,
        diagnostic.related[0].span.start.column,
    ) == (4, 1)


def test_malformed_cel_reports_the_authored_predicate_source_span() -> None:
    """Given malformed CEL, its diagnostic remains anchored to the DSL source."""
    # given: valid router structure with an invalid CEL comparison on its consume arc
    source = """\
net "source-router"
@route: (sample_in) -sample-> [route_co2mon] -sample-> (co2mon_samples)
[route_co2mon] handler "route_co2mon"
@route predicate cel "source == == \\"co2mon\\""
"""

    # when: public compilation validates the predicate
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "malformed-source-router.petrinet")

    # then: the error identifies the authored fact rather than generated JSON
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN203"
    assert diagnostic.span.source == "malformed-source-router.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (4, 1)
    assert diagnostic.message == "invalid CEL predicate for arc @route"
    assert diagnostic.help == "fix the CEL expression syntax"


def test_rich_json_types_and_canonical_predicate_handles_round_trip() -> None:
    """Given unannotated JSON, canonical DSL preserves rich values and arc identity."""
    # given: the ladder JSON contains one exact rich CO2 record and two predicates
    expected = _fixture_document()
    expected_canonical = """\
net source_router "Source router"

@arc_0: (sample_in) -sample-> [route_co2mon]
[route_co2mon] -sample-> (co2mon_samples)
@arc_2: (sample_in) -sample-> [route_weather]
[route_weather] -sample-> (weather_samples)

[route_co2mon] handler "route_co2mon"
[route_weather] handler "route_weather"

@arc_0 predicate cel "source == \\"co2mon\\""
@arc_2 predicate cel "source == \\"weather\\""

marking initial (sample_in) <- $token_0
$token_0: sample {"co2_ppm":602,"error":null,"humidity_pct":51,"source":"co2mon","temp_c":28.9,"timestamp_ms":1234567890000}
"""

    # when: unannotated JSON is emitted and compiled twice
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(canonical, "source-router.canonical.petrinet")
    second = emit_petrinet(round_tripped)

    # then: generated handles identify only predicate-bearing consume arcs;
    # produce arcs are separate statements and rich JSON is compact and key-sorted
    assert canonical == expected_canonical
    assert canonical.splitlines()[2:6] == [
        "@arc_0: (sample_in) -sample-> [route_co2mon]",
        "[route_co2mon] -sample-> (co2mon_samples)",
        "@arc_2: (sample_in) -sample-> [route_weather]",
        "[route_weather] -sample-> (weather_samples)",
    ]
    assert round_tripped == expected
    assert second == canonical

    assert expected["initialMarking"] == {
        "sample_in": [
            {
                "type": "sample",
                "data": {
                    "source": "co2mon",
                    "co2_ppm": 602,
                    "temp_c": 28.9,
                    "humidity_pct": 51.0,
                    "timestamp_ms": 1_234_567_890_000,
                    "error": None,
                },
            }
        ]
    }
