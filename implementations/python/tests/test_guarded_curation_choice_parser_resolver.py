"""Red parser, resolver, and canonical contracts for Slice 08."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import parse_net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "08-guarded-curation-choice"
)
_DSL_PATH = _FIXTURE_ROOT / "guarded-curation-choice.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "guarded-curation-choice.json"


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def test_direct_guard_facts_lower_and_resolve_as_opaque_transition_refs() -> None:
    """Given direct guard facts, lowering and resolution preserve exact refs."""
    # given: the canonical two-branch source with @-scoped runtime refs
    source = _DSL_PATH.read_text(encoding="utf-8")

    # when: the source is progressively lowered and completely resolved
    portable = lower_petrinet_text(source, str(_DSL_PATH))
    actual = compile_petrinet_text(source, str(_DSL_PATH))

    # then: guards remain direct transition facts in declaration order
    guard_facts = [
        fact for fact in portable["contributions"] if fact["kind"] == "transition.guard"
    ]
    assert [fact["target"] for fact in guard_facts] == [
        {"type": "transition", "name": "speak_gate_speak"},
        {"type": "transition", "name": "speak_gate_skip"},
    ]
    assert [fact["value"] for fact in guard_facts] == [
        {"guard": "speak_eligible@curate"},
        {"guard": "speak_skip@curate"},
    ]
    assert actual["transitions"] == [
        {
            "name": "speak_gate_speak",
            "handler": "request_speak@curate",
            "guard": "speak_eligible@curate",
        },
        {
            "name": "speak_gate_skip",
            "handler": "skip_speak@curate",
            "guard": "speak_skip@curate",
        },
    ]


def test_identical_repeated_guard_facts_are_idempotent() -> None:
    """Given equal guard facts, resolution treats the repetition as idempotent."""
    # given: one transition with the same opaque guard declared twice
    source = """\
net equal_guard
(curation_token) -curation-> [speak_gate_speak] -speak_req-> (speak_request)
[speak_gate_speak] handler "request_speak@curate"
[speak_gate_speak] guard "speak_eligible@curate"
[speak_gate_speak] guard "speak_eligible@curate"
"""

    # when: the progressive facts are resolved
    actual = compile_petrinet_text(source, "equal-guard.petrinet")

    # then: one scalar guard field remains, without duplicate structure
    assert actual["transitions"] == [
        {
            "name": "speak_gate_speak",
            "handler": "request_speak@curate",
            "guard": "speak_eligible@curate",
        }
    ]


def test_unequal_guard_facts_report_fresh_diagnostic_and_both_spans() -> None:
    """Given unequal guards, PN204 points to the later and first declarations."""
    # given: one transition receives two distinct guard registry refs
    source = """\
net conflicting_guard
(curation_token) -curation-> [speak_gate_speak] -speak_req-> (speak_request)
[speak_gate_speak] handler "request_speak@curate"
[speak_gate_speak] guard "speak_eligible@curate"
[speak_gate_speak] guard "speak_skip@curate"
"""

    # when: final resolution encounters the contradictory fact
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "conflicting-guard.petrinet")

    # then: the feature-specific code and deterministic source relationship are exact
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN204"
    assert diagnostic.message == (
        "conflicting guard facts for transition [speak_gate_speak]"
    )
    assert diagnostic.help == (
        "remove one declaration or make both guard values identical"
    )
    assert diagnostic.span.source == "conflicting-guard.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (5, 1)
    assert len(diagnostic.related) == 1
    assert diagnostic.related[0].message == "first guard was declared here"
    assert diagnostic.related[0].span.source == "conflicting-guard.petrinet"
    assert (
        diagnostic.related[0].span.start.line,
        diagnostic.related[0].span.start.column,
    ) == (4, 1)


def test_guard_without_authored_handler_remains_handlerless() -> None:
    """Given only a guard, resolution preserves absent behavior."""
    # given: topology and a valid guard fact but no authored transition handler fact
    source = """\
net missing_handler
(curation_token) -curation-> [speak_gate_speak] -speak_req-> (speak_request)
[speak_gate_speak] guard "speak_eligible@curate"
"""

    # when: complete resolution retains the transition's authored facts
    actual = compile_petrinet_text(source, "missing-handler.petrinet")
    canonical = emit_petrinet(actual)

    # then: the guard coexists with an honestly absent behavior ref
    assert actual["transitions"] == [
        {
            "name": "speak_gate_speak",
            "guard": "speak_eligible@curate",
        }
    ]
    assert "[speak_gate_speak] handler " not in canonical
    assert '[speak_gate_speak] guard "speak_eligible@curate"' in canonical
    assert compile_petrinet_text(canonical, "canonical-handlerless.petrinet") == actual


def test_fixture_resolves_to_exact_ordered_structural_conflict() -> None:
    """Given paired fixtures, resolution produces four arcs sharing one place."""
    # given: independently paired source and core JSON documents
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: public compilation and core parsing consume the pair
    actual = compile_petrinet_text(source, str(_DSL_PATH))
    net = parse_net(expected)

    # then: the complete model and all declaration-ordered arcs are exact
    assert actual == expected
    assert [transition.name for transition in net.transitions] == [
        "speak_gate_speak",
        "speak_gate_skip",
    ]
    assert actual["arcs"] == [
        {
            "from": {"place": "curation_token"},
            "to": {"transition": "speak_gate_speak"},
            "consume": {"type": "curation"},
        },
        {
            "from": {"transition": "speak_gate_speak"},
            "to": {"place": "speak_request"},
            "produce": {"type": "speak_req", "destination": "speak_request"},
        },
        {
            "from": {"place": "curation_token"},
            "to": {"transition": "speak_gate_skip"},
            "consume": {"type": "curation"},
        },
        {
            "from": {"transition": "speak_gate_skip"},
            "to": {"place": "final_utterance"},
            "produce": {
                "type": "utterance",
                "destination": "final_utterance",
            },
        },
    ]
    assert [arc["from"].get("place") for arc in actual["arcs"]] == [
        "curation_token",
        None,
        "curation_token",
        None,
    ]


def test_canonical_emission_orders_guards_after_handlers_and_is_a_fixed_point() -> None:
    """Given guarded JSON, canonical emission retains order, refs, and semantics."""
    # given: the exact paired core document
    expected = _fixture_document()

    # when: it is emitted once
    canonical = emit_petrinet(expected)

    # then: one contiguous fact group orders handlers before guards
    transition_facts = """\
[speak_gate_speak] handler "request_speak@curate"
[speak_gate_skip] handler "skip_speak@curate"
[speak_gate_speak] guard "speak_eligible@curate"
[speak_gate_skip] guard "speak_skip@curate"
"""
    assert f"\n{transition_facts}\nmarking initial" in canonical

    # when: that canonical source is compiled and emitted a second time
    reparsed = compile_petrinet_text(
        canonical, "guarded-curation-choice.canonical.petrinet"
    )
    second = emit_petrinet(reparsed)

    # then: topology precedes facts, marking follows, and the form is a fixed point
    lines = canonical.splitlines()
    topology_end = max(index for index, line in enumerate(lines) if "->" in line)
    fact_start = lines.index('[speak_gate_speak] handler "request_speak@curate"')
    marking_index = lines.index("marking initial (curation_token) <- $token_0")
    assert topology_end < fact_start < marking_index
    assert reparsed == expected
    assert second == canonical
    assert canonical.endswith("\n")
