"""Red CLI, visualization, engine, and property contracts for Batch Gate."""

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
from velocitron.properties import AtMostN, capacity_properties, check_marking
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "06-batch-gate"
_DSL_PATH = _FIXTURE_ROOT / "batch-gate.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "batch-gate.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "batch-gate.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _registry() -> HandlerRegistry:
    """Load the fixture's exact form_batch handler without changing sys.path."""
    spec = importlib.util.spec_from_file_location("batch_gate_handlers", _HANDLERS_PATH)
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


def test_batch_fixture_api_and_cli_preserve_exact_first_class_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired fixtures, API and CLI preserve one exact core document."""
    # given: the authoritative Batch Gate source and paired JSON
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: API compilation, both validators, and both conversions run
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

    # then: every path exposes the exact 2-place/1-transition/2-arc net
    assert actual == expected
    assert (len(actual["places"]), len(actual["transitions"]), len(actual["arcs"])) == (
        2,
        1,
        2,
    )
    assert validated_dsl.out == validated_json.out == "net\n"
    assert validated_dsl.err == validated_json.err == ""
    assert json.loads(converted_json.out) == expected
    assert converted_json.err == ""
    assert (
        compile_petrinet_text(converted_dsl.out, "batch-gate.cli-roundtrip.petrinet")
        == expected
    )
    assert converted_dsl.err == ""
    # and: capacity remains a place field while weight remains a consume inscription
    assert actual["places"][1]["capacityPerColorKey"] == {
        "key": "batch_id",
        "max": 1,
    }
    assert actual["arcs"][0]["consume"] == {"type": "item", "weight": 2}
    assert "annotations" not in actual["places"][1]


def test_batch_viz_shows_weight_without_implying_runtime_capacity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Batch Gate DSL, DOT shows weight without a gate or inhibitor glyph."""
    # given: one weighted consume and one ordinary produce around form_batch

    # when: the real visualization CLI renders the resolved fixture
    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: topology and weighted input are visible using the existing weight glyph
    assert dot.count("shape=ellipse") == 2
    assert dot.count("shape=box") == 1
    assert len(edges) == 2
    assert any(
        '"items" -> "form_batch"' in line and "2 &#215; item" in line for line in edges
    )
    assert any(
        '"form_batch" -> "batches"' in line and "batch" in line for line in edges
    )
    # and: the verification declaration never masquerades as inhibition/fullness
    assert "arrowhead=odot" not in dot
    assert "no batch" not in dot
    assert "full" not in dot.lower()


@pytest.mark.parametrize(
    "case_name",
    [
        "zero items are below weight threshold",
        "one item is below weight threshold",
    ],
)
def test_weight_threshold_disables_zero_or_one_item_without_mutation(
    case_name: str,
) -> None:
    """Given fewer than two items, form_batch has no selectable binding."""
    # given: an authored threshold scenario with zero or one matching item
    scenario = _scenario(case_name)
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: real enablement and binding selection apply consume weight two
    enabled = engine.enabled_transitions(net, before)
    binding = engine.select_binding(net, "form_batch", before)

    # then: the transition is disabled and the immutable marking is unchanged
    assert enabled == scenario["expectedEnabledTransitions"] == []
    assert binding is None
    assert _nonempty_marking(before) == scenario["expectedFinalMarking"]


def test_weighted_firing_binds_and_consumes_exactly_two_in_marking_order() -> None:
    """Given three items, one firing binds the first two and leaves the third."""
    # given: the authored three-item exact-consumption scenario
    scenario = _scenario("weighted firing consumes exactly two items")
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    original_items = list(before["items"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: form_batch selects one deterministic weighted binding and fires once
    assert (
        engine.enabled_transitions(net, before)
        == scenario["expectedEnabledTransitions"]
    )
    selected = engine.select_binding(net, "form_batch", before)
    assert selected is not None
    after, record = engine.fire(net, before, "form_batch", attempt=0)

    # then: binding and journal preserve the first two tokens in marking order
    assert list(selected["items"]) == original_items[:2]
    assert list(record["inputTokens"]) == ["items"]
    assert list(record["inputTokens"]["items"]) == original_items[:2]
    assert [
        {"transition": record["transition"], "status": record["status"]}
    ] == scenario["expectedFiringSequence"]
    # and: only those two are consumed; the third and handler output remain exact
    assert list(after["items"]) == original_items[2:]
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]


def test_capacity_is_checker_only_and_reports_one_at_most_n_violation() -> None:
    """Given an existing A batch, firing deposits another A before checking."""
    # given: the canonical marking already at its declared per-key maximum
    scenario = _scenario("capacity is checker-only")
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(), deposit_violation="raise")

    # when: enablement and firing run without consulting verification capacity
    assert (
        engine.enabled_transitions(net, before)
        == scenario["expectedEnabledTransitions"]
    )
    after, record = engine.fire(net, before, "form_batch", attempt=0)

    # then: firing completes, consumes both inputs, and retains both A batches
    assert [
        {"transition": record["transition"], "status": record["status"]}
    ] == scenario["expectedFiringSequence"]
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]
    assert [token.data["batch_id"] for token in after["batches"]] == ["A", "A"]
    # and: only the subsequent property pass reports the keyed overflow
    assert capacity_properties(net) == [
        AtMostN(place="batches", max=1, key=("batch_id",))
    ]
    report = check_marking(net, after)
    assert not report.ok
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.kind == "at-most-n"
    assert violation.place == "batches"
    assert violation.step is None
    assert "holds 2 tokens" in violation.message
    assert "batch_id" in violation.message
    assert "A" in violation.message
    assert "at most 1 allowed" in violation.message


def test_capacity_partitions_a_and_b_as_independent_nonviolating_keys() -> None:
    """Given one A and one B, keyed AtMostN treats each partition separately."""
    # given: the authored property-only scenario with two distinct batch ids
    scenario = _scenario("capacity partitions distinct batch ids")
    net = parse_net(_JSON_PATH)
    marking = _marking(scenario["initialMarking"])

    # when: the automatic capacity property checks the complete marking
    report = check_marking(net, marking)

    # then: neither singleton key partition exceeds max one and nothing mutates
    assert report.ok
    assert report.violations == ()
    assert _nonempty_marking(marking) == scenario["expectedFinalMarking"]
