"""BDD coverage for arbitrary-length chain expansion in the DSL frontend.

The spec grammar (`spec/petrinet-language.md`) defines
`Chain := [ "@" Name ":" ] Endpoint ( ArcSegment Endpoint )+`, so a chain
may run through any number of alternating place/transition endpoints,
e.g. `(a)->[b]->(c)->[d]->(e)` or `[b]->(c)->[d]`. These tests pin that
contract: left-to-right arc order, per-segment colors, the
place-to-transition-only rule for read/inhibit segments applied
per-segment, place/transition alternation, chain handles covering every
expanded arc (through full resolution end-to-end, not just lowering —
elaborations on a multi-arc handle stay governed by the per-elaboration
uniqueness diagnostics), and canonical round-trip.
"""

from __future__ import annotations

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text
from velocitron.dsl.diagnostics import PetrinetDslError


def test_place_first_chain_expands_arbitrarily_many_segments() -> None:
    # given: a five-endpoint place-first chain
    source = "net long_chain\n\n(a) -> [b] -> (c) -> [d] -> (e)\n"

    # when: the source is compiled
    document = compile_petrinet_text(source, "long-chain.petrinet")

    # then: four arcs appear in left-to-right chain-expansion order
    assert document["arcs"] == [
        {
            "from": {"place": "a"},
            "to": {"transition": "b"},
            "consume": {"type": "token"},
        },
        {
            "from": {"transition": "b"},
            "to": {"place": "c"},
            "produce": {"destination": "c", "type": "token"},
        },
        {
            "from": {"place": "c"},
            "to": {"transition": "d"},
            "consume": {"type": "token"},
        },
        {
            "from": {"transition": "d"},
            "to": {"place": "e"},
            "produce": {"destination": "e", "type": "token"},
        },
    ]
    # and: endpoints participate in first-appearance ordering
    assert [place["name"] for place in document["places"]] == ["a", "c", "e"]
    assert [transition["name"] for transition in document["transitions"]] == ["b", "d"]


def test_transition_first_chain_expands_arbitrarily_many_segments() -> None:
    # given: a transition-first chain continuing past its first place
    source = "net long_chain\n\n[b] -> (c) -> [d] -> (e)\n"

    # when: the source is compiled
    document = compile_petrinet_text(source, "long-chain.petrinet")

    # then: produce, consume, produce arcs appear in left-to-right order
    assert document["arcs"] == [
        {
            "from": {"transition": "b"},
            "to": {"place": "c"},
            "produce": {"destination": "c", "type": "token"},
        },
        {
            "from": {"place": "c"},
            "to": {"transition": "d"},
            "consume": {"type": "token"},
        },
        {
            "from": {"transition": "d"},
            "to": {"place": "e"},
            "produce": {"destination": "e", "type": "token"},
        },
    ]


def test_long_chain_carries_per_segment_colors_and_input_modes() -> None:
    # given: a long chain mixing explicit colors, a read segment, and an inhibit segment
    source = "net colored_chain\n\n(a) -red->? [b] -blue-> (c) ->0 [d] -green-> (e)\n"

    # when: the source is compiled
    document = compile_petrinet_text(source, "colored-chain.petrinet")

    # then: each segment keeps its own color and mode
    assert document["arcs"] == [
        {
            "from": {"place": "a"},
            "to": {"transition": "b"},
            "consume": {"type": "red", "mode": "read"},
        },
        {
            "from": {"transition": "b"},
            "to": {"place": "c"},
            "produce": {"destination": "c", "type": "blue"},
        },
        {
            "from": {"place": "c"},
            "to": {"transition": "d"},
            "consume": {"type": "token", "mode": "inhibit"},
        },
        {
            "from": {"transition": "d"},
            "to": {"place": "e"},
            "produce": {"destination": "e", "type": "green"},
        },
    ]


@pytest.mark.parametrize(
    "source",
    [
        "(a) -> [b] ->? (c) -> [d]\n",
        "(a) -> [b] -> (c) -> [d] ->0 (e)\n",
    ],
)
def test_read_and_inhibit_segments_are_rejected_on_any_produce_position(
    source: str,
) -> None:
    # given: a chain whose read/inhibit operator sits on a transition-to-place segment
    # when: the source is lowered
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, "bad-produce.petrinet")

    # then: the place-to-transition-only rule rejects it
    assert raised.value.diagnostic.code == "PN101"
    assert "place-to-transition" in raised.value.diagnostic.message


@pytest.mark.parametrize(
    "source",
    [
        "(a) -> (b)\n",
        "[a] -> [b]\n",
        "(a) -> [b] -> (c) -> (d)\n",
        "[a] -> (b) -> [c] -> [d]\n",
    ],
)
def test_chain_segments_must_alternate_places_and_transitions(source: str) -> None:
    # given: a chain with two like endpoints joined by one segment
    # when: the source is lowered
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, "no-alternation.petrinet")

    # then: the non-alternating segment is a deterministic PN101 error
    assert raised.value.diagnostic.code == "PN101"


