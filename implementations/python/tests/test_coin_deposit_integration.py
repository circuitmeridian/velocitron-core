"""End-to-end coverage for the first paired DSL/JSON Coin Deposit fixture."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

from velocitron.dsl.api import parse_petrinet_text
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_DIR = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "01-coin-deposit"
_DSL_PATH = _FIXTURE_DIR / "coin-deposit.petrinet"
_JSON_PATH = _FIXTURE_DIR / "coin-deposit.json"
_SCENARIO_PATH = _FIXTURE_DIR / "coin-deposit.test.json"
_HANDLERS_PATH = _REPOSITORY_ROOT / "examples" / "contrived" / "handlers.py"


def _contrived_registry() -> HandlerRegistry:
    """Load the real shared handler registry without changing ``sys.path``."""
    spec = importlib.util.spec_from_file_location("contrived_handlers", _HANDLERS_PATH)
    assert spec is not None and spec.loader is not None
    handlers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handlers)
    registry = HandlerRegistry()
    handlers.register_all(registry)
    return registry


def test_coin_deposit_dsl_and_json_share_model_and_dot_topology(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The paired representations resolve to the same two-place coin net."""
    dsl_net = parse_petrinet_text(
        _DSL_PATH.read_text(encoding="utf-8"), source_name=_DSL_PATH.name
    )
    json_net = parse_net(_JSON_PATH)

    assert dsl_net == json_net

    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    assert dot.count("shape=ellipse") == 2
    assert dot.count("shape=box") == 1
    edge_lines = [line for line in dot.splitlines() if " -> " in line]
    assert len(edge_lines) == 2
    assert all('font point-size="10">coin</font>' in line for line in edge_lines)
    assert any('"coin_slot" -> "accept_coin"' in line for line in edge_lines)
    assert any('"accept_coin" -> "cash_box"' in line for line in edge_lines)


def test_coin_deposit_fires_once_then_quiesces_in_cash_box() -> None:
    """The DSL net satisfies its paired execution scenario with real handlers."""
    net = parse_petrinet_text(
        _DSL_PATH.read_text(encoding="utf-8"), source_name=_DSL_PATH.name
    )
    assert net.initial_marking is not None
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    journal = JsonlJournal()
    engine = Engine(_contrived_registry(), journal=journal, deposit_violation="raise")

    final_marking = engine.run(net, net.initial_marking, max_steps=scenario["maxSteps"])

    actual_sequence = [
        (record["transition"], record["status"])
        for record in journal._records  # pyright: ignore[reportPrivateUsage]
    ]
    expected_sequence = [
        (record["transition"], record["status"])
        for record in scenario["expectedFiringSequence"]
    ]
    assert actual_sequence == expected_sequence
    assert engine.enabled_transitions(net, final_marking) == []
    actual_final_marking = {
        place: [{"type": token.type, "data": token.data} for token in tokens]
        for place, tokens in final_marking.items()
        if tokens
    }
    assert actual_final_marking == scenario["expectedFinalMarking"]
