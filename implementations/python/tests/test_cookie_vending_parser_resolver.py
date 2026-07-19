"""Red parser/resolver contracts for Slice 03 Cookie Vending."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError


_REPOSITORY_ROOT = Path(__file__).parents[3]
_COOKIE_JSON_PATH = _REPOSITORY_ROOT / "examples" / "contrived" / "cookie_vending.json"
_COOKIE_SOURCE = """\
net "cookie-vending"

(coin_slot) -coin-> [accept_coin]
(coin_slot) -coin-> [return_coin]
[accept_coin] -coin-> (cash_box)
@vend_signal: [accept_coin] -signal-> (signal)
(signal) -signal-> [vend_packet]
(storage) -packet-> [vend_packet]
[vend_packet] -packet-> (compartment)
(compartment) -packet-> [take_packet]

[accept_coin] handler "accept_coin"
[vend_packet] handler "vend_packet"
[return_coin] handler "return_coin"
[take_packet] handler "take_packet"

[accept_coin] order 1
[vend_packet] order 2
[return_coin] order 3
[take_packet] order 4

@vend_signal data {}

marking initial (coin_slot) <- $inserted_coin
marking initial (storage) <- 5 * $packet

$inserted_coin: coin {}
$packet: packet {}
"""


def _cookie_document() -> dict[str, object]:
    return json.loads(_COOKIE_JSON_PATH.read_text(encoding="utf-8"))


def test_cookie_vending_resolves_exact_order_topology_data_and_repeated_marking() -> (
    None
):
    """Given the authored net, resolution reproduces the existing JSON without decoration."""
    # given: eight flat arcs with repeated object references, two templates, and multiplicity
    expected = _cookie_document()

    # when: the complete Cookie Vending source is compiled
    actual = compile_petrinet_text(_COOKIE_SOURCE, "cookie-vending.petrinet")

    # then: progressive declarations merge into the exact existing core arrays
    assert actual == expected
    assert len(actual["places"]) == 5
    assert len(actual["transitions"]) == 4
    assert len(actual["arcs"]) == 8
    assert [item["name"] for item in actual["places"]] == [
        "coin_slot",
        "cash_box",
        "signal",
        "storage",
        "compartment",
    ]
    assert [item["name"] for item in actual["transitions"]] == [
        "accept_coin",
        "vend_packet",
        "return_coin",
        "take_packet",
    ]

    accept_outputs = [
        arc["to"]["place"]
        for arc in actual["arcs"]
        if arc["from"] == {"transition": "accept_coin"}
    ]
    vend_inputs = [
        arc["from"]["place"]
        for arc in actual["arcs"]
        if arc["to"] == {"transition": "vend_packet"}
    ]
    assert accept_outputs == ["cash_box", "signal"]
    assert vend_inputs == ["signal", "storage"]

    produce_templates = [arc["produce"] for arc in actual["arcs"] if "produce" in arc]
    assert produce_templates == [
        {"destination": "cash_box", "type": "coin"},
        {"data": {}, "destination": "signal", "type": "signal"},
        {"destination": "compartment", "type": "packet"},
    ]
    assert actual["initialMarking"] == expected["initialMarking"]
    assert len(actual["initialMarking"]["storage"]) == 5
    assert all(
        token == {"type": "packet", "data": {}}
        for token in actual["initialMarking"]["storage"]
    )
    assert len({id(token) for token in actual["initialMarking"]["storage"]}) == 5


def test_identical_arc_data_facts_are_idempotent_but_conflicts_point_to_both() -> None:
    """Given repeated data facts, equal values merge and a later unequal value is primary."""
    # given: the exceptional produce arc receives the same literal fact twice
    identical = _COOKIE_SOURCE.replace(
        "@vend_signal data {}\n", "@vend_signal data {}\n@vend_signal data {}\n"
    )

    # when: equal progressive facts resolve
    resolved = compile_petrinet_text(identical, "cookie-vending.petrinet")

    # then: the duplicate is idempotent and does not duplicate or decorate the arc
    assert resolved == _cookie_document()

    # given: a later declaration contradicts the first literal template
    conflicting = """\
net "cookie-vending"

