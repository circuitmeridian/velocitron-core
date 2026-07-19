"""Slice 12 CLI, visualization, and end-to-end graduation contracts."""

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
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT
    / "examples"
    / "capability-ladder"
    / "12-guinan-graduation-fragment"
)
_CADENCE_DSL = _FIXTURE_ROOT / "graduation_cadence.petrinet"
_CADENCE_JSON = _FIXTURE_ROOT / "graduation_cadence.json"
_CURATE_DSL = _FIXTURE_ROOT / "graduation_curate.petrinet"
_CURATE_JSON = _FIXTURE_ROOT / "graduation_curate.json"
_SPEAKS_DSL = _FIXTURE_ROOT / "graduation_speaks.petrinet"
_SPEAKS_JSON = _FIXTURE_ROOT / "graduation_speaks.json"
_COMPOSITION_DSL = _FIXTURE_ROOT / "guinan_graduation.petrinet"
_COMPOSITION_JSON = _FIXTURE_ROOT / "guinan_graduation.json"
_SCENARIO_JSON = _FIXTURE_ROOT / "guinan_graduation.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"
_ALL_PAIRS = [
    (_CADENCE_DSL, _CADENCE_JSON, "net"),
    (_CURATE_DSL, _CURATE_JSON, "net"),
    (_SPEAKS_DSL, _SPEAKS_JSON, "net"),
    (_COMPOSITION_DSL, _COMPOSITION_JSON, "composition"),
]


def _document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _compile(path: Path) -> dict[str, Any]:
    return compile_petrinet_text(path.read_text(encoding="utf-8"), str(path))


def _merged_from_dsl() -> Net:
    def load_net(ref: str) -> dict[str, Any]:
        return _compile(_FIXTURE_ROOT / ref)

    composition = cast(Any, parse_composition)(
        _compile(_COMPOSITION_DSL), origin=_FIXTURE_ROOT, net_loader=load_net
    )
    return merge_composition(composition)


def _merged_from_json() -> Net:
    composition = cast(Any, parse_composition)(
        _document(_COMPOSITION_JSON), origin=_FIXTURE_ROOT
    )
    return merge_composition(composition)


def _destinations(net: Net, transition: str) -> list[str]:
    return [
        cast(str, arc.to_place)
        for arc in net.arcs
        if arc.from_transition == transition and arc.produce is not None
    ]


