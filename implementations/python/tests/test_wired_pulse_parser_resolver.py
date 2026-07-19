"""Red parser, resolver, loading, and canonical contracts for Slice 10."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from velocitron.composition import merge_composition
from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import NetValidationError, parse_composition, parse_net
from velocitron.schema import Net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "10-wired-pulse"
_CORPUS_ROOT = _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "10-wired-pulse"
_SOURCE_DSL = _FIXTURE_ROOT / "pulse_source.petrinet"
_SOURCE_JSON = _FIXTURE_ROOT / "pulse_source.json"
_SINK_DSL = _FIXTURE_ROOT / "pulse_sink.petrinet"
_SINK_JSON = _FIXTURE_ROOT / "pulse_sink.json"
_COMPOSITION_DSL = _FIXTURE_ROOT / "wired_pulse.petrinet"
_COMPOSITION_JSON = _FIXTURE_ROOT / "wired_pulse.json"
_IR_SCHEMA = _REPOSITORY_ROOT / "spec" / "petrinet-contribution-ir.schema.json"

_COMPOSITION_SOURCE = """\
composition wired_pulse
use "pulse_source.petrinet" as source
use "pulse_sink.petrinet" as sink
wire source.(pulse_out) -> sink.(pulse_in)
"""


def _document(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _validate_ir_schema(instance: object) -> None:
    validator = cast(Any, Draft202012Validator(_document(_IR_SCHEMA)))
    validator.validate(instance)


def _ir_schema_accepts(instance: object) -> bool:
    validator = cast(Any, Draft202012Validator(_document(_IR_SCHEMA)))
    return cast(bool, validator.is_valid(instance))


def _net(name: str, port_name: str, direction: str, color: str) -> dict[str, Any]:
    return {
        "name": name,
        "places": [
            {
                "name": port_name,
                "accepts": [color],
                "port": {"direction": direction, "type": color},
            }
        ],
        "transitions": [],
        "arcs": [],
    }


def _write_pair(
    root: Path,
    *,
    source_direction: str = "output",
    sink_direction: str = "input",
    source_color: str = "pulse",
    sink_color: str = "pulse",
) -> None:
    (root / "pulse_source.json").write_text(
        json.dumps(_net("pulse_source", "pulse_out", source_direction, source_color)),
        encoding="utf-8",
    )
    (root / "pulse_sink.json").write_text(
        json.dumps(_net("pulse_sink", "pulse_in", sink_direction, sink_color)),
        encoding="utf-8",
    )


def test_port_and_composition_syntax_lower_to_exact_typed_facts() -> None:
    """Given native port/use/wire syntax, lowering retains typed progressive facts."""
    # given: one port declaration and one complete composition
    port_source = """\
net port_source
(pulse_out) -pulse-> [emit]
[emit] handler "emit"
(pulse_out) port output pulse
"""

    # when: both documents are lowered without semantic resolution
    port_ir = lower_petrinet_text(port_source, "port-source.petrinet")
    composition_ir = lower_petrinet_text(_COMPOSITION_SOURCE, "wired_pulse.petrinet")
    _validate_ir_schema(composition_ir)

    # then: the port fact elaborates an existing place with one accepted color
    assert [
        (fact["kind"], fact["target"], fact["value"])
        for fact in port_ir["contributions"]
        if fact["kind"] == "place.port"
    ] == [
        (
            "place.port",
            {"type": "place", "name": "pulse_out"},
            {"direction": "output", "color": "pulse"},
        )
    ]
    # and: composition identity stays IR-local while use refs remain literal
    assert composition_ir["documentKind"] == "composition"
    assert [
        (fact["kind"], fact["target"], fact["value"])
        for fact in composition_ir["contributions"]
    ] == [
        (
            "document.composition-header",
            {"type": "document"},
            {"namespace": "wired_pulse"},
        ),
        (
            "composition.use",
            {"type": "document"},
            {"ref": "pulse_source.petrinet", "alias": "source"},
        ),
        (
            "composition.use",
            {"type": "document"},
            {"ref": "pulse_sink.petrinet", "alias": "sink"},
        ),
        (
            "composition.wire",
            {"type": "document"},
            {
                "from": {
                    "alias": "source",
                    "place": "pulse_out",
                    "span": {
                        "source": "wired_pulse.petrinet",
                        "start": {"byteOffset": 101, "line": 4, "column": 6},
                        "end": {"byteOffset": 119, "line": 4, "column": 24},
                    },
                },
                "to": {
                    "alias": "sink",
                    "place": "pulse_in",
                    "span": {
                        "source": "wired_pulse.petrinet",
                        "start": {"byteOffset": 123, "line": 4, "column": 28},
                        "end": {"byteOffset": 138, "line": 4, "column": 43},
                    },
                },
            },
        ),
    ]


def test_equal_port_use_and_wire_facts_are_idempotent() -> None:
    """Given repeated equal facts, resolution emits one semantic facet or entry."""
    # given: equal port facts and equal composition use/wire facts
    net_source = """\