@vend_signal: [accept_coin] -signal-> (signal)
@vend_signal data {"source": "handler"}
@vend_signal data {}
"""

    # when: the contradictory contribution is resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(conflicting, "cookie-vending.petrinet")

    # then: the later declaration is primary and the first declaration is related context
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == "conflicting data facts for arc @vend_signal"
    assert diagnostic.span.start.line == 5
    assert diagnostic.span.start.column == 1
    assert len(diagnostic.related) == 1
    assert (
        diagnostic.related[0].message
        == 'first value {"source":"handler"} was declared here'
    )
    assert diagnostic.related[0].span.start.line == 4
    assert diagnostic.related[0].span.start.column == 1


def test_cookie_json_emits_generated_arc_handle_and_is_a_canonical_fixed_point() -> (
    None
):
    """Given unannotated core JSON, canonical output generates @arc_3 and repetition."""
    # given: the existing JSON has no authored presentation identity
    expected = _cookie_document()

    # when: canonical DSL is emitted twice through a compile round trip
    canonical = emit_petrinet(expected)
    second = emit_petrinet(
        compile_petrinet_text(canonical, "cookie-vending.canonical.petrinet")
    )

    # then: only the fourth core arc gains generated identity and literal data
    assert second == canonical
    assert canonical.count("@arc_3:") == 1
    assert "@arc_3: [accept_coin] -signal-> (signal)" in canonical
    assert canonical.count("@arc_3 data {}") == 1
    assert "@vend_signal" not in canonical
    assert canonical.count(" data {}") == 1
    assert "marking initial (coin_slot) <- $token_0" in canonical
    assert "marking initial (storage) <- 5 * $token_1" in canonical
    assert "$token_0: coin {}" in canonical
    assert "$token_1: packet {}" in canonical
    assert (
        compile_petrinet_text(canonical, "cookie-vending.canonical.petrinet")
        == expected
    )


def test_generic_marking_runs_emit_counts_without_consuming_template_indexes() -> None:
    """Generic empty tokens use count shorthand while every other token uses templates."""
    # given: initial and named markings interleave empty and data-bearing
    # Generic runs, while a separate single-color place carries typed tokens
    generic: dict[str, Any] = {"type": "token", "data": {}}
    job_a = {"type": "job", "data": {"id": "a"}}
    generic_with_data = {"type": "token", "data": {"id": "generic"}}
    job_b = {"type": "job", "data": {"id": "b"}}
    expected: dict[str, object] = {
        "name": "generic-canonical",
        "places": [
            {"name": "ready", "accepts": ["token"]},
            {"name": "done", "accepts": ["job"]},
        ],
        "transitions": [],
        "arcs": [],
        "initialMarking": {
            "ready": [
                generic,
                generic,
                generic_with_data,
                generic,
                generic_with_data,
            ],
            "done": [job_a],
        },
        "annotations": {
            "petrinet.dsl/v1": {
                "arcHandles": {},
                "markings": {
                    "checkpoint": {
                        "ready": [generic, generic_with_data, generic, generic],
                        "done": [job_b, job_a],
                    }
                },
                "views": {},
                "extensions": {},
            }
        },
    }

    # when: JSON is emitted and compiled, then emitted again
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(canonical, "generic-canonical.petrinet")

    # then: each Generic run is an integer (including one), nonempty Generic
    # data remains template-backed, and only template-backed runs take ordinals
    assert (
        canonical
        == """\
net "generic-canonical"

(ready) accepts [token]
(done) accepts [job]

marking initial (ready) <- 2
marking initial (ready) <- $token_0
marking initial (ready) <- 1
marking initial (ready) <- $token_0
marking initial (done) <- $token_1
marking checkpoint (ready) <- 1
marking checkpoint (ready) <- $token_0
marking checkpoint (ready) <- 2
marking checkpoint (done) <- $token_2
marking checkpoint (done) <- $token_1
$token_0: token {"id":"generic"}
$token_1: job {"id":"a"}
$token_2: job {"id":"b"}