def _registry(net: Net) -> HandlerRegistry:
    """Bind fixture factories to destinations resolved from this merged instance."""
    spec = importlib.util.spec_from_file_location(
        "guinan_graduation_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert _destinations(net, "cadence.on_tick") == [
        "cadence.tick_latch",
        "cadence.refresh_due_out",
    ]
    assert _destinations(net, "curate.curate") == ["curate.speak_request"]
    assert _destinations(net, "speaks.start") == [
        "speaks.in_flight",
        "speaks.work",
    ]
    assert _destinations(net, "speaks.finish") == ["speaks.utterance_out"]

    registry = HandlerRegistry()
    module.register_instance(
        registry,
        cadence_scope="cadence",
        curate_scope="curate",
        speaks_scope="speaks",
        latch_destination=_destinations(net, "cadence.on_tick")[0],
        refresh_due_destination=_destinations(net, "cadence.on_tick")[1],
        speak_request_destination=_destinations(net, "curate.curate")[0],
        in_flight_destination=_destinations(net, "speaks.start")[0],
        work_destination=_destinations(net, "speaks.start")[1],
        utterance_destination=_destinations(net, "speaks.finish")[0],
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


def _plain_tokens(tokens: dict[str, list[Token]]) -> dict[str, list[dict[str, Any]]]:
    return {
        place: [{"type": token.type, "data": token.data} for token in values]
        for place, values in tokens.items()
    }


def _plain_marking(marking: Marking) -> dict[str, list[dict[str, Any]]]:
    return _plain_tokens(
        {place: list(tokens) for place, tokens in marking.items() if tokens}
    )


def _scenario() -> dict[str, Any]:
    return cast(dict[str, Any], _document(_SCENARIO_JSON)["cases"][0])


def _run(net: Net) -> tuple[list[dict[str, Any]], Marking, list[dict[str, Any]]]:
    scenario = _scenario()
    journal = JsonlJournal()
    engine = Engine(_registry(net), journal=journal, deposit_violation="raise")
    marking = _marking(scenario["initialMarking"])
    injection = scenario["injections"][0]
    marking, _ = engine.inject_token(
        net,
        marking,
        injection["place"],
        _token(injection["token"]),
        attempt=injection["attempt"],
        replace=injection["replace"],
    )
    final = engine.run(net, marking, max_steps=scenario["maxSteps"])
    records = journal._records  # pyright: ignore[reportPrivateUsage]
    firings = [record for record in records if "transition" in record]
    return firings, final, records


def _stable_firing(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "transition": record["transition"],
        "status": record["status"],
        "sequence": record["sequence"],
        "attempt": record["attempt"],
        "inputTokens": _plain_tokens(record["inputTokens"]),
        "outputTokens": _plain_tokens(record["outputTokens"]),
        "error": record["error"],
        "metadata": record["metadata"],
    }


def test_cli_validates_and_converts_all_four_documents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Public CLI paths resolve portable refs and preserve each paired document."""
    for dsl_path, json_path, kind in _ALL_PAIRS:
        expected = _document(json_path)
        authored_expected = _document(json_path)
        if kind == "composition":
            for item in authored_expected["nets"]:
                item["ref"] = item["ref"].replace(".json", ".petrinet")

        assert dsl_main(["validate", str(dsl_path)]) == 0
        validated = capsys.readouterr()
        assert validated.out == f"{kind}\n" and validated.err == ""

        assert dsl_main(["to-json", str(dsl_path)]) == 0
        converted_json = capsys.readouterr()
        assert json.loads(converted_json.out) == authored_expected
        assert converted_json.err == ""

        assert dsl_main(["to-petrinet", str(json_path)]) == 0
        converted_dsl = capsys.readouterr()
        assert compile_petrinet_text(converted_dsl.out, str(dsl_path)) == expected
        assert converted_dsl.err == ""


def test_merged_visualization_exposes_the_complete_native_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Merged DOT keeps every native arc and boundary without synthetic routing."""
    assert viz_main([str(_COMPOSITION_DSL), "--merged"]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    assert len(edges) == 14
    assert dot.count("shape=box") == 4
    for name in [
        "cadence.on_tick",
        "cadence.refresh_due_out",
        "curate.curate",
        "curate.speak_request",
        "speaks.start",
        "speaks.finish",
        "speaks.utterance_out",
    ]:
        assert name in dot
    assert "curate.refresh_due_in" not in dot
    assert "speaks.request_in" not in dot
    assert dot.count("arrowhead=odot") == 2
    assert 'color="#1f6feb", style=dashed' in dot
    assert "clock.now &gt;= latch.fired_at + latch.cadence_s" in dot
    assert "port output" in dot and "utterance" in dot
    assert dot.count('class="fusion"') >= 2


def test_clock_replacement_is_passive_then_enables_only_cadence() -> None:
    """Clock injection updates one internal token without an implicit scheduler step."""
    scenario = _scenario()
    net = _merged_from_dsl()
    journal = JsonlJournal()
    engine = Engine(_registry(net), journal=journal)
    before = _marking(scenario["initialMarking"])
    assert before == net.initial_marking
    assert (
        engine.enabled_transitions(net, before)
        == scenario["expectedEnabledTransitions"]
    )

    injection = scenario["injections"][0]
    after, record = engine.inject_token(
        net,
        before,
        injection["place"],
        _token(injection["token"]),
        attempt=injection["attempt"],
        replace=injection["replace"],
    )

    assert list(after["cadence.clock"]) == [Token("clock", {"now": 300})]
    assert list(after["cadence.tick_latch"]) == list(before["cadence.tick_latch"])
    assert record["kind"] == "update"
    assert record["place"] == "cadence.clock"
    assert (
        engine.enabled_transitions(net, after)
        == scenario["expectedEnabledTransitionsAfterInjection"]
    )
    assert len(journal._records) == 1  # pyright: ignore[reportPrivateUsage]


def test_exact_four_firing_chain_drains_fusions_and_internal_window() -> None:
    """The timer drives Curate and Speaks to one re-exposed utterance."""
    scenario = _scenario()
    firings, final, records = _run(_merged_from_dsl())

    assert [
        {"transition": record["transition"], "status": record["status"]}
        for record in firings
    ] == scenario["expectedFiringSequence"]
    assert [
        (
            {
                "kind": record["kind"],
                "place": record["place"],
                "sequence": record["sequence"],
            }
            if "kind" in record
            else {"transition": record["transition"], "sequence": record["sequence"]}
        )
        for record in records
    ] == scenario["expectedJournalSequence"]
    assert [_stable_firing(record) for record in firings] == [
        {
            "transition": "cadence.on_tick",
            "status": "completed",
            "sequence": 1,
            "attempt": 0,
            "inputTokens": {
                "cadence.tick_latch": [
                    {"type": "tick_latch", "data": {"fired_at": 0, "cadence_s": 300}}
                ],
                "cadence.clock": [{"type": "clock", "data": {"now": 300}}],
            },
            "outputTokens": {
                "cadence.tick_latch": [
                    {"type": "tick_latch", "data": {"fired_at": 300, "cadence_s": 300}}
                ],
                "cadence.refresh_due_out": [
                    {"type": "refresh_due", "data": {"trigger": "tick"}}
                ],
            },
            "error": None,
            "metadata": {},
        },
        {
            "transition": "curate.curate",
            "status": "completed",
            "sequence": 2,
            "attempt": 1,
            "inputTokens": {
                "cadence.refresh_due_out": [
                    {"type": "refresh_due", "data": {"trigger": "tick"}}
                ]
            },
            "outputTokens": {
                "curate.speak_request": [
                    {"type": "speak_req", "data": {"trigger": "tick"}}
                ]
            },
            "error": None,
            "metadata": {},
        },
        {
            "transition": "speaks.start",
            "status": "completed",
            "sequence": 3,
            "attempt": 2,
            "inputTokens": {
                "curate.speak_request": [
                    {"type": "speak_req", "data": {"trigger": "tick"}}
                ]
            },
            "outputTokens": {
                "speaks.in_flight": [
                    {"type": "speak_token", "data": {"trigger": "tick"}}
                ],
                "speaks.work": [{"type": "speak_work", "data": {"trigger": "tick"}}],
            },
            "error": None,
            "metadata": {},
        },
        {
            "transition": "speaks.finish",
            "status": "completed",
            "sequence": 4,
            "attempt": 3,
            "inputTokens": {
                "speaks.work": [{"type": "speak_work", "data": {"trigger": "tick"}}],
                "speaks.in_flight": [
                    {"type": "speak_token", "data": {"trigger": "tick"}}
                ],
            },
            "outputTokens": {
                "speaks.utterance_out": [
                    {"type": "utterance", "data": {"trigger": "tick"}}
                ]
            },
            "error": None,
            "metadata": {},
        },
    ]
    assert _plain_marking(final) == scenario["expectedFinalMarking"]
    for drained in [
        "cadence.refresh_due_out",
        "curate.speak_request",
        "speaks.in_flight",
        "speaks.work",
    ]:
        assert not final.get(drained)


@pytest.mark.parametrize(
    ("stale_place", "stale_token"),
    [
        ("speaks.in_flight", Token("speak_token", {"trigger": "stale"})),
        ("speaks.work", Token("speak_work", {"trigger": "stale"})),
    ],
    ids=["in-flight", "work"],
)
def test_either_stale_internal_token_inhibits_speaks_start(
    stale_place: str, stale_token: Token
) -> None:
    """Each clean-start zero-test independently blocks a queued request."""
    net = _merged_from_dsl()
    engine = Engine(_registry(net))
    marking = _marking(_scenario()["initialMarking"])
    marking, _ = engine.inject_token(
        net,
        marking,
        "cadence.clock",
        Token("clock", {"now": 300}),
        attempt=0,
        replace=True,
    )
    marking = engine.run(net, marking, max_steps=2)
    assert engine.enabled_transitions(net, marking) == ["speaks.start"]

    blocked = marking.set(stale_place, [stale_token])
    assert blocked.get("curate.speak_request")
    assert "speaks.start" not in engine.enabled_transitions(net, blocked)


def _enabled_sequence(net: Net) -> list[list[str]]:
    scenario = _scenario()
    engine = Engine(_registry(net), deposit_violation="raise")
    marking = _marking(scenario["initialMarking"])
    sequence = [engine.enabled_transitions(net, marking)]
    injection = scenario["injections"][0]
    marking, _ = engine.inject_token(
        net,
        marking,
        injection["place"],
        _token(injection["token"]),
        attempt=injection["attempt"],
        replace=injection["replace"],
    )
    sequence.append(engine.enabled_transitions(net, marking))
    for attempt in range(scenario["maxSteps"]):
        enabled = sequence[-1]
        assert len(enabled) == 1
        marking, record = engine.fire(net, marking, enabled[0], attempt=attempt)
        assert record["status"] == "completed"
        sequence.append(engine.enabled_transitions(net, marking))
    return sequence


def test_dsl_and_json_runs_have_identical_records_and_final_markings() -> None:
    """Both origins preserve opaque refs, enablement, firing, and token state."""
    dsl_net = _merged_from_dsl()
    json_net = _merged_from_json()
    expected_handlers = [
        "on_tick@cadence",
        "curate@curate",
        "start@speaks",
        "finish@speaks",
    ]
    assert [
        transition.handler for transition in dsl_net.transitions
    ] == expected_handlers
    assert [
        transition.handler for transition in json_net.transitions
    ] == expected_handlers

    assert (
        _enabled_sequence(dsl_net)
        == _enabled_sequence(json_net)
        == [
            [],
            ["cadence.on_tick"],
            ["curate.curate"],
            ["speaks.start"],
            ["speaks.finish"],
            [],
        ]
    )
    dsl_firings, dsl_final, dsl_records = _run(dsl_net)
    json_firings, json_final, json_records = _run(json_net)
    assert [_stable_firing(record) for record in dsl_firings] == [
        _stable_firing(record) for record in json_firings
    ]
    assert [
        (record.get("kind"), record.get("transition"), record["sequence"])
        for record in dsl_records
    ] == [
        (record.get("kind"), record.get("transition"), record["sequence"])
        for record in json_records
    ]
    assert dsl_final == json_final
    assert _plain_marking(dsl_final) == _scenario()["expectedFinalMarking"]
