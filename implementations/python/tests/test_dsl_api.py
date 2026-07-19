"""BDD coverage for the Slice 01 Coin Deposit DSL frontend."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from velocitron.dsl import cli
from velocitron.dsl.api import (
    compile_petrinet_text,
    emit_petrinet,
    load_petrinet,
    parse_petrinet_text,
    read_petrinet_text,
)
from velocitron.dsl.cli import main
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import NetValidationError, parse_net


_ROOT = Path(__file__).parents[3]
_CASE = _ROOT / "spec" / "conformance" / "petrinet" / "01-coin-deposit"
_IR_SCHEMA = _ROOT / "spec" / "petrinet-contribution-ir.schema.json"
_SOURCE = json.loads((_CASE / "coin-deposit.source.json").read_text())
_TEXT = _SOURCE["text"]
_NAME = _SOURCE["sourceId"]
_EXPECTED_IR = json.loads((_CASE / "coin-deposit.contribution-ir.json").read_text())
_EXPECTED_NET = json.loads((_CASE / "coin-deposit.net.json").read_text())


def test_given_coin_deposit_when_lowered_then_contributions_preserve_source_order() -> (
    None
):
    """Given the canonical source, lowering produces its portable v1 IR verbatim."""
    assert lower_petrinet_text(_TEXT, _NAME) == _EXPECTED_IR


def test_bare_arcs_lower_to_schema_valid_explicit_token_ir_with_authored_spans() -> (
    None
):
    # Given a bare chain next to explicitly colored topology.
    source = """\
net explicit_token

(p) -red-> [p_anchor]
[p_anchor] handler "p_anchor"
@bare: (p) -> [step] -> (q)
[step] handler "step"
[q_anchor] -red-> (q)
[q_anchor] handler "q_anchor"
"""

    # When the source is lowered to portable contribution IR.
    portable = lower_petrinet_text(source, "explicit-token.petrinet")
    schema = json.loads(_IR_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(portable)  # pyright: ignore[reportUnknownMemberType]
    handle = next(
        item for item in portable["contributions"] if item["kind"] == "arc.handle"
    )
    bare_arcs = [
        item
        for item in portable["contributions"]
        if item["kind"] == "arc.declare"
        and item["target"]["id"] in handle["value"]["arcIds"]
    ]

    # Then both arcs carry explicit core-token colors and their authored spans.
    assert [item["value"]["color"] for item in bare_arcs] == [
        {"kind": "explicit", "value": "token"},
        {"kind": "explicit", "value": "token"},
    ]
    authored = "(p) -> [step] -> (q)"
    authored_start = source.index(authored)
    assert bare_arcs[0]["span"] == {
        "source": "explicit-token.petrinet",
        "start": {"byteOffset": authored_start, "line": 5, "column": 8},
        "end": {
            "byteOffset": authored_start + len("(p) -> [step]"),
            "line": 5,
            "column": 21,
        },
    }
    output_start = source.index("[step]", authored_start)
    assert bare_arcs[1]["span"] == {
        "source": "explicit-token.petrinet",
        "start": {"byteOffset": output_start, "line": 5, "column": 15},
        "end": {
            "byteOffset": authored_start + len(authored),
            "line": 5,
            "column": 28,
        },
    }

    resolved = resolve_contribution_ir(portable)
    assert [
        arc.get("consume", arc.get("produce"))["type"] for arc in resolved["arcs"]
    ] == [
        "red",
        "token",
        "token",
        "red",
    ]


def test_legacy_inferred_arc_color_is_rejected_by_clean_v1_resolution() -> None:
    # Given otherwise valid v1 IR containing the removed inferred-color shape.
    portable = lower_petrinet_text("(p) -> [step]\n", "legacy-inferred.petrinet")
    arc = next(
        item for item in portable["contributions"] if item["kind"] == "arc.declare"
    )
    arc["value"]["color"] = {"kind": "inferred"}

    # When the clean v1 resolver decodes the legacy arc.
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    # Then the obsolete shape is rejected as invalid portable IR.
    assert raised.value.diagnostic.code == "PN200"


@pytest.mark.parametrize(
    "evidence",
    [
        "",
        "(p) -red-> [red_anchor]\n(p) -blue-> [blue_anchor]\n",
    ],
)
def test_bare_arc_is_token_with_zero_or_multiple_adjacent_colors(
    evidence: str,
) -> None:
    # Given a bare arc with either no other color evidence or two adjacent colors.
    source = "net boundary\n\n" + evidence + "(p) -> [step]\n"

    # When the complete source is compiled.
    document = compile_petrinet_text(source, "boundary.petrinet")

    # Then contextual color candidates do not change the bare arc's core token color.
    assert document["arcs"][-1]["consume"] == {"type": "token"}


def test_headerless_net_compiles_as_unnamed_and_emits_an_explicit_header() -> None:
    # Given a headerless net using progressive shorthand.
    source = "(ready) -> [run] -> (done)\n"

    # When it is compiled and canonically emitted.
    document = compile_petrinet_text(source, "headerless.petrinet")
    canonical = emit_petrinet(document)

    # Then the effective name is materialized without inventing transition behavior.
    assert document["transitions"] == [{"name": "run"}]
    assert (document["name"], canonical) == (
        "unnamed",
        """\
