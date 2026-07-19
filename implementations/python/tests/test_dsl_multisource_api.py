"""Behavioral coverage for same-namespace source aggregation."""

from __future__ import annotations

import pytest

from velocitron.dsl.api import compile_petrinet_sources, compile_petrinet_text
from velocitron.dsl.compiler import SourceInput
from velocitron.dsl.diagnostics import PetrinetDslError


_HEADER = 'net coin_deposit "Coin deposit"\n\n'
_TOPOLOGY = "(coin_slot) -coin-> [accept_coin] -coin-> (cash_box)\n"
_FACTS = """\
[accept_coin] handler "accept_coin"

marking initial (coin_slot) <- $inserted_coin
$inserted_coin: coin {}
"""


def test_same_file_and_separate_same_namespace_sources_resolve_equivalently() -> None:
    combined = compile_petrinet_text(_HEADER + _TOPOLOGY + "\n" + _FACTS)

    separate = compile_petrinet_sources(
        [
            SourceInput("topology.petrinet", _HEADER + _TOPOLOGY),
            SourceInput("facts.petrinet", _HEADER + _FACTS),
        ]
    )

    assert separate == combined


def test_headerless_and_explicit_unnamed_sources_aggregate() -> None:
    # Given one headerless source and one explicitly named ``net unnamed`` source.
    topology = "(p) -> [step]\n"
    continuation = "net unnamed\n\n[step] -> (q)\n"

    # When the sources are compiled as one namespace.
    separate = compile_petrinet_sources(
        [
            SourceInput("topology.petrinet", topology),
            SourceInput("continuation.petrinet", continuation),
        ]
    )

    # Then they resolve exactly like the equivalent headerless single source.
    assert separate == compile_petrinet_text("(p) -> [step] -> (q)\n")


def test_cross_source_color_evidence_does_not_change_bare_token() -> None:
    # Given a bare arc and two adjacent explicit colors in a later source.
    bare = "net stable_token\n\n(p) -> [step]\n"
    color_evidence = """\
net stable_token

(p) -red-> [red_step]
(p) -blue-> [blue_step]
"""

    # When all contributions are resolved together.
    document = compile_petrinet_sources(
        [
            SourceInput("topology.petrinet", bare),
            SourceInput("colors.petrinet", color_evidence),
        ]
    )

    # Then the earlier bare arc remains an explicit core-token consume.
    assert document["arcs"][0]["consume"] == {"type": "token"}


def test_composition_sources_aggregate_before_single_resolution() -> None:
    components: dict[str, dict[str, object]] = {
        "source.json": {
            "name": "source",
            "places": [
                {
                    "name": "out",
                    "accepts": ["pulse"],
                    "port": {"direction": "output", "type": "pulse"},
                }
            ],
            "transitions": [],
            "arcs": [],
        },
        "sink.json": {
            "name": "sink",
            "places": [
                {
                    "name": "in",
                    "accepts": ["pulse"],
                    "port": {"direction": "input", "type": "pulse"},
                }
            ],
            "transitions": [],
            "arcs": [],
        },
    }
    first_body = 'use "source.json" as source\n'
    second_body = 'use "sink.json" as sink\nwire source.(out) -> sink.(in)\n'

    combined = compile_petrinet_text(
        "composition wired\n" + first_body + second_body,
        net_loader=components.__getitem__,
    )
    separate = compile_petrinet_sources(
        [
            SourceInput("uses.petrinet", "composition wired\n" + first_body),
            SourceInput("wires.petrinet", "composition wired\n" + second_body),
        ],
        net_loader=components.__getitem__,
    )

    assert separate == combined


def test_sources_preserve_supplied_first_appearance_order() -> None:
    document = compile_petrinet_sources(
        [
            SourceInput(
                "later-name.petrinet",
                'net ordered\n\n(zeta) -token-> [z_step] -token-> (middle)\n[z_step] handler "z"\n',
            ),
            SourceInput(
                "earlier-name.petrinet",
                'net ordered\n\n(alpha) -token-> [a_step] -token-> (omega)\n[a_step] handler "a"\n',
            ),
        ]
    )

    assert [place["name"] for place in document["places"]] == [
        "zeta",
        "middle",
        "alpha",
        "omega",
    ]
    assert [transition["name"] for transition in document["transitions"]] == [
        "z_step",
        "a_step",
    ]


def test_duplicate_source_ids_are_pn200_before_resolution() -> None:
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_sources(
            [
                SourceInput("part.petrinet", _HEADER + _TOPOLOGY),
                SourceInput("part.petrinet", _HEADER + _FACTS),
            ]
        )

    assert raised.value.diagnostic.code == "PN200"
    assert (
        raised.value.diagnostic.message
        == "duplicate aggregate source id 'part.petrinet'"
    )


def test_headerless_and_mismatched_explicit_name_are_pn200() -> None:
    # Given a headerless source whose effective name is ``unnamed`` and a named net.
    sources = [
        SourceInput("topology.petrinet", "(p) -> [step]\n"),
        SourceInput("facts.petrinet", "net another_name\n\n[step] -> (q)\n"),
    ]

    # When they are compiled as one aggregate namespace.
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_sources(sources)

    # Then the explicit mismatch is rejected at the named source.
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == (
        "aggregate sources must have the same decoded header name"
    )
    assert raised.value.diagnostic.span.source == "facts.petrinet"


def test_mixed_document_kinds_are_pn200() -> None:
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_sources(
            [
                SourceInput("net.petrinet", _HEADER + _TOPOLOGY),
                SourceInput("composition.petrinet", "composition coin_deposit\n"),
            ]
        )

    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == (
        "aggregate sources must have the same document kind"
    )
    assert raised.value.diagnostic.span.source == "composition.petrinet"


def test_cross_source_conflict_keeps_both_source_spans() -> None:
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_sources(
            [
                SourceInput(
                    "first.petrinet",
                    'net conflict\n\n(p) -token-> [step] -token-> (q)\n[step] handler "first"\n[step] guard "first_guard"\n',
                ),
                SourceInput(
                    "second.petrinet",
                    'net conflict\n\n[step] guard "second_guard"\n',
                ),
            ]
        )

    assert raised.value.diagnostic.code.startswith("PN2")
    assert raised.value.diagnostic.span.source == "second.petrinet"
    assert raised.value.diagnostic.related[0].span.source == "first.petrinet"
