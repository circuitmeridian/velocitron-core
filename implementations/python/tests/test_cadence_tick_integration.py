"""Red CLI, visualization, and timed runtime contracts for Slice 09."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token
from velocitron.viz import main as viz_main


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "09-cadence-tick"
_DSL_PATH = _FIXTURE_ROOT / "cadence-tick.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "cadence-tick.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def _registry() -> HandlerRegistry:
    """Load only the cadence fixture handlers, without changing sys.path."""
    spec = importlib.util.spec_from_file_location(
        "cadence_tick_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_all(registry)
    return registry


def _token(type_: str, **data: Any) -> Token:
    return Token(type=type_, data=data)


def _marking(*, now: int, fired_at: int = 0, alert: bool = False) -> Marking:
    places: dict[str, list[Token]] = {
        "clock": [_token("clock", now=now)],
        "tick_latch": [_token("tick_latch", fired_at=fired_at, cadence_s=300)],
    }
    if alert:
        places["alert"] = [_token("alert")]
    return Marking(places)


def _records(journal: JsonlJournal) -> list[dict[str, Any]]:
    return journal._records  # pyright: ignore[reportPrivateUsage]


def test_cadence_cli_validates_and_converts_each_direction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired cadence fixtures, every DSL CLI path shares one exact net."""
    # given: the native timed DSL and its independent semantic JSON pair
    expected = _fixture_document()

    # when: validation and both conversion directions run through the real CLI
    assert dsl_main(["validate", str(_DSL_PATH)]) == 0
    validated = capsys.readouterr()
    assert dsl_main(["to-json", str(_DSL_PATH)]) == 0
    converted_json = capsys.readouterr()
    assert dsl_main(["to-petrinet", str(_JSON_PATH)]) == 0
    converted_dsl = capsys.readouterr()

    # then: output is deterministic and both conversions preserve the semantic model
    assert validated.out == "net\n" and validated.err == ""
    assert json.loads(converted_json.out) == expected and converted_json.err == ""
    assert (
        compile_petrinet_text(converted_dsl.out, "cadence-tick.cli-roundtrip.petrinet")
        == expected
    )
    assert converted_dsl.err == ""