net unnamed

(ready) -token-> [run] -token-> (done)
""",
    )


def test_nondefault_arc_facts_apply_to_bare_token_arcs() -> None:
    # Given a named bare chain with nondefault consume and produce facts.
    source = """\
net elaborated_token

@bare: (p) -> [step] -> (q)
@bare weight 2
@bare predicate cel "data.ready"
@bare data {"kind": "receipt"}
"""

    # When the source is compiled.
    document = compile_petrinet_text(source, "elaborated-token.petrinet")

    # Then the facts elaborate explicit token arcs without changing their color.
    assert document["arcs"] == [
        {
            "from": {"place": "p"},
            "to": {"transition": "step"},
            "consume": {
                "type": "token",
                "weight": 2,
                "predicate": {"cel": "data.ready"},
            },
        },
        {
            "from": {"transition": "step"},
            "to": {"place": "q"},
            "produce": {
                "destination": "q",
                "type": "token",
                "data": {"kind": "receipt"},
            },
        },
    ]


def test_given_forward_template_when_compiled_then_json_and_model_match_contract() -> (
    None
):
    """A marking may use its immutable template before the later definition."""
    document = compile_petrinet_text(_TEXT, _NAME)
    assert document == _EXPECTED_NET
    assert parse_petrinet_text(_TEXT, _NAME) == parse_net(_EXPECTED_NET)


def test_count_only_initial_and_named_markings_lower_to_generic_token_literals() -> (
    None
):
    source = """\
net generic_counts

(ready) -> [finish] -> (done)
marking initial (ready) <- 2
marking initial (done) <- 1
marking checkpoint (done) <- 1
"""

    portable = lower_petrinet_text(source, "generic-counts.petrinet")
    schema = json.loads(_IR_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(portable)  # pyright: ignore[reportUnknownMemberType]
    marking_values = [
        contribution["value"]
        for contribution in portable["contributions"]
        if contribution["kind"] in {"marking.append", "metadata.named-marking"}
    ]

    generic_literal: dict[str, object] = {
        "color": "token",
        "data": {"type": "object", "entries": []},
    }
    assert marking_values == [
        {"count": 2, "token": generic_literal},
        {"count": 1, "token": generic_literal},
        {
            "name": "checkpoint",
            "entries": [
                {
                    "place": {"type": "place", "name": "done"},
                    "count": 1,
                    "token": generic_literal,
                }
            ],
        },
    ]

    document = resolve_contribution_ir(portable)
    generic: dict[str, object] = {"type": "token", "data": {}}
    assert document["initialMarking"] == {
        "ready": [generic, generic],
        "done": [generic],
    }
    assert document["annotations"]["petrinet.dsl/v1"]["markings"] == {
        "checkpoint": {"done": [generic]}
    }


def test_literal_marking_entries_accept_declared_colors_and_append_in_fact_order() -> (
    None
):
    source = """\
net literal_order

