"""Red CLI, visualization, and engine contracts for Slice 05 Crossing Window."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, load_petrinet
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "05-crossing-window"
)
_DSL_PATH = _FIXTURE_ROOT / "crossing-window.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "crossing-window.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "crossing-window.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _registry() -> HandlerRegistry:
    """Load the fixture's exact detect_crossing@ingest handler in isolation."""
    spec = importlib.util.spec_from_file_location(
        "crossing_window_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_all(registry)
    return registry


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def _scenarios() -> list[dict[str, Any]]:
    document = cast(
        dict[str, Any], json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    )
    return cast(list[dict[str, Any]], document["cases"])


def _marking(document: dict[str, list[dict[str, Any]]]) -> Marking:
    return Marking(
        {
            place: [Token(type=token["type"], data=token["data"]) for token in tokens]
            for place, tokens in document.items()
        }
    )


def _nonempty_marking(marking: Marking) -> dict[str, list[dict[str, Any]]]:
    return {
        place: [{"type": token.type, "data": token.data} for token in tokens]
        for place, tokens in marking.items()
        if tokens
    }


def test_crossing_fixture_api_and_all_cli_conversions_share_one_resolved_net(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired fixtures, API and CLI preserve the same exact core document."""
    # given: the authoritative five-place DSL and paired JSON
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: API compilation, both validators, and both conversion directions run
    actual = compile_petrinet_text(source, str(_DSL_PATH))
    assert load_petrinet(_DSL_PATH) == parse_net(_JSON_PATH)

    assert dsl_main(["validate", str(_DSL_PATH)]) == 0
    validated_dsl = capsys.readouterr()
    assert dsl_main(["validate", str(_JSON_PATH)]) == 0
    validated_json = capsys.readouterr()
    assert dsl_main(["to-json", str(_DSL_PATH)]) == 0
    converted_json = capsys.readouterr()
    assert dsl_main(["to-petrinet", str(_JSON_PATH)]) == 0
    converted_dsl = capsys.readouterr()

    # then: every path exposes the exact 5-place/1-transition/5-arc semantic net
    assert actual == expected
    assert (len(actual["places"]), len(actual["transitions"]), len(actual["arcs"])) == (
        5,
        1,
        5,
    )
    assert validated_dsl.out == validated_json.out == "net\n"
    assert validated_dsl.err == validated_json.err == ""
    assert json.loads(converted_json.out) == expected
    assert converted_json.err == ""
    assert (
        compile_petrinet_text(
            converted_dsl.out, "crossing-window.cli-roundtrip.petrinet"
        )
        == expected
    )
    assert converted_dsl.err == ""


def test_crossing_viz_distinguishes_consume_read_and_inhibit_glyphs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Crossing Window DSL, DOT makes all three input semantics visible."""
    # given: one ordinary consume, two reads, one inhibitor, and one produce

    # when: the real visualization CLI renders the resolved DSL fixture
    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: the exact topology renders with blue read labels and an open-dot inhibitor
    assert dot.count("shape=ellipse") == 5
    assert dot.count("shape=box") == 1
    assert len(edges) == 5
    assert any(
        '"arrival" -> "detect_crossing"' in line
        and "arrival" in line
        and "read arrival" not in line
        and "arrowhead=odot" not in line
        for line in edges
    )
    for place in ("latest_sample", "previous_sample"):
        assert any(
            f'"{place}" -> "detect_crossing"' in line
            and "read sample" in line
            and 'color="#1f6feb", style=dashed' in line
            for line in edges
        )
    assert any(
        '"open_alert" -> "detect_crossing"' in line
        and "no alert" in line
        and "arrowhead=odot" in line
        and 'color="crimson", style=dashed' in line
        for line in edges
    )
    assert any(
        '"detect_crossing" -> "alert_out"' in line and "alert" in line for line in edges
    )


def test_real_engine_retains_and_binds_reads_while_excluding_inhibit() -> None:
    """Given the initial window, firing consumes arrival but only reads samples."""
    # given: the exact fixture marking, empty inhibitor place, and registered exact handler
    net = parse_net(_JSON_PATH)
    assert net.initial_marking is not None
    before = net.initial_marking
    arrival = before["arrival"][0]
    latest = before["latest_sample"][0]
    previous = before["previous_sample"][0]
    engine = Engine(_registry(), deposit_violation="raise")

    # when: enablement selects and fires detect_crossing once
    assert engine.enabled_transitions(net, before) == ["detect_crossing"]
    after, record = engine.fire(net, before, "detect_crossing", attempt=0)

    # then: destructive arrival is gone, both read tokens remain exact, and alert is produced
    assert record["status"] == "completed"
    assert list(after.get("arrival", ())) == []
    assert list(after["latest_sample"]) == [latest]
    assert list(after["previous_sample"]) == [previous]
    assert list(after.get("open_alert", ())) == []
    assert list(after["alert_out"]) == [Token(type="alert", data={})]
    # and: record binding follows arc order and inhibitor contributes no bound token
    assert list(record["inputTokens"]) == [
        "arrival",
        "latest_sample",
        "previous_sample",
    ]
    assert record["inputTokens"] == {
        "arrival": [arrival],
        "latest_sample": [latest],
        "previous_sample": [previous],
    }
    assert "open_alert" not in record["inputTokens"]


@pytest.mark.parametrize(
    "case_name",
    [
        "given an empty latest sample, when queried, then detection is disabled",
        "given an empty previous sample, when queried, then detection is disabled",
        "given an open alert, when queried, then the inhibitor blocks detection",
    ],
)
def test_real_engine_missing_read_or_present_inhibitor_disables_without_mutation(
    case_name: str,
) -> None:
    """Given an unsatisfied exceptional input, enablement fails without mutation."""
    # given: an authored BDD case with one missing read or one matching inhibit token
    scenario = next(case for case in _scenarios() if case["name"] == case_name)
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: real enablement evaluates read presence and the uncorrelated zero-test
    enabled = engine.enabled_transitions(net, before)

    # then: no binding survives and the immutable marking remains byte-for-byte equivalent
    assert enabled == scenario["expectedEnabledTransitions"] == []
    assert engine.select_binding(net, "detect_crossing", before) is None
    assert _nonempty_marking(before) == scenario["expectedFinalMarking"]
