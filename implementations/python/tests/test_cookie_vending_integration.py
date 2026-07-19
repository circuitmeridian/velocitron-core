"""Red end-to-end contracts reusing the existing Cookie Vending artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet, load_petrinet
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_DSL_PATH = (
    _REPOSITORY_ROOT
    / "examples"
    / "capability-ladder"
    / "03-cookie-vending"
    / "cookie-vending.petrinet"
)
_COOKIE_JSON_PATH = _REPOSITORY_ROOT / "examples" / "contrived" / "cookie_vending.json"
_SCENARIO_PATH = (
    _REPOSITORY_ROOT / "examples" / "contrived" / "cookie_vending.test.json"
)
_HANDLERS_PATH = _REPOSITORY_ROOT / "examples" / "contrived" / "handlers.py"


def _contrived_registry() -> HandlerRegistry:
    """Load the real shared handlers without changing ``sys.path``."""
    spec = importlib.util.spec_from_file_location("contrived_handlers", _HANDLERS_PATH)
    assert spec is not None and spec.loader is not None
    handlers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handlers)
    registry = HandlerRegistry()
    handlers.register_all(registry)
    return registry


def _nonempty_marking(marking: Any) -> dict[str, list[dict[str, Any]]]:
    """Normalize immutable engine tokens to the existing scenario representation."""
    return {
        place: [{"type": token.type, "data": token.data} for token in tokens]
        for place, tokens in marking.items()
        if tokens
    }


def _firing_sequence(journal: JsonlJournal) -> list[tuple[str, str]]:
    """Project the observable trace from the real engine journal."""
    return [
        (record["transition"], record["status"])
        for record in journal._records  # pyright: ignore[reportPrivateUsage]
    ]


def test_cookie_dsl_reuses_core_json_and_cli_conversion_is_deterministic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the paired source, compile and CLI paths use the existing core validator."""
    # given: the Slice 03 DSL and the pre-existing contrived JSON document
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = json.loads(_COOKIE_JSON_PATH.read_text(encoding="utf-8"))

    # when: API compilation and model loading traverse the real parser
    assert compile_petrinet_text(source, str(_DSL_PATH)) == expected
    assert load_petrinet(_DSL_PATH) == parse_net(_COOKIE_JSON_PATH)

    # then: validation and JSON conversion are clean stdout-only CLI operations
    assert dsl_main(["validate", str(_DSL_PATH)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "net\n"
    assert captured.err == ""

    assert dsl_main(["to-json", str(_DSL_PATH)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == expected
    assert captured.err == ""

    # when: unannotated JSON is converted repeatedly through canonical DSL
    assert dsl_main(["to-petrinet", str(_COOKIE_JSON_PATH)]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert first.out == emit_petrinet(expected)
    assert (
        compile_petrinet_text(first.out, "cookie-vending.canonical.petrinet")
        == expected
    )

    # then: a second canonical conversion is byte-for-byte deterministic
    assert (
        emit_petrinet(
            compile_petrinet_text(first.out, "cookie-vending.canonical.petrinet")
        )
        == first.out
    )


def test_cookie_dsl_visualizes_exact_fan_out_fan_in_conflict_and_sink(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Cookie Vending DSL, DOT contains only its five nodes, four transitions, and arcs."""
    # given: the direct DSL path with conflict, fan-out, fan-in, and consume-only sink

    # when: the existing visualization CLI loads and renders it
    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    edge_lines = [line for line in dot.splitlines() if " -> " in line]

    # then: all and only the eight semantic edges appear with their colors visible
    assert dot.count("shape=ellipse") == 5
    assert dot.count("shape=box") == 4
    assert len(edge_lines) == 8
    expected_edges = {
        ('"coin_slot"', '"accept_coin"', "coin"),
        ('"coin_slot"', '"return_coin"', "coin"),
        ('"accept_coin"', '"cash_box"', "coin"),
        ('"accept_coin"', '"signal"', "signal"),
        ('"signal"', '"vend_packet"', "signal"),
        ('"storage"', '"vend_packet"', "packet"),
        ('"vend_packet"', '"compartment"', "packet"),
        ('"compartment"', '"take_packet"', "packet"),
    }
    for source, destination, color in expected_edges:
        assert any(
            f"{source} -> {destination}" in line
            and f'<font point-size="10">{color}</font>' in line
            for line in edge_lines
        )
    assert '"return_coin" ->' not in dot
    assert '"take_packet" ->' not in dot


def test_cookie_scenario_routes_literal_signal_and_packet_then_quiesces() -> None:
    """Given existing handlers and scenario, the DSL preserves the exact trace and marking."""
    # given: the compiled DSL net, authoritative shared handlers, and existing scenario
    net = load_petrinet(_DSL_PATH)
    assert net.initial_marking is not None
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    journal = JsonlJournal()
    engine = Engine(_contrived_registry(), journal=journal, deposit_violation="raise")

    # when: first-found enablement observes the shared coin-place conflict
    assert engine.enabled_transitions(net, net.initial_marking) == [
        "accept_coin",
        "return_coin",
    ]
    after_accept, accept_record = engine.fire(
        net, net.initial_marking, "accept_coin", attempt=0
    )

    # then: the handler routes the coin while the literal template independently signals
    assert _nonempty_marking(after_accept) == {
        "cash_box": [{"type": "coin", "data": {}}],
        "signal": [{"type": "signal", "data": {}}],
        "storage": [{"type": "packet", "data": {}}] * 5,
    }
    assert engine.enabled_transitions(net, after_accept) == ["vend_packet"]

    # when: vend consumes both required colors and routes the stored packet
    after_vend, vend_record = engine.fire(net, after_accept, "vend_packet", attempt=1)

    # then: no signal remains and only the compartment sink is enabled
    assert _nonempty_marking(after_vend) == {
        "cash_box": [{"type": "coin", "data": {}}],
        "storage": [{"type": "packet", "data": {}}] * 4,
        "compartment": [{"type": "packet", "data": {}}],
    }
    assert engine.enabled_transitions(net, after_vend) == ["take_packet"]

    # when: the customer takes the routed packet
    final_marking, take_record = engine.fire(net, after_vend, "take_packet", attempt=2)

    # then: the reused scenario's exact trace and final marking are preserved
    assert [
        (record["transition"], record["status"])
        for record in (accept_record, vend_record, take_record)
    ] == [
        (record["transition"], record["status"])
        for record in scenario["expectedFiringSequence"]
    ]
    assert _firing_sequence(journal) == [
        (record["transition"], record["status"])
        for record in scenario["expectedFiringSequence"]
    ]
    assert _nonempty_marking(final_marking) == scenario["expectedFinalMarking"]
    assert engine.enabled_transitions(net, final_marking) == []
