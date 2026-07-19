"""Red CLI, visualization, and runtime contracts for Slice 11 Speaks Window."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "11-speaks-window"
_DSL_PATH = _FIXTURE_ROOT / "speaks_window.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "speaks_window.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "speaks_window.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"
_RESERVED = "petrinet.dsl/v1"


def _document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _registry() -> HandlerRegistry:
    """Load the fixture's instance-scoped handlers without changing sys.path."""
    spec = importlib.util.spec_from_file_location(
        "speaks_window_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_instance(
        registry,
        scope="speaks",
        in_flight_destination="in_flight",
        judge_requested_destination="judge_requested",
        utterance_destination="utterance_out",
    )
    return registry


def _token(raw: dict[str, Any]) -> Token:
    return Token(type=raw["type"], data=raw["data"])


def _marking(raw: dict[str, Any]) -> Marking:
    return Marking(
        {
            place: [_token(cast(dict[str, Any], token)) for token in tokens]
            for place, tokens in raw.items()
        }
    )


def _plain_marking(marking: Marking) -> dict[str, list[dict[str, Any]]]:
    return {
        place: [{"type": token.type, "data": token.data} for token in tokens]
        for place, tokens in marking.items()
        if tokens
    }


def _selected_marking(document: dict[str, Any], scenario: dict[str, Any]) -> Marking:
    """Select named metadata explicitly; parsing a net never activates it."""
    selected = scenario.get("selectedMarking")
    if selected is None:
        return _marking(scenario["initialMarking"])
    raw = document["annotations"][_RESERVED]["markings"][selected]
    return _marking(cast(dict[str, Any], raw))


def _semantic_only(document: dict[str, Any]) -> dict[str, Any]:
    """Remove documentation fields recursively while retaining firing semantics."""
    result = deepcopy(document)
    result.pop("description", None)
    result.pop("annotations", None)
    for collection in ("places", "transitions", "arcs"):
        for item in result[collection]:
            item.pop("description", None)
            item.pop("annotations", None)
    return result


def _run_case(net: Net, document: dict[str, Any], scenario: dict[str, Any]):
    journal = JsonlJournal()
    engine = Engine(_registry(), journal=journal, deposit_violation="raise")
    initial = _selected_marking(document, scenario)
    enabled = engine.enabled_transitions(net, initial)
    final = engine.run(net, initial, max_steps=scenario["maxSteps"])
    firings = [
        {"transition": row["transition"], "status": row["status"]}
        for row in journal._records  # pyright: ignore[reportPrivateUsage]
    ]
    return enabled, firings, _plain_marking(final)


def test_json_runtime_baseline_explicitly_selects_named_marking_and_finishes() -> None:
    """Given queued metadata selected explicitly, both clean gates and finish work."""
    # given: strict JSON accepted without an initialMarking
    document = _document(_JSON_PATH)
    scenario = _document(_SCENARIO_PATH)["cases"][0]
    net = parse_net(document)
    assert net.initial_marking is None
    assert "initialMarking" not in document

    # when: tooling explicitly materializes queued and runs exactly two firings
    enabled, firings, final = _run_case(net, document, scenario)

    # then: start and finish consume both internal tokens and produce one utterance
    assert enabled == scenario["expectedEnabledTransitions"] == ["start"]
    assert firings == scenario["expectedFiringSequence"]
    assert final == scenario["expectedFinalMarking"]
    assert set(final) == {"utterance_out"}


def test_named_marking_is_inert_until_explicitly_selected() -> None:
    """Given only queued metadata, ordinary empty runtime marking stays empty."""
    document = _document(_JSON_PATH)
    net = parse_net(document)
    engine = Engine(_registry())

    assert engine.enabled_transitions(net, Marking({})) == []
    assert not net.initial_marking
    assert _plain_marking(engine.run(net, Marking({}), max_steps=2)) == {}


@pytest.mark.parametrize("case_index", [1, 2], ids=["stale-judge", "stale-in-flight"])
def test_each_clean_start_inhibitor_independently_blocks_a_stale_cycle(
    case_index: int,
) -> None:
    """Given either stale internal token, start cannot admit the next request."""
    document = _document(_JSON_PATH)
    scenario = _document(_SCENARIO_PATH)["cases"][case_index]

    enabled, firings, final = _run_case(parse_net(document), document, scenario)

    assert enabled == scenario["expectedEnabledTransitions"] == []
    assert firings == scenario["expectedFiringSequence"] == []
    assert final == scenario["expectedFinalMarking"]


