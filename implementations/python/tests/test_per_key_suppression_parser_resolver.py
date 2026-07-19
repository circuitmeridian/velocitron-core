"""Red lowering, validation, CEL, and canonical contracts for Slice 07."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import parse_net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = (
    _REPOSITORY_ROOT / "examples" / "capability-ladder" / "07-per-key-suppression"
)
_DSL_PATH = _FIXTURE_ROOT / "per-key-suppression.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "per-key-suppression.json"
_CONFORMANCE_ROOT = (
    _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "07-per-key-suppression"
)
_CANONICAL_SOURCE_PATH = _CONFORMANCE_ROOT / "per-key-suppression.canonical.source.json"
_SOURCE_CASE_PATH = _CONFORMANCE_ROOT / "per-key-suppression.source.json"
_IR_CASE_PATH = _CONFORMANCE_ROOT / "per-key-suppression.contribution-ir.json"
_NET_CASE_PATH = _CONFORMANCE_ROOT / "per-key-suppression.net.json"
_INVALID_TARGET_PATH = (
    _CONFORMANCE_ROOT / "per-key-suppression.invalid-non-inhibit-target.json"
)
_MALFORMED_CEL_PATH = _CONFORMANCE_ROOT / "per-key-suppression.malformed-cel.json"
_IR_SCHEMA_PATH = _REPOSITORY_ROOT / "spec" / "petrinet-contribution-ir.schema.json"
_CORRELATE = "token.key == binding.requests[0].key"


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def _conformance_document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_fresh_suppression_ir_matches_schema_and_frozen_portable_contract() -> None:
    """Given frozen source, lowering reproduces schema-valid portable IR exactly."""
    # given: the portable source tuple plus independent frozen IR and schema
    source_case = _conformance_document(_SOURCE_CASE_PATH)
    expected = _conformance_document(_IR_CASE_PATH)
    schema = _conformance_document(_IR_SCHEMA_PATH)

    # when: the source is freshly lowered rather than loading expected output
    actual = lower_petrinet_text(source_case["text"], source_case["sourceId"])

    # then: complete contribution order, spans, target, and CEL bytes are frozen
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(actual)  # pyright: ignore[reportUnknownMemberType]
    assert actual == expected


def test_frozen_suppression_ir_resolves_to_exact_frozen_net() -> None:
    """Given portable IR, resolution reproduces exact ordered core JSON."""
    # given: independently frozen contribution and net artifacts
    portable = _conformance_document(_IR_CASE_PATH)
    expected = _conformance_document(_NET_CASE_PATH)

    # when: a consumer resolves the portable contribution document directly
    actual = resolve_contribution_ir(portable)

    # then: correlation remains nested on the third ordered inhibit arc
    assert actual == expected
    assert actual["arcs"][2]["consume"]["correlate"] == {"cel": _CORRELATE}


def test_correlate_fact_lowers_after_handler_and_targets_named_inhibitor() -> None:
    """Given grouped source, lowering retains exact fact shape and source order."""
    # given: topology, then handler, then correlate, then markings and templates
    source = _DSL_PATH.read_text(encoding="utf-8")

    # when: ANTLR lowering produces portable progressive contributions
    portable = lower_petrinet_text(source, str(_DSL_PATH))
    contributions = portable["contributions"]

    # then: correlation is a dedicated one-member CEL fact on the authored handle
    facts = [fact for fact in contributions if fact["kind"] == "arc.correlate"]
    assert len(facts) == 1
    assert facts[0]["target"] == {"type": "arcHandle", "name": "not_suppressed"}
    assert facts[0]["value"] == {"cel": _CORRELATE}
    # and: normative grouped order survives lowering without folding into topology
    kinds = [fact["kind"] for fact in contributions]
    assert kinds == [
        "document.net-header",
        "arc.handle",
        "arc.declare",
        "arc.declare",
        "arc.handle",
        "arc.declare",
        "transition.handler",
        "arc.correlate",
        "marking.append",
        "marking.append",
        "marking.append",
        "template.define",
        "template.define",
        "template.define",
    ]


def test_suppression_source_resolves_to_exact_first_class_core_document() -> None:
    """Given the canonical source, resolution equals its paired JSON exactly."""
    # given: the authoritative source and independently paired core document
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: the public compiler resolves the progressive source
    actual = compile_petrinet_text(source, str(_DSL_PATH))

    # then: topology, declaration order, correlation, and marking are exact
    assert actual == expected
    assert actual["places"] == [
        {"name": "requests", "accepts": ["request"]},
        {"name": "admitted", "accepts": ["request"]},
        {"name": "suppressions", "accepts": ["suppression"]},
    ]
    assert actual["transitions"] == [{"name": "admit", "handler": "admit_request"}]
    assert actual["arcs"] == [
        {
            "from": {"place": "requests"},
            "to": {"transition": "admit"},
            "consume": {"type": "request"},
        },
        {
            "from": {"transition": "admit"},
            "to": {"place": "admitted"},
            "produce": {"type": "request", "destination": "admitted"},
        },
        {
            "from": {"place": "suppressions"},
            "to": {"transition": "admit"},
            "consume": {
                "type": "suppression",
                "mode": "inhibit",
                "correlate": {"cel": _CORRELATE},
            },
        },
    ]
    assert actual["initialMarking"] == {
        "requests": [
            {"type": "request", "data": {"key": "A"}},
            {"type": "request", "data": {"key": "B"}},
        ],
        "suppressions": [{"type": "suppression", "data": {"key": "A"}}],
    }
    inhibit = actual["arcs"][2]["consume"]
    assert inhibit["correlate"] == {"cel": _CORRELATE}
    assert "predicate" not in inhibit
    assert "guard" not in actual["transitions"][0]
    assert "annotations" not in actual["arcs"][2]
    assert all("capacityPerColorKey" not in place for place in actual["places"])


def test_resolved_json_parses_and_compiles_correlate_at_parse_time() -> None:
    """Given resolved JSON, parse_net accepts the inhibit-only CEL inscription."""
    # given: the exact resolved core fixture
    expected = _fixture_document()

    # when: core parsing validates structure and compiles inline CEL immediately
    net = parse_net(expected)

    # then: the schema-level object is represented as the exact CEL source
    inhibitor = next(arc for arc in net.arcs if arc.from_place == "suppressions")
    assert inhibitor.consume is not None
    assert inhibitor.consume.mode == "inhibit"
    assert inhibitor.consume.correlate == _CORRELATE


@pytest.mark.parametrize(
    "topology",
    [
        "@target: (requests) -request-> [admit]",
        "@target: (requests) -request->? [admit]",
        "@target: [admit] -request-> (admitted)",
    ],
)
def test_correlate_rejects_every_non_inhibit_arc_at_authored_fact(
    topology: str,
) -> None:
    """Given correlate on a non-inhibitor, PN202 identifies the fact and arc."""
    # given: a consume, read, or produce handle with a separately authored correlation
    source = (
        "net invalid_correlate\n\n"
        f"{topology}\n"
        '[admit] handler "admit_request"\n'
        '@target correlate cel "token.key == binding.requests[0].key"\n'
    )

    # when: progressive resolution mode-checks the targeted arc
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "invalid-correlate-mode.petrinet")

    # then: the error is attached to correlate, describes inhibit-only use,
    # and retains the topology declaration as related source context
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.span.source == "invalid-correlate-mode.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (5, 1)
    assert "correlate is only allowed on ->0 inhibit arcs" in diagnostic.message
    assert len(diagnostic.related) == 1
    assert diagnostic.related[0].span.start.line == 3


def test_malformed_correlate_cel_fails_during_resolution_at_expression_fact() -> None:
    """Given invalid CEL, PN203 is raised before parsing or engine execution."""
    # given: a valid inhibit topology carrying syntactically malformed CEL
    source = """\