extensions {}
"""
    )
    assert round_tripped == expected
    assert emit_petrinet(round_tripped) == canonical


@pytest.mark.parametrize(
    ("first_value", "second_value", "first_literal", "second_literal"),
    [
        (True, 1, "true", "1"),
        (False, 0, "false", "0"),
    ],
)
def test_json_scalar_types_remain_distinct_templates_and_marking_runs(
    first_value: bool,
    second_value: int,
    first_literal: str,
    second_literal: str,
) -> None:
    """Given bool/int token data, emission preserves JSON types and run boundaries."""
    # given: adjacent tokens compare equal in Python but differ under JSON equality
    expected = _cookie_document()
    expected["initialMarking"] = {
        "coin_slot": [
            {"type": "coin", "data": {"value": first_value}},
            {"type": "coin", "data": {"value": second_value}},
        ]
    }

    # when: the resolved document is emitted and compiled through public behavior
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(
        canonical, "cookie-vending.scalar-types.petrinet"
    )

    # then: neither template identity nor adjacent marking runs coalesce
    assert f'$token_0: coin {{"value":{first_literal}}}' in canonical
    assert f'$token_1: coin {{"value":{second_literal}}}' in canonical
    assert canonical.count("marking initial (coin_slot) <- $token_") == 2
    assert "marking initial (coin_slot) <- 2 * " not in canonical
    assert round_tripped == expected


def test_template_renaming_does_not_rewrite_quoted_place_names() -> None:
    """Given a template-like place name, token_n naming changes references only."""
    # given: two templates force generated token_n names beside a quoted place name
    expected = deepcopy(_cookie_document())
    quoted_name = "$inserted_coin depot"
    places = cast(list[dict[str, Any]], expected["places"])
    arcs = cast(list[dict[str, Any]], expected["arcs"])
    initial_marking = cast(dict[str, list[dict[str, Any]]], expected["initialMarking"])
    for place in places:
        if place["name"] == "coin_slot":
            place["name"] = quoted_name
    for arc in arcs:
        if arc["from"] == {"place": "coin_slot"}:
            arc["from"] = {"place": quoted_name}
    coin_tokens = initial_marking.pop("coin_slot")
    expected["initialMarking"] = {
        quoted_name: coin_tokens,
        **initial_marking,
    }

    # when: canonical DSL is emitted and compiled
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(
        canonical, "cookie-vending.quoted-template-name.petrinet"
    )

    # then: only template references are renamed, never quoted identifier contents
    assert f'marking initial ("{quoted_name}") <- $token_0' in canonical
    assert '"$token_0 depot"' not in canonical
    assert round_tripped == expected


def test_bool_and_number_arc_data_facts_conflict_with_later_primary_span() -> None:
    """Given JSON true then 1, PN202 keeps the later fact primary and first related."""
    # given: Python-equal values are distinct JSON data facts on one produce arc
    conflicting = """\
net "cookie-vending"

@vend_signal: [accept_coin] -signal-> (signal)
@vend_signal data {"value": true}
@vend_signal data {"value": 1}
"""

    # when: the contradictory contribution is resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(conflicting, "cookie-vending.bool-number-data.petrinet")

    # then: the later declaration is primary and the boolean fact is related context
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == "conflicting data facts for arc @vend_signal"
    assert diagnostic.span.start.line == 5
    assert diagnostic.span.start.column == 1
    assert len(diagnostic.related) == 1
    assert (
        diagnostic.related[0].message == 'first value {"value":true} was declared here'
    )
    assert diagnostic.related[0].span.start.line == 4
    assert diagnostic.related[0].span.start.column == 1


def test_portable_ir_rejects_arc_handle_with_valid_and_stale_arc_ids() -> None:
    """Given a mixed-validity imported handle, resolution rejects the whole handle."""
    # given: JSON-round-tripped portable IR names one real produce arc plus a stale ID
    portable_ir = json.loads(
        json.dumps(
            lower_petrinet_text(
                _COOKIE_SOURCE, "cookie-vending.stale-arc-handle.petrinet"
            )
        )
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
        and contribution["target"]["name"] == "vend_signal"
    )
    handle["value"]["arcIds"].append(
        {
            "document": "cookie-vending.stale-arc-handle.petrinet",
            "statement": 999,
            "part": 0,
        }
    )

    # when: the mixed-validity imported portable IR is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: strict handle integrity is diagnosed at the importing handle declaration
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid arc handle contribution"
    assert diagnostic.span.start.line == 6
    assert diagnostic.span.start.column == 1


def test_portable_ir_rejects_extra_member_in_arc_produce_data_target() -> None:
    """Given an extended arcHandle target, portable-IR resolution rejects its shape."""
    # given: lowering produced a valid data fact, then an importer added a target member
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.extra-produce-data-target.petrinet"
    )
    produce_data = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.produce-data"
    )
    produce_data["target"]["unexpected"] = True

    # when: the schema-invalid portable IR is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: arcHandleTarget remains closed to exactly type and name
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid arc produce-data contribution"
    assert diagnostic.span.start.line == 22
    assert diagnostic.span.start.column == 1


def test_portable_ir_treats_equal_json_numbers_as_one_fact_and_token_value() -> None:
    """Given 1 and 1.0 lexemes, resolution merges facts and canonical token values."""
    # given: equal binary64 numbers occur in arc data, one progressive template,
    # and adjacent marking tokens, while a boolean token remains distinct
    source = """\
