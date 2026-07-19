"""Red grammar, resolution, validation, and canonical contracts for Batch Gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "06-batch-gate"
_DSL_PATH = _FIXTURE_ROOT / "batch-gate.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "batch-gate.json"
_CONFORMANCE_ROOT = (
    _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "06-batch-gate"
)
_SOURCE_CASE_PATH = _CONFORMANCE_ROOT / "batch-gate.source.json"
_CANONICAL_SOURCE_CASE_PATH = _CONFORMANCE_ROOT / "batch-gate.canonical.source.json"
_IR_CASE_PATH = _CONFORMANCE_ROOT / "batch-gate.contribution-ir.json"
_NET_CASE_PATH = _CONFORMANCE_ROOT / "batch-gate.net.json"
_INVALID_WEIGHT_PATH = _CONFORMANCE_ROOT / "batch-gate.invalid-weight.json"
_INVALID_CAPACITY_PATH = _CONFORMANCE_ROOT / "batch-gate.invalid-capacity.json"
_IR_SCHEMA_PATH = _REPOSITORY_ROOT / "spec" / "petrinet-contribution-ir.schema.json"


def _fixture_document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def _conformance_document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _capacity_source(value: str) -> str:
    return (
        "net capacity_shape\n\n"
        "(batches) -batch-> [take]\n"
        '[take] handler "take"\n'
        f"(batches) capacityPerColorKey {value}\n"
    )


def test_fresh_batch_ir_matches_schema_and_frozen_portable_contract() -> None:
    """Given the conformance source, lowering reproduces schema-valid frozen IR."""
    # given: the portable source tuple and independently frozen IR/schema artifacts
    source_case = _conformance_document(_SOURCE_CASE_PATH)
    expected = _conformance_document(_IR_CASE_PATH)
    schema = _conformance_document(_IR_SCHEMA_PATH)

    # when: source is freshly lowered instead of loading the expected artifact
    actual = lower_petrinet_text(source_case["text"], source_case["sourceId"])

    # then: the result is valid portable IR and exactly reproduces the frozen corpus
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(actual)  # pyright: ignore[reportUnknownMemberType]
    assert actual == expected


def test_frozen_batch_ir_resolves_to_exact_frozen_net() -> None:
    """Given frozen portable IR, resolution reproduces exact ordered core JSON."""
    # given: the independently frozen contribution artifact and core net artifact
    portable = _conformance_document(_IR_CASE_PATH)
    expected = _conformance_document(_NET_CASE_PATH)

    # when: a consumer imports and resolves the contribution IR directly
    actual = resolve_contribution_ir(portable)

    # then: topology, weight, capacity, and marking order all match exactly
    assert actual == expected


def test_batch_gate_facts_lower_to_distinct_portable_contributions() -> None:
    """Given weight and capacity facts, lowering keeps both portable and ordered."""
    # given: the exact authored Batch Gate source and its named input handle
    source = _DSL_PATH.read_text(encoding="utf-8")

    # when: ANTLR lowering creates contribution IR before core resolution
    portable = lower_petrinet_text(source, str(_DSL_PATH))
    feature_facts = [
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] in {"place.capacity-per-color-key", "arc.weight"}
    ]

    # then: capacity is place-local and weight targets the authored arc identity
    assert [fact["kind"] for fact in feature_facts] == [
        "place.capacity-per-color-key",
        "arc.weight",
    ]
    assert feature_facts[0]["target"] == {"type": "place", "name": "batches"}
    assert feature_facts[0]["value"] == {"key": "batch_id", "max": 1}
    assert feature_facts[1]["target"] == {"type": "arcHandle", "name": "items"}
    assert feature_facts[1]["value"] == {"weight": 2}


def test_batch_gate_resolves_to_exact_first_class_core_document() -> None:
    """Given the exact source, resolution preserves weight, capacity, and ordering."""
    # given: the paired authoritative source and JSON lowering
    source = _DSL_PATH.read_text(encoding="utf-8")
    expected = _fixture_document()

    # when: the public compiler resolves all progressive facts
    actual = compile_petrinet_text(source, str(_DSL_PATH))

    # then: the complete semantic document is the paired fixture, not an approximation
    assert actual == expected
    assert (len(actual["places"]), len(actual["transitions"]), len(actual["arcs"])) == (
        2,
        1,
        2,
    )
    assert actual["places"] == [
        {"name": "items", "accepts": ["item"]},
        {
            "name": "batches",
            "accepts": ["batch"],
            "capacityPerColorKey": {"key": "batch_id", "max": 1},
        },
    ]
    assert actual["transitions"] == [{"name": "form_batch", "handler": "form_batch"}]
    assert actual["arcs"] == [
        {
            "from": {"place": "items"},
            "to": {"transition": "form_batch"},
            "consume": {"type": "item", "weight": 2},
        },
        {
            "from": {"transition": "form_batch"},
            "to": {"place": "batches"},
            "produce": {"type": "batch", "destination": "batches"},
        },
    ]
    # and: multiplier expansion is consecutive and no runtime/annotation shim appears
    assert actual["initialMarking"] == {
        "items": [
            {"type": "item", "data": {"batch_id": "A"}},
            {"type": "item", "data": {"batch_id": "A"}},
        ],
        "batches": [{"type": "batch", "data": {"batch_id": "A"}}],
    }
    assert "annotations" not in actual["places"][1]
    assert all("capacity" not in transition for transition in actual["transitions"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('{"key":"batch_id","max":1}', {"key": "batch_id", "max": 1}),
        (
            '{"key":["tenant_id","batch_id"],"max":2}',
            {"key": ["tenant_id", "batch_id"], "max": 2},
        ),
    ],
)
def test_capacity_accepts_only_the_two_normative_key_shapes(
    value: str, expected: dict[str, object]
) -> None:
    """Given a scalar or composite non-empty key, resolution preserves JSON shape."""
    # given: a place fact with exactly key and max
    source = _capacity_source(value)

    # when: the fact resolves onto its declared place
    actual = compile_petrinet_text(source, "capacity-valid.petrinet")

    # then: the first-class schema spelling and scalar/array shape are unchanged
    assert actual["places"] == [
        {
            "name": "batches",
            "accepts": ["batch"],
            "capacityPerColorKey": expected,
        }
    ]


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '{"key":"batch_id"}',
        '{"max":1}',
        '{"key":"","max":1}',
        '{"key":[],"max":1}',
        '{"key":["batch_id", ""],"max":1}',
        '{"key":true,"max":1}',
        '{"key":["batch_id",1],"max":1}',
        '{"key":"batch_id","max":-1}',
        '{"key":"batch_id","max":true}',
        '{"key":"batch_id","max":1,"extra":false}',
    ],
)
def test_capacity_rejects_invalid_shape_at_the_place_fact(value: str) -> None:
    """Given an invalid capacity object, validation rejects it at authored source."""
    # given: one invalid key, max, boolean-as-integer, or extra-member variant
    source = _capacity_source(value)

    # when: resolution validates the progressive place fact
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "capacity-invalid.petrinet")

    # then: PN202 remains attached to the authored fact, not generated JSON
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.span.source == "capacity-invalid.petrinet"
    assert diagnostic.span.start.line == 5


def test_zero_capacity_reports_the_exact_corpus_diagnostic() -> None:
    """Given the invalid corpus case, PN202 reproduces its complete diagnostic."""
    # given: the independently frozen source tuple and expected diagnostic
    case = _conformance_document(_INVALID_CAPACITY_PATH)
    source = cast(dict[str, str], case["source"])
    expected = cast(dict[str, Any], case["diagnostic"])

    # when: resolution validates max against the schema minimum
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source["text"], source["sourceId"])

    # then: the literal zero, stable wording, and portable span all match the corpus
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == expected["code"]
    assert diagnostic.message == expected["message"]
    assert diagnostic.help is None
    assert diagnostic.span.as_dict() == expected["span"]


@pytest.mark.parametrize("weight", ["-1", "true", "1.5"])
def test_consume_weight_rejects_non_positive_or_non_integer_values(weight: str) -> None:
    """Given an invalid consume weight, validation rejects the authored weight fact."""
    # given: an otherwise valid handled consume arc
    source = (
        "net batch_gate_invalid\n\n"
        "@items: (items) -item-> [form_batch]\n"
        '[form_batch] handler "form_batch"\n'
        f"@items weight {weight}\n"
    )

    # when: the progressive weight is parsed and resolved
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "batch_gate_invalid.petrinet")

    # then: PN202 targets the invalid value and identifies the submitted handle
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.span.source == "batch_gate_invalid.petrinet"
    assert diagnostic.span.start.line == 5
    assert "weight" in diagnostic.message
    assert "greater than or equal to 1" in diagnostic.message
    assert "@items" in diagnostic.message


def test_zero_weight_reports_the_exact_corpus_diagnostic() -> None:
    """Given the invalid corpus case, PN202 reproduces its complete diagnostic."""
    # given: the independently frozen source tuple and expected diagnostic
    case = _conformance_document(_INVALID_WEIGHT_PATH)
    source = cast(dict[str, str], case["source"])
    expected = cast(dict[str, Any], case["diagnostic"])

    # when: resolution validates the consume weight
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source["text"], source["sourceId"])

    # then: the literal zero, stable wording, and portable span all match the corpus
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == expected["code"]
    assert diagnostic.message == expected["message"]
    assert diagnostic.help is None
    assert diagnostic.span.as_dict() == expected["span"]


@pytest.mark.parametrize(
    ("topology", "expected"),
    [
        (
            "@input: (items) -item-> [form_batch]",
            {"type": "item"},
        ),
        (
            "@open: (batches) -batch->0 [form_batch]",
            {"type": "batch", "mode": "inhibit"},
        ),
    ],
)
def test_explicit_default_weight_one_is_semantically_omitted(
    topology: str, expected: dict[str, object]
) -> None:
    """Given weight one on consume/inhibit, resolution retains the default semantics."""
    # given: a valid consume or inhibit arc with an explicit default weight fact
    handle = topology.split(":", 1)[0][1:]
    source = (
        "net default_weight\n\n"
        f"{topology}\n"
        '[form_batch] handler "form_batch"\n'
        f"@{handle} weight 1\n"
    )

    # when: the weight fact resolves
    actual = compile_petrinet_text(source, "default-weight.petrinet")

    # then: core JSON omits default weight while preserving the arc's input mode
    assert actual["arcs"][0]["consume"] == expected


@pytest.mark.parametrize(
    ("topology", "weight", "expected_fragment"),
    [
        ("@out: [form_batch] -batch-> (batches)", 2, "produce"),
        ("@open: (batches) -batch->0 [form_batch]", 2, "inhibit"),
    ],
)
def test_weight_rejects_produce_and_non_default_inhibit_targets(
    topology: str, weight: int, expected_fragment: str
) -> None:
    """Given a non-consumptive target, weight cannot change its arc semantics."""
    # given: a produce arc or an inhibit zero-test carrying a non-default weight
    source = (
        "net invalid_weight_target\n\n"
        f"{topology}\n"
        '[form_batch] handler "form_batch"\n'
        f"@{topology.split(':', 1)[0][1:]} weight {weight}\n"
    )

    # when: the resolver applies the weight fact to the selected arc
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "invalid-weight-target.petrinet")

    # then: PN202 rejects it at the feature fact and names the arc mode
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.span.source == "invalid-weight-target.petrinet"
    assert diagnostic.span.start.line == 5
    assert "weight" in diagnostic.message
    assert expected_fragment in diagnostic.message


def test_batch_gate_json_emits_normative_grouping_as_a_canonical_fixed_point() -> None:
    """Given paired JSON, canonical DSL exactly reproduces its frozen source."""
    # given: the exact core fixture and independently frozen canonical source bytes
    expected = _fixture_document()
    canonical_case = _conformance_document(_CANONICAL_SOURCE_CASE_PATH)

    # when: JSON is emitted, compiled, lowered, and emitted a second time
    canonical = emit_petrinet(expected)
    round_tripped = compile_petrinet_text(
        canonical, cast(str, canonical_case["sourceId"])
    )
    portable = lower_petrinet_text(canonical, cast(str, canonical_case["sourceId"]))
    second = emit_petrinet(round_tripped)

    # then: emission is the exact conformance source fixed point, byte for byte
    assert canonical == canonical_case["text"]
    assert round_tripped == expected
    assert second == canonical

    # and: the generated handle owns only the weighted consume; produce is separate
    contributions = cast(list[dict[str, Any]], portable["contributions"])
    handle = next(item for item in contributions if item["kind"] == "arc.handle")
    arcs = [item for item in contributions if item["kind"] == "arc.declare"]
    consume = next(item for item in arcs if item["value"]["mode"] == "consume")
    produce = next(item for item in arcs if item["value"]["mode"] == "produce")

    assert handle["target"] == {"type": "arcHandle", "name": "arc_0"}
    assert handle["value"]["arcIds"] == [consume["target"]["id"]]
    assert produce["target"]["id"] not in handle["value"]["arcIds"]
