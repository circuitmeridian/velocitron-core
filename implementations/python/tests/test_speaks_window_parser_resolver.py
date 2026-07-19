"""Red parser, resolver, strict IR, and canonical contracts for Slice 11."""

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
_FIXTURE_ROOT = _REPOSITORY_ROOT / "examples" / "capability-ladder" / "11-speaks-window"
_DSL_PATH = _FIXTURE_ROOT / "speaks_window.petrinet"
_JSON_PATH = _FIXTURE_ROOT / "speaks_window.json"
_CORPUS_ROOT = (
    _REPOSITORY_ROOT / "spec" / "conformance" / "petrinet" / "11-speaks-window"
)
_RESERVED = "petrinet.dsl/v1"

_SOURCE = """\
net speaks_window "Minimal clean-start Speaks window"

(request_in) -speak_req-> [start] -speak_token-> (in_flight)
@in_flight_clear: (in_flight) -speak_token->0 [start]
@judge_clear: (judge_requested) -judge_req->0 [start]
[start] -judge_req-> (judge_requested)

(judge_requested) -judge_req-> [finish] -utterance-> (utterance_out)
@window_close: (in_flight) -speak_token-> [finish]

[start] handler "chip_start@speaks"
[finish] handler "finish@speaks"

(request_in) port input speak_req
(utterance_out) port output utterance

(in_flight) description "Singleton token delimiting one active request"
(in_flight) annotation fusion true
[start] description "Accept a request only when all internal state is clean"
[finish] annotation role "terminal"
@window_close description "Terminally consumes the in-flight token"
@window_close annotation window "close"

marking queued (request_in) <- $demo_request
$demo_request: speak_req {"text": "Status?"}

view window position (request_in) at {"x": 0, "y": 80}
view window position [start] at {"x": 160, "y": 80}
view window position (in_flight) at {"x": 320, "y": 20}
view window position (judge_requested) at {"x": 320, "y": 140}
view window position [finish] at {"x": 500, "y": 80}
view window position (utterance_out) at {"x": 680, "y": 80}
view window route @window_close orthogonal [{"x": 400, "y": 20}, {"x": 500, "y": 20}]
extensions {}
"""


def _document(path: Path = _JSON_PATH) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _portable() -> dict[str, Any]:
    return lower_petrinet_text(_SOURCE, "speaks_window.petrinet")