net "numeric-equivalence"
@numeric: [emit] -number-> (output)
[emit] handler "emit"
@numeric data {"value": 1}
@numeric data {"value": 1.0}
marking initial (output) <- $integer
marking initial (output) <- $float
marking initial (output) <- $boolean
$integer: number {"value": 1}
$integer: number {"value": 1.0}
$float: number {"value": 1.0}
$boolean: number {"value": true}
"""

    # when: the public lowering and portable-IR resolver APIs process the source
    resolved = resolve_contribution_ir(
        lower_petrinet_text(source, "numeric-equivalence.petrinet")
    )
    canonical = emit_petrinet(resolved)

    # then: numeric spellings are one JSON value for facts, templates, and runs,
    # but the JSON boolean has its own canonical template and marking statement
    assert resolved["arcs"][0]["produce"]["data"] == {"value": 1}
    assert "marking initial (output) <- 2 * $token_0" in canonical
    assert "marking initial (output) <- $token_1" in canonical
    assert '$token_0: number {"value":1}' in canonical
    assert '$token_1: number {"value":true}' in canonical
    assert canonical.count("$token_") == 4


def test_portable_ir_keeps_boolean_distinct_from_numeric_template_value() -> None:
    """Given numeric then boolean template facts, resolution rejects the conflict."""
    # given: Python-equal values are authored as distinct JSON template values
    source = """\
