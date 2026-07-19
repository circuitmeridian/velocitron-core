"""Red CLI, visualization, engine, and composition contracts for Slice 08."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.contract import (
    GuardHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.composition import merge_nets
from velocitron.dsl.api import compile_petrinet_text, load_petrinet
from velocitron.dsl.cli import main as dsl_main
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token, Wire
from velocitron.viz import main as viz_main
from velocitron.viz import net_to_dot


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "08-guarded-curation-choice"
)
_DSL_PATH = _FIXTURE_ROOT / "guarded-curation-choice.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "guarded-curation-choice.json"
_SCENARIO_PATH = _FIXTURE_ROOT / "guarded-curation-choice.test.json"
_HANDLERS_PATH = _FIXTURE_ROOT / "handlers.py"


def _registry(variant: str = "complementary") -> HandlerRegistry:
    """Load the fixture registry variant without changing sys.path."""
    spec = importlib.util.spec_from_file_location(
        "guarded_curation_choice_handlers", _HANDLERS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = HandlerRegistry()
    module.register_all(registry, variant=variant)
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


def test_guarded_fixture_api_and_cli_preserve_exact_runtime_refs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given paired fixtures, APIs and CLI preserve exact opaque guard refs."""
    # given: the canonical guarded source and independently paired JSON
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: API compilation, validation, and both conversions run without a registry
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

    # then: every route retains the same structural model and registry strings
    assert actual == expected
    assert validated_dsl.out == validated_json.out == "net\n"
    assert validated_dsl.err == validated_json.err == ""
    assert json.loads(converted_json.out) == expected
    assert converted_json.err == ""
    assert (
        compile_petrinet_text(
            converted_dsl.out, "guarded-curation-choice.cli-roundtrip.petrinet"
        )
        == expected
    )
    assert converted_dsl.err == ""