def test_json_metadata_variants_have_identical_enablement_and_firing() -> None:
    """Given metadata-only variants, the engine observes exactly one semantic net."""
    # given: one full document and one recursively stripped semantic document
    full_document = _document(_JSON_PATH)
    semantic_document = _semantic_only(full_document)
    scenario = _document(_SCENARIO_PATH)["cases"][0]
    active = _selected_marking(full_document, scenario)
    full = parse_net(full_document)
    semantic = parse_net(semantic_document)

    # when: each net evaluates and fires start from the same active marking
    full_engine = Engine(_registry(), deposit_violation="raise")
    semantic_engine = Engine(_registry(), deposit_violation="raise")
    full_after, full_record = full_engine.fire(full, active, "start", attempt=0)
    semantic_after, semantic_record = semantic_engine.fire(
        semantic, active, "start", attempt=0
    )

    # then: descriptions, fusion, markings, geometry, routes, and extensions are inert
    assert full_engine.enabled_transitions(full, active) == ["start"]
    assert semantic_engine.enabled_transitions(semantic, active) == ["start"]
    assert _plain_marking(full_after) == _plain_marking(semantic_after)
    assert full_record["status"] == semantic_record["status"] == "completed"


def test_default_cli_preserves_full_document_and_semantic_only_is_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given authored metadata, only --semantic-only may remove it from JSON."""
    expected = _document(_JSON_PATH)

    assert dsl_main(["validate", str(_DSL_PATH)]) == 0
    validated = capsys.readouterr()
    assert dsl_main(["to-json", str(_DSL_PATH)]) == 0
    full = capsys.readouterr()
    assert dsl_main(["to-json", str(_DSL_PATH), "--semantic-only"]) == 0
    semantic = capsys.readouterr()

    assert validated.out == "net\n" and validated.err == ""
    assert json.loads(full.out) == expected and full.err == ""
    assert json.loads(semantic.out) == _semantic_only(expected) and semantic.err == ""
    assert _RESERVED in json.loads(full.out)["annotations"]
    assert "annotations" not in json.loads(semantic.out)


def test_cli_json_to_dsl_preserves_reserved_payload_as_a_fixed_point(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given full JSON, canonical CLI output reattaches handles and all metadata."""
    expected = _document(_JSON_PATH)

    assert dsl_main(["to-petrinet", str(_JSON_PATH)]) == 0
    converted = capsys.readouterr()
    reparsed = compile_petrinet_text(
        converted.out, "speaks_window.cli-roundtrip.petrinet"
    )

    assert converted.err == ""
    assert converted.out == emit_petrinet(expected)
    assert reparsed == expected
    assert converted.out.count("->0") == 2
    assert "@window_close:" in converted.out
    assert "marking queued" in converted.out
    assert "view window route @window_close orthogonal" in converted.out
    assert "extensions {}" in converted.out


def test_selected_view_honors_authored_geometry_route_and_fusion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given view window, DOT honors points while keeping fusion local instances."""
    assert viz_main([str(_JSON_PATH), "--view", "window"]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    assert len(edges) == 8
    for position in ("0,80!", "160,80!", "320,20!", "320,140!", "500,80!", "680,80!"):
        assert f'pos="{position}"' in dot
    assert 'pos="400,20 500,20"' in dot
    assert "splines=ortho" in dot
    assert dot.count('style=dashed, color="gray40"') == 2
    assert '"in_flight" [' not in dot


def test_selected_view_from_dsl_uses_compiled_full_document(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert viz_main([str(_DSL_PATH), "--view", "window"]) == 0
    dot = capsys.readouterr().out

    assert 'pos="0,80!"' in dot
    assert 'pos="400,20 500,20"' in dot


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        ("position-bool", "invalid position in view 'window'"),
        ("route-infinity", "invalid JSON source"),
    ],
)
def test_selected_view_rejects_non_finite_or_boolean_coordinates(
    invalid: str,
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # given: a resolved document with invalid authored geometry
    document = _document(_JSON_PATH)
    view = document["annotations"][_RESERVED]["views"]["window"]
    if invalid == "position-bool":
        view["positions"]["place:request_in"]["x"] = True
    else:
        view["routes"]["window_close"]["points"][0]["x"] = float("inf")
    path = tmp_path / "invalid-view.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    # when: the visualization CLI renders the selected view
    exit_code = viz_main([str(path), "--view", "window"])

    # then: the CLI reports its stable failure boundary without raising
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("velocitron-viz: error: ")
    assert message in captured.err


def test_automatic_layout_keeps_same_firing_graph_and_fusion_convention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given no selected view, automatic layout still renders all semantic arcs."""
    assert viz_main([str(_JSON_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    assert len(edges) == 8
    assert (
        sum("arrowhead=odot" in line and 'color="crimson"' in line for line in edges)
        == 2
    )
    assert dot.count('style=dashed, color="gray40"') == 2
    assert 'pos="0,80!"' not in dot