net "numeric-equivalence"
(output) -number-> [emit]
[emit] handler "emit"
marking initial (output) <- $value
$value: number {"value": 1}
$value: number {"value": true}
"""

    # when: lowered portable IR is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(
            lower_petrinet_text(source, "boolean-template-conflict.petrinet")
        )

    # then: the boolean does not merge with the numeric template fact
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == "conflicting definitions for template $value"
    assert diagnostic.span.start.line == 6
    assert diagnostic.related[0].span.start.line == 5


def test_portable_ir_rejects_empty_arc_handle_identity_set() -> None:
    """Given a handle with no ArcIds, portable-IR resolution rejects its shape."""
    # given: lowering produced a valid handle, then an importer emptied arcIds
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.empty-arc-handle.petrinet"
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
        and contribution["target"]["name"] == "vend_signal"
    )
    handle["value"]["arcIds"] = []

    # when: the malformed portable IR is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: schema-aligned nonempty arcIds validation rejects the handle
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid arc handle contribution"
    assert diagnostic.span.start.line == 6


@pytest.mark.parametrize(
    ("contribution_kind", "invalid_document", "expected_message"),
    [
        ("arc.handle", "", "invalid arc handle contribution"),
        ("arc.handle", "/absolute/source.petrinet", "invalid arc handle contribution"),
        (
            "arc.handle",
            "https://example.test/source.petrinet",
            "invalid arc handle contribution",
        ),
        ("arc.declare", "", "invalid arc declaration"),
        ("arc.declare", "/absolute/source.petrinet", "invalid arc declaration"),
        (
            "arc.declare",
            "https://example.test/source.petrinet",
            "invalid arc declaration",
        ),
    ],
)
def test_portable_ir_rejects_nonrelative_arc_id_documents(
    contribution_kind: str,
    invalid_document: str,
    expected_message: str,
) -> None:
    """Given a non-relative ArcId document, resolution enforces the IR schema."""
    # given: one valid lowered ArcId is minimally changed at one use site
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.invalid-arc-document.petrinet"
    )
    contribution = next(
        item
        for item in portable_ir["contributions"]
        if item["kind"] == contribution_kind
        and (
            contribution_kind == "arc.declare"
            or item["target"]["name"] == "vend_signal"
        )
    )
    arc_id = (
        contribution["value"]["arcIds"][0]
        if contribution_kind == "arc.handle"
        else contribution["target"]["id"]
    )
    arc_id["document"] = invalid_document

    # when: portable IR containing the schema-invalid ArcId is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: the contribution using that identity is rejected deterministically
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == expected_message


def test_arc_declaration_target_must_match_its_contribution_identity() -> None:
    """Given a rewritten declaration target, the contribution ID still binds."""
    # given: an importer rewrites an arc declaration target to a
    # valid-but-fabricated ArcId, leaving its contribution ID intact
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.mismatched-arc-identity.petrinet"
    )
    declaration = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.declare"
    )
    original_arc_id = declaration["target"]["id"]
    declaration["target"]["id"] = {
        **original_arc_id,
        "statement": original_arc_id["statement"] + 100,
    }

    # when: the mismatched declaration target is resolved
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: arc.declare identity must still correspond to its own contribution ID
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid arc declaration"
    assert diagnostic.span.start.line == 3


_TWO_ARC_HANDLE_SOURCE = """\
net "two-arc-handle"
@through: (input) -item-> [move] -item-> (output)
[move] handler "move"
"""


def test_portable_ir_accepts_handle_for_its_ordered_two_arc_expansion_run() -> None:
    """Given one handled chain, its ordered consume/produce run resolves intact."""
    # given: lowering assigns the handle both consecutive arcs from its statement
    portable_ir = lower_petrinet_text(_TWO_ARC_HANDLE_SOURCE, "two-arc-handle.petrinet")
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    )

    # when: the unmodified portable IR is resolved
    resolved = resolve_contribution_ir(portable_ir)

    # then: the valid control preserves the authored consume-then-produce run
    assert handle["value"]["arcIds"] == [
        {"document": "two-arc-handle.petrinet", "statement": 1, "part": 1},
        {"document": "two-arc-handle.petrinet", "statement": 1, "part": 2},
    ]
    assert resolved["arcs"] == [
        {
            "from": {"place": "input"},
            "to": {"transition": "move"},
            "consume": {"type": "item"},
        },
        {
            "from": {"transition": "move"},
            "to": {"place": "output"},
            "produce": {"destination": "output", "type": "item"},
        },
    ]


def test_portable_ir_rejects_truncated_two_arc_handle_expansion_run() -> None:
    """Given a two-arc statement with one ID omitted, the handle run is incomplete."""
    # given: a valid two-arc handle is truncated to its real first arc
    portable_ir = lower_petrinet_text(
        _TWO_ARC_HANDLE_SOURCE, "two-arc-handle.truncated.petrinet"
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    )
    handle["value"]["arcIds"] = handle["value"]["arcIds"][:1]

    # when: the referentially valid prefix is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: the handle must list the statement's complete ordered declaration run
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == "invalid arc handle contribution"
    assert diagnostic.span.start.line == 2
    assert diagnostic.span.start.column == 1


def test_portable_ir_rejects_handle_ids_from_another_statement() -> None:
    """Given a handle retargeted to a real foreign arc, its own statement still binds."""
    # given: the handle's valid IDs are replaced by an existing arc from another statement
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.foreign-handle-run.petrinet"
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    )
    foreign_arc = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.declare"
        and contribution["id"]["statement"] != handle["id"]["statement"]
    )
    handle["value"]["arcIds"] = [deepcopy(foreign_arc["target"]["id"])]

    # when: the referentially valid but foreign handle is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: handle ownership is rejected rather than accepted by ID existence alone
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid arc handle contribution"


def test_portable_ir_rejects_reversed_handle_expansion_run() -> None:
    """Given both own arcs in reverse order, the handle's source order still binds."""
    # given: a valid two-arc handle is mutated to list its own run backwards
    portable_ir = lower_petrinet_text(
        _TWO_ARC_HANDLE_SOURCE, "two-arc-handle.reversed.petrinet"
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    )
    handle["value"]["arcIds"].reverse()

    # when: the reversed run is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: an ordered expansion run is required
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid arc handle contribution"