def test_cadence_viz_exposes_topology_and_timed_metadata(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given cadence DSL, DOT shows conflict, read arcs, timer, and priority."""
    # given: one shared latch conflict and two named clock read arcs

    # when: the real visualization CLI renders the DSL fixture
    assert viz_main([str(_DSL_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: nine written arcs remain nine visible edges with both read glyphs
    assert len(edges) == 9
    assert (
        sum(
            '"clock" -> ' in line
            and "read clock" in line
            and 'color="#1f6feb", style=dashed' in line
            for line in edges
        )
        == 2
    )
    assert sum('"tick_latch" -> ' in line for line in edges) == 2
    assert sum(' -> "tick_latch"' in line for line in edges) == 2
    assert sum(' -> "refresh_due"' in line for line in edges) == 2
    assert "clock.now &gt;= latch.fired_at + latch.cadence_s" in dot
    assert "priority 10" in dot


@pytest.mark.parametrize(("now", "enabled"), [(0, False), (300, True)])
def test_timer_enablement_changes_only_at_maturity(now: int, enabled: bool) -> None:
    """Given token-carried cadence, runtime enablement follows its exact deadline."""
    # given: the same latch at one immature and one mature injected clock value
    net = parse_net(_JSON_PATH)
    engine = Engine(HandlerRegistry())

    # when: ordinary enablement evaluates the native timer per candidate binding
    actual = "on_tick" in engine.enabled_transitions(net, _marking(now=now))

    # then: equality at fired_at + cadence_s is mature, while time zero is not
    assert actual is enabled


def test_replacement_clock_injection_is_singleton_journaled_and_passive() -> None:
    """Given replacement injection, it records time without running a scheduler."""
    # given: an immature cadence net and the engine journal sequence
    net = parse_net(_JSON_PATH)
    journal = JsonlJournal()
    engine = Engine(_registry(), journal=journal)
    before = _marking(now=0)

    # when: the clock is replaced at the exact maturity boundary
    after, record = engine.inject_token(
        net, before, "clock", _token("clock", now=300), attempt=0, replace=True
    )

    # then: replacement is one journaled update and no transition fired implicitly
    assert list(after["clock"]) == [_token("clock", now=300)]
    assert list(after["tick_latch"]) == list(before["tick_latch"])
    assert not after.get("refresh_due")
    assert record["kind"] == "update"
    assert len(_records(journal)) == 1
    assert _records(journal)[0]["injectionId"] == record["injectionId"]
    assert _records(journal)[0]["sequence"] == 0
    assert engine.enabled_transitions(net, after) == ["on_tick"]


def test_mature_tick_firing_preserves_clock_read_and_rearms_latch() -> None:
    """Given a mature tick, firing consumes the latch but preserves its clock read."""
    # given: exactly one mature clock and one old cadence latch
    net = parse_net(_JSON_PATH)
    engine = Engine(_registry(), deposit_violation="raise")
    before = _marking(now=300)

    # when: on_tick fires once
    after, record = engine.fire(net, before, "on_tick", attempt=0)

    # then: the read clock remains and the handler deposits exact rearmed outputs
    assert record["status"] == "completed"
    assert list(after["clock"]) == [_token("clock", now=300)]
    assert list(after["tick_latch"]) == [
        _token("tick_latch", fired_at=300, cadence_s=300)
    ]
    assert [token.type for token in after["refresh_due"]] == ["refresh_due"]


def test_priority_alert_resets_mature_tick_before_it_can_fire() -> None:
    """Given simultaneous maturity and alert, priority reset wins to quiescence."""
    # given: both conflicting transitions enabled over one old latch
    net = parse_net(_JSON_PATH)
    journal = JsonlJournal()
    engine = Engine(_registry(), journal=journal, policy="priority")

    # when: the priority policy runs the net to quiescence
    after = engine.run(net, _marking(now=300, alert=True), max_steps=3)

    # then: alert alone fired, reset the latch deadline, and suppressed stale tick
    firings = [
        record["transition"] for record in _records(journal) if "transition" in record
    ]
    assert firings == ["on_alert"]
    assert list(after["tick_latch"]) == [
        _token("tick_latch", fired_at=300, cadence_s=300)
    ]
    assert [token.type for token in after["refresh_due"]] == ["refresh_due"]
    assert "on_tick" not in engine.enabled_transitions(net, after)


def test_default_first_found_ignores_alert_priority() -> None:
    """Given the same conflict, default policy keeps declaration order authoritative."""
    # given: simultaneous mature tick and higher-priority alert
    net = parse_net(_JSON_PATH)
    journal = JsonlJournal()
    engine = Engine(_registry(), journal=journal)

    # when: default first-found runs exactly one step
    engine.run(net, _marking(now=300, alert=True), max_steps=1)

    # then: earlier-declared on_tick fires despite on_alert priority 10
    assert [record["transition"] for record in _records(journal)] == ["on_tick"]


def test_reset_latch_has_an_isolated_next_deadline() -> None:
    """Given a reset latch, only its own token-carried next deadline matures."""
    # given: alert reset the cadence at 300, making its next deadline 600
    net = parse_net(_JSON_PATH)
    engine = Engine(HandlerRegistry())
    reset = _marking(now=300, fired_at=300)

    # when: replacement injection advances first below and then onto that deadline
    before_deadline, _ = engine.inject_token(
        net, reset, "clock", _token("clock", now=599), attempt=0, replace=True
    )
    at_deadline, _ = engine.inject_token(
        net, before_deadline, "clock", _token("clock", now=600), attempt=1, replace=True
    )

    # then: the old deadline stays irrelevant and exactly the reset deadline enables
    assert "on_tick" not in engine.enabled_transitions(net, before_deadline)
    assert "on_tick" in engine.enabled_transitions(net, at_deadline)