net equal_port
(pulse_out) -pulse-> [emit]
[emit] handler "emit"
(pulse_out) port output pulse
(pulse_out) port output pulse
"""
    composition_source = _COMPOSITION_SOURCE.replace(
        'use "pulse_source.petrinet" as source\n',
        'use "pulse_source.petrinet" as source\nuse "pulse_source.petrinet" as source\n',
    ).replace(
        "wire source.(pulse_out) -> sink.(pulse_in)\n",
        "wire source.(pulse_out) -> sink.(pulse_in)\nwire source.(pulse_out) -> sink.(pulse_in)\n",
    )

    # when: both progressive documents resolve
    net = compile_petrinet_text(net_source, "equal-port.petrinet")
    composition = compile_petrinet_text(composition_source, "equal-wired.petrinet")

    # then: equal facts collapse without changing canonical core JSON
    assert net["places"][0]["port"] == {"direction": "output", "type": "pulse"}
    assert composition == {
        "nets": [
            {"ref": "pulse_source.petrinet", "alias": "source"},
            {"ref": "pulse_sink.petrinet", "alias": "sink"},
        ],
        "wires": [
            {
                "from": {"net": "source", "port": "pulse_out"},
                "to": {"net": "sink", "port": "pulse_in"},
            }
        ],
    }


@pytest.mark.parametrize(
    ("source", "later_line"),
    [
        (
            """\
net conflicting_port
(pulse_out) -pulse-> [emit]
[emit] handler "emit"
(pulse_out) port output pulse
(pulse_out) port input pulse
""",
            5,
        ),
        (
            """\
composition conflicting_use
use "a.json" as source
use "b.json" as source
""",
            3,
        ),
        (
            """\
composition conflicting_wire
use "a.json" as source
use "b.json" as sink
wire source.(pulse_out) -> sink.(pulse_in)
wire sink.(pulse_in) -> source.(pulse_out)
""",
            5,
        ),
    ],
    ids=["port", "use-alias", "opposite-wire"],
)
def test_unequal_facts_cite_later_and_first_spans(source: str, later_line: int) -> None:
    """Given contradictory facts, the diagnostic relates later and first authorship."""
    # when: resolution reaches the unequal repeated fact
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "conflicting-wire-fact.petrinet")

    # then: the primary span is later and the related span is the first definition
    diagnostic = raised.value.diagnostic
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (later_line, 1)
    assert len(diagnostic.related) == 1
    assert diagnostic.related[0].span.start.column == 1
    assert diagnostic.related[0].span.start.line < later_line


def test_use_alias_is_mandatory_at_eof() -> None:
    """Given a use without `as IDENT`, parsing reports zero-width EOF."""
    # given: a composition header followed by the prohibited alias-less inclusion
    source = """\