(queue) accepts [token, red]
marking initial (queue) <- 1
marking initial (queue) <- 1
marking initial (queue) <- 1
marking saved (queue) <- 1
marking saved (queue) <- 1
"""
    portable = lower_petrinet_text(source, "literal-order.petrinet")
    initial_facts = [
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "marking.append"
    ]
    named_facts = [
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "metadata.named-marking"
    ]
    initial_facts[1]["value"]["token"] = {
        "color": "red",
        "data": {
            "type": "object",
            "entries": [
                {"key": "sequence", "value": {"type": "number", "lexeme": "2"}}
            ],
        },
    }
    named_facts[0]["value"]["entries"][0]["token"] = {
        "color": "red",
        "data": {
            "type": "object",
            "entries": [
                {"key": "sequence", "value": {"type": "number", "lexeme": "4"}}
            ],
        },
    }

    document = resolve_contribution_ir(portable)
    assert document["initialMarking"]["queue"] == [
        {"type": "token", "data": {}},
        {"type": "red", "data": {"sequence": 2}},
        {"type": "token", "data": {}},
    ]
    assert document["annotations"]["petrinet.dsl/v1"]["markings"]["saved"]["queue"] == [
        {"type": "red", "data": {"sequence": 4}},
        {"type": "token", "data": {}},
    ]


def test_literal_marking_color_must_be_accepted_by_its_place() -> None:
    portable = lower_petrinet_text(
        "net rejected_color\n\n(queue) accepts [token]\nmarking initial (queue) <- 1\n",
        "rejected-color.petrinet",
    )
    marking = next(
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "marking.append"
    )
    marking["value"]["token"]["color"] = "red"

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "marking token color 'red' is not accepted by place (queue)"
    )


@pytest.mark.parametrize(
    ("count", "token"),
    [
        (0, {"color": "token", "data": {"type": "object", "entries": []}}),
        (-1, {"color": "token", "data": {"type": "object", "entries": []}}),
        (True, {"color": "token", "data": {"type": "object", "entries": []}}),
        (1, {"color": "token", "data": {"type": "string", "value": "not-object"}}),
        (
            1,
            {
                "color": "token",
                "data": {"type": "object", "entries": []},
                "unexpected": True,
            },
        ),
        (
            1,
            {
                "template": {"type": "template", "name": "seed"},
                "unexpected": True,
            },
        ),
    ],
)
def test_marking_resolver_rejects_non_schema_or_non_object_literal_entries(
    count: object, token: dict[str, object]
) -> None:
    portable = lower_petrinet_text(
        "net invalid_literal\n\n(queue) accepts [token]\n"
        "marking initial (queue) <- 1\n",
        "invalid-literal.petrinet",
    )
    marking = next(
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "marking.append"
    )
    marking["value"] = {"count": count, "token": token}

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid initial marking contribution"


@pytest.mark.parametrize(
    ("count", "token", "unexpected"),
    [
        (0, {"color": "token", "data": {"type": "object", "entries": []}}, False),
        (1, {"color": "token", "data": {"type": "array", "items": []}}, False),
        (1, {"color": "token", "data": {"type": "object", "entries": []}}, True),
    ],
)
def test_named_marking_resolver_validates_literal_entry_shape_data_and_count(
    count: object, token: dict[str, object], unexpected: bool
) -> None:
    portable = lower_petrinet_text(
        "net invalid_named_literal\n\n(queue) accepts [token]\n"
        "marking checkpoint (queue) <- 1\n",
        "invalid-named-literal.petrinet",
    )
    marking = next(
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "metadata.named-marking"
    )
    entry = marking["value"]["entries"][0]
    entry["count"] = count
    entry["token"] = token
    if unexpected:
        entry["unexpected"] = True

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid named marking contribution"


def test_named_literal_marking_color_must_be_accepted_by_its_place() -> None:
    portable = lower_petrinet_text(
        "net rejected_named_color\n\n(queue) accepts [token]\n"
        "marking checkpoint (queue) <- 1\n",
        "rejected-named-color.petrinet",
    )
    marking = next(
        contribution
        for contribution in portable["contributions"]
        if contribution["kind"] == "metadata.named-marking"
    )
    marking["value"]["entries"][0]["token"]["color"] = "red"

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "named marking 'checkpoint' token color 'red' is not accepted by place (queue)"
    )


@pytest.mark.parametrize(
    ("fact", "message"),
    [
        (
            "marking initial (missing) <- 1",
            "marking refers to unknown place (missing)",
        ),
        (
            "marking saved (missing) <- 1",
            "named marking 'saved' references unknown place 'missing'; "
            "marking facts cannot declare semantic objects",
        ),
    ],
)
def test_count_only_marking_rejects_unknown_places(fact: str, message: str) -> None:
    source = f"net unknown_marking\n\n(known) accepts [token]\n{fact}\n"

    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "unknown-marking.petrinet")

    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == message


@pytest.mark.parametrize("value", ["0", "-1", "", "1 * 2", "2 *"])
def test_count_only_marking_requires_one_bare_positive_integer(value: str) -> None:
    source = (
        "net invalid_count\n\n(queue) accepts [token]\n"
        f"marking initial (queue) <- {value}\n"
    )

    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, "invalid-count.petrinet")

    assert raised.value.diagnostic.code == "PN101"


def test_template_marking_forms_keep_default_and_explicit_counts() -> None:
    source = """\
