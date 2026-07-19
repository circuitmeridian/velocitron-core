"""BDD coverage for explicit accepted-color declarations in the Petri-net DSL."""

from __future__ import annotations
from typing import Any

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text
from velocitron.dsl.diagnostics import PetrinetDslError


def test_isolated_place_with_explicit_accepted_color_compiles() -> None:
    # given: a standalone place that no topology arc can declare
    source = """\
net isolated

(spare) accepts [artifact]
"""

    # when: the DSL is compiled to the core Net shape
    compiled = compile_petrinet_text(source, "isolated.petrinet")

    # then: the isolated place and its accepted color are preserved
    assert compiled["places"] == [{"name": "spare", "accepts": ["artifact"]}]
    # and: the accepted-color fact has the portable closed IR shape
    contribution = lower_petrinet_text(source, "isolated.petrinet")["contributions"][1]
    assert contribution["kind"] == "place.accepts"
    assert contribution["target"] == {"type": "place", "name": "spare"}
    assert contribution["value"] == {"colors": ["artifact"]}


def test_explicit_and_topology_colors_combine_in_declared_order() -> None:
    # given: a declaration containing an arc color and an otherwise unused color
    source = """\
net combined

(buffer) accepts [unused, active]
(buffer) -active-> [drain]
[drain] handler "drain"
(buffer) accepts [unused, active]
"""

    # when: equal progressive facts and topology are resolved
    compiled = compile_petrinet_text(source, "combined.petrinet")

    # then: the declaration order is authoritative and repetition is idempotent
    assert compiled["places"] == [{"name": "buffer", "accepts": ["unused", "active"]}]


def test_declared_non_token_color_rejects_bare_token_arc() -> None:
    # given: a bare arc beside a place that does not accept the core token color
    source = """\
net conflict

(queue) accepts [job]
(queue) -> [run]
"""

    # when: the bare arc is resolved as the explicit core token color
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "bare-token-conflict.petrinet")

    # then: the authoritative place declaration rejects that color
    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "arc color 'token' conflicts with accepted colors declared for (queue)"
    )


def test_declared_token_color_accepts_bare_arc() -> None:
    # given: a bare arc beside a place that accepts the core token color
    source = """\
net accepted

(queue) accepts [token]
(queue) -> [run]
"""

    # when: the bare arc is resolved
    compiled = compile_petrinet_text(source, "bare-token-accepted.petrinet")

    # then: its inscription uses the explicit core token color
    assert compiled["arcs"][0]["consume"]["type"] == "token"


def test_headerless_standalone_declarations_preserve_order_and_defaults() -> None:
    # given: isolated places and transitions interleaved without a net header
    source = """\
(second)
[first]
(first)
[second]
"""

    # when: the shorthand declarations are compiled
    compiled = compile_petrinet_text(source, "standalone.petrinet")

    # then: defaults and first-appearance order are preserved per entity kind
    assert compiled == {
        "name": "unnamed",
        "places": [
            {"name": "second", "accepts": ["token"]},
            {"name": "first", "accepts": ["token"]},
        ],
        "transitions": [{"name": "first"}, {"name": "second"}],
        "arcs": [],
        "initialMarking": {},
    }


def test_duplicate_color_in_declaration_is_rejected() -> None:
    # given: one malformed accepted-color declaration
    source = "net duplicate\n\n(queue) accepts [job, job]\n"

    # when: the source is lowered
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "duplicate.petrinet")

    # then: lowering identifies the invalid declaration at its source statement
    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "accepted colors for (queue) must not contain duplicates"
    )
    assert raised.value.diagnostic.span.start.line == 3


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        (
            "(queue) accepts [job]\n(queue) accepts [other]\n",
            "conflicting accepted-color facts for place (queue)",
        ),
        (
            '(queue) accepts [job]\n(queue) -other-> [run]\n[run] handler "run"\n',
            "arc color 'other' conflicts with accepted colors declared for (queue)",
        ),
    ],
)
def test_conflicting_accepted_color_facts_are_rejected(
    facts: str, message: str
) -> None:
    # given: incompatible progressive accepted-color evidence
    source = f"net conflict\n\n{facts}"

    # when: the evidence is resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "conflict.petrinet")

    # then: resolution reports a stable semantic conflict
    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == message


def test_emitter_round_trips_isolated_and_unused_accepted_colors() -> None:
    # given: core place order and accepted-color order topology cannot reconstruct
    document: dict[str, Any] = {
        "name": "round_trip",
        "places": [
            {"name": "isolated", "accepts": ["audit"]},
            {"name": "queue", "accepts": ["unused", "job"]},
        ],
        "transitions": [{"name": "run", "handler": "run"}],
        "arcs": [
            {
                "from": {"place": "queue"},
                "to": {"transition": "run"},
                "consume": {"type": "job"},
            }
        ],
        "initialMarking": {},
    }

    # when: canonical DSL is emitted and compiled again
    emitted = emit_petrinet(document)
    round_tripped = compile_petrinet_text(emitted, "round-trip.petrinet")

    # then: declarations precede topology and preserve the complete core shape
    assert emitted.index("(isolated) accepts [audit]") < emitted.index(
        "(queue) -job-> [run]"
    )
    # and: every place is declared because one declaration is required for order
    assert "(queue) accepts [unused, job]" in emitted
    assert round_tripped == document


def test_emitter_omits_redundant_accepted_color_facts() -> None:
    # given: topology already reconstructs every place and accepted-color order
    document: dict[str, Any] = {
        "name": "topology_only",
        "places": [{"name": "queue", "accepts": ["job"]}],
        "transitions": [{"name": "run", "handler": "run"}],
        "arcs": [
            {
                "from": {"place": "queue"},
                "to": {"transition": "run"},
                "consume": {"type": "job"},
            }
        ],
        "initialMarking": {},
    }

    # when: canonical DSL is emitted
    emitted = emit_petrinet(document)

    # then: it carries no redundant place declaration
    assert " accepts " not in emitted
    # and: the existing topology remains a semantic fixed point
    assert compile_petrinet_text(emitted, "topology-only.petrinet") == document