composition missing_alias
use "pulse_source.petrinet"
"""

    # when: the DSL parser reads the incomplete use fact
    with pytest.raises(PetrinetDslError) as raised:
        lower_petrinet_text(source, "missing-use-alias.petrinet")

    # then: the diagnostic is anchored at zero-width EOF
    assert raised.value.diagnostic.code == "PN101"
    assert raised.value.diagnostic.span.start.line == 3
    assert raised.value.diagnostic.span.start == raised.value.diagnostic.span.end


def test_composition_requires_a_use_fact_before_loader_selection() -> None:
    """Given only a header, resolution rejects the empty composition at its source."""
    # when: compilation has neither a loader nor an origin to trigger backend validation
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text("composition empty\n", "empty-composition.petrinet")

    # then: the authored header receives a deterministic semantic diagnostic
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert "at least one use" in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (1, 1)


def test_port_color_must_already_be_accepted_by_its_place() -> None:
    """Given a mismatched port color, a port cannot expand a place's color set."""
    # given: topology declares only pulse while the port fact claims receipt
    source = """\
net invalid_port_color
(pulse_out) -pulse-> [emit]
[emit] handler "emit"
(pulse_out) port output receipt
"""

    # when: the net is resolved and delegated to semantic validation
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "invalid-port-color.petrinet")

    # then: the authored port fact is blamed and both colors remain visible
    diagnostic = raised.value.diagnostic
    assert "receipt" in diagnostic.message and "accept" in diagnostic.message.lower()
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (4, 1)


def test_composition_contribution_ir_is_strictly_closed() -> None:
    """Given imported composition IR, host-only or untyped members are rejected."""
    # given: valid portable composition IR with one illicit resolver shortcut
    ir = lower_petrinet_text(_COMPOSITION_SOURCE, "wired_pulse.petrinet")
    malformed = deepcopy(ir)
    malformed["contributions"][1]["value"]["parsedNet"] = Net(
        name="bypass", places=[], transitions=[], arcs=[]
    )

    # when: the imported IR is decoded
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(malformed)

    # then: strict IR shape validation rejects the bypass before resolution
    assert raised.value.diagnostic.code == "PN200"


@pytest.mark.parametrize("mutation", ["foreign-source", "invalid-position"])
def test_imported_wire_endpoint_spans_are_strictly_decoded(mutation: str) -> None:
    """Imported endpoint provenance must remain a closed, well-formed source span."""
    ir = lower_petrinet_text(_COMPOSITION_SOURCE, "wired_pulse.petrinet")
    malformed = deepcopy(ir)
    endpoint_span = malformed["contributions"][3]["value"]["from"]["span"]
    if mutation == "foreign-source":
        endpoint_span["source"] = "other.petrinet"
    else:
        endpoint_span["start"]["column"] = 0

    if mutation == "foreign-source":
        assert _ir_schema_accepts(malformed)
    else:
        assert not _ir_schema_accepts(malformed)
    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(malformed)
    assert raised.value.diagnostic.code == "PN200"


@pytest.mark.parametrize(
    (
        "source_port",
        "source_direction",
        "source_color",
        "target_direction",
        "target_color",
        "endpoint_lexeme",
    ),
    [
        ('dé"part', "input", "pulse", "input", "pulse", 'source.("dé\\"part")'),
        ('dé"part', "output", "pulse", "output", "pulse", 'sink.("雪")'),
        ("other", "output", "pulse", "input", "pulse", 'source.("dé\\"part")'),
        ('dé"part', "output", "pulse", "input", "receipt", 'sink.("雪")'),
    ],
    ids=["source-direction", "target-direction", "unknown-port", "type"],
)
def test_wire_endpoint_diagnostics_use_exact_authored_unicode_spans(
    source_port: str,
    source_direction: str,
    source_color: str,
    target_direction: str,
    target_color: str,
    endpoint_lexeme: str,
) -> None:
    """Direction, lookup, and type errors cite parse-derived endpoint provenance."""
    source = """\
composition unicode_wires
use "source.json" as source
use "sink.json" as sink
wire source.("dé\\"part") -> sink.("雪")
"""
    nets = {
        "source.json": _net("source", source_port, source_direction, source_color),
        "sink.json": _net("sink", "雪", target_direction, target_color),
    }

    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(
            source, "unicode-wires.petrinet", net_loader=nets.__getitem__
        )

    start = source.index(endpoint_lexeme)
    end = start + len(endpoint_lexeme)
    line_start = source.rfind("\n", 0, start) + 1
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.span.as_dict() == {
        "source": "unicode-wires.petrinet",
        "start": {
            "byteOffset": len(source[:start].encode("utf-8")),
            "line": 4,
            "column": start - line_start + 1,
        },
        "end": {
            "byteOffset": len(source[:end].encode("utf-8")),
            "line": 4,
            "column": end - line_start + 1,
        },
    }