net template_counts

(queue) accepts [red]
marking initial (queue) <- $first
marking initial (queue) <- 2 * $second
$first: red {"id": "first"}
$second: red {"id": "second"}
"""

    document = compile_petrinet_text(source, "template-counts.petrinet")

    assert document["initialMarking"]["queue"] == [
        {"type": "red", "data": {"id": "first"}},
        {"type": "red", "data": {"id": "second"}},
        {"type": "red", "data": {"id": "second"}},
    ]


@pytest.mark.parametrize(
    ("lexeme", "rendered"),
    [("1e-6", "1e-06"), ("1e20", "1e+20"), ("-0.0", "-0.0")],
)
def test_invalid_numeric_diagnostics_use_python_binary64_spelling(
    lexeme: str, rendered: str
) -> None:
    # Given: a non-integral weight represented near Python exponent boundaries.
    source = (
        f"net numbers\n\n@weighted: (queue) -> [advance]\n@weighted weight {lexeme}\n"
    )

    # When: the source is compiled.
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "numbers.petrinet")

    # Then: the exact diagnostic carries Python's stable binary64 spelling.
    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "arc weight must be an integer greater than or equal to 1; "
        f"got {rendered} for @weighted"
    )


def test_template_marking_requires_object_token_data() -> None:
    # Given: a scalar template used as marking token data.
    source = """\
net templates

(queue) -> [advance]
marking initial (queue) <- $payload
$payload: token "scalar"
"""

    # When: the source is compiled.
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "templates.petrinet")

    # Then: resolution rejects the non-object token data.
    assert raised.value.diagnostic.code == "PN202"
    assert raised.value.diagnostic.message == (
        "template $payload data must be a JSON object"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "[advance] priority 9007199254740992",
        "[advance] order 9007199254740992",
        "marking initial (queue) <- 9007199254740992",
    ],
)
def test_integer_literals_cannot_exceed_the_safe_ieee754_range(statement: str) -> None:
    # Given: a DSL integer whose value cannot cross the JSON boundary exactly.
    source = f"net unsafe\n\n(queue) -> [advance]\n{statement}\n"

    # When: the source is lowered.
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, "unsafe.petrinet")

    # Then: lowering rejects it before any lossy numeric conversion.
    assert raised.value.diagnostic.code == "PN101"
    assert raised.value.diagnostic.message == (
        "integer exceeds the safe IEEE-754 range"
    )


def test_handler_facts_are_idempotent_and_require_declared_targets() -> None:
    # Given: equal repeated handler facts for one declared transition.
    equal = """\
net handlers