def _portable_with_imported_metadata(
    kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append one schema-valid metadata fact without requiring its source syntax."""
    portable = lower_petrinet_text(
        """\
net strict_metadata
(p) -tok-> [t]
[t] handler "h"
""",
        "strict-metadata.petrinet",
    )
    values: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
        "documentation.description": (
            {"type": "place", "name": "p"},
            {"text": "A place"},
        ),
        "documentation.annotation": (
            {"type": "place", "name": "p"},
            {
                "key": "fusion",
                "value": {"type": "boolean", "value": True},
            },
        ),
        "metadata.named-marking": (
            {"type": "document"},
            {"name": "queued", "entries": []},
        ),
        "view.position": (
            {"type": "view", "name": "window"},
            {
                "subject": {"type": "place", "name": "p"},
                "position": {
                    "type": "object",
                    "entries": [
                        {"key": "x", "value": {"type": "number", "lexeme": "0"}},
                        {"key": "y", "value": {"type": "number", "lexeme": "0"}},
                    ],
                },
            },
        ),
        "view.route": (
            {"type": "arcHandle", "name": "window_close"},
            {
                "view": {"type": "view", "name": "window"},
                "points": [
                    {
                        "type": "object",
                        "entries": [
                            {"key": "x", "value": {"type": "number", "lexeme": "0"}},
                            {"key": "y", "value": {"type": "number", "lexeme": "0"}},
                        ],
                    }
                ],
            },
        ),
        "document.extensions": (
            {"type": "document"},
            {"extensions": {"type": "object", "entries": []}},
        ),
    }
    target, value = values[kind]
    prior = portable["contributions"][-1]
    ordinal = len(portable["contributions"])
    fact = {
        "id": {
            "source": portable["document"]["id"],
            "statement": ordinal,
            "part": 0,
        },
        "kind": kind,
        "ordinal": ordinal,
        "span": deepcopy(prior["span"]),
        "target": target,
        "value": value,
    }
    portable["contributions"].append(fact)
    return portable, fact


def test_metadata_syntax_lowers_to_closed_typed_progressive_facts() -> None:
    """Given every metadata form, lowering retains typed targets and closed kinds."""
    # when: the authoritative source lowers without semantic resolution
    portable = _portable()
    facts = [
        (fact["kind"], fact["target"])
        for fact in portable["contributions"]
        if fact["kind"]
        in {
            "documentation.description",
            "documentation.annotation",
            "metadata.named-marking",
            "view.position",
            "view.route",
            "document.extensions",
        }
    ]

    # then: documentation targets existing elements and presentation has typed scope
    assert facts == [
        ("documentation.description", {"type": "place", "name": "in_flight"}),
        ("documentation.annotation", {"type": "place", "name": "in_flight"}),
        ("documentation.description", {"type": "transition", "name": "start"}),
        ("documentation.annotation", {"type": "transition", "name": "finish"}),
        ("documentation.description", {"type": "arcHandle", "name": "window_close"}),
        ("documentation.annotation", {"type": "arcHandle", "name": "window_close"}),
        ("metadata.named-marking", {"type": "document"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.position", {"type": "view", "name": "window"}),
        ("view.route", {"type": "arcHandle", "name": "window_close"}),
        ("document.extensions", {"type": "document"}),
    ]


def test_authoritative_corpus_pins_exact_ir_resolution_and_canonical_text() -> None:
    """Given the valid corpus, every portable and canonical byte is contractual."""
    source = _document(_CORPUS_ROOT / "speaks-window.source.json")
    expected_ir = _document(_CORPUS_ROOT / "speaks-window.contribution-ir.json")
    expected_net = _document(_CORPUS_ROOT / "speaks-window.net.json")
    canonical = _document(_CORPUS_ROOT / "speaks-window.canonical.source.json")

    portable = lower_petrinet_text(source["text"], source["sourceId"])
    resolved = resolve_contribution_ir(portable)

    assert portable == expected_ir
    assert resolved == expected_net
    assert emit_petrinet(resolved) == canonical["text"]
    assert (
        compile_petrinet_text(canonical["text"], canonical["sourceId"]) == expected_net
    )


def test_documentation_facts_cover_net_place_transition_and_arc_targets() -> None:
    """Given every metadata target, descriptions and annotations elaborate it."""
    source = """\
net metadata_targets
@flow: (p) -tok-> [t]
[t] -tok-> (q)
[t] handler "h"
net description "The net"
net annotation owner {"team": "speech"}
(p) description "The input"
(p) annotation lane 1
[t] description "The step"
[t] annotation stage true
@flow description "Both arc segments"
@flow annotation trace null
"""

    actual = compile_petrinet_text(source, "metadata-targets.petrinet")

    assert actual["description"] == "The net"
    assert actual["annotations"]["owner"] == {"team": "speech"}
    assert actual["annotations"][_RESERVED]["arcHandles"]["flow"]["index"] == 0
    assert actual["places"][0] == {
        "name": "p",
        "accepts": ["tok"],
        "description": "The input",
        "annotations": {"lane": 1},
    }
    assert actual["transitions"][0] == {
        "name": "t",
        "handler": "h",
        "description": "The step",
        "annotations": {"stage": True},
    }
    assert actual["arcs"][0]["description"] == "Both arc segments"
    assert actual["arcs"][0]["annotations"] == {"trace": None}
    assert "description" not in actual["arcs"][1]
    assert "annotations" not in actual["arcs"][1]


def test_authoritative_source_resolves_to_exact_full_document() -> None:
    """Given paired source and JSON, resolution preserves core and reserved payload."""
    # when: public compilation resolves all facts and core validation parses the pair
    actual = compile_petrinet_text(_SOURCE, str(_DSL_PATH))
    expected = _document()
    parsed = parse_net(expected)

    # then: the full document, topology, and absence of an active marking are exact
    assert actual == expected
    assert [place.name for place in parsed.places] == [
        "request_in",
        "in_flight",
        "judge_requested",
        "utterance_out",
    ]
    assert [transition.name for transition in parsed.transitions] == ["start", "finish"]
    assert len(parsed.arcs) == 8
    assert parsed.initial_marking is None
    assert "initialMarking" not in actual

    payload = actual["annotations"][_RESERVED]
    assert set(payload) == {"arcHandles", "markings", "views", "extensions"}
    assert payload["arcHandles"] == {
        "window_close": {
            "index": 7,
            "fingerprint": {
                "from": {"place": "in_flight"},
                "to": {"transition": "finish"},
                "type": "speak_token",
                "mode": "consume",
            },
        }
    }
    assert payload["markings"] == {
        "queued": {"request_in": [{"type": "speak_req", "data": {"text": "Status?"}}]}
    }


def test_metadata_elaborates_existing_objects_without_declaring_topology() -> None:
    """Given only metadata additions, semantic identities and colors stay unchanged."""
    # given: the source with all metadata removed but identical semantic declarations
    semantic_source = _SOURCE[: _SOURCE.index("\n(in_flight) description")] + "\n"

    # when: both forms resolve
    enriched = compile_petrinet_text(_SOURCE, "enriched-speaks.petrinet")
    plain = compile_petrinet_text(semantic_source, "plain-speaks.petrinet")

    # then: recursively removing documentation/full-document fields yields the
    # same semantic graph; an absent marking and an explicit empty marking are
    # equivalent at the engine boundary.
    stripped = deepcopy(enriched)
    plain_core = deepcopy(plain)
    for candidate in (stripped, plain_core):
        candidate.pop("description", None)
        candidate.pop("annotations", None)
        if not candidate.get("initialMarking"):
            candidate.pop("initialMarking", None)
        for collection in ("places", "transitions", "arcs"):
            for item in candidate[collection]:
                item.pop("description", None)
                item.pop("annotations", None)
    assert stripped == plain_core


@pytest.mark.parametrize(
    ("fact", "message", "column"),
    [
        (
            'view window position (ghost) at {"x": 20, "y": 20}',
            "view 'window' references unknown place 'ghost'; presentation facts cannot declare semantic objects",
            22,
        ),
        (
            "marking queued (ghost) <- $demo_request",
            "named marking 'queued' references unknown place 'ghost'; marking facts cannot declare semantic objects",
            1,
        ),
        (
            'view window route @ghost orthogonal [{"x": 1, "y": 2}]',
            "view 'window' route references unknown arc handle @ghost",
            1,
        ),
    ],
    ids=["position", "named-marking", "route"],
)
def test_metadata_references_cannot_declare_semantic_objects(
    fact: str, message: str, column: int
) -> None:
    """Given an unknown metadata target, resolution fails at its contract span."""
    source = f"""\
net bad_window

(request_in) -speak_req-> [start] -speak_token-> (in_flight)
[start] handler "chip_start@speaks"
$demo_request: speak_req {{"text": "Status?"}}
{fact}
"""

    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "bad_window.petrinet")

    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (6, column)


def test_reserved_user_annotation_key_is_rejected_exactly() -> None:
    """Given an ordinary reserved annotation, only the compiler may own v1."""
    source = """\
net reserved_metadata
net annotation "petrinet.dsl/v1" {}
"""

    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "reserved_metadata.petrinet")

    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == (
        "annotation key 'petrinet.dsl/v1' is reserved for compiler-owned metadata"
    )
    assert diagnostic.help == "use extensions for opaque full-document data"
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (2, 1)


def test_equal_metadata_facts_are_idempotent_and_unequal_facts_conflict() -> None:
    """Given repeated annotation/position facts, equality collapses and inequality cites both."""
    equal = _SOURCE.replace(
        "(in_flight) annotation fusion true\n",
        "(in_flight) annotation fusion true\n(in_flight) annotation fusion true\n",
    ).replace(
        'view window position (request_in) at {"x": 0, "y": 80}\n',
        'view window position (request_in) at {"x": 0, "y": 80}\n'
        'view window position (request_in) at {"x": 0, "y": 80}\n',
    )
    assert compile_petrinet_text(equal, "equal-metadata.petrinet") == _document()

    conflict = equal.replace(
        'view window position (request_in) at {"x": 0, "y": 80}\n'
        'view window position (request_in) at {"x": 0, "y": 80}\n',
        'view window position (request_in) at {"x": 0, "y": 80}\n'
        'view window position (request_in) at {"x": 1, "y": 80}\n',
    )
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(conflict, "conflicting-metadata.petrinet")

    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert diagnostic.message == (
        "conflicting position facts for place (request_in) in view 'window'"
    )
    assert len(diagnostic.related) == 1
    assert diagnostic.related[0].message == "first position was declared here"
    assert diagnostic.related[0].span.start.line < diagnostic.span.start.line


@pytest.mark.parametrize(
    ("kind", "field", "value", "message"),
    [
        (
            "documentation.description",
            "format",
            "markdown",
            "invalid documentation description contribution",
        ),
        (
            "documentation.annotation",
            "semantic",
            True,
            "invalid documentation annotation contribution",
        ),
        (
            "metadata.named-marking",
            "initial",
            True,
            "invalid named marking contribution",
        ),
        (
            "view.position",
            "rank",
            1,
            "invalid view position contribution",
        ),
        (
            "view.route",
            "style",
            "spline",
            "invalid view route contribution",
        ),
        (
            "document.extensions",
            "objects",
            [],
            "invalid document extensions contribution",
        ),
    ],
)
def test_imported_metadata_ir_is_closed_and_strict(
    kind: str, field: str, value: Any, message: str
) -> None:
    """Given imported IR, unknown fields cannot smuggle semantics past lowering."""
    portable, fact = _portable_with_imported_metadata(kind)
    fact["value"][field] = value

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN200"
    assert diagnostic.message == message
    assert diagnostic.span.as_dict() == fact["span"]


def test_imported_extensions_must_remain_a_tagged_object() -> None:
    """Given imported extensions IR, a scalar cannot become the opaque carrier."""
    portable, fact = _portable_with_imported_metadata("document.extensions")
    fact["value"]["extensions"] = {"type": "string", "value": "not-an-object"}

    with pytest.raises(PetrinetDslError) as raised:
        resolve_contribution_ir(portable)

    assert raised.value.diagnostic.code == "PN200"
    assert raised.value.diagnostic.message == "invalid document extensions contribution"
    assert raised.value.diagnostic.span.as_dict() == fact["span"]


def test_canonical_emission_groups_metadata_and_is_a_fixed_point() -> None:
    """Given full JSON, canonical emission groups facts and reparses identically."""
    document = _document()

    first = emit_petrinet(document)
    resolved = compile_petrinet_text(first, "speaks-window.canonical.petrinet")
    second = emit_petrinet(resolved)

    assert resolved == document
    assert second == first
    assert first.endswith("\n")
    assert first.count("->0") == 2
    assert first.index('[finish] handler "finish@speaks"') < first.index(
        '(in_flight) description "Singleton token delimiting one active request"'
    )
    assert first.index("marking queued") < first.index("view window position")
    assert first.index("view window route @window_close orthogonal") < first.index(
        "extensions {}"
    )


def test_plain_core_json_roundtrips_ordinary_annotations_without_inventing_v1() -> None:
    """Given unrelated annotations, emission preserves them and invents no DSL layer."""
    document = {
        "name": "plain_metadata",
        "description": "Ordinary documentation",
        "places": [
            {
                "name": "p",
                "accepts": ["tok"],
                "annotations": {"owner": {"team": "speech"}},
            },
            {"name": "q", "accepts": ["tok"]},
        ],
        "transitions": [{"name": "t", "handler": "h"}],
        "arcs": [
            {
                "from": {"place": "p"},
                "to": {"transition": "t"},
                "consume": {"type": "tok"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "q"},
                "produce": {"type": "tok", "destination": "q"},
            },
        ],
        "initialMarking": {},
        "annotations": {"owner": "docs"},
    }

    emitted = emit_petrinet(document)
    reparsed = compile_petrinet_text(emitted, "plain-metadata.petrinet")

    assert reparsed == document
    assert _RESERVED not in reparsed["annotations"]
    assert "arcHandles" not in emitted
    assert "marking queued" not in emitted
    assert "view " not in emitted


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "index",
            99,
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.index 99 is outside arcs[0:8]",
        ),
        (
            "index",
            0,
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.fingerprint does not match arcs[0]",
        ),
        (
            "from",
            {"place": "judge_requested"},
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.fingerprint does not match arcs[7]",
        ),
        (
            "to",
            {"transition": "start"},
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.fingerprint does not match arcs[7]",
        ),
        (
            "type",
            "judge_req",
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.fingerprint does not match arcs[7]",
        ),
        (
            "mode",
            "read",
            "annotations['petrinet.dsl/v1'].arcHandles.window_close.fingerprint does not match arcs[7]",
        ),
    ],
    ids=[
        "index-out-of-range",
        "index-first-no-search",
        "stale-from",
        "stale-to",
        "stale-type",
        "stale-mode",
    ],
)
def test_json_reconstruction_validates_arc_handle_index_before_fingerprint(
    field: str, value: Any, message: str
) -> None:
    """Given stale reserved data, emission rejects its exact path without retargeting."""
    document = deepcopy(_document())
    handle = document["annotations"][_RESERVED]["arcHandles"]["window_close"]
    if field == "index":
        handle[field] = value
    else:
        handle["fingerprint"][field] = value

    with pytest.raises(
        ValueError, match=f"^{message.replace('[', r'\[').replace(']', r'\]')}$"
    ):
        emit_petrinet(document)


def test_json_reconstruction_rejects_unknown_route_handle_exactly() -> None:
    """Given a route absent from arcHandles, emission cannot infer by endpoints."""
    document = deepcopy(_document())
    route = document["annotations"][_RESERVED]["views"]["window"]["routes"].pop(
        "window_close"
    )
    document["annotations"][_RESERVED]["views"]["window"]["routes"]["ghost"] = route

    with pytest.raises(
        ValueError,
        match=r"^annotations\['petrinet\.dsl/v1'\]\.views\.window\.routes\.ghost references unknown arc handle$",
    ):
        emit_petrinet(document)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("root", "future"),
        ("arcHandles.window_close", "label"),
        ("markings.queued", "description"),
        ("views.window", "rankdir"),
        ("views.window.routes.window_close", "color"),
    ],
)
def test_reserved_v1_structured_sections_reject_unknown_fields(
    section: str, field: str
) -> None:
    """Given unknown structured v1 data, only extensions is an opaque escape hatch."""
    document = deepcopy(_document())
    target = document["annotations"][_RESERVED]
    path = "annotations['petrinet.dsl/v1']"
    if section != "root":
        for part in section.split("."):
            target = target[part]
            path += f".{part}"
    target[field] = "unsupported"

    with pytest.raises(
        ValueError,
        match=rf"^{path.replace('[', r'\[').replace(']', r'\]')} has unsupported field {field!r}$",
    ):
        emit_petrinet(document)


@pytest.mark.parametrize(
    ("subject", "message"),
    [
        (
            "state:request_in",
            "must target place:<name> or transition:<name>",
        ),
        (
            "place:ghost",
            "references unknown place 'ghost'",
        ),
    ],
)
def test_json_view_positions_require_known_typed_semantic_targets(
    subject: str, message: str
) -> None:
    document = deepcopy(_document())
    positions = document["annotations"][_RESERVED]["views"]["window"]["positions"]
    positions[subject] = positions.pop("place:request_in")

    with pytest.raises(ValueError, match=message):
        emit_petrinet(document)


def test_metadata_handle_index_uses_arc_identity_not_structural_equality() -> None:
    document = compile_petrinet_text(
        """\
net duplicate_arcs
@first: (p) -tok-> [t]
@second: (p) -tok-> [t]
[t] handler "h"
@second description "the second arc"
""",
        "duplicate-arcs.petrinet",
    )

    assert document["annotations"][_RESERVED]["arcHandles"]["second"]["index"] == 1
    assert "description" not in document["arcs"][0]
    assert document["arcs"][1]["description"] == "the second arc"


def test_multi_arc_handle_cannot_carry_reconstructable_metadata() -> None:
    with pytest.raises(
        PetrinetDslError,
        match="metadata arc handle '@flow' must identify exactly one arc",
    ):
        compile_petrinet_text(
            """\
net multi_arc_metadata
@flow: (p) -tok-> [t] -tok-> (q)
[t] handler "h"
@flow description "ambiguous reconstruction"
""",
            "multi-arc-metadata.petrinet",
        )


def test_extensions_are_the_only_opaque_lossless_future_data() -> None:
    """Given nested future extension data, canonical DSL preserves it losslessly."""
    document = deepcopy(_document())
    extensions = {
        "vendor.example/layout": {
            "enabled": True,
            "layers": [1, "two", None, {"nested": 3.5}],
        }
    }
    document["annotations"][_RESERVED]["extensions"] = extensions

    emitted = emit_petrinet(document)
    reparsed = compile_petrinet_text(emitted, "opaque-extensions.petrinet")

    assert reparsed == document
    assert reparsed["annotations"][_RESERVED]["extensions"] == extensions


@pytest.mark.parametrize(
    "case",
    [
        "unknown-position-target",
        "unknown-marking-place",
        "unknown-route-handle",
        "reserved-annotation-key",
        "conflicting-annotation",
        "conflicting-position",
        "non-object-extensions",
    ],
)
def test_metadata_diagnostics_match_committed_conformance(case: str) -> None:
    """Resolver diagnostics are the exact checked-in Slice 11 language contract."""
    corpus = json.loads(
        (_CORPUS_ROOT / f"speaks_window.{case}.json").read_text(encoding="utf-8")
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