def test_relative_refs_load_from_explicit_origin(tmp_path: Path) -> None:
    """Given raw composition data, an explicit origin resolves its literal refs."""
    # given: valid constituent JSON files and literal relative references
    _write_pair(tmp_path)
    source = _COMPOSITION_SOURCE.replace(".petrinet", ".json")
    document = compile_petrinet_text(source, "wired_pulse.petrinet")

    # when: the sole composition parser validates and retains loaded nets
    composition = cast(Any, parse_composition)(document, origin=tmp_path)
    merged = merge_composition(composition)

    # then: loading is relative, fusion removes the sink endpoint, and JSON stays metadata-free
    assert set(document) == {"nets", "wires"}
    assert set(composition.parsed_nets or {}) == {"source", "sink"}
    assert {place.name for place in merged.places} == {"source.pulse_out"}


def test_relative_ref_without_origin_is_rejected() -> None:
    """Given in-memory relative refs, default loading never guesses the process cwd."""
    # given: a valid composition-shaped document with relative refs
    document = compile_petrinet_text(_COMPOSITION_SOURCE, "wired_pulse.petrinet")

    # when/then: the parser requires origin or an explicit loader
    with pytest.raises((PetrinetDslError, NetValidationError)) as raised:
        parse_composition(document)
    assert (
        "origin" in str(raised.value).lower() or "relative" in str(raised.value).lower()
    )


@pytest.mark.parametrize(
    ("source_direction", "sink_direction", "source_color", "sink_color", "message"),
    [
        ("input", "input", "pulse", "pulse", "output"),
        ("output", "output", "pulse", "pulse", "input"),
        ("output", "input", "pulse", "receipt", "type"),
    ],
    ids=["source-direction", "target-direction", "type"],
)
def test_dsl_wire_rejects_invalid_loaded_endpoints(
    tmp_path: Path,
    source_direction: str,
    sink_direction: str,
    source_color: str,
    sink_color: str,
    message: str,
) -> None:
    """Given authored wire syntax, loaded endpoint direction and type stay strict."""
    # given: a DSL composition whose referenced JSON nets violate one wire rule
    _write_pair(
        tmp_path,
        source_direction=source_direction,
        sink_direction=sink_direction,
        source_color=source_color,
        sink_color=sink_color,
    )
    document = compile_petrinet_text(
        _COMPOSITION_SOURCE.replace(".petrinet", ".json"), "invalid-wire.petrinet"
    )

    # when/then: semantic composition parsing rejects that exact endpoint rule
    with pytest.raises((PetrinetDslError, NetValidationError)) as raised:
        cast(Any, parse_composition)(document, origin=tmp_path)
    assert message in str(raised.value).lower()


