"""Regression contracts for suppression handle allocation and topology spans."""

from __future__ import annotations

from typing import Any


from velocitron.dsl.api import emit_petrinet
from velocitron.dsl.compiler import lower_petrinet_text, resolve_contribution_ir


_SOURCE_ID = "mixed-suppression.petrinet"
_CORRELATE = "token.key == binding.requests[0].key"
_MIXED_DOCUMENT: dict[str, Any] = {
    "name": "mixed-suppression",
    "places": [
        {"name": "requests", "accepts": ["request"]},
        {"name": "quota", "accepts": ["permit"]},
        {"name": "accepted", "accepts": ["request"]},
        {"name": "suppressions", "accepts": ["suppression"]},
    ],
    "transitions": [{"name": "admit", "handler": "admit_request"}],
    "arcs": [
        {
            "from": {"place": "requests"},
            "to": {"transition": "admit"},
            "consume": {
                "type": "request",
                "predicate": {"cel": "priority == 1"},
            },
        },
        {
            "from": {"place": "quota"},
            "to": {"transition": "admit"},
            "consume": {"type": "permit", "weight": 2},
        },
        {
            "from": {"transition": "admit"},
            "to": {"place": "accepted"},
            "produce": {
                "type": "request",
                "destination": "accepted",
                "data": {"status": "accepted"},
            },
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
    ],
    "initialMarking": {"requests": [{"type": "request", "data": {"priority": 1}}]},
}
_MIXED_SOURCE = """\
net "mixed-suppression"

@arc_0: (requests) -request-> [admit]
@arc_1: (quota) -permit-> [admit]
@arc_2: [admit] -request-> (accepted)
@arc_3: (suppressions) -suppression->0 [admit]

[admit] handler "admit_request"

@arc_1 weight 2

@arc_2 data {"status":"accepted"}

@arc_0 predicate cel "priority == 1"

@arc_3 correlate cel "token.key == binding.requests[0].key"

marking initial (requests) <- $token_0
$token_0: request {"priority":1}
"""


def _point(byte_offset: int, line: int, column: int) -> dict[str, int]:
    return {"byteOffset": byte_offset, "line": line, "column": column}


def _span(
    start_offset: int,
    start_line: int,
    start_column: int,
    end_offset: int,
    end_line: int,
    end_column: int,
) -> dict[str, object]:
    return {
        "source": _SOURCE_ID,
        "start": _point(start_offset, start_line, start_column),
        "end": _point(end_offset, end_line, end_column),
    }


def _id(statement: int, part: int = 0) -> dict[str, object]:
    return {"source": _SOURCE_ID, "statement": statement, "part": part}


def _arc_id(statement: int) -> dict[str, object]:
    return {"document": _SOURCE_ID, "statement": statement, "part": 1}


def _contribution(
    statement: int,
    kind: str,
    ordinal: int,
    span: dict[str, object],
    target: dict[str, object],
    value: dict[str, object],
    *,
    part: int = 0,
) -> dict[str, object]:
    return {
        "id": _id(statement, part),
        "kind": kind,
        "ordinal": ordinal,
        "span": span,
        "target": target,
        "value": value,
    }


def _expected_mixed_ir() -> dict[str, object]:
    arc_specs: list[
        tuple[
            int,
            str,
            dict[str, object],
            dict[str, object],
            dict[str, object],
        ]
    ] = [
        (
            1,
            "arc_0",
            _span(25, 3, 1, 62, 3, 38),
            _span(33, 3, 9, 62, 3, 38),
            {
                "from": {"type": "place", "name": "requests"},
                "to": {"type": "transition", "name": "admit"},
                "color": {"kind": "explicit", "value": "request"},
                "mode": "consume",
                "transitionNameSpan": _span(56, 3, 32, 61, 3, 37),
            },
        ),
        (
            2,
            "arc_1",
            _span(63, 4, 1, 96, 4, 34),
            _span(71, 4, 9, 96, 4, 34),
            {
                "from": {"type": "place", "name": "quota"},
                "to": {"type": "transition", "name": "admit"},
                "color": {"kind": "explicit", "value": "permit"},
                "mode": "consume",
                "transitionNameSpan": _span(90, 4, 28, 95, 4, 33),
            },
        ),
        (
            3,
            "arc_2",
            _span(97, 5, 1, 134, 5, 38),
            _span(105, 5, 9, 134, 5, 38),
            {
                "from": {"type": "transition", "name": "admit"},
                "to": {"type": "place", "name": "accepted"},
                "color": {"kind": "explicit", "value": "request"},
                "mode": "produce",
            },
        ),
        (
            4,
            "arc_3",
            _span(135, 6, 1, 181, 6, 47),
            _span(143, 6, 9, 181, 6, 47),
            {
                "from": {"type": "place", "name": "suppressions"},
                "to": {"type": "transition", "name": "admit"},
                "color": {"kind": "explicit", "value": "suppression"},
                "mode": "inhibit",
            },
        ),
    ]
    contributions: list[dict[str, object]] = [
        _contribution(
            0,
            "document.net-header",
            0,
            _span(0, 1, 1, 23, 1, 24),
            {"type": "document"},
            {"name": "mixed-suppression"},
        )
    ]
    for statement, handle, handle_span, arc_span, value in arc_specs:
        contributions.extend(
            [
                _contribution(
                    statement,
                    "arc.handle",
                    2 * statement - 1,
                    handle_span,
                    {"type": "arcHandle", "name": handle},
                    {"arcIds": [_arc_id(statement)]},
                ),
                _contribution(
                    statement,
                    "arc.declare",
                    2 * statement,
                    arc_span,
                    {"type": "arc", "id": _arc_id(statement)},
                    value,
                    part=1,
                ),
            ]
        )
    contributions.extend(
        [
            _contribution(
                5,
                "transition.handler",
                9,
                _span(183, 8, 1, 214, 8, 32),
                {"type": "transition", "name": "admit"},
                {"handler": "admit_request"},
            ),
            _contribution(
                6,
                "arc.weight",
                10,
                _span(216, 10, 1, 231, 10, 16),
                {"type": "arcHandle", "name": "arc_1"},
                {"weight": 2},
            ),
            _contribution(
                7,
                "arc.produce-data",
                11,
                _span(233, 12, 1, 266, 12, 34),
                {"type": "arcHandle", "name": "arc_2"},
                {
                    "data": {
                        "type": "object",
                        "entries": [
                            {
                                "key": "status",
                                "value": {"type": "string", "value": "accepted"},
                            }
                        ],
                    }
                },
            ),
            _contribution(
                8,
                "arc.predicate",
                12,
                _span(268, 14, 1, 304, 14, 37),
                {"type": "arcHandle", "name": "arc_0"},
                {"kind": "cel", "cel": "priority == 1"},
            ),
            _contribution(
                9,
                "arc.correlate",
                13,
                _span(306, 16, 1, 365, 16, 60),
                {"type": "arcHandle", "name": "arc_3"},
                {"cel": _CORRELATE},
            ),
            _contribution(
                10,
                "marking.append",
                14,
                _span(367, 18, 1, 405, 18, 39),
                {"type": "place", "name": "requests"},
                {
                    "count": 1,
                    "token": {"template": {"type": "template", "name": "token_0"}},
                },
            ),
            _contribution(
                11,
                "template.define",
                15,
                _span(406, 19, 1, 438, 19, 33),
                {"type": "template", "name": "token_0"},
                {
                    "value": {
                        "type": "object",
                        "entries": [
                            {
                                "key": "priority",
                                "value": {"type": "number", "lexeme": "1"},
                            }
                        ],
                    }
                },
            ),
        ]
    )
    return {
        "format": "velocitron.petrinet/contribution-ir",
        "version": 1,
        "documentKind": "net",
        "document": {"id": _SOURCE_ID},
        "contributions": contributions,
    }


def test_mixed_decorated_arcs_use_core_indexes_in_one_handle_namespace() -> None:
    """Generated handles use actual arc indexes across every decoration kind."""
    # given: earlier predicate, weight, and data arcs followed by a correlator
    canonical = emit_petrinet(_MIXED_DOCUMENT)

    # when: canonical source is lowered, resolved, and emitted a second time
    portable = lower_petrinet_text(canonical, _SOURCE_ID)
    resolved = resolve_contribution_ir(portable)
    second = emit_petrinet(resolved)

    # then: source and complete portable IR are stable, not merely the core JSON
    assert canonical == _MIXED_SOURCE
    assert portable == _expected_mixed_ir()
    assert resolved == _MIXED_DOCUMENT
    assert second == canonical

    # and: every decoration shares one collision-free core-index namespace
    handles = [
        contribution["target"]["name"]
        for contribution in portable["contributions"]
        if contribution["kind"] == "arc.handle"
    ]
    assert handles == ["arc_0", "arc_1", "arc_2", "arc_3"]
    assert canonical.count("@arc_3:") == 1
    assert canonical.count("@arc_3 correlate") == 1


def test_correlate_fact_does_not_change_topology_arc_spans() -> None:
    """A later correlate fact cannot rewrite earlier topology token spans."""
    # given: byte-identical topology, once alone and once with a later correlate fact
    topology = """\
net span_stability

@blocked: (suppressions) -suppression->0 [admit]
"""
    with_correlate = (
        topology + '\n@blocked correlate cel "token.key == binding.requests[0].key"\n'
    )

    # when: both sources are lowered and successfully resolved
    without_ir = lower_petrinet_text(topology, "span-stability.petrinet")
    with_ir = lower_petrinet_text(with_correlate, "span-stability.petrinet")
    without_resolved = resolve_contribution_ir(without_ir)
    with_resolved = resolve_contribution_ir(with_ir)

    without_arc = next(
        item for item in without_ir["contributions"] if item["kind"] == "arc.declare"
    )
    with_arc = next(
        item for item in with_ir["contributions"] if item["kind"] == "arc.declare"
    )

    # then: the complete topology contribution and exact span ignore the later fact
    expected_arc_span = {
        "source": "span-stability.petrinet",
        "start": {"byteOffset": 30, "line": 3, "column": 11},
        "end": {"byteOffset": 68, "line": 3, "column": 49},
    }
    assert without_arc == with_arc
    assert without_arc["span"] == expected_arc_span

    # and: resolution differs only by the authored correlator
    assert without_resolved == {
        "name": "span_stability",
        "places": [{"name": "suppressions", "accepts": ["suppression"]}],
        "transitions": [{"name": "admit"}],
        "arcs": [
            {
                "from": {"place": "suppressions"},
                "to": {"transition": "admit"},
                "consume": {"type": "suppression", "mode": "inhibit"},
            }
        ],
        "initialMarking": {},
    }
    assert with_resolved == {
        "name": "span_stability",
        "places": [{"name": "suppressions", "accepts": ["suppression"]}],
        "transitions": [{"name": "admit"}],
        "arcs": [
            {
                "from": {"place": "suppressions"},
                "to": {"transition": "admit"},
                "consume": {
                    "type": "suppression",
                    "mode": "inhibit",
                    "correlate": {"cel": _CORRELATE},
                },
            }
        ],
        "initialMarking": {},
    }
