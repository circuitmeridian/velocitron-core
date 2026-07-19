"""Red CLI, visualization, and real anti-join contracts for Slice 07."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
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
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "07-per-key-suppression"
)
_DSL_PATH = _FIXTURE_ROOT / "per-key-suppression.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "per-key-suppression.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "per-key-suppression.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"
_CORRELATE = "token.key == binding.requests[0].key"


def _registry() -> HandlerRegistry:
    """Load the fixture's exact request passthrough without changing sys.path."""
    spec = importlib.util.spec_from_file_location(
        "per_key_suppression_handlers", _HANDLERS_PATH
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


def _scenario(name: str) -> dict[str, Any]:
    return next(case for case in _scenarios() if case["name"] == name)


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


def test_suppression_fixture_api_and_cli_preserve_exact_correlated_net(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired fixtures, APIs and CLI conversions share one exact net."""
    # given: the authoritative source and paired resolved JSON
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: compilation, loading, validation, and both conversions run
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

    # then: all public paths preserve the 3-place/1-transition/3-arc document
    assert actual == expected
    assert (len(actual["places"]), len(actual["transitions"]), len(actual["arcs"])) == (
        3,
        1,
        3,
    )
    assert validated_dsl.out == validated_json.out == "net\n"
    assert validated_dsl.err == validated_json.err == ""
    assert json.loads(converted_json.out) == expected
    assert converted_json.err == ""
    assert (
        compile_petrinet_text(
            converted_dsl.out, "per-key-suppression.cli-roundtrip.petrinet"
        )
        == expected
    )
    assert converted_dsl.err == ""


def test_invalid_correlate_cel_cli_reports_authored_location_before_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given malformed CEL, validate reports its source fact and returns failure."""
    # given: an on-disk DSL with valid inhibition but malformed correlation syntax
    source_path = tmp_path / "invalid-correlate.petrinet"
    source_path.write_text(
        "net invalid_correlate\n\n"
        "@blocked: (suppressions) -suppression->0 [admit]\n"
        '[admit] handler "admit_request"\n'
        '@blocked correlate cel "token.key ==== binding.requests[0].key"\n',
        encoding="utf-8",
    )

    # when: the real CLI validates without constructing or firing an engine
    result = dsl_main(["validate", str(source_path)])
    captured = capsys.readouterr()

    # then: it fails at the authored expression line with the stable CEL diagnostic
    assert result == 1
    assert captured.out == ""
    assert f"{source_path.name}:5:1: error[PN203]:" in captured.err
    assert "invalid CEL correlate for arc @blocked" in captured.err


def test_suppression_viz_exposes_correlated_inhibitor_not_guard(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given valid DSL, DOT visibly renders inhibition and its anti-join CEL."""
    # given: one consume, one produce, and one correlated inhibitor

    # when: the visualization CLI renders the resolved source
    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: exact topology and the conventional inhibitor glyph remain visible
    assert dot.count("shape=ellipse") == 3
    assert dot.count("shape=box") == 1
    assert len(edges) == 3
    inhibitor = next(line for line in edges if '"suppressions" -> "admit"' in line)
    assert "no suppression" in inhibitor
    assert "arrowhead=odot" in inhibitor
    assert 'color="crimson", style=dashed' in inhibitor
    # and: correlation is a visible inscription rather than an invisible guard
    assert _CORRELATE in inhibitor
    assert "guard" not in dot.lower()
    assert any(
        '"requests" -> "admit"' in line
        and "request" in line
        and "arrowhead=odot" not in line
        for line in edges
    )
    assert any('"admit" -> "admitted"' in line and "request" in line for line in edges)


def test_real_engine_selects_first_unsuppressed_binding_and_fires_exactly_once() -> (
    None
):
    """Given requests A/B and suppression A, the anti-join selects and admits B."""
    # given: the canonical marking in request order A then B, suppressed only for A
    scenario = _scenario(
        "same-key candidate is filtered before different-key candidate"
    )
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    original_a, original_b = before["requests"]
    original_suppression = before["suppressions"][0]
    engine = Engine(_registry(), deposit_violation="raise")

    # when: deterministic binding selection filters A and fires surviving B
    assert (
        engine.enabled_transitions(net, before)
        == scenario["expectedEnabledTransitions"]
    )
    selected = engine.select_binding(net, "admit", before)
    assert selected is not None
    after, record = engine.fire(net, before, "admit", attempt=0)

    # then: only B is bound; the correlated inhibit contributes no input token
    assert selected == {"requests": [original_b]}
    assert record["status"] == "completed"
    assert record["inputTokens"] == {"requests": [original_b]}
    assert "suppressions" not in record["inputTokens"]
    assert [
        {"transition": record["transition"], "status": record["status"]}
    ] == scenario["expectedFiringSequence"]
    # and: A and its suppression remain untouched while B reaches admitted
    assert list(after["requests"]) == [original_a]
    assert list(after["suppressions"]) == [original_suppression]
    assert list(after["admitted"]) == [original_b]
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]
    # and: the remaining same-key request is now disabled
    assert engine.enabled_transitions(net, after) == []
    assert engine.select_binding(net, "admit", after) is None


@pytest.mark.parametrize(
    ("case_name", "selected_key"),
    [
        ("different-key suppression permits the request", "B"),
        ("same-key suppression blocks the request", None),
    ],
)
def test_real_engine_distinguishes_different_key_permission_from_same_key_block(
    case_name: str, selected_key: str | None
) -> None:
    scenario = _scenario(case_name)
    net = parse_net(_JSON_PATH)
    marking = _marking(scenario["initialMarking"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: real enablement evaluates the suppression per candidate binding
    enabled = engine.enabled_transitions(net, marking)
    selected = engine.select_binding(net, "admit", marking)

    # then: different keys survive despite nonempty suppressions; equal keys do not
    assert enabled == scenario["expectedEnabledTransitions"]
    if selected_key is None:
        assert selected is None
        assert _nonempty_marking(marking) == scenario["expectedFinalMarking"]
        assert scenario["expectedFiringSequence"] == []
    else:
        assert selected is not None
        assert selected["requests"][0].data["key"] == selected_key
        after, record = engine.fire(net, marking, "admit", attempt=0)
        assert record["status"] == "completed"
        assert [
            {"transition": record["transition"], "status": record["status"]}
        ] == scenario["expectedFiringSequence"]
        assert _nonempty_marking(after) == scenario["expectedFinalMarking"]
        assert list(after["suppressions"]) == list(marking["suppressions"])


def test_missing_suppression_key_fails_closed_without_crashing_or_mutating() -> None:
    """Given token.key evaluation failure, the candidate binding is blocked."""
    # given: an authored request and malformed suppression token without key
    scenario = _scenario("correlation evaluation error fails closed")
    net = parse_net(_JSON_PATH)
    marking = _marking(scenario["initialMarking"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: enablement evaluates correlation against the malformed candidate token
    enabled = engine.enabled_transitions(net, marking)
    selected = engine.select_binding(net, "admit", marking)

    # then: evaluation degrades toward not-enabled rather than passing or raising
    assert enabled == scenario["expectedEnabledTransitions"] == []
    assert selected is None
    assert scenario["expectedFiringSequence"] == []
    assert _nonempty_marking(marking) == scenario["expectedFinalMarking"]


@pytest.mark.parametrize(
    "expression",
    [
        "token.key == binding.unknown_place[0].key",
        "key == binding.requests[0].key",
    ],
)
def test_correlation_environment_is_closed_and_access_errors_fail_closed(
    expression: str,
) -> None:
    """Given invalid runtime access, parse succeeds and engine blocks safely."""
    # given: valid CEL naming an absent binding place or forbidden flattened token field
    document = deepcopy(_fixture_document())
    correlate = document["arcs"][2]["consume"]["correlate"]
    correlate["cel"] = expression

    # when: core parsing compiles syntax, then enablement evaluates it for a token
    net = parse_net(document)
    marking = Marking(
        {
            "requests": [Token(type="request", data={"key": "A"})],
            "suppressions": [Token(type="suppression", data={"key": "A"})],
        }
    )
    engine = Engine(_registry(), deposit_violation="raise")

    # then: only token/binding roots exist, and bad runtime access fails safely closed
    assert engine.enabled_transitions(net, marking) == []
    assert engine.select_binding(net, "admit", marking) is None
    assert list(marking["requests"]) == [Token(type="request", data={"key": "A"})]
    assert list(marking["suppressions"]) == [
        Token(type="suppression", data={"key": "A"})
    ]