@pytest.mark.parametrize(
    ("wire", "bad_net", "message", "column"),
    [
        (
            "source.(pulse_out) -> bad.(missing)",
            _net("bad", "port", "input", "pulse"),
            "unknown port",
            28,
        ),
        (
            "bad.(port) -> sink.(pulse_in)",
            _net("bad", "port", "input", "pulse"),
            "a wire must run from an output port to an input port",
            6,
        ),
        (
            "source.(pulse_out) -> bad.(port)",
            _net("bad", "port", "output", "pulse"),
            "must be an input port",
            28,
        ),
        (
            "source.(pulse_out) -> bad.(port)",
            _net("bad", "port", "input", "receipt"),
            "colors differ",
            28,
        ),
    ],
    ids=["missing-port", "source-direction", "target-direction", "type"],
)
def test_later_invalid_wire_receives_its_own_span(
    wire: str, bad_net: dict[str, Any], message: str, column: int
) -> None:
    """Given multiple wires, backend validation maps failure to the matching fact."""
    source = f"""\
composition multi_wire
use "source.json" as source
use "sink.json" as sink
use "bad.json" as bad
wire source.(pulse_out) -> sink.(pulse_in)
wire {wire}
"""
    nets = {
        "source.json": _net("source", "pulse_out", "output", "pulse"),
        "sink.json": _net("sink", "pulse_in", "input", "pulse"),
        "bad.json": bad_net,
    }

    # when: the second wire fails validation after the first one succeeds
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(
            source,
            "multi-wire.petrinet",
            net_loader=nets.__getitem__,
        )

    # then: PN202 cites the second wire, not the first wire's retained span
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert message in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (6, column)


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        ("wire ghost.(pulse_out) -> sink.(pulse_in)", "alias"),
        ("wire source.(missing) -> sink.(pulse_in)", "port"),
    ],
    ids=["unknown-alias", "unknown-port"],
)
def test_dsl_wire_rejects_dangling_endpoints(
    tmp_path: Path, wire: str, message: str
) -> None:
    """Given DSL wire endpoints, every alias and port must resolve in loaded nets."""
    # given: valid JSON constituents and one authored dangling endpoint
    _write_pair(tmp_path)
    source = _COMPOSITION_SOURCE.replace(".petrinet", ".json")
    source = source.replace("wire source.(pulse_out) -> sink.(pulse_in)", wire)

    # when/then: DSL resolution or semantic composition parsing rejects the endpoint
    with pytest.raises((PetrinetDslError, NetValidationError)) as raised:
        document = compile_petrinet_text(source, "dangling-wire.petrinet")
        cast(Any, parse_composition)(document, origin=tmp_path)
    assert message in str(raised.value).lower()


def test_composition_loader_cannot_return_a_prevalidated_net() -> None:
    """Given a custom loader, returned model objects cannot bypass parse_net."""
    # given: a resolved composition document and a loader returning a Net object
    document = {
        "nets": [{"ref": "source.json", "alias": "source"}],
        "wires": [],
    }
    bypass = parse_net(_net("source", "pulse_out", "output", "pulse"))

    def invalid_loader(ref: str) -> Net:
        del ref
        return bypass

    # when/then: the parser rejects a prevalidated host object at the loader boundary
    with pytest.raises((PetrinetDslError, NetValidationError)) as raised:
        cast(Any, parse_composition)(document, net_loader=invalid_loader)
    assert "loader" in str(raised.value).lower() or "net" in str(raised.value).lower()


@pytest.mark.parametrize(
    "alias",
    ["net", "use", "wire", "timer", "clock", "bind", "priority"],
)
def test_emitter_rejects_reserved_aliases_that_are_not_ident_tokens(
    alias: str,
) -> None:
    """Canonical emission cannot represent a reserved word as grammar IDENT."""
    document = {
        "nets": [{"ref": "constituent.json", "alias": alias}],
        "wires": [],
    }

    with pytest.raises(ValueError, match="not a resolved composition document"):
        emit_petrinet(document)


def test_fixture_json_and_canonical_dsl_are_exact_fixed_points() -> None:
    """Given net fixtures and composition JSON, canonical forms converge exactly."""
    for dsl_path, json_path in [
        (_SOURCE_DSL, _SOURCE_JSON),
        (_SINK_DSL, _SINK_JSON),
    ]:
        expected = _document(json_path)
        actual = compile_petrinet_text(
            dsl_path.read_text(encoding="utf-8"), str(dsl_path)
        )
        emitted = emit_petrinet(expected)
        assert actual == expected
        assert compile_petrinet_text(emitted, str(dsl_path)) == expected

    # Composition headers are DSL-local and refs remain literal: the authored
    # .petrinet document and legacy .json document therefore have independent
    # canonical fixed points.
    authored = _document(_COMPOSITION_JSON)
    for entry in authored["nets"]:
        entry["ref"] = entry["ref"].replace(".json", ".petrinet")
    assert (
        compile_petrinet_text(
            _COMPOSITION_DSL.read_text(encoding="utf-8"), str(_COMPOSITION_DSL)
        )
        == authored
    )
    expected = _document(_COMPOSITION_JSON)
    emitted = emit_petrinet(expected)
    assert emitted.startswith("composition composition\n")
    assert compile_petrinet_text(emitted, str(_COMPOSITION_DSL)) == expected