def test_guarded_viz_exposes_two_conflicting_transitions_and_both_guards(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given guarded JSON, DOT shows the structural choice and named guards."""
    # given: two transitions consuming the same curation place

    # when: the real visualization CLI renders the core fixture
    assert viz_main([str(_JSON_PATH)]) == 0
    dot = capsys.readouterr().out
    edges = [line for line in dot.splitlines() if " -> " in line]

    # then: both guarded boxes and four distinct arcs make the conflict visible
    assert dot.count("shape=box") == 2
    assert "guard: speak_eligible@curate?" in dot
    assert "guard: speak_skip@curate?" in dot
    assert len(edges) == 4
    assert sum('"curation_token" -> ' in line for line in edges) == 2
    assert any('"speak_gate_speak" -> "speak_request"' in line for line in edges)
    assert any('"speak_gate_skip" -> "final_utterance"' in line for line in edges)


@pytest.mark.parametrize(
    "case_name",
    [
        "should_speak true selects speak branch",
        "should_speak false selects skip branch",
    ],
)
def test_complementary_guards_select_and_fire_exactly_one_branch(
    case_name: str,
) -> None:
    """Given a boolean curation fact, exactly its complementary branch fires."""
    # given: a scenario with the fixture's complementary guard registry
    scenario = _scenario(case_name)
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(cast(str, scenario["registryVariant"])))

    # when: enablement is queried and the default policy runs one step
    enabled = engine.enabled_transitions(net, before)
    after = engine.run(net, before, max_steps=cast(int, scenario["maxSteps"]))

    # then: only the expected branch was enabled and only its output was deposited
    assert enabled == scenario["expectedEnabledTransitions"]
    assert len(enabled) == 1
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]
    assert "curation_token" not in _nonempty_marking(after)


@pytest.mark.parametrize(
    "case_name",
    [
        "missing speak guard disables speak branch",
        "raising speak guard disables speak branch",
    ],
)
def test_missing_or_raising_guard_fails_closed_without_mutation(case_name: str) -> None:
    """Given an unavailable or raising guard, its branch is simply not enabled."""
    # given: a true curation token and the requested broken registry variant
    scenario = _scenario(case_name)
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(cast(str, scenario["registryVariant"])))

    # when: enablement and a bounded run exercise runtime guard resolution
    enabled = engine.enabled_transitions(net, before)
    after = engine.run(net, before, max_steps=cast(int, scenario["maxSteps"]))

    # then: no exception escapes, no transition fires, and the token remains exact
    assert enabled == scenario["expectedEnabledTransitions"] == []
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]


@pytest.mark.parametrize(
    "case_name",
    [
        "both true follows declaration order",
        "both false preserves curation token",
    ],
)
def test_noncomplementary_guards_expose_policy_and_quiescence(case_name: str) -> None:
    """Given broken complementarity, the engine neither proves nor repairs it."""
    # given: a fixture scenario where both guards deliberately return one value
    scenario = _scenario(case_name)
    net = parse_net(_JSON_PATH)
    before = _marking(scenario["initialMarking"])
    engine = Engine(_registry(cast(str, scenario["registryVariant"])))

    # when: declaration-ordered enablement and the first-found policy run
    enabled = engine.enabled_transitions(net, before)
    after = engine.run(net, before, max_steps=cast(int, scenario["maxSteps"]))

    # then: both true selects speak first; both false leaves the shared token
    assert enabled == scenario["expectedEnabledTransitions"]
    assert _nonempty_marking(after) == scenario["expectedFinalMarking"]
    if case_name.startswith("both true"):
        assert enabled == ["speak_gate_speak", "speak_gate_skip"]
        assert "speak_request" in _nonempty_marking(after)
    else:
        assert enabled == []
        assert "curation_token" in _nonempty_marking(after)


def test_guard_and_handler_share_the_exact_selected_binding_object() -> None:
    """Given one candidate, guard and handler receive one place-keyed binding."""
    # given: spies registered in independent guard and transition namespaces
    net = parse_net(_JSON_PATH)
    token = Token(type="curation", data={"should_speak": True})
    marking = Marking({"curation_token": [token]})
    observed: dict[str, object] = {}
    registry = HandlerRegistry()

    def guard(inp: GuardHandlerInput) -> bool:
        observed["guard"] = inp["inputTokens"]
        return True

    def handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        observed["handler"] = inp["inputTokens"]
        return {
            "status": "completed",
            "outputTokens": {
                "speak_request": [Token(type="speak_req", data={})],
            },
            "error": None,
            "metadata": {},
        }

    registry.register_guard("speak_eligible@curate", guard)
    registry.register_transition("request_speak@curate", handler)
    engine = Engine(registry)

    # when: the speak branch performs one direct firing
    after, record = engine.fire(net, marking, "speak_gate_speak", attempt=0)

    # then: both callables saw the same dict, key, list, and original token identity
    assert record["status"] == "completed"
    assert observed["guard"] is observed["handler"]
    binding = cast(dict[str, list[Token]], observed["handler"])
    assert list(binding) == ["curation_token"]
    assert len(binding["curation_token"]) == 1
    assert binding["curation_token"][0] is token
    assert list(after["speak_request"]) == [Token(type="speak_req", data={})]


def test_composition_qualifies_structure_but_preserves_scoped_runtime_refs() -> None:
    """Given alias composition, only structural names are qualified."""
    # given: the guarded fixture parsed as one constituent named curate
    net = parse_net(_JSON_PATH)

    # when: the real composition merge qualifies an otherwise unwired constituent
    merged = merge_nets({"curate": net}, cast(list[Wire], []))
    dot = net_to_dot(merged)

    # then: places, transitions, and arcs are qualified while refs remain opaque
    assert [transition.name for transition in merged.transitions] == [
        "curate.speak_gate_speak",
        "curate.speak_gate_skip",
    ]
    assert [
        (transition.handler, transition.guard) for transition in merged.transitions
    ] == [
        ("request_speak@curate", "speak_eligible@curate"),
        ("skip_speak@curate", "speak_skip@curate"),
    ]
    assert {place.name for place in merged.places} == {
        "curate.curation_token",
        "curate.speak_request",
        "curate.final_utterance",
    }
    assert [arc.from_place for arc in merged.arcs if arc.from_place is not None] == [
        "curate.curation_token",
        "curate.curation_token",
    ]
    assert "curate.speak_gate_speak" in dot
    assert "guard: speak_eligible@curate?" in dot
    assert "curate.speak_eligible@curate" not in dot