def test_chain_handle_names_every_arc_of_a_long_expansion_run() -> None:
    # given: a handled five-endpoint chain
    source = "net handled\n\n@run: (a) -> [b] -> (c) -> [d] -> (e)\n"

    # when: the source is lowered to portable contribution IR
    portable = lower_petrinet_text(source, "handled.petrinet")

    # then: the handle's arcIds cover all four expanded arcs in order
    handle = next(
        item for item in portable["contributions"] if item["kind"] == "arc.handle"
    )
    declared = [
        item["target"]["id"]
        for item in portable["contributions"]
        if item["kind"] == "arc.declare"
    ]
    assert handle["value"]["arcIds"] == declared
    assert len(declared) == 4


def test_long_chain_round_trips_through_canonical_emission() -> None:
    # given: a compiled long chain
    source = "net round_trip\n\n(a) -> [b] -> (c) -> [d] -> (e)\n"
    document = compile_petrinet_text(source, "round-trip.petrinet")

    # when: the document is canonically emitted and recompiled
    canonical = emit_petrinet(document)
    recompiled = compile_petrinet_text(canonical, "round-trip-canonical.petrinet")

    # then: the canonical form is a fixed point of the same net
    assert recompiled == document
    assert emit_petrinet(recompiled) == canonical


def test_handled_long_chain_compiles_to_the_same_document_as_its_unhandled_form() -> (
    None
):
    # given: the same five-endpoint chain with and without a chain handle
    handled = "net handled\n\n@run: (a) -> [b] -> (c) -> [d] -> (e)\n"
    unhandled = "net handled\n\n(a) -> [b] -> (c) -> [d] -> (e)\n"

    # when: both sources are fully compiled (resolution, not just lowering)
    handled_document = compile_petrinet_text(handled, "handled.petrinet")
    unhandled_document = compile_petrinet_text(unhandled, "unhandled.petrinet")

    # then: the handle is transparent — the documents are identical, with the
    # four expanded arcs intact
    assert handled_document == unhandled_document
    assert len(handled_document["arcs"]) == 4


def test_handled_long_chain_round_trips_through_canonical_emission() -> None:
    # given: a compiled handled long chain (the handle is not a metadata
    # target, so canonical emission drops it)
    source = "net handled_round_trip\n\n@run: (a) -> [b] -> (c) -> [d] -> (e)\n"
    document = compile_petrinet_text(source, "handled-round-trip.petrinet")

    # when: the document is canonically emitted and recompiled
    canonical = emit_petrinet(document)
    recompiled = compile_petrinet_text(canonical, "handled-canonical.petrinet")

    # then: the canonical form is a fixed point of the same net
    assert recompiled == document
    assert emit_petrinet(recompiled) == canonical


def test_weight_on_long_chain_handle_with_two_consume_arcs_is_rejected() -> None:
    # given: a handled five-endpoint chain whose handle covers two consume
    # arcs (a->b and c->d), making a weight elaboration ambiguous
    source = "net ambiguous\n\n@run: (a) -> [b] -> (c) -> [d] -> (e)\n@run weight 2\n"

    # when: the source is compiled
    with pytest.raises(
        PetrinetDslError,
        match="arc handle @run must identify exactly one input arc for weight",
    ) as raised:
        compile_petrinet_text(source, "ambiguous-weight.petrinet")

    # then: the ambiguity is the deterministic PN202 elaboration error, not a
    # generic PN200 handle-shape rejection
    assert raised.value.diagnostic.code == "PN202"


def test_description_on_long_chain_handle_is_rejected_as_ambiguous_metadata() -> None:
    # given: a handled five-endpoint chain whose handle covers four arcs,
    # making a description elaboration unreconstructable
    source = (
        "net ambiguous\n\n@run: (a) -> [b] -> (c) -> [d] -> (e)\n"
        '@run description "which arc?"\n'
    )

    # when: the source is compiled
    with pytest.raises(
        PetrinetDslError,
        match="metadata arc handle '@run' must identify exactly one arc",
    ) as raised:
        compile_petrinet_text(source, "ambiguous-description.petrinet")

    # then: the ambiguity is the deterministic PN202 metadata error, not a
    # generic PN200 handle-shape rejection
    assert raised.value.diagnostic.code == "PN202"


def test_weight_on_transition_first_long_chain_handle_targets_its_only_consume_arc() -> (  # noqa: E501
    None
):
    # given: a handled transition-first chain whose three arcs include
    # exactly one consume arc (c->d), so a weight elaboration is unambiguous
    source = "net weighted\n\n@run: [b] -> (c) -> [d] -> (e)\n@run weight 2\n"

    # when: the source is compiled
    document = compile_petrinet_text(source, "weighted.petrinet")

    # then: the weight lands on the single consume arc and nowhere else
    assert document["arcs"] == [
        {
            "from": {"transition": "b"},
            "to": {"place": "c"},
            "produce": {"destination": "c", "type": "token"},
        },
        {
            "from": {"place": "c"},
            "to": {"transition": "d"},
            "consume": {"type": "token", "weight": 2},
        },
        {
            "from": {"transition": "d"},
            "to": {"place": "e"},
            "produce": {"destination": "e", "type": "token"},
        },
    ]