def test_net_only_parse_api_rejects_composition() -> None:
    """Given composition JSON, the Net-returning parse API does not blur models."""
    # given: a resolved composition mapping
    document = compile_petrinet_text(_COMPOSITION_SOURCE, "wired_pulse.petrinet")

    # when/then: only parse_composition accepts it; parse_net stays Net-only
    with pytest.raises((NetValidationError, ValueError, TypeError)):
        parse_net(document)


@pytest.mark.parametrize(
    ("case_name", "semantic_suffix"),
    [
        ("pulse_source", "net"),
        ("pulse_sink", "net"),
        ("wired_pulse", "composition"),
        ("pulse_source.duplicate-port", "net"),
        ("wired_pulse.duplicate-use", "composition"),
        ("wired_pulse.duplicate-wire", "composition"),
    ],
)
def test_valid_corpus_ir_semantics_and_canonical_source(
    case_name: str, semantic_suffix: str
) -> None:
    source = _document(_CORPUS_ROOT / f"{case_name}.source.json")
    expected_ir = _document(_CORPUS_ROOT / f"{case_name}.contribution-ir.json")
    expected = _document(_CORPUS_ROOT / f"{case_name}.{semantic_suffix}.json")

    ir = lower_petrinet_text(source["text"], source["sourceId"])
    resolved = resolve_contribution_ir(ir)
    emitted = emit_petrinet(expected)

    assert ir == expected_ir
    assert resolved == expected
    assert compile_petrinet_text(emitted, source["sourceId"]) == expected
    if semantic_suffix == "net":
        assert " port " in emitted
    else:
        assert emitted.startswith("composition composition\n")


@pytest.mark.parametrize(
    "case_name",
    [
        "pulse_source.conflicting-port",
        "pulse_source.port-type-not-accepted",
        "wired_pulse.conflicting-use",
        "wired_pulse.conflicting-wire",
        "wired_pulse.missing-use-alias",
        "wired_pulse.reversed-wire-direction",
        "wired_pulse.unknown-wire-alias",
        "wired_pulse.unknown-wire-port",
        "wired_pulse.wire-type-mismatch",
    ],
)
def test_invalid_corpus_diagnostics_are_exact(case_name: str) -> None:
    expectation = _document(_CORPUS_ROOT / f"{case_name}.json")
    source = expectation["source"]

    def load_net(ref: str) -> dict[str, Any]:
        base_ref = "pulse_sink.petrinet" if "sink" in ref else ref
        wrapper = _document(
            _CORPUS_ROOT / base_ref.replace(".petrinet", ".source.json")
        )
        net = compile_petrinet_text(wrapper["text"], wrapper["sourceId"])
        if ref == "pulse_sink_alert.petrinet":
            net["places"][0]["accepts"] = ["alert"]
            net["places"][0]["port"]["type"] = "alert"
            net["arcs"][0]["consume"]["type"] = "alert"
        return net

    with pytest.raises(PetrinetDslError) as raised:
        ir = lower_petrinet_text(source["text"], source["sourceId"])
        resolve_contribution_ir(
            ir,
            net_loader=load_net if "wire-" in case_name else None,
        )

    diagnostic = raised.value.diagnostic
    actual: dict[str, Any] = {
        "code": diagnostic.code,
        "file": diagnostic.span.source,
        "line": diagnostic.span.start.line,
        "column": diagnostic.span.start.column,
        "message": diagnostic.message,
        "span": diagnostic.span.as_dict(),
    }
    if diagnostic.help is not None:
        actual["help"] = diagnostic.help
    if diagnostic.related:
        actual["related"] = [
            {"message": item.message, "span": item.span.as_dict()}
            for item in diagnostic.related
        ]
    assert actual == expectation["diagnostic"]