[advance]
[advance] handler "go"
[advance] handler "go"
"""

    # When: the source is compiled.
    document = compile_petrinet_text(equal, "handlers.petrinet")

    # Then: equal facts collapse to one handler.
    assert document["transitions"] == [{"name": "advance", "handler": "go"}]

    # When: a repeated handler value conflicts.
    with pytest.raises(PetrinetDslError) as conflict:
        compile_petrinet_text(
            equal.replace('[advance] handler "go"\n', '[advance] handler "stop"\n', 1),
            "handlers.petrinet",
        )

    # Then: the conflict cites both facts.
    assert conflict.value.diagnostic.code == "PN202"
    assert conflict.value.diagnostic.message == (
        "conflicting handler facts for transition [advance]"
    )
    assert conflict.value.diagnostic.related[0].message == (
        "first handler was declared here"
    )

    # When: a handler fact targets no topology or standalone declaration.
    with pytest.raises(PetrinetDslError) as unknown:
        compile_petrinet_text(
            'net handlers\n\n[ghost] handler "go"\n',
            "handlers.petrinet",
        )

    # Then: the fact cannot declare the transition.
    assert unknown.value.diagnostic.code == "PN202"
    assert unknown.value.diagnostic.message == (
        "handler refers to unknown transition [ghost]"
    )


def test_eof_and_unicode_relative_spans_keep_portable_coordinates() -> None:
    # Given: source truncated exactly at EOF.
    with pytest.raises(PetrinetDslError) as eof:
        lower_petrinet_text("net", "truncated.petrinet")

    # Then: EOF is a zero-width span at source length.
    assert eof.value.diagnostic.span.as_dict() == {
        "source": "truncated.petrinet",
        "start": {"byteOffset": 3, "line": 1, "column": 4},
        "end": {"byteOffset": 3, "line": 1, "column": 4},
    }

    # Given: an unknown target after a supplementary scalar in a quoted view name.
    source = (
        'net unicode\n\n(known)\nview "😀" position ("two words") at {"x": 0, "y": 0}\n'
    )

    # When: the resolver reports the unknown target.
    with pytest.raises(PetrinetDslError) as unicode_error:
        compile_petrinet_text(source, "unicode.petrinet")

    # Then: bytes and Unicode-scalar columns are counted independently.
    target = '("two words")'
    target_start = source.index(target)
    assert unicode_error.value.diagnostic.span.start.byte_offset == len(
        source[:target_start].encode("utf-8")
    )
    assert unicode_error.value.diagnostic.span.start.column == 19
    assert unicode_error.value.diagnostic.span.end.byte_offset == len(
        source[: target_start + len(target)].encode("utf-8")
    )
    assert unicode_error.value.diagnostic.span.end.column == 32


def test_missing_handler_stays_absent_after_portable_ir_json_round_trip() -> None:
    # Given valid source whose declared transition has no handler fact.
    source = _TEXT.replace('\n[accept_coin] handler "accept_coin"\n', "\n")
    portable_ir = json.loads(json.dumps(lower_petrinet_text(source, _NAME)))

    # When serialized portable IR is resolved.
    document = resolve_contribution_ir(portable_ir)

    # Then resolution preserves the absence rather than inventing behavior.
    assert document["transitions"] == [{"name": "accept_coin"}]

    assert parse_petrinet_text(source, _NAME).transitions[0].handler is None


def test_absent_and_explicit_same_name_handlers_remain_distinct() -> None:
    # Given otherwise identical sources with absent and explicit handler facts.
    handlerless_source = _TEXT.replace('\n[accept_coin] handler "accept_coin"\n', "\n")

    # When both sources are compiled and emitted canonically.
    handlerless = compile_petrinet_text(handlerless_source, _NAME)
    explicit = compile_petrinet_text(_TEXT, _NAME)
    handlerless_canonical = emit_petrinet(handlerless)
    explicit_canonical = emit_petrinet(explicit)

    # Then absence is preserved while the explicit same-name ref remains authored.
    assert handlerless["transitions"] == [{"name": "accept_coin"}]
    assert explicit["transitions"] == [
        {"name": "accept_coin", "handler": "accept_coin"}
    ]
    assert '[accept_coin] handler "accept_coin"' not in handlerless_canonical
    assert '[accept_coin] handler "accept_coin"' in explicit_canonical
    assert compile_petrinet_text(handlerless_canonical, _NAME) == handlerless
    assert compile_petrinet_text(explicit_canonical, _NAME) == explicit


def test_given_syntax_error_when_lowered_then_it_remains_a_pn101_parse_error() -> None:
    """Malformed handler syntax remains a parser error."""
    malformed = _TEXT.replace('handler "accept_coin"', "handler accept_coin")
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(malformed, _NAME)
    assert raised.value.diagnostic.code == "PN101"


def test_quoted_transition_without_handler_remains_handlerless() -> None:
    # Given a quoted transition name with an escape and no handler fact.
    source = """\
