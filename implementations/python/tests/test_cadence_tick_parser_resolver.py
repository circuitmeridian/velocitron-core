"""Red parser, resolver, and canonical contracts for Slice 09 Cadence Tick."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import parse_net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "09-cadence-tick"
_DSL_PATH = _FIXTURE_ROOT / "cadence-tick.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "cadence-tick.json"
_CORPUS_ROOT = (
    _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "09-cadence-tick"
)

_SOURCE = """\
net cadence_tick "Timed cadence with alert reset"

(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
@tick_clock: (clock) -clock->? [on_tick]
[on_tick] -refresh_due-> (refresh_due)

(alert) -alert-> [on_alert] -refresh_due-> (refresh_due)
(tick_latch) -tick_latch-> [on_alert] -tick_latch-> (tick_latch)
@alert_clock: (clock) -clock->? [on_alert]

[on_tick] handler "on_tick@cadence"
[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at + latch.cadence_s"
[on_tick] timer bind latch (tick_latch)

[on_alert] handler "on_alert@cadence"
[on_alert] priority 10

marking initial (clock) <- $clock_zero
marking initial (tick_latch) <- $cadence_latch

$clock_zero: clock {"now": 0}
$cadence_latch: tick_latch {"fired_at": 0, "cadence_s": 300}
"""


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def test_combined_timer_bind_and_priority_lower_as_direct_transition_facts() -> None:
    """Given native timer syntax, lowering records only direct transition facts."""
    # given: one timer clock/CEL fact, one bind fact, and one priority fact

    # when: the source is lowered without resolution
    portable = lower_petrinet_text(_SOURCE, "cadence-tick.petrinet")
    facts = [
        (fact["kind"], fact["target"], fact["value"])
        for fact in portable["contributions"]
        if fact["kind"].startswith("transition.timer")
        or fact["kind"] == "transition.priority"
    ]

    # then: exact portable values preserve the clock, CEL, bind, and integer priority
    assert facts == [
        (
            "transition.timer",
            {"type": "transition", "name": "on_tick"},
            {
                "clock": {"type": "place", "name": "clock"},
                "cel": "clock.now >= latch.fired_at + latch.cadence_s",
            },
        ),
        (
            "transition.timer-bind",
            {"type": "transition", "name": "on_tick"},
            {"name": "latch", "place": {"type": "place", "name": "tick_latch"}},
        ),
        (
            "transition.priority",
            {"type": "transition", "name": "on_alert"},
            {"priority": 10},
        ),
    ]


def test_equal_timer_bind_and_priority_facts_are_idempotent() -> None:
    """Given repeated equal timed facts, resolution keeps one semantic value."""
    # given: every newly introduced fact is repeated byte-for-byte
    source = (
        _SOURCE.replace(
            "[on_tick] timer bind latch (tick_latch)\n",
            "[on_tick] timer bind latch (tick_latch)\n[on_tick] timer bind latch (tick_latch)\n",
        )
        .replace(
            '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at + latch.cadence_s"\n',
            '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at + latch.cadence_s"\n'
            '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at + latch.cadence_s"\n',
        )
        .replace(
            "[on_alert] priority 10\n",
            "[on_alert] priority 10\n[on_alert] priority 10\n",
        )
    )

    # when: the progressive facts resolve
    actual = compile_petrinet_text(source, "equal-cadence.petrinet")

    # then: scalar and map values occur once in the semantic transition objects
    assert actual["transitions"] == [
        {
            "name": "on_tick",
            "handler": "on_tick@cadence",
            "timer": {
                "clock": "clock",
                "cel": "clock.now >= latch.fired_at + latch.cadence_s",
                "bind": {"latch": "tick_latch"},
            },
        },
        {"name": "on_alert", "handler": "on_alert@cadence", "priority": 10},
    ]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (
            '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at"',
            '[on_tick] timer clock (other_clock) cel "clock.now >= latch.fired_at"',
        ),
        (
            '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at"',
            '[on_tick] timer clock (clock) cel "clock.now > latch.fired_at"',
        ),
        (
            "[on_tick] timer bind latch (tick_latch)",
            "[on_tick] timer bind latch (other_latch)",
        ),
        ("[on_tick] priority 1", "[on_tick] priority 2"),
    ],
    ids=["clock", "cel", "bind", "priority"],
)
def test_unequal_timed_facts_cite_later_and_first_spans(
    first: str, second: str
) -> None:
    """Given unequal duplicate facts, the conflict relates both source definitions."""
    # given: valid topology and contradictory facts on consecutive lines
    source = f"""\
net conflicting_cadence
(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
(other_latch) -tick_latch-> [on_tick]
(clock) -clock->? [on_tick]
(other_clock) -clock->? [on_tick]
[on_tick] handler "on_tick@cadence"
{first}
{second}
"""

    # when: resolution encounters the later contribution
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "conflicting-cadence.petrinet")

    # then: the primary span is later and the related span is the first definition
    diagnostic = raised.value.diagnostic
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (8, 1)
    assert len(diagnostic.related) == 1
    assert (
        diagnostic.related[0].span.start.line,
        diagnostic.related[0].span.start.column,
    ) == (7, 1)


def test_timer_clock_must_resolve_to_a_declared_place() -> None:
    """Given an unknown timer clock, resolution rejects the offending fact."""
    # given: complete topology whose timer alone names ghost_clock
    source = """\
net unknown_clock
(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
[on_tick] handler "on_tick@cadence"
[on_tick] timer clock (ghost_clock) cel "clock.now >= 0"
"""

    # when: timer references are resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "unknown-clock.petrinet")

    # then: the diagnostic identifies the unresolved clock at its fact
    diagnostic = raised.value.diagnostic
    assert "ghost_clock" in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (4, 1)


def test_timer_bind_place_must_feed_the_transition() -> None:
    """Given a non-input bind place, resolution rejects hidden timer topology."""
    # given: alert is declared but feeds another completed transition, not on_tick
    source = """\
net invalid_bind_topology
(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
(clock) -clock->? [on_tick]
(alert) -alert-> [on_alert] -alert-> (alert)
[on_tick] handler "on_tick@cadence"
[on_alert] handler "on_alert@cadence"
[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at"
[on_tick] timer bind latch (alert)
"""

    # when: the bind environment is resolved against actual input arcs
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "invalid-bind-topology.petrinet")

    # then: the error names both the variable and structurally unrelated place
    diagnostic = raised.value.diagnostic
    assert "latch" in diagnostic.message and "alert" in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (8, 1)


def test_timer_bind_rejects_reserved_clock_name() -> None:
    """Given a bind named clock, resolution protects the native timer variable."""
    # given: a structurally valid bind using the reserved environment key
    source = """\
net reserved_clock
(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
(clock) -clock->? [on_tick]
[on_tick] handler "on_tick@cadence"
[on_tick] timer clock (clock) cel "clock.now >= 0"
[on_tick] timer bind clock (tick_latch)
"""

    # when: the timer environment is resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "reserved-clock.petrinet")

    # then: the reserved identifier is rejected at the bind fact
    diagnostic = raised.value.diagnostic
    assert "clock" in diagnostic.message and "reserved" in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (6, 1)


def test_malformed_timer_cel_fails_during_compilation_with_source_span() -> None:
    """Given malformed timer CEL, ordinary net compilation fails before runtime."""
    # given: syntactically valid DSL wrapping an invalid CEL expression
    source = """\
net malformed_timer
(tick_latch) -tick_latch-> [on_tick] -tick_latch-> (tick_latch)
(clock) -clock->? [on_tick]
[on_tick] handler "on_tick@cadence"
[on_tick] timer clock (clock) cel "clock.now >="
"""

    # when: the complete semantic net is compiled
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "malformed-timer.petrinet")

    # then: CEL compilation is reported on the authored timer fact
    diagnostic = raised.value.diagnostic
    assert "CEL" in diagnostic.message
    assert diagnostic.span.source == "malformed-timer.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (5, 1)


@pytest.mark.parametrize(
    "case",
    [
        "conflicting-timer-clock",
        "conflicting-timer-cel",
        "conflicting-timer-bind",
        "conflicting-priority",
        "unresolved-clock",
        "non-input-bind-place",
        "reserved-clock-bind",
        "malformed-cel",
    ],
)
def test_timed_diagnostics_match_committed_conformance(case: str) -> None:
    """Resolver diagnostics are the stable checked-in language contract."""
    corpus = json.loads(
        (_CORPUS_ROOT / f"cadence-tick.{case}.json").read_text(encoding="utf-8")
    )
    source = corpus["source"]
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source["text"], source["sourceId"])

    diagnostic = raised.value.diagnostic
    expected = corpus["diagnostic"]
    assert diagnostic.code == expected["code"]
    assert diagnostic.message == expected["message"]
    assert diagnostic.help == expected.get("help")
    assert diagnostic.span.as_dict() == expected["span"]
    assert [
        {"message": related.message, "span": related.span.as_dict()}
        for related in diagnostic.related
    ] == expected.get("related", [])


def test_imported_timer_bind_name_must_be_a_cel_identifier() -> None:
    """Imported IR cannot bypass the DSL's timer-bind identifier grammar."""
    portable = deepcopy(lower_petrinet_text(_SOURCE, "strict-bind-ir.petrinet"))
    bind = next(
        fact
        for fact in portable["contributions"]
        if fact["kind"] == "transition.timer-bind"
    )
    bind["value"]["name"] = "not-valid"

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid transition timer bind contribution"
    assert diagnostic.span.as_dict() == bind["span"]


def test_imported_timer_ir_is_closed_and_strict() -> None:
    """Given imported timer IR, resolver rejects unrecognized timer value fields."""
    # given: fresh valid portable IR with an unknown timer member added externally
    portable = deepcopy(lower_petrinet_text(_SOURCE, "strict-timer-ir.petrinet"))
    timer = next(
        fact for fact in portable["contributions"] if fact["kind"] == "transition.timer"
    )
    timer["value"]["scheduler"] = "implicit-wall-clock"

    # when: the imported artifact bypasses source lowering and resolves directly
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    # then: strict IR validation rejects the extension at the timer contribution
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.span.start.line == 12


def test_fixture_resolves_to_exact_ordered_timed_model() -> None:
    """Given paired cadence fixtures, resolution preserves exact model and order."""
    # given: independently paired source and semantic JSON documents
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: public compilation and core parsing consume the pair
    actual = compile_petrinet_text(source, str(_DSL_PATH))
    net = parse_net(expected)

    # then: the four-place/two-transition/nine-arc/two-token model is exact
    assert actual == expected
    assert [place.name for place in net.places] == [
        "tick_latch",
        "clock",
        "refresh_due",
        "alert",
    ]
    assert [transition.name for transition in net.transitions] == [
        "on_tick",
        "on_alert",
    ]
    assert len(net.arcs) == 9
    assert net.initial_marking is not None
    assert set(net.initial_marking) == {"clock", "tick_latch"}


def test_canonical_timed_emission_is_ordered_and_a_fixed_point() -> None:
    """Given timed JSON, canonical emission preserves facts and reaches a fixed point."""
    # given: the exact paired semantic document
    expected = _fixture_document()

    # when: it is canonically emitted, reparsed, and emitted again
    canonical = emit_petrinet(expected)
    reparsed = compile_petrinet_text(canonical, "cadence-tick.canonical.petrinet")
    second = emit_petrinet(reparsed)

    # then: timer, bind, and nondefault priority facts have canonical relative order
    lines = canonical.splitlines()
    timer_index = lines.index(
        '[on_tick] timer clock (clock) cel "clock.now >= latch.fired_at + latch.cadence_s"'
    )
    bind_index = lines.index("[on_tick] timer bind latch (tick_latch)")
    priority_index = lines.index("[on_alert] priority 10")
    assert timer_index < bind_index < priority_index
    assert "[on_tick] priority 0" not in canonical
    assert reparsed == expected
    assert second == canonical
