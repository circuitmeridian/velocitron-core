"""Focused contracts for canonical JSON primitives in DSL and CLI output."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from velocitron.dsl import cli
from velocitron.dsl.api import (
    compile_petrinet_text,
    emit_petrinet,
    render_canonical_json,
)


def test_canonical_binary64_boundaries_and_shortest_spellings() -> None:
    """Binary64 values use ECMAScript fixed/scientific thresholds and shortest form."""
    values = [
        -9_007_199_254_740_991,
        9_007_199_254_740_991,
        -0.0,
        1.0,
        0.000001,
        0.0000001,
        1e20,
        1e21,
        333333333.33333329,
        4.50,
        2e-3,
        1e-27,
        5e-324,
        1.7976931348623157e308,
        9007199254740992.0,
    ]

    assert render_canonical_json(values) == (
        "[-9007199254740991,9007199254740991,0,1,0.000001,1e-7,"
        "100000000000000000000,1e21,333333333.3333333,4.5,0.002,"
        "1e-27,5e-324,1.7976931348623157e308,9007199254740992]"
    )


def test_canonical_nested_values_sort_keys_without_reordering_arrays() -> None:
    """Nested values retain array semantics and sort names by UTF-16 code units."""
    value = {
        "\ue000": 2.0,
        "😀": [{"é": "雪", "a": -0.0}, 1e21, 1e-7],
    }

    assert render_canonical_json(value) == (
        '{"😀":[{"a":0,"é":"雪"},1e21,1e-7],"\ue000":2}'
    )


def test_embedded_dsl_json_uses_the_same_canonical_primitive_renderer() -> None:
    document: dict[str, Any] = {
        "name": "numbers",
        "places": [{"name": "out", "accepts": ["number"]}],
        "transitions": [{"name": "emit", "handler": "emit"}],
        "arcs": [
            {
                "from": {"transition": "emit"},
                "to": {"place": "out"},
                "produce": {
                    "destination": "out",
                    "type": "number",
                    "data": {"nested": [-0.0, 1.0, 1e-7, 1e20, 1e21, {"é": "雪"}]},
                },
            }
        ],
        "initialMarking": {},
    }

    assert (
        '@arc_0 data {"nested":[0,1,1e-7,100000000000000000000,1e21,{"é":"雪"}]}'
    ) in emit_petrinet(document)


@pytest.mark.parametrize("value", [-9_007_199_254_740_992, 9_007_199_254_740_992])
def test_canonical_json_rejects_integers_outside_ieee_safe_range(value: int) -> None:
    with pytest.raises(ValueError, match="safe IEEE-754 range"):
        render_canonical_json({"outer": [value]})


def test_canonical_metadata_is_independent_of_mapping_insertion_order() -> None:
    source = """\
net ordering
@first: (p) -tok-> [t]
@second: [t] -tok-> (q)
[t] handler "handle"
net annotation z 1
net annotation a 2
(p) annotation z 1
(p) annotation a 2
[t] annotation z 1
[t] annotation a 2
@first annotation z 1
@first annotation a 2
view z position (p) at {"x": 1, "y": 1}
view a position [t] at {"x": 0, "y": 0}
view a position (p) at {"x": 0, "y": 0}
view a route @second orthogonal [{"x": 0, "y": 0}, {"x": 0, "y": 1}]
view a route @first orthogonal [{"x": 1, "y": 0}, {"x": 1, "y": 1}]
extensions {"z": 1, "a": 2}
"""
    document = compile_petrinet_text(source, "ordering.petrinet")
    reordered = deepcopy(document)

    def reverse(mapping: dict[str, Any]) -> dict[str, Any]:
        return dict(reversed(mapping.items()))

    reordered["annotations"] = reverse(reordered["annotations"])
    reordered["places"][0]["annotations"] = reverse(
        reordered["places"][0]["annotations"]
    )
    reordered["transitions"][0]["annotations"] = reverse(
        reordered["transitions"][0]["annotations"]
    )
    reordered["arcs"][0]["annotations"] = reverse(reordered["arcs"][0]["annotations"])
    metadata = reordered["annotations"]["petrinet.dsl/v1"]
    metadata["views"] = reverse(metadata["views"])
    for view in metadata["views"].values():
        view["positions"] = reverse(view["positions"])
        view["routes"] = reverse(view["routes"])
    metadata["extensions"] = reverse(metadata["extensions"])

    canonical = emit_petrinet(document)
    assert emit_petrinet(reordered) == canonical
    assert canonical.index("net annotation a 2") < canonical.index("net annotation z 1")
    assert canonical.index("view a ") < canonical.index("view z ")
    assert canonical.index("route @first") < canonical.index("route @second")
    assert 'extensions {"a":2,"z":1}' in canonical


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_nonfinite_values_even_when_nested(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        render_canonical_json({"outer": [0, {"number": value}]})


def test_to_json_writes_pretty_canonical_primitives_byte_for_byte(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI pretty path changes whitespace only, not canonical primitives."""
    source = tmp_path / "numbers.petrinet"
    document: dict[str, Any] = {
        "z": 1.0,
        "array": [-0.0, 0.000001, 1e-7, 1e20, 1e21, {"é": "雪"}],
    }

    def compile_document(path: str) -> dict[str, Any]:
        del path
        return document

    def validate_document(value: dict[str, Any], path: str) -> str:
        del value, path
        return "net"

    monkeypatch.setattr(cli, "_compile_document", compile_document)
    monkeypatch.setattr(cli, "_validate_document", validate_document)

    assert cli.main(["to-json", str(source)]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert captured.out.encode("utf-8") == (
        "{\n"
        '  "array": [\n'
        "    0,\n"
        "    0.000001,\n"
        "    1e-7,\n"
        "    100000000000000000000,\n"
        "    1e21,\n"
        "    {\n"
        '      "é": "雪"\n'
        "    }\n"
        "  ],\n"
        '  "z": 1\n'
        "}\n"
    ).encode("utf-8")
