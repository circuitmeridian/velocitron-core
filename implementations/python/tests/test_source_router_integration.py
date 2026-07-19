"""Red end-to-end contracts for the Slice 04 Source Router ladder fixtures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from velocitron.dsl.api import compile_petrinet_text, load_petrinet
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "04-source-router"
_CEL_PATH = _FIXTURE_ROOT / "source-router.petrinet"
_HANDLER_PATH = _FIXTURE_ROOT / "source-router-predicate-handlers.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "source-router.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _registry() -> HandlerRegistry:
    """Load the ladder's real transition and predicate handlers in isolation."""
    spec = importlib.util.spec_from_file_location(
        "source_router_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_all(registry)
    return registry


def _token(**data: Any) -> Token:
    return Token(type="sample", data=data)


def _data_at(marking: Marking, place: str) -> list[dict[str, Any]]:
    return [token.data for token in marking.get(place, ())]


def test_router_fixture_compiles_through_api_and_cli_without_topology_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the primary ladder fixture, API and CLI expose its canonical JSON."""
    # given: the authored CEL source and paired rich JSON document
    source = _CEL_PATH.read_text(encoding="utf-8")
    expected = json.loads(_JSON_PATH.read_text(encoding="utf-8"))

    # when: public API and CLI compile and validate the source
    actual = compile_petrinet_text(source, str(_CEL_PATH))
    assert dsl_main(["validate", str(_CEL_PATH)]) == 0
    validated = capsys.readouterr()
    assert dsl_main(["to-json", str(_CEL_PATH)]) == 0
    converted = capsys.readouterr()

    # then: all paths preserve the same three-place, two-transition, four-arc net
    assert actual == expected
    assert actual["description"] == "Source router"
    assert (len(actual["places"]), len(actual["transitions"]), len(actual["arcs"])) == (
        3,
        2,
        4,
    )
    assert validated.out == "net\n"
    assert validated.err == ""
    assert json.loads(converted.out) == expected
    assert converted.err == ""


def test_router_viz_labels_predicates_and_exact_topology(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Source Router DSL, DOT labels both consume predicates on four edges."""
    # given: the primary CEL router fixture

    # when: the visualization CLI renders it
    assert viz_main([str(_CEL_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: topology and predicate labels distinguish the two mutually exclusive routes
    assert dot.count("shape=ellipse") == 3
    assert dot.count("shape=box") == 2
    assert len(edges) == 4
    assert any(
        '"sample_in" -> "route_co2mon"' in line and 'source == "co2mon"' in line
        for line in edges
    )
    assert any(
        '"route_co2mon" -> "co2mon_samples"' in line and "sample" in line
        for line in edges
    )
    assert any(
        '"sample_in" -> "route_weather"' in line and 'source == "weather"' in line
        for line in edges
    )
    assert any(
        '"route_weather" -> "weather_samples"' in line and "sample" in line
        for line in edges
    )


@pytest.mark.parametrize(
    ("data", "enabled", "destination"),
    [
        (
            {
                "source": "co2mon",
                "co2_ppm": 602,
                "temp_c": 28.9,
                "humidity_pct": 51.0,
                "timestamp_ms": 1_234_567_890_000,
                "error": None,
            },
            ["route_co2mon"],
            "co2mon_samples",
        ),
        (
            {
                "source": "weather",
                "temp_c": 16.25,
                "humidity_pct": 68.0,
                "timestamp_ms": 1_234_567_891_000,
                "error": None,
            },
            ["route_weather"],
            "weather_samples",
        ),
        (
            {
                "source": "air-quality-v2",
                "value": 7,
                "timestamp_ms": 1_234_567_892_000,
                "error": None,
            },
            [],
            None,
        ),
        (
            {
                "value": 0,
                "timestamp_ms": 1_234_567_893_000,
                "error": "source field omitted",
            },
            [],
            None,
        ),
    ],
)
def test_cel_router_enablement_is_mutually_exclusive_and_preserves_tokens(
    data: dict[str, Any], enabled: list[str], destination: str | None
) -> None:
    """Given one sample, CEL routes supported sources and leaves others untouched."""
    # given: a rich sample whose source is supported, unsupported, or absent
    net = load_petrinet(_CEL_PATH)
    engine = Engine(_registry(), deposit_violation="raise")
    marking = Marking({"sample_in": [Token(type="sample", data=data)]})

    # when: enablement evaluates the two consume inscriptions
    assert engine.enabled_transitions(net, marking) == enabled

    # then: a supported route moves the exact token; no match consumes nothing
    if destination is None:
        assert _data_at(marking, "sample_in") == [data]
    else:
        after, record = engine.fire(net, marking, enabled[0], attempt=0)
        assert record["status"] == "completed"
        assert _data_at(after, "sample_in") == []
        assert _data_at(after, destination) == [data]
        assert engine.enabled_transitions(net, after) == []


def test_cel_router_selects_each_matching_token_without_consuming_nonmatches() -> None:
    """Given mixed sample input, successive routes consume only matching samples."""
    # given: CO2, weather, unsupported, and missing-source samples share sample_in
    net = load_petrinet(_CEL_PATH)
    engine = Engine(_registry(), deposit_violation="raise")
    samples = [
        _token(
            source="co2mon",
            co2_ppm=602,
            temp_c=28.9,
            humidity_pct=51.0,
            timestamp_ms=1_234_567_890_000,
            error=None,
        ),
        _token(
            source="weather",
            temp_c=16.25,
            humidity_pct=68.0,
            timestamp_ms=1_234_567_891_000,
            error=None,
        ),
        _token(
            source="air-quality-v2",
            value=7,
            timestamp_ms=1_234_567_892_000,
            error=None,
        ),
        _token(
            value=0,
            timestamp_ms=1_234_567_893_000,
            error="source field omitted",
        ),
    ]
    marking = Marking({"sample_in": samples})

    # when: both supported routes fire once
    assert engine.enabled_transitions(net, marking) == ["route_co2mon", "route_weather"]
    after_co2, _ = engine.fire(net, marking, "route_co2mon", attempt=0)
    assert engine.enabled_transitions(net, after_co2) == ["route_weather"]
    after_weather, _ = engine.fire(net, after_co2, "route_weather", attempt=1)

    # then: supported tokens retain all JSON data and unmatched tokens remain at sample_in
    assert _data_at(after_weather, "co2mon_samples") == [samples[0].data]
    assert _data_at(after_weather, "weather_samples") == [samples[1].data]
    assert _data_at(after_weather, "sample_in") == [samples[2].data, samples[3].data]
    assert engine.enabled_transitions(net, after_weather) == []


def test_named_predicate_fixture_has_the_same_real_routing_behavior() -> None:
    """Given registered named predicates, handler routing matches CEL routing."""
    # given: the alternative ladder net and one token for each supported source
    net = load_petrinet(_HANDLER_PATH)
    engine = Engine(_registry(), deposit_violation="raise")
    co2 = _token(
        source="co2mon",
        co2_ppm=602,
        temp_c=28.9,
        humidity_pct=51.0,
        timestamp_ms=1_234_567_890_000,
        error=None,
    )
    weather = _token(
        source="weather",
        temp_c=16.25,
        humidity_pct=68.0,
        timestamp_ms=1_234_567_891_000,
        error=None,
    )

    # when: each named predicate is used for enablement and firing
    co2_marking = Marking({"sample_in": [co2]})
    weather_marking = Marking({"sample_in": [weather]})
    assert engine.enabled_transitions(net, co2_marking) == ["route_co2mon"]
    assert engine.enabled_transitions(net, weather_marking) == ["route_weather"]
    after_co2, _ = engine.fire(net, co2_marking, "route_co2mon", attempt=0)
    after_weather, _ = engine.fire(net, weather_marking, "route_weather", attempt=0)

    # then: transition handlers preserve the complete rich token data in each destination
    assert _data_at(after_co2, "co2mon_samples") == [co2.data]
    assert _data_at(after_weather, "weather_samples") == [weather.data]