net invalid_correlate_cel

@blocked: (suppressions) -suppression->0 [admit]
[admit] handler "admit_request"
@blocked correlate cel "token.key ==== binding.requests[0].key"
"""

    # when: resolution compiles the inline expression
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "invalid-correlate-cel.petrinet")

    # then: the stable diagnostic points to the progressive correlate fact
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN203"
    assert diagnostic.message == "invalid CEL correlate for arc @blocked"
    assert diagnostic.help == "fix the CEL expression syntax"
    assert diagnostic.span.source == "invalid-correlate-cel.petrinet"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (5, 1)


@pytest.mark.parametrize("case_path", [_INVALID_TARGET_PATH, _MALFORMED_CEL_PATH])
def test_invalid_corpus_case_reproduces_complete_diagnostic(case_path: Path) -> None:
    """Given a frozen invalid source, resolution reproduces its exact diagnostic."""
    # given: an independent source tuple and complete expected diagnostic
    case = _conformance_document(case_path)
    source = cast(dict[str, str], case["source"])
    expected = cast(dict[str, Any], case["diagnostic"])

    # when: public compilation validates target mode or CEL syntax
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source["text"], source["sourceId"])

    # then: code, wording, help, full portable span, and related topology all match
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == expected["code"]
    assert diagnostic.message == expected["message"]
    assert diagnostic.help == expected["help"]
    assert diagnostic.span.as_dict() == expected["span"]
    assert [
        {"message": related.message, "span": related.span.as_dict()}
        for related in diagnostic.related
    ] == expected.get("related", [])


def test_canonical_emission_names_inhibitor_and_round_trips_correlation_exactly() -> (
    None
):
    """Given paired JSON, canonical DSL preserves operator, CEL bytes, and arc order."""
    # given: an unannotated resolved document with one correlated inhibitor
    expected = _fixture_document()
    canonical_case = cast(
        dict[str, str], json.loads(_CANONICAL_SOURCE_PATH.read_text(encoding="utf-8"))
    )
    expected_canonical = canonical_case["text"]

    # when: canonical emission and a complete reparse are performed twice
    canonical = emit_petrinet(expected)
    reparsed = compile_petrinet_text(canonical, canonical_case["sourceId"])

    # then: the generated handle owns only ->0 plus one correlate elaboration
    assert canonical == expected_canonical
    assert canonical.count("@arc_2") == 2
    assert "@arc_2: (suppressions) -suppression->0 [admit]" in canonical
    assert f'@arc_2 correlate cel "{_CORRELATE}"' in canonical
    assert reparsed == expected
    assert emit_petrinet(reparsed) == canonical