@pytest.mark.parametrize("replacement_parts", [(1, 3), (2, 3)])
def test_portable_ir_rejects_noncanonical_handle_parts(
    replacement_parts: tuple[int, int],
) -> None:
    """Given consistently rewritten arc identities, only canonical run parts are valid."""
    # given: handle and declaration IDs agree on existing arcs but their parts
    # are either nonconsecutive or start at the wrong expansion part
    portable_ir = lower_petrinet_text(
        _TWO_ARC_HANDLE_SOURCE, "two-arc-handle.invalid-parts.petrinet"
    )
    handle = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    )
    declarations = [
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.declare"
    ]
    for arc_id, declaration, replacement_part in zip(
        handle["value"]["arcIds"], declarations, replacement_parts, strict=True
    ):
        arc_id["part"] = replacement_part
        declaration["id"]["part"] = replacement_part
        declaration["target"]["id"]["part"] = replacement_part

    # when: internally consistent but noncanonical parts are imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: the handle must name exactly its canonical consecutive expansion
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid arc handle contribution"


def test_portable_ir_rejects_overlapping_arc_handles() -> None:
    """Given distinct handles naming one arc, imported handle ownership is exclusive."""
    source = """\
net "overlapping-handles"
@first: (first_input) -item-> [first]
@second: (second_input) -item-> [second]
[first] handler "first"
[second] handler "second"
"""
    # given: a second handle is mutated to name the first handle's existing arc
    portable_ir = lower_petrinet_text(source, "overlapping-handles.petrinet")
    handles = [
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.handle"
    ]
    handles[1]["value"]["arcIds"] = deepcopy(handles[0]["value"]["arcIds"])

    # when: the overlapping handles are imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: one arc expansion cannot be owned by two handles
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid arc handle contribution"


def test_portable_ir_rejects_contribution_source_differing_from_document_id() -> None:
    """Given a portable relative ID from another document, document identity still binds."""
    # given: one contribution has a valid relative source unequal to document.id
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.contribution-source.petrinet"
    )
    portable_ir["contributions"][0]["id"]["source"] = "other-document.petrinet"

    # when: the cross-document contribution is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: every contribution identity belongs to the enclosing document
    assert raised.value.diagnostic.code == "PN200"
    assert (
        raised.value.diagnostic.message
        == "contribution identity source must match document id"
    )


@pytest.mark.parametrize(
    "invalid_source", ["other-document.petrinet", "/absolute/source.petrinet"]
)
def test_portable_ir_rejects_nonportable_or_foreign_primary_span_source(
    invalid_source: str,
) -> None:
    """Given a foreign or absolute primary span, contribution provenance is rejected."""
    # given: one contribution span is not the relative source named by its identity
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.primary-span-source.petrinet"
    )
    portable_ir["contributions"][0]["span"]["source"] = invalid_source

    # when: the provenance-invalid contribution is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: all primary spans are portable and source-equal to their contribution
    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid contribution span"


@pytest.mark.parametrize(
    "invalid_source", ["other-document.petrinet", "/absolute/source.petrinet"]
)
def test_portable_ir_rejects_nonportable_or_foreign_transition_name_span_source(
    invalid_source: str,
) -> None:
    """Given a foreign or absolute nested span, consumed-arc provenance is rejected."""
    # given: a consumed arc's transitionNameSpan source differs from its contribution
    portable_ir = lower_petrinet_text(
        _COOKIE_SOURCE, "cookie-vending.transition-span-source.petrinet"
    )
    consumed_arc = next(
        contribution
        for contribution in portable_ir["contributions"]
        if contribution["kind"] == "arc.declare"
        and contribution["value"]["mode"] == "consume"
    )
    consumed_arc["value"]["transitionNameSpan"]["source"] = invalid_source

    # when: the nested provenance-invalid span is imported
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable_ir)

    # then: transitionNameSpan follows the same portable source identity contract
    assert raised.value.diagnostic.code == "PN200"
    assert (
        raised.value.diagnostic.message == "consumed arc has invalid transitionNameSpan"
    )