net "coin deposit"

("coin slot") -"coin\\\"type"-> ["accept\\\"coin"] -"coin\\\"type"-> ("cash box")

marking initial ("coin slot") <- $inserted_coin
$inserted_coin: "coin\\\"type" {}
"""
    portable_ir = json.loads(json.dumps(lower_petrinet_text(source, "quoted.petrinet")))

    # When portable IR is resolved and emitted canonically.
    document = resolve_contribution_ir(portable_ir)
    canonical = emit_petrinet(document)

    # Then the decoded name remains structural and no behavior fact is invented.
    assert document["transitions"] == [{"name": 'accept"coin'}]
    assert '["accept\\"coin"] handler ' not in canonical
    assert compile_petrinet_text(canonical, "canonical-quoted.petrinet") == document


def test_given_resolved_json_when_emitted_then_canonical_source_is_the_exact_golden_fixed_point() -> (
    None
):
    """Emission is exact Coin Deposit canonical text and its own accepted source form."""
    first = emit_petrinet(_EXPECTED_NET)
    assert first == _TEXT
    assert emit_petrinet(compile_petrinet_text(first, _NAME)) == first


def test_given_cli_commands_when_successful_then_only_stdout_is_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The console API validates, serializes, and formats without diagnostic noise."""
    petrinet = tmp_path / "coin-deposit.petrinet"
    petrinet.write_text(_TEXT, encoding="utf-8")
    net_json = tmp_path / "coin-deposit.json"
    net_json.write_text(json.dumps(_EXPECTED_NET), encoding="utf-8")

    assert main(["validate", str(petrinet)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "net\n"
    assert captured.err == ""

    assert main(["to-json", str(petrinet)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == _EXPECTED_NET
    assert captured.err == ""

    assert main(["to-petrinet", str(net_json)]) == 0
    captured = capsys.readouterr()
    assert captured.out == emit_petrinet(_EXPECTED_NET)
    assert captured.err == ""


def test_given_crlf_source_when_compiled_then_it_preserves_coin_deposit_semantics() -> (
    None
):
    """CRLF is valid source input; only bare CR is prohibited."""
    assert compile_petrinet_text(_TEXT.replace("\n", "\r\n"), _NAME) == _EXPECTED_NET


def test_given_crlf_file_when_loaded_then_readers_preserve_bytes_and_source_offsets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Filesystem DSL reads do not apply universal-newline translation."""
    source = _TEXT.replace("\n", "\r\n")
    petrinet = tmp_path / "coin-deposit.petrinet"
    petrinet.write_bytes(source.encode("utf-8"))
    assert read_petrinet_text(petrinet) == source
    assert load_petrinet(petrinet).name == "coin_deposit"
    ir = lower_petrinet_text(read_petrinet_text(petrinet), str(petrinet))
    handler = ir["contributions"][3]
    assert handler["span"]["start"]["byteOffset"] == len(
        source[: source.index("[accept_coin] handler")].encode("utf-8")
    )
    assert main(["to-json", str(petrinet)]) == 0
    assert json.loads(capsys.readouterr().out) == _EXPECTED_NET


def test_given_non_ascii_or_reserved_names_when_emitted_then_they_are_json_quoted() -> (
    None
):
    """Only ASCII non-keyword identifiers may use bare DSL name syntax."""
    document = deepcopy(_EXPECTED_NET)
    document["name"] = "net"
    document["places"][0]["name"] = "café"
    document["places"][1]["name"] = "initial"
    document["transitions"][0]["name"] = "false"
    document["arcs"][0]["from"]["place"] = "café"
    document["arcs"][0]["to"]["transition"] = "false"
    document["arcs"][1]["from"]["transition"] = "false"
    document["arcs"][1]["to"]["place"] = "initial"
    document["arcs"][1]["produce"]["destination"] = "initial"
    document["initialMarking"] = {"café": document["initialMarking"]["coin_slot"]}
    rendered = emit_petrinet(document)
    assert 'net "net"' in rendered
    assert '("café")' in rendered
    assert '("initial")' in rendered
    assert '["false"]' in rendered


def test_given_missing_handler_cli_when_validated_then_absence_is_preserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a DSL file whose transition has no behavior handler.
    petrinet = tmp_path / "missing-handler.petrinet"
    petrinet.write_text(
        _TEXT.replace('\n[accept_coin] handler "accept_coin"\n', "\n"), encoding="utf-8"
    )

    # When the CLI validates and canonically formats that file.
    assert main(["validate", str(petrinet)]) == 0
    validation = capsys.readouterr()
    assert main(["to-petrinet", str(petrinet)]) == 0
    formatted = capsys.readouterr()

    # Then validation succeeds and canonical output remains handlerless.
    assert (validation.out, validation.err) == ("net\n", "")
    assert "[accept_coin] handler " not in formatted.out
    assert formatted.err == ""


def test_given_to_json_when_model_validation_fails_then_it_writes_no_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serialization validates the JSON model before exposing any output."""
    petrinet = tmp_path / "coin-deposit.petrinet"
    petrinet.write_text(_TEXT, encoding="utf-8")

    def reject(_: object) -> None:
        raise NetValidationError("sentinel validation failure")

    monkeypatch.setattr(cli, "parse_net", reject)
    assert main(["to-json", str(petrinet)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sentinel validation failure" in captured.err


def test_given_compact_option_when_converting_then_only_safe_colors_are_elided(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The API and CLI share compact output, which recompiles to the same net."""
    compact = emit_petrinet(_EXPECTED_NET, compact=True)
    assert "(coin_slot) -coin-> [accept_coin] -coin-> (cash_box)" in compact
    assert emit_petrinet(_EXPECTED_NET) == _TEXT
    assert compile_petrinet_text(compact, "compact.petrinet") == _EXPECTED_NET

    net_json = tmp_path / "coin-deposit.json"
    net_json.write_text(json.dumps(_EXPECTED_NET), encoding="utf-8")
    assert main(["to-petrinet", str(net_json), "--compact"]) == 0
    captured = capsys.readouterr()
    assert captured.out == compact
    assert captured.err == ""


def test_compact_emission_keeps_the_arc_that_makes_a_port_color_accepted() -> None:
    document: dict[str, object] = {
        "name": "port_default",
        "places": [
            {
                "name": "out",
                "accepts": ["pulse"],
                "port": {"direction": "output", "type": "pulse"},
            }
        ],
        "transitions": [{"name": "emit", "handler": "emit"}],
        "arcs": [
            {
                "from": {"transition": "emit"},
                "to": {"place": "out"},
                "produce": {"destination": "out", "type": "pulse"},
            }
        ],
        "initialMarking": {},
    }

    compact = emit_petrinet(document, compact=True)

    assert "[emit] -pulse-> (out)" in compact
    assert compile_petrinet_text(compact, "port-compact.petrinet") == document


def test_given_arrow_text_in_json_when_compact_reparsed_then_it_stays_literal() -> None:
    """Arrow-like JSON text is parsed as JSON and never as compact topology."""
    document = deepcopy(_EXPECTED_NET)
    document["annotations"] = {"example": "(coin_slot) -> [accept_coin]"}
    compact = emit_petrinet(document, compact=True)
    assert compile_petrinet_text(compact, "compact-string.petrinet") == document


def test_given_compact_flag_on_another_command_then_argument_misuse_exits_two() -> None:
    """Only ``to-petrinet`` accepts the compact formatter option."""
    with pytest.raises(SystemExit) as raised:
        main(["to-json", "coin-deposit.petrinet", "--compact"])
    assert raised.value.code == 2


def test_given_unsafe_arcs_when_compact_then_exceptional_semantics_stay_explicit() -> (
    None
):
    """Compact output never hides ambiguous colors or elaborated inscriptions."""
    cases: list[tuple[dict[str, object], str]] = [
        ({"ambiguous": True}, "(coin_slot) -coin-> [accept_coin]"),
        ({"weight": 2}, "(coin_slot) -coin-> [accept_coin]"),
        (
            {"predicate": {"cel": "data.valid"}},
            "(coin_slot) -coin-> [accept_coin]",
        ),
        ({"mode": "read"}, "(coin_slot) -coin->? [accept_coin]"),
        (
            {"mode": "inhibit", "correlate": {"cel": "data.id"}},
            "(coin_slot) -coin->0 [accept_coin]",
        ),
        ({"produce_data": {"receipt": True}}, "[accept_coin] -coin-> (cash_box)"),
    ]
    for change, expected in cases:
        document = deepcopy(_EXPECTED_NET)
        ambiguous = bool(change.pop("ambiguous", False))
        if ambiguous:
            document["places"][0]["accepts"].append("voucher")
        produce_data = change.pop("produce_data", None)
        if produce_data is not None:
            document["arcs"][1]["produce"]["data"] = produce_data
        else:
            document["arcs"][0]["consume"].update(change)
        compact = emit_petrinet(document, compact=True)
        assert expected in compact
        if not ambiguous:
            assert (
                compile_petrinet_text(compact, "compact-boundary.petrinet") == document
            )


def test_given_composition_when_compact_then_constituents_remain_references() -> None:
    """Formatting a composition never embeds or rewrites constituent documents."""
    composition = {
        "nets": [
            {"alias": "source", "ref": "source.petrinet"},
            {"alias": "sink", "ref": "sink.petrinet"},
        ],
        "wires": [
            {
                "from": {"net": "source", "port": "out"},
                "to": {"net": "sink", "port": "in"},
            }
        ],
    }
    assert emit_petrinet(composition, compact=True) == emit_petrinet(composition)


@pytest.mark.parametrize(
    ("json_value", "message"),
    [
        ('{"x": 1, "x": 2}', "duplicate JSON object key"),
        ("1e400", "must be finite"),
        ("9007199254740992", "safe IEEE-754 range"),
        ('"\\ud800"', "isolated surrogate"),
    ],
)
def test_given_invalid_json_template_when_lowered_then_pn101_has_the_json_span(
    json_value: str, message: str
) -> None:
    """Template JSON is source-lowered strictly, rather than delegated to json.loads."""
    source = _TEXT.replace(
        "$inserted_coin: coin {}", f"$inserted_coin: coin {json_value}"
    )
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, _NAME)
    assert raised.value.diagnostic.code == "PN101"
    assert message in raised.value.diagnostic.message


def test_given_exponent_number_when_lowered_then_ir_preserves_its_lexeme() -> None:
    """ANTLR-tree lowering retains RFC 8259 numeric spelling for portable IR."""
    source = _TEXT.replace("$inserted_coin: coin {}", "$inserted_coin: coin 1e2")
    ir = lower_petrinet_text(source, _NAME)
    assert ir["contributions"][-1]["value"]["value"] == {
        "type": "number",
        "lexeme": "1e2",
    }


@pytest.mark.parametrize("lexeme", ["+1", "01", "1.", " 1", "1 "])
def test_given_invalid_portable_number_lexeme_when_resolved_then_pn200(
    lexeme: str,
) -> None:
    """The portable decoder enforces RFC 8259 number syntax before conversion."""
    ir = deepcopy(_EXPECTED_IR)
    ir["contributions"][-1]["value"]["value"] = {"type": "number", "lexeme": lexeme}
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(ir)
    assert raised.value.diagnostic.code == "PN200"


def test_timer_maturity_round_trips_through_textual_dsl() -> None:
    """Runtime scheduler metadata survives lowering, resolving, and emission."""
    source = """\
net maturity_test
(clock) -clock->? [wait]
(latch) -latch->? [wait]
[wait] handler "wait"
[wait] timer clock (clock) cel "clock.now >= latch.at"
[wait] timer maturity cel "latch.at"
[wait] timer bind latch (latch)
"""
    document = compile_petrinet_text(source, "maturity.petrinet")

    assert document["transitions"][0]["timer"] == {
        "clock": "clock",
        "cel": "clock.now >= latch.at",
        "maturity": "latch.at",
        "bind": {"latch": "latch"},
    }
    assert (
        compile_petrinet_text(emit_petrinet(document), "emitted.petrinet") == document
    )
