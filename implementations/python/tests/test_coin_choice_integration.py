"""BDD integration coverage for the paired Slice02 Coin Choice fixtures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from velocitron.dsl.api import compile_petrinet_text, load_petrinet
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.viz import main as viz_main

_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_DIR = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "02-coin-choice"
_ORDERED_DSL_PATH = _FIXTURE_DIR / "coin-choice.petrinet"
_ORDERED_JSON_PATH = _FIXTURE_DIR / "coin-choice.json"
_ORDER_FREE_DSL_PATH = _FIXTURE_DIR / "coin-choice-order-free.petrinet"
_ORDER_FREE_JSON_PATH = _FIXTURE_DIR / "coin-choice-order-free.json"
_SCENARIO_PATH = _FIXTURE_DIR / "coin-choice.test.json"
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
    """Normalize immutable engine tokens to the JSON scenario representation."""
    return {
        place: [{"type": token.type, "data": token.data} for token in tokens]
        for place, tokens in marking.items()
        if tokens
    }


def _firing_sequence(journal: JsonlJournal) -> list[tuple[str, str]]:
    """Project the observable firing trace from the real engine journal."""
    return [
        (record["transition"], record["status"])
        for record in journal._records  # pyright: ignore[reportPrivateUsage]
    ]


def test_ordered_choice_compiles_to_its_json_pair_and_real_cli_draws_only_real_edges(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given explicit order facts, the paired model and DOT retain the fork, not a chain."""
    # given: the ordered source and its canonical JSON pair
    source = _ORDERED_DSL_PATH.read_text(encoding="utf-8")
    expected_document = json.loads(_ORDERED_JSON_PATH.read_text(encoding="utf-8"))

    # when: source is compiled/resolved, loaded, and rendered through the real CLI
    assert compile_petrinet_text(source, str(_ORDERED_DSL_PATH)) == expected_document
    dsl_net = load_petrinet(_ORDERED_DSL_PATH)
    assert dsl_net == parse_net(_ORDERED_JSON_PATH)
    assert viz_main([str(_ORDERED_DSL_PATH)]) == 0
    dot = capsys.readouterr().out

    # then: accept and return share one source, accept alone deposits, and return is a sink
    edge_lines = [line for line in dot.splitlines() if " -> " in line]
    assert len(edge_lines) == 3
    assert any('"coin_slot" -> "accept_coin"' in line for line in edge_lines)
    assert any('"coin_slot" -> "return_coin"' in line for line in edge_lines)
    assert any('"accept_coin" -> "cash_box"' in line for line in edge_lines)
    assert '"return_coin" ->' not in dot
    assert '"return_coin" -> "accept_coin"' not in dot


def test_ordered_choice_uses_fixture_scenario_for_first_found_accept_and_cash_box() -> (
    None
):
    """Given ordered conflict facts, first-found accepts the coin as the scenario specifies."""
    # given: the real ordered DSL net, shared handlers, and its declarative scenario
    net = load_petrinet(_ORDERED_DSL_PATH)
    assert net.initial_marking is not None
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    journal = JsonlJournal()
    engine = Engine(_contrived_registry(), journal=journal, deposit_violation="raise")

    # when: the engine runs no further than the fixture's stated step budget
    assert engine.enabled_transitions(net, net.initial_marking) == [
        "accept_coin",
        "return_coin",
    ]
    final_marking = engine.run(net, net.initial_marking, max_steps=scenario["maxSteps"])

    # then: ordered first-found selection and the final marking exactly follow the fixture
    assert _firing_sequence(journal) == [
        (record["transition"], record["status"])
        for record in scenario["expectedFiringSequence"]
    ]
    assert _nonempty_marking(final_marking) == scenario["expectedFinalMarking"]
    assert engine.enabled_transitions(net, final_marking) == []


def test_order_free_choice_returns_the_coin_to_its_direct_sink_without_output() -> None:
    """Given no order facts, declaration order selects the direct return sink first."""
    # given: the order-free source/JSON pair, whose return declaration comes first
    source = _ORDER_FREE_DSL_PATH.read_text(encoding="utf-8")
    expected_document = json.loads(_ORDER_FREE_JSON_PATH.read_text(encoding="utf-8"))
    assert compile_petrinet_text(source, str(_ORDER_FREE_DSL_PATH)) == expected_document
    net = load_petrinet(_ORDER_FREE_DSL_PATH)
    assert net == parse_net(_ORDER_FREE_JSON_PATH)
    assert net.initial_marking is not None
    journal = JsonlJournal()
    engine = Engine(_contrived_registry(), journal=journal, deposit_violation="raise")

    # when: the conflict is resolved by the order-free declaration order
    assert engine.enabled_transitions(net, net.initial_marking) == [
        "return_coin",
        "accept_coin",
    ]
    final_marking = engine.run(net, net.initial_marking, max_steps=1)

    # then: return wins, consumes the coin, and produces no cash-box output
    assert _firing_sequence(journal) == [("return_coin", "completed")]
    assert _nonempty_marking(final_marking) == {}
    assert engine.enabled_transitions(net, final_marking) == []
