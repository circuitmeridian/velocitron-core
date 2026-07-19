"""Red CLI, visualization, merge, and runtime contracts for Slice 10."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.composition import merge_composition
from velocitron.dsl.api import compile_petrinet_text
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_composition
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "10-wired-pulse"
_DSL_PATH = _FIXTURE_ROOT / "wired_pulse.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "wired_pulse.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "wired_pulse.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _merged_from_json() -> Net:
    document = _document(_JSON_PATH)

    def load_net(ref: str) -> dict[str, Any]:
        return _document((_FIXTURE_ROOT / ref).with_suffix(".json"))

    return merge_composition(parse_composition(document, net_loader=load_net))


def _merged_from_dsl() -> Net:
    document = compile_petrinet_text(
        _DSL_PATH.read_text(encoding="utf-8"), str(_DSL_PATH)
    )

    def load_net(ref: str) -> dict[str, Any]:
        path = _FIXTURE_ROOT / ref
        return compile_petrinet_text(path.read_text(encoding="utf-8"), str(path))

    return merge_composition(
        cast(Any, parse_composition)(
            document, origin=_FIXTURE_ROOT, net_loader=load_net
        )
    )


def _registry() -> HandlerRegistry:
    """Load fixture handlers and bind their outputs to resolved merged places."""
    spec = importlib.util.spec_from_file_location(
        "wired_pulse_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_instance(
        registry,
        pulse_destination="source.pulse_out",
        receipt_destination="sink.received",
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


def _run(net: Net) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    scenario = _document(_SCENARIO_PATH)["cases"][0]
    journal = JsonlJournal()
    final = Engine(_registry(), journal=journal).run(
        net,
        _marking(scenario["initialMarking"]),
        max_steps=scenario["maxSteps"],
    )
    records = [
        {
            "transition": record["transition"],
            "status": record["status"],
            "sequence": record["sequence"],
        }
        for record in journal._records  # pyright: ignore[reportPrivateUsage]
    ]
    return records, _plain_marking(final)


def test_wired_cli_validates_and_converts_each_direction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired composition fixtures, each real CLI path shares exact JSON."""
    # given: canonical composition DSL and its legacy metadata-free JSON pair
    expected = _document(_JSON_PATH)
    authored = _document(_JSON_PATH)

    # when: validation and both conversions run through the public CLI
    assert dsl_main(["validate", str(_DSL_PATH)]) == 0
    validated = capsys.readouterr()
    assert dsl_main(["to-json", str(_DSL_PATH)]) == 0
    converted_json = capsys.readouterr()
    assert dsl_main(["to-petrinet", str(_JSON_PATH)]) == 0
    converted_dsl = capsys.readouterr()

    # then: composition is discriminated and both conversions preserve exact core JSON
    assert validated.out == "composition\n" and validated.err == ""
    assert json.loads(converted_json.out) == authored and converted_json.err == ""
    assert (
        compile_petrinet_text(converted_dsl.out, "wired_pulse.cli-roundtrip.petrinet")
        == expected
    )
    assert (
        converted_dsl.out
        == """\
composition composition

use "pulse_source.petrinet" as source
use "pulse_sink.petrinet" as sink
wire source.(pulse_out) -> sink.(pulse_in)
"""
    )
    assert converted_dsl.err == ""


def test_wired_viz_renders_the_merged_fusion_place(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given composition DSL, merged DOT exposes one fused place and full flow."""
    # given: a source output wired to a sink input

    # when: the real visualization CLI renders the fused realization
    assert viz_main([str(_DSL_PATH), "--merged"]) == 0
    dot = capsys.readouterr().out

    # then: one source-named fusion place replaces sink.pulse_in between both transitions
    assert "source.pulse_out" in dot
    assert "sink.pulse_in" not in dot
    assert "source.emit" in dot
    assert "sink.receive" in dot
    assert dot.count("fusion") >= 1
    assert 'style="dashed,filled"' in dot or "style=dashed" in dot


def test_json_backed_merge_and_runtime_are_already_valid() -> None:
    """Given legacy composition JSON, existing fusion and runtime need no DSL shim."""
    # given: the same committed BDD scenario loaded through JSON only
    scenario = _document(_SCENARIO_PATH)["cases"][0]

    # when: existing composition/parser/merge/engine helpers run two steps
    records, final = _run(_merged_from_json())

    # then: the source and sink handlers complete and leave the expected receipt
    assert [(record["transition"], record["status"]) for record in records] == [
        ("source.emit", "completed"),
        ("sink.receive", "completed"),
    ]
    assert final == scenario["expectedFinalMarking"]


def test_merge_preserves_opaque_handler_refs_while_qualifying_transition_ids() -> None:
    """Given place fusion, structural names change but handler registry keys do not."""
    # given: the DSL-backed composition merged through existing helpers
    merged = _merged_from_dsl()

    # when: transition structure is inspected after qualification and fusion
    handlers = {
        transition.name: transition.handler for transition in merged.transitions
    }

    # then: transition IDs are qualified but both opaque refs remain byte-for-byte stable
    assert handlers == {
        "source.emit": "emit@source",
        "sink.receive": "receive@sink",
    }
    places = {place.name: place for place in merged.places}
    assert "sink.pulse_in" not in places
    assert places["source.pulse_out"].port is None
    assert (places["source.pulse_out"].annotations or {}).get("fusion") is True


def test_dsl_and_json_compositions_have_identical_records_and_final_marking() -> None:
    """Given both authoring forms, one pulse follows the same two-step engine trace."""
    # given: the BDD scenario and independently loaded DSL/JSON compositions
    scenario = _document(_SCENARIO_PATH)["cases"][0]
    expected_records = [
        {
            "transition": item["transition"],
            "status": item["status"],
            "sequence": index,
        }
        for index, item in enumerate(scenario["expectedFiringSequence"])
    ]

    # when: each merged net runs with fresh handlers and the same initial marking
    dsl_records, dsl_final = _run(_merged_from_dsl())
    json_records, json_final = _run(_merged_from_json())

    # then: source emits before sink receives, with exact equivalent observable state
    assert dsl_records == json_records == expected_records
    assert dsl_final == json_final == scenario["expectedFinalMarking"]
    assert [record["transition"] for record in dsl_records] == [
        "source.emit",
        "sink.receive",
    ]


def test_scenario_enabled_transition_and_journal_contract_are_exact() -> None:
    """Given the committed BDD case, declaration order and journal sequence are pinned."""
    # given: one seed in source.seed on the merged DSL net
    scenario = _document(_SCENARIO_PATH)["cases"][0]
    net = _merged_from_dsl()
    journal = JsonlJournal()
    engine = Engine(_registry(), journal=journal)
    initial = _marking(scenario["initialMarking"])

    # when: enablement is observed and the net runs exactly the scenario step bound
    enabled = engine.enabled_transitions(net, initial)
    engine.run(net, initial, max_steps=scenario["maxSteps"])

    # then: only emission starts enabled and journal transitions are contiguous
    assert enabled == scenario["expectedEnabledTransitions"]
    assert [
        {"transition": record["transition"], "sequence": record["sequence"]}
        for record in journal._records  # pyright: ignore[reportPrivateUsage]
    ] == scenario["expectedJournalSequence"]
