"""Graphviz DOT rendering for velocitron nets and compositions.

Renders a parsed :class:`~velocitron.schema.Net` or
:class:`~velocitron.schema.Composition` as a graphviz ``digraph`` covering
every net-schema feature (`spec/net-schema.md`, `spec/composition.md`):

- **Places** are ellipses labeled with their accepted token types; a selected
  marking renders bounded, deterministic complete-token detail inside each
  nonempty place.
- **Ports** (boundary places) are double-ringed and tinted — blue for
  ``input``, orange for ``output`` — with the port direction and type named
  on the node.
- **Transitions** are boxes naming their handler ref; a guarded transition
  gains a purple border and a ``guard:`` line; a declared ``priority``
  (honored by the built-in ``priority`` firing policy, ADR 0014) is shown
  grayed.
- **Consume arcs** are solid black edges labeled with the token type
  (``n × type`` when ``weight > 1``) and the predicate (inline CEL, or
  ``pred: name`` for a handler ref). ``mode: "inhibit"`` renders as the
  classic inhibitor glyph — crimson, dashed, open-dot arrowhead — labeled
  ``no type``; ``mode: "read"`` (test-without-consume) renders blue and
  dashed, labeled ``read``.
- **Produce arcs** are solid black edges labeled with the token type, plus a
  ``+ data`` line when the template carries a literal payload, or a
  ``+ data cel`` line (the DSL's ``data cel`` fact) when it carries a
  computed CEL fallback expression (ADR 0023).
- **Compositions** render each aliased net as a cluster and each wire as a
  bold purple edge between the two port places; ``merged=True`` renders the
  single fused net produced by :func:`velocitron.composition.merge_composition`
  instead, and :func:`composition_ports_dot` (CLI ``--ports-only``) collapses
  each net to just its boundary ports for a compact wiring diagram.
- **Fusion places** (``annotations: {"fusion": true}`` — CONTEXT.md "Fusion
  place") render as a local dashed gray instance at each connected transition
  instead of one hub node with arcs radiating everywhere; ``render_fusion=
  False`` (CLI ``--no-fusion``) restores the single-node view.
- ``description`` fields become SVG tooltips on nodes and edges.

Output is markdown-embeddable on request: :class:`RenderStyle` (CLI
``--plain-labels`` / ``--no-tooltips``, or the ``--doc`` preset) swaps the
HTML-like labels for plain quoted ones and drops tooltips, and ``--fence``
wraps the DOT in a ```` ```dot ```` code fence — so the CLI output can be
pasted into a markdown document unedited.

Inhibit arcs render as crimson dashed ``odot`` edges. Run as
``python -m velocitron.viz`` or via the
``velocitron-viz`` console script; see ``main`` for the CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from velocitron.composition import merge_composition
from velocitron.dsl.api import (
    compile_petrinet_text,
    read_petrinet_text,
    render_canonical_json,
)
from velocitron.dsl.diagnostics import PetrinetDslError
from velocitron.parser import NetValidationError, parse_composition, parse_net
from velocitron.schema import (
    Arc,
    Composition,
    ConsumePattern,
    Marking,
    Net,
    Place,
    Port,
    Predicate,
    ProduceTemplate,
    Token,
    Transition,
)

# ── Palette ──────────────────────────────────────────────────────────────

_INPUT_PORT_FILL = "#e7f1fb"
_INPUT_PORT_BORDER = "#1f6feb"
_OUTPUT_PORT_FILL = "#fff3e0"
_OUTPUT_PORT_BORDER = "#b45309"
_TRANSITION_FILL = "lightyellow"
_GUARD_BORDER = "#7b2d8b"
_READ_COLOR = "#1f6feb"
_INHIBIT_COLOR = "crimson"
_WIRE_COLOR = "#6a3d9a"
_MARKING_COLOR = "#1a7f37"
_MUTED = "#666666"
_FUSION_BORDER = "gray40"
_FUSION_FONT = "gray30"

_PREDICATE_MAX_CHARS = 48
_TOKEN_GROUP_LIMIT = 8
_TOKEN_TYPE_LIMIT = 64
_TOKEN_DATA_LIMIT = 160
_UNNAMED_NET_NAME = "unnamed"
_GENERIC_TOKEN_TYPE = "token"


class _VizError(Exception):
    """A deterministic, user-facing ``velocitron-viz`` failure."""


class _SourceError(_VizError):
    """A source document could not be read, compiled, or validated."""


def _json_quote(value: str) -> str:
    """Quote a user-supplied name with JSON's deterministic string grammar."""
    return json.dumps(value, ensure_ascii=False)


# ── Render style ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RenderStyle:
    """Rendering options threaded through the attribute helpers.

    ``html_labels=False`` swaps the HTML-like labels for plain double-quoted
    ones — same content, far less markup — so the DOT source stays readable
    when embedded in a markdown document. ``tooltips=False`` omits the
    ``description`` tooltips (noise in embedded source; SVG-only anyway).
    The CLI exposes these as ``--plain-labels`` / ``--no-tooltips``, with
    ``--doc`` as the both-at-once preset.
    """

    html_labels: bool = True
    tooltips: bool = True


_DEFAULT_STYLE = RenderStyle()


# ── Escaping ─────────────────────────────────────────────────────────────


def _quote(identifier: str) -> str:
    """Quote a DOT node ID (qualified names like ``alias.place`` need it)."""
    escaped = identifier.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dot_string(text: str) -> str:
    """Escape text for a double-quoted DOT attribute value (e.g. tooltip)."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _html(text: str) -> str:
    """Escape text for use inside an HTML-like label."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Label fragments ──────────────────────────────────────────────────────


def _truncate_display(text: str, limit: int) -> str:
    """Bound a display string by Unicode scalars without affecting semantics."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _break_after_commas(text: str) -> str:
    """Insert a line break after each JSON comma outside a quoted string.

    Wide inline token data (e.g. ``{"a":1,"b":2,...}``) otherwise renders as
    one unbroken line, stretching the node far past its neighbors.
    """
    pieces: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        pieces.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif char == "," and not in_string:
            pieces.append("\n")
    return "".join(pieces)


def _token_group_rows(tokens: Sequence[Token]) -> list[str]:
    """Render the selected tokens as sorted, full-value grouped text rows."""
    groups: dict[tuple[str, str], int] = {}
    for token in tokens:
        canonical_data = render_canonical_json(token.data)
        key = (token.type, canonical_data)
        groups[key] = groups.get(key, 0) + 1

    rows: list[str] = []
    for index, ((type_, canonical_data), count) in enumerate(sorted(groups.items())):
        if index == _TOKEN_GROUP_LIMIT:
            remaining = len(groups) - index
            suffix = "group" if remaining == 1 else "groups"
            rows.append(f"… + {remaining} more token {suffix}")
            break
        display_type = (
            ""
            if type_ == _GENERIC_TOKEN_TYPE
            else f" {_truncate_display(type_, _TOKEN_TYPE_LIMIT)}"
        )
        display_data = (
            ""
            if canonical_data == "{}"
            else " "
            + _break_after_commas(_truncate_display(canonical_data, _TOKEN_DATA_LIMIT))
        )
        prefix = "●" if count == 1 else f"● × {count}"
        rows.append(f"{prefix}{display_type}{display_data}")
    return rows


def _marking_lines(
    tokens: Sequence[Token], style: RenderStyle = _DEFAULT_STYLE
) -> list[str]:
    """Bounded complete-token summaries for a selected Place marking."""
    rows = _token_group_rows(tokens)
    if not rows:
        return []
    if not style.html_labels:
        return rows
    html_rows = "".join(
        f'<tr><td align="left"><font point-size="9" color="{_MARKING_COLOR}">'
        f"{_html(row).replace(chr(10), '<br/>')}</font></td></tr>"
        for row in rows
    )
    return [
        f'<table border="1" cellborder="0" cellspacing="0" cellpadding="4" '
        f'style="rounded" color="{_MARKING_COLOR}">{html_rows}</table>'
    ]


def _port_line(port: Port, style: RenderStyle = _DEFAULT_STYLE) -> str:
    type_suffix = "" if port.type == _GENERIC_TOKEN_TYPE else f" · {port.type}"
    if not style.html_labels:
        return f"port {port.direction}{type_suffix}"
    color = _INPUT_PORT_BORDER if port.direction == "input" else _OUTPUT_PORT_BORDER
    html_type_suffix = (
        "" if port.type == _GENERIC_TOKEN_TYPE else f" &#183; {_html(port.type)}"
    )
    return (
        f'<font point-size="9" color="{color}">'
        f"port {port.direction}{html_type_suffix}</font>"
    )


def _predicate_line(predicate: Predicate, style: RenderStyle = _DEFAULT_STYLE) -> str:
    if predicate.cel is not None:
        text = predicate.cel
        if len(text) > _PREDICATE_MAX_CHARS:
            text = text[: _PREDICATE_MAX_CHARS - 1] + "…"
    else:
        text = f"pred: {predicate.handler}"
    if not style.html_labels:
        return text
    return f'<font point-size="9" color="{_MUTED}"><i>{_html(text)}</i></font>'


def _tooltip_attr(description: str | None, style: RenderStyle = _DEFAULT_STYLE) -> str:
    """A leading-comma ``tooltip`` attribute, or empty when no description."""
    if not description or not style.tooltips:
        return ""
    return f', tooltip="{_dot_string(description)}"'


def _label_attr(lines: list[str], style: RenderStyle) -> str:
    """A ``label=`` attribute joining lines per the style's label mode."""
    if not style.html_labels:
        return f'label="{_dot_string(chr(10).join(lines))}"'
    # A Graphviz HTML-like label is EITHER a text run OR a single <table>;
    # top-level text cannot precede a <table>, and `dot` exits 1 on the
    # syntax error (strict backends such as Kroki then reject the graph).
    # The marking badge (`_marking_lines`) is the module's only <table>, so
    # when one is present wrap the whole label in a borderless outer table:
    # each text line becomes its own row above the badge's row. Without a
    # badge the plain <br/>-joined text run is already valid and unchanged.
    if not any(line.startswith("<table") for line in lines):
        return f"label=<{'<br/>'.join(lines)}>"
    cells = "".join(
        f'<tr><td align="center">{line}</td></tr>' for line in lines
    )
    return (
        'label=<<table border="0" cellborder="0" cellspacing="0" '
        f'cellpadding="0">{cells}</table>>'
    )


# ── Node attributes ──────────────────────────────────────────────────────


def _is_fusion(place: Place) -> bool:
    """Whether a place carries the ``annotations.fusion`` tag (CONTEXT.md
    "Fusion place"): one logical place, rendered as a local dashed instance
    at each transition that connects to it."""
    return bool(place.annotations and place.annotations.get("fusion"))


def _place_attrs(
    place: Place,
    tokens: Sequence[Token] = (),
    *,
    label: str | None = None,
    style: RenderStyle = _DEFAULT_STYLE,
    fusion: bool = False,
) -> str:
    """Bracket contents for a place node (ellipse; ports double-ringed;
    ``fusion=True`` styles a local dashed fusion-place instance)."""
    name = label or place.name
    accepted_colors = [type_ for type_ in place.accepts if type_ != _GENERIC_TOKEN_TYPE]
    if style.html_labels:
        lines = [f"<b>{_html(name)}</b>"]
        if accepted_colors:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"&#10216;{_html(', '.join(accepted_colors))}&#10217;</font>"
            )
    else:
        lines = [name]
        if accepted_colors:
            lines.append(f"⟨{', '.join(accepted_colors)}⟩")
    if place.port is not None:
        lines.append(_port_line(place.port, style))
    lines.extend(_marking_lines(tokens, style))
    attrs = f"shape=ellipse, {_label_attr(lines, style)}"
    if fusion:
        # A fusion instance is dashed and muted; the fusion styling replaces
        # the port tint (a fusion port would be pathological anyway).
        attrs += (
            f', class="fusion", style=dashed, color="{_FUSION_BORDER}", '
            f'fontcolor="{_FUSION_FONT}"'
        )
    elif place.port is not None:
        if place.port.direction == "input":
            fill, border = _INPUT_PORT_FILL, _INPUT_PORT_BORDER
        else:
            fill, border = _OUTPUT_PORT_FILL, _OUTPUT_PORT_BORDER
        attrs += f', peripheries=2, style=filled, fillcolor="{fill}", color="{border}"'
    return attrs + _tooltip_attr(place.description, style)


def _transition_attrs(
    transition: Transition,
    *,
    label: str | None = None,
    style: RenderStyle = _DEFAULT_STYLE,
) -> str:
    """Bracket contents for a transition node (filled box; guard purpled)."""
    if style.html_labels:
        lines = [f"<b>{_html(label or transition.name)}</b>"]
        if transition.handler is not None:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"{_html(transition.handler)}()</font>"
            )
        if transition.guard is not None:
            lines.append(
                f'<font point-size="9" color="{_GUARD_BORDER}">'
                f"guard: {_html(transition.guard)}?</font>"
            )
        if transition.timer is not None:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"timer: {_html(transition.timer.cel)}</font>"
            )
        if transition.priority is not None:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"priority {transition.priority}</font>"
            )
    else:
        lines = [label or transition.name]
        if transition.handler is not None:
            lines.append(f"{transition.handler}()")
        if transition.guard is not None:
            lines.append(f"guard: {transition.guard}?")
        if transition.timer is not None:
            lines.append(f"timer: {transition.timer.cel}")
        if transition.priority is not None:
            lines.append(f"priority {transition.priority}")
    attrs = (
        f"shape=box, style=filled, fillcolor={_TRANSITION_FILL}, "
        f"{_label_attr(lines, style)}"
    )
    if transition.guard is not None:
        attrs += f', color="{_GUARD_BORDER}", penwidth=1.6'
    return attrs + _tooltip_attr(transition.description, style)


# ── Edge attributes ──────────────────────────────────────────────────────


def _consume_edge_attrs(
    pattern: ConsumePattern,
    description: str | None,
    style: RenderStyle = _DEFAULT_STYLE,
) -> str:
    """Bracket contents for a consume-arc edge, styled by mode."""
    times = "&#215;" if style.html_labels else "×"
    generic = pattern.type == _GENERIC_TOKEN_TYPE
    weight = f"{pattern.weight} {times} " if pattern.weight != 1 else ""
    type_ = (
        "" if generic else (_html(pattern.type) if style.html_labels else pattern.type)
    )
    if pattern.mode == "inhibit":
        head = "no" if generic else f"no {type_}"
        edge_style = f'arrowhead=odot, color="{_INHIBIT_COLOR}", style=dashed'
        if style.html_labels:
            head = f'<font color="{_INHIBIT_COLOR}">{head}</font>'
    elif pattern.mode == "read":
        head = f"read {weight}{type_}".rstrip()
        edge_style = f'color="{_READ_COLOR}", style=dashed'
        if style.html_labels:
            head = f'<font color="{_READ_COLOR}">{head}</font>'
    else:
        head = f"{weight}{type_}".rstrip()
        edge_style = ""
    if style.html_labels and head:
        head = f'<font point-size="10">{head}</font>'
    lines = [head] if head else []
    if pattern.predicate is not None:
        lines.append(_predicate_line(pattern.predicate, style))
    if pattern.correlate is not None:
        correlate = pattern.correlate
        if len(correlate) > _PREDICATE_MAX_CHARS:
            correlate = correlate[: _PREDICATE_MAX_CHARS - 1] + "…"
        if style.html_labels:
            correlate = (
                f'<font point-size="9" color="{_MUTED}">'
                f"<i>{_html(correlate)}</i></font>"
            )
        lines.append(correlate)
    attrs = _label_attr(lines, style) if lines else ""
    if edge_style:
        attrs += f", {edge_style}" if attrs else edge_style
    tooltip = _tooltip_attr(description, style)
    return attrs + tooltip if attrs else tooltip.removeprefix(", ")


def _produce_edge_attrs(
    template: ProduceTemplate,
    description: str | None,
    style: RenderStyle = _DEFAULT_STYLE,
) -> str:
    """Bracket contents for a produce-arc edge."""
    generic = template.type == _GENERIC_TOKEN_TYPE
    if style.html_labels:
        lines = (
            [] if generic else [f'<font point-size="10">{_html(template.type)}</font>']
        )
        if template.data is not None:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"<i>+ data {_html(json.dumps(template.data))}</i></font>"
            )
        elif template.cel is not None:
            lines.append(
                f'<font point-size="9" color="{_MUTED}">'
                f"<i>+ data cel {_html(template.cel)}</i></font>"
            )
    else:
        lines = [] if generic else [template.type]
        if template.data is not None:
            lines.append(f"+ data {json.dumps(template.data)}")
        elif template.cel is not None:
            lines.append(f"+ data cel {template.cel}")
    attrs = _label_attr(lines, style) if lines else ""
    tooltip = _tooltip_attr(description, style)
    return attrs + tooltip if attrs else tooltip.removeprefix(", ")


_WIRE_ATTRS = f'penwidth=2.2, color="{_WIRE_COLOR}"'


# ── Graph assembly ───────────────────────────────────────────────────────

_GRAPH_DEFAULTS = [
    '    node [fontname="Helvetica"];',
    '    edge [fontname="Helvetica", fontsize=10];',
]


def _arc_edge(
    arc: Arc,
    indent: str = "    ",
    prefix: str = "",
    *,
    style: RenderStyle = _DEFAULT_STYLE,
    fusion: frozenset[str] | set[str] = frozenset(),
) -> str:
    """One DOT edge statement for an arc (consume or produce).

    An endpoint naming a place in ``fusion`` is rewritten to that place's
    local instance at the arc's transition (``place@transition``).
    """
    if arc.consume is not None:
        from_place = arc.from_place
        if from_place in fusion:
            from_place = f"{from_place}@{arc.to_transition}"
        source = _quote(f"{prefix}{from_place}")
        target = _quote(f"{prefix}{arc.to_transition}")
        attrs = _consume_edge_attrs(arc.consume, arc.description, style)
    else:
        assert arc.produce is not None
        to_place = arc.to_place
        if to_place in fusion:
            to_place = f"{to_place}@{arc.from_transition}"
        source = _quote(f"{prefix}{arc.from_transition}")
        target = _quote(f"{prefix}{to_place}")
        attrs = _produce_edge_attrs(arc.produce, arc.description, style)
    if attrs:
        return f"{indent}{source} -> {target} [{attrs}];"
    return f"{indent}{source} -> {target};"


def _fusion_instances(net: Net, fusion: set[str]) -> list[tuple[str, str]]:
    """(place, transition) pairs needing a local fusion instance, in arc
    order, deduplicated (a transition touching the place via several arcs —
    e.g. inhibit + produce — shares one instance)."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for arc in net.arcs:
        if arc.from_place in fusion:
            assert arc.to_transition is not None
            pair = (arc.from_place, arc.to_transition)
        elif arc.to_place in fusion:
            assert arc.from_transition is not None
            pair = (arc.to_place, arc.from_transition)
        else:
            continue
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _net_body(
    net: Net,
    *,
    indent: str = "    ",
    prefix: str = "",
    include_marking: bool = True,
    marking: Marking | None = None,
    style: RenderStyle = _DEFAULT_STYLE,
    render_fusion: bool = True,
) -> list[str]:
    """Node and edge statements for one net, optionally name-prefixed."""
    selected_marking = (
        (marking if marking is not None else net.initial_marking)
        if include_marking
        else None
    )
    fusion: set[str] = (
        {p.name for p in net.places if _is_fusion(p)} if render_fusion else set()
    )

    def _tokens(place: Place) -> Sequence[Token]:
        if selected_marking is not None and place.name in selected_marking:
            return selected_marking[place.name]
        return ()

    lines: list[str] = []
    for place in net.places:
        if place.name in fusion:
            continue  # rendered as local instances below
        node = _quote(f"{prefix}{place.name}")
        lines.append(
            f"{indent}{node} [{_place_attrs(place, _tokens(place), style=style)}];"
        )
    lines.append("")
    for transition in net.transitions:
        node = _quote(f"{prefix}{transition.name}")
        lines.append(f"{indent}{node} [{_transition_attrs(transition, style=style)}];")
    lines.append("")
    if fusion:
        place_by_name = {p.name: p for p in net.places}
        for place_name, transition_name in _fusion_instances(net, fusion):
            place = place_by_name[place_name]
            node = _quote(f"{prefix}{place_name}@{transition_name}")
            attrs = _place_attrs(
                place,
                _tokens(place),
                label=place_name,
                style=style,
                fusion=True,
            )
            lines.append(f"{indent}{node} [{attrs}];")
        lines.append("")
    for arc in net.arcs:
        lines.append(_arc_edge(arc, indent, prefix, style=style, fusion=fusion))
    return lines


def net_to_dot(
    net: Net,
    *,
    rankdir: str = "TB",
    include_marking: bool = True,
    marking: Marking | None = None,
    style: RenderStyle = _DEFAULT_STYLE,
    render_fusion: bool = True,
) -> str:
    """Render a standalone net, optionally replacing its default marking."""
    lines = [
        "digraph net {",
        f"    rankdir={rankdir};",
    ]
    if net.name != _UNNAMED_NET_NAME:
        if style.html_labels:
            title = f"label=<<b>{_html(net.name)}</b>>; labelloc=t;"
        else:
            title = f'label="{_dot_string(net.name)}"; labelloc=t;'
        lines.append(f"    {title}")
    lines.extend(
        [
            *_GRAPH_DEFAULTS,
            "",
            *_net_body(
                net,
                include_marking=include_marking,
                marking=marking,
                style=style,
                render_fusion=render_fusion,
            ),
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def composition_to_dot(
    composition: Composition,
    *,
    rankdir: str = "TB",
    include_marking: bool = True,
    style: RenderStyle = _DEFAULT_STYLE,
    render_fusion: bool = True,
) -> str:
    """Render a composition: one cluster per aliased net, wires between ports.

    Requires ``composition.parsed_nets`` (as produced by
    :func:`velocitron.parser.parse_composition`).
    """
    parsed = composition.parsed_nets
    if parsed is None:
        raise ValueError(
            "composition has no parsed_nets; parse it via parse_composition"
        )
    lines = [
        "digraph composition {",
        f"    rankdir={rankdir};",
        "    compound=true;",
        *_GRAPH_DEFAULTS,
        "",
    ]
    for index, (alias, net) in enumerate(parsed.items()):
        if style.html_labels:
            title = _html(alias)
            if net.name != alias:
                title += (
                    f'<br/><font point-size="9" color="{_MUTED}">'
                    f"{_html(net.name)}</font>"
                )
            title_stmt = f"label=<<b>{title}</b>>; labeljust=l;"
        else:
            text = alias if net.name == alias else f"{alias}\n{net.name}"
            title_stmt = f'label="{_dot_string(text)}"; labeljust=l;'
        lines.append(f"    subgraph cluster_{index} {{")
        lines.append(f"        {title_stmt}")
        lines.append('        style=filled; fillcolor="#f7f7f7"; color="#bbbbbb";')
        if net.description and style.tooltips:
            lines.append(f'        tooltip="{_dot_string(net.description)}";')
        lines.extend(
            _net_body(
                net,
                indent="        ",
                prefix=f"{alias}.",
                include_marking=include_marking,
                style=style,
                render_fusion=render_fusion,
            )
        )
        lines.append("    }")
        lines.append("")
    for wire in composition.wires:
        source = _quote(f"{wire.from_net}.{wire.from_port}")
        target = _quote(f"{wire.to_net}.{wire.to_port}")
        lines.append(f"    {source} -> {target} [{_WIRE_ATTRS}];")
    lines.append("}")
    return "\n".join(lines) + "\n"


def composition_ports_dot(
    composition: Composition,
    *,
    rankdir: str = "TB",
    style: RenderStyle = _DEFAULT_STYLE,
) -> str:
    """Render a composition as a compact wiring diagram: ports only.

    Each aliased net collapses to a cluster holding just its boundary ports
    (styled as in the full view); wires join them as usual. Internal places,
    transitions, arcs, and markings are omitted — this is the view for
    reading the cross-net topology, not any net's internals. Unwired ports
    still render: they are the composition's own boundary.

    Requires ``composition.parsed_nets`` (as produced by
    :func:`velocitron.parser.parse_composition`).
    """
    parsed = composition.parsed_nets
    if parsed is None:
        raise ValueError(
            "composition has no parsed_nets; parse it via parse_composition"
        )
    lines = [
        "digraph composition_ports {",
        f"    rankdir={rankdir};",
        *_GRAPH_DEFAULTS,
        "",
    ]
    for index, (alias, net) in enumerate(parsed.items()):
        if style.html_labels:
            title = _html(alias)
            if net.name != alias:
                title += (
                    f'<br/><font point-size="9" color="{_MUTED}">'
                    f"{_html(net.name)}</font>"
                )
            title_stmt = f"label=<<b>{title}</b>>; labeljust=l;"
        else:
            text = alias if net.name == alias else f"{alias}\n{net.name}"
            title_stmt = f'label="{_dot_string(text)}"; labeljust=l;'
        lines.append(f"    subgraph cluster_{index} {{")
        lines.append(f"        {title_stmt}")
        lines.append('        style=filled; fillcolor="#f7f7f7"; color="#bbbbbb";')
        if net.description and style.tooltips:
            lines.append(f'        tooltip="{_dot_string(net.description)}";')
        for place in net.places:
            if place.port is None:
                continue
            node = _quote(f"{alias}.{place.name}")
            lines.append(f"        {node} [{_place_attrs(place, style=style)}];")
        lines.append("    }")
        lines.append("")
    for wire in composition.wires:
        source = _quote(f"{wire.from_net}.{wire.from_port}")
        target = _quote(f"{wire.to_net}.{wire.to_port}")
        lines.append(f"    {source} -> {target} [{_WIRE_ATTRS}];")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── Legend ───────────────────────────────────────────────────────────────


def legend_dot(*, rankdir: str = "LR") -> str:
    """A self-contained legend digraph exercising every visual convention.

    Built through the same attribute helpers as real renders, so the legend
    cannot drift from the styles it documents.
    """
    place = Place(name="place", accepts=["colorA", "colorB"])
    marked = Place(name="marked place", accepts=["colorA"])
    port_in = Place(
        name="input port",
        accepts=["colorA"],
        port=Port(direction="input", type="colorA"),
    )
    port_out = Place(
        name="output port",
        accepts=["colorA"],
        port=Port(direction="output", type="colorA"),
    )
    fusion_place = Place(
        name="fusion place", accepts=["colorA"], annotations={"fusion": True}
    )
    plain_t = Transition(name="transition", handler="handler_ref")
    guarded_t = Transition(
        name="guarded transition",
        handler="handler_ref",
        guard="guard_ref",
        priority=5,
    )
    tokens = [Token(type="colorA", data={}), Token(type="colorA", data={})]
    consume = ConsumePattern(type="colorA", predicate=None, mode="consume")
    weighted = ConsumePattern(type="colorA", predicate=None, mode="consume", weight=2)
    cel = ConsumePattern(
        type="colorA",
        predicate=Predicate(cel='field == "value"', handler=None),
        mode="consume",
    )
    named = ConsumePattern(
        type="colorA",
        predicate=Predicate(cel=None, handler="predicate_ref"),
        mode="consume",
    )
    read = ConsumePattern(type="colorA", predicate=None, mode="read")
    inhibit = ConsumePattern(type="colorA", predicate=None, mode="inhibit")
    produce = ProduceTemplate(type="colorA", destination="ignored")
    produce_data = ProduceTemplate(
        type="colorA", destination="ignored", data={"fixed": True}
    )
    produce_cel = ProduceTemplate(
        type="colorA", destination="ignored", cel='{"n": field - 1}'
    )

    edges = [
        ("p1", "t1", _consume_edge_attrs(consume, None), "consume: removed on fire"),
        ("p2", "t2", _consume_edge_attrs(weighted, None), "weight 2: two tokens"),
        ("p3", "t3", _consume_edge_attrs(cel, None), "inline CEL predicate"),
        ("p4", "t4", _consume_edge_attrs(named, None), "named predicate handler"),
        ("p5", "t5", _consume_edge_attrs(read, None), "read: gate, don't consume"),
        ("p6", "t6", _consume_edge_attrs(inhibit, None), "inhibit: require absence"),
        ("t7", "p7", _produce_edge_attrs(produce, None), "produce: routing contract"),
        ("t8", "p8", _produce_edge_attrs(produce_data, None), "literal data template"),
        ("t9", "p9", _produce_edge_attrs(produce_cel, None), "computed data template"),
        ("p9", "p10", _WIRE_ATTRS, "composition wire (port to port)"),
    ]
    lines = [
        "digraph legend {",
        f"    rankdir={rankdir};",
        "    label=<<b>velocitron net legend</b>>; labelloc=t;",
        *_GRAPH_DEFAULTS,
        "",
        f"    place [{_place_attrs(place)}];",
        f"    marked [{_place_attrs(marked, tokens, label='marked place')}];",
        f"    port_in [{_place_attrs(port_in, label='input port')}];",
        f"    port_out [{_place_attrs(port_out, label='output port')}];",
        f"    fusion [{_place_attrs(fusion_place, label='fusion place (local instance)', fusion=True)}];",
        f"    transition [{_transition_attrs(plain_t)}];",
        f"    guarded [{_transition_attrs(guarded_t, label='guarded transition')}];",
        "",
    ]
    for index, (src, dst, attrs, caption) in enumerate(edges):
        for node in (src, dst):
            shape = "box" if node.startswith("t") else "ellipse"
            fill = (
                f", style=filled, fillcolor={_TRANSITION_FILL}"
                if shape == "box"
                else ""
            )
            lines.append(
                f'    {node}_{index} [shape={shape}, label=""{fill}, '
                f"width=0.35, height=0.28, fixedsize=true];"
            )
        lines.append(f"    {src}_{index} -> {dst}_{index} [{attrs}];")
        lines.append(
            f"    c_{index} [shape=plaintext, label=<"
            f'<font point-size="9" color="{_MUTED}">{_html(caption)}</font>>];'
        )
        lines.append(f"    {dst}_{index} -> c_{index} [style=invis];")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────


def _decode_json_object(text: str, *, context: str) -> dict[str, Any]:
    """Decode strict JSON, rejecting duplicate keys at every object depth."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant {value!r}")

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _VizError(
                    f"invalid {context}: duplicate object key {_json_quote(key)}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_constant=reject_constant,
        )
    except _VizError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise _VizError(f"invalid {context}") from error
    if not isinstance(value, dict):
        raise _VizError(f"invalid {context}: expected a JSON object")
    return cast(dict[str, Any], value)


def _load_document(
    path: Path,
) -> tuple[str, Net | Composition, dict[str, Any]]:
    """Load and validate a JSON or DSL net/composition document."""
    try:
        if path.suffix.lower() == ".petrinet":
            doc = compile_petrinet_text(read_petrinet_text(path), str(path))
        else:
            doc = _decode_json_object(path.read_text(), context="JSON source")
    except OSError as error:
        raise _SourceError(f"could not read source {_json_quote(str(path))}") from error
    except PetrinetDslError as error:
        raise _SourceError(f"invalid DSL source {_json_quote(str(path))}") from error
    except _VizError as error:
        raise _SourceError(f"invalid JSON source {_json_quote(str(path))}") from error

    try:
        if "places" in doc:
            return "net", parse_net(doc), doc
        if "nets" in doc:

            def load_ref(ref: str) -> dict[str, Any] | Path | str:
                ref_path = Path(ref)
                if not ref_path.is_absolute():
                    ref_path = path.parent / ref_path
                if ref_path.suffix.lower() == ".json":
                    return ref_path
                if ref_path.suffix.lower() == ".petrinet":
                    return compile_petrinet_text(
                        read_petrinet_text(ref_path), str(ref_path)
                    )
                raise ValueError(f"unsupported composition net reference: {ref!r}")

            return (
                "composition",
                parse_composition(doc, origin=path.parent, net_loader=load_ref),
                doc,
            )
    except (NetValidationError, OSError, PetrinetDslError, ValueError) as error:
        raise _SourceError(f"invalid source {_json_quote(str(path))}") from error
    raise _SourceError(f"unsupported source document {_json_quote(str(path))}")


def _validate_selected_marking(raw_marking: Mapping[str, Any], net: Net) -> Marking:
    """Validate one named or inline marking against the standalone Net."""
    places = {place.name: place for place in net.places}
    parsed: dict[str, list[Token]] = {}
    for place_name in sorted(raw_marking):
        raw_tokens = raw_marking[place_name]
        if place_name not in places:
            raise _VizError(f"invalid marking: unknown place {_json_quote(place_name)}")
        if not isinstance(raw_tokens, list):
            raise _VizError(
                f"invalid marking at place {_json_quote(place_name)}: "
                "tokens must be an array"
            )
        raw_tokens = cast(list[Any], raw_tokens)
        tokens: list[Token] = []
        for index, raw_token in enumerate(raw_tokens):
            prefix = (
                f"invalid marking at place {_json_quote(place_name)}, token {index}: "
            )
            if not isinstance(raw_token, dict):
                raise _VizError(prefix + "token must be an object")
            raw_token = cast(dict[str, Any], raw_token)
            if set(raw_token) != {"type", "data"}:
                raise _VizError(prefix + "token must contain exactly type and data")
            type_ = raw_token["type"]
            data = raw_token["data"]
            if not isinstance(type_, str) or not type_:
                raise _VizError(prefix + "type must be a nonempty string")
            if not isinstance(data, dict):
                raise _VizError(prefix + "data must be an object")
            data = cast(dict[str, Any], data)
            if type_ not in places[place_name].accepts:
                colors = ", ".join(
                    _json_quote(color) for color in sorted(places[place_name].accepts)
                )
                raise _VizError(
                    prefix
                    + f"type {_json_quote(type_)} is not accepted "
                    + f"(accepted colors: {colors})"
                )
            tokens.append(Token(type=type_, data=data))
        parsed[place_name] = tokens
    return Marking(parsed)


def _json_object_fields(value: Any) -> Mapping[str, Any]:
    """Return JSON object fields, treating every non-object as absent."""
    return cast(Mapping[str, Any], value) if isinstance(value, dict) else {}


def _named_marking(raw_document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """Get an authored named marking without ever falling back to initial."""
    annotations = _json_object_fields(raw_document.get("annotations"))
    payload = _json_object_fields(annotations.get("petrinet.dsl/v1"))
    markings = _json_object_fields(payload.get("markings"))
    available = sorted(markings)
    if name not in markings:
        rendered = (
            ", ".join(_json_quote(candidate) for candidate in available) or "none"
        )
        raise _VizError(
            f"unknown named marking {_json_quote(name)}; available markings: {rendered}"
        )
    marking: Any = markings[name]
    if not isinstance(marking, dict):
        raise _VizError(f"invalid named marking {_json_quote(name)}")
    return cast(dict[str, Any], marking)


def _selected_view_dot(dot: str, raw: dict[str, Any], view_name: str) -> str:
    """Apply inert authored geometry to an already rendered semantic graph."""
    try:
        annotations = cast(dict[str, Any], raw["annotations"])
        payload = cast(dict[str, Any], annotations["petrinet.dsl/v1"])
        views = cast(dict[str, Any], payload["views"])
        view = cast(dict[str, Any], views[view_name])
        handles = cast(dict[str, Any], payload["arcHandles"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"unknown view {view_name!r}") from error
    positions = view.get("positions")
    routes = view.get("routes")
    if not isinstance(positions, dict) or not isinstance(routes, dict):
        raise ValueError(f"invalid view {view_name!r}")
    position_values = cast(dict[str, Any], positions)
    route_values = cast(dict[str, Any], routes)

    lines = dot.splitlines()
    for subject, raw_position in position_values.items():
        position_value = (
            cast(dict[str, Any], raw_position)
            if isinstance(raw_position, dict)
            else None
        )
        if (
            position_value is None
            or set(position_value) != {"x", "y"}
            or any(
                not isinstance(position_value.get(axis), (int, float))
                or isinstance(position_value.get(axis), bool)
                or not math.isfinite(position_value[axis])
                for axis in ("x", "y")
            )
        ):
            raise ValueError(f"invalid position in view {view_name!r}")
        position = cast(dict[str, float | int], position_value)
        subject_type, name = subject.split(":", 1)
        node_prefixes = (
            (f'    "{name}" [', f'    "{name}@')
            if subject_type == "place"
            else (f'    "{name}" [',)
        )
        rendered = f"{position['x']},{position['y']}!"
        for index, line in enumerate(lines):
            if line.startswith(node_prefixes) and line.endswith("];"):
                lines[index] = f'{line[:-2]}, pos="{rendered}"];'

    route_indexes: dict[int, dict[str, Any]] = {}
    for handle, raw_route in route_values.items():
        if not isinstance(raw_route, dict):
            raise ValueError(f"invalid route in view {view_name!r}")
        route = cast(dict[str, Any], raw_route)
        try:
            handle_value = cast(dict[str, Any], handles[handle])
            handle_index = handle_value["index"]
            if not isinstance(handle_index, int) or isinstance(handle_index, bool):
                raise TypeError
            route_indexes[handle_index] = route
        except (KeyError, TypeError) as error:
            raise ValueError(
                f"view {view_name!r} route references unknown arc handle {handle!r}"
            ) from error
    edge_index = 0
    for index, line in enumerate(lines):
        if " -> " not in line:
            continue
        route = route_indexes.get(edge_index)
        if route is not None:
            raw_points = route.get("points")
            if (
                route.get("style") != "orthogonal"
                or not isinstance(raw_points, list)
                or not raw_points
            ):
                raise ValueError(f"invalid route in view {view_name!r}")
            points: list[dict[str, float | int]] = []
            for raw_point in cast(list[Any], raw_points):
                point_value = (
                    cast(dict[str, Any], raw_point)
                    if isinstance(raw_point, dict)
                    else None
                )
                if (
                    point_value is None
                    or set(point_value) != {"x", "y"}
                    or any(
                        not isinstance(point_value.get(axis), (int, float))
                        or isinstance(point_value.get(axis), bool)
                        or not math.isfinite(point_value[axis])
                        for axis in ("x", "y")
                    )
                ):
                    raise ValueError(f"invalid route in view {view_name!r}")
                points.append(cast(dict[str, float | int], point_value))
            rendered = " ".join(f"{point['x']},{point['y']}" for point in points)
            lines[index] = f'{line[:-2]}, pos="{rendered}"];'
        edge_index += 1
    lines.insert(2, "    splines=ortho;")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: render a net or composition document to Graphviz DOT."""
    parser = argparse.ArgumentParser(
        prog="velocitron-viz",
        description=(
            "Render a velocitron Net or composition JSON/.petrinet document "
            "as a Graphviz DOT digraph (to stdout or a .dot file)."
        ),
        epilog=(
            "Marking selection (standalone Net sources only):\n"
            "  Default: render the authored initial marking. If none is declared, "
            "render an empty marking.\n"
            "  --marking NAME       Render authored named marking NAME. "
            "NAME is case-sensitive.\n"
            "  --marking-json JSON  Render one inline JSON marking object. "
            "No file or stdin indirection.\n"
            "  --no-marking         Omit marking detail. Mutually exclusive with "
            "--marking and --marking-json.\n"
            "\n"
            "Examples:\n"
            "  velocitron-viz design.petrinet\n"
            "  velocitron-viz design.petrinet --marking queued\n"
            "  velocitron-viz design.petrinet --marking-json "
            '\'{"request_in":[{"type":"request","data":{"id":"r-17"}}]}\'\n'
            "  velocitron-viz design.petrinet --no-marking\n"
            "  velocitron-viz design.petrinet --direction=lr"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="Path to a Net or composition JSON/.petrinet document.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write DOT here instead of stdout.",
    )
    parser.add_argument(
        "--direction",
        "--rankdir",
        dest="rankdir",
        type=str.upper,
        choices=["TB", "LR", "BT", "RL"],
        metavar="{tb,lr,bt,rl}",
        help="Graph direction (default: tb).",
    )
    parser.add_argument(
        "--merged",
        action="store_true",
        help=(
            "For a composition: render the single merged net "
            "(place-fusion realization) instead of clusters + wires."
        ),
    )
    parser.add_argument(
        "--ports-only",
        action="store_true",
        help=(
            "For a composition: collapse each net to just its boundary "
            "ports — a compact wiring diagram of the cross-net topology."
        ),
    )
    marking_options = parser.add_mutually_exclusive_group()
    marking_options.add_argument(
        "--marking",
        metavar="NAME",
        help="Render authored named marking NAME (case-sensitive; standalone Nets only).",
    )
    marking_options.add_argument(
        "--marking-json",
        metavar="JSON",
        help="Render one inline JSON marking object (no file or stdin indirection).",
    )
    marking_options.add_argument(
        "--no-marking",
        action="store_true",
        help=(
            "Omit marking detail. Mutually exclusive with --marking and --marking-json."
        ),
    )
    parser.add_argument(
        "--plain-labels",
        action="store_true",
        help=(
            "Plain double-quoted labels instead of HTML-like labels "
            "(same content, readable DOT source)."
        ),
    )
    parser.add_argument(
        "--no-tooltips",
        action="store_true",
        help="Omit description tooltips from nodes and edges.",
    )
    parser.add_argument(
        "--doc",
        action="store_true",
        help=(
            "Markdown-embedding preset: --plain-labels --no-tooltips. "
            "Pair with --fence to emit a ready-to-paste code fence."
        ),
    )
    parser.add_argument(
        "--fence",
        action="store_true",
        help="Wrap the output in a ```dot markdown code fence.",
    )
    parser.add_argument(
        "--no-fusion",
        action="store_true",
        help=(
            "Render fusion-annotated places (annotations.fusion) as a single "
            "node instead of per-transition local instances."
        ),
    )
    parser.add_argument(
        "--view",
        help="Apply a named view from annotations['petrinet.dsl/v1'].views.",
    )
    parser.add_argument(
        "--legend",
        action="store_true",
        help="Emit the feature legend instead of rendering a document.",
    )
    args = parser.parse_args(argv)

    style = RenderStyle(
        html_labels=not (args.plain_labels or args.doc),
        tooltips=not (args.no_tooltips or args.doc),
    )
    render_fusion = not args.no_fusion

    has_marking_selector = args.marking is not None or args.marking_json is not None
    if args.legend:
        if has_marking_selector:
            parser.error("--marking and --marking-json are invalid with --legend")
        dot = legend_dot(rankdir=args.rankdir or "LR")
    elif args.source is None:
        parser.error("a source document is required unless --legend is given")
    else:
        try:
            kind, document, raw_document = _load_document(args.source)
            include_marking = not args.no_marking
            if args.merged and args.ports_only:
                parser.error("--merged and --ports-only are mutually exclusive")
            if kind == "net":
                if args.merged:
                    parser.error("--merged only applies to composition documents")
                if args.ports_only:
                    parser.error("--ports-only only applies to composition documents")
                assert isinstance(document, Net)
                selected_marking: Marking | None = None
                if args.marking is not None:
                    selected_marking = _validate_selected_marking(
                        _named_marking(raw_document, args.marking), document
                    )
                elif args.marking_json is not None:
                    selected_marking = _validate_selected_marking(
                        _decode_json_object(
                            args.marking_json,
                            context="inline marking JSON",
                        ),
                        document,
                    )
                dot = net_to_dot(
                    document,
                    rankdir=args.rankdir or "TB",
                    include_marking=include_marking,
                    marking=selected_marking,
                    style=style,
                    render_fusion=render_fusion,
                )
                if args.view is not None:
                    dot = _selected_view_dot(dot, raw_document, args.view)
            else:
                assert isinstance(document, Composition)
                if has_marking_selector:
                    parser.error(
                        "--marking and --marking-json apply only to standalone Nets"
                    )
                if args.merged:
                    dot = net_to_dot(
                        merge_composition(document),
                        rankdir=args.rankdir or "TB",
                        include_marking=include_marking,
                        style=style,
                        render_fusion=render_fusion,
                    )
                elif args.ports_only:
                    dot = composition_ports_dot(
                        document,
                        rankdir=args.rankdir or "TB",
                        style=style,
                    )
                else:
                    dot = composition_to_dot(
                        document,
                        rankdir=args.rankdir or "TB",
                        include_marking=include_marking,
                        style=style,
                        render_fusion=render_fusion,
                    )
        except _VizError as error:
            sys.stderr.write(f"velocitron-viz: error: {error}\n")
            return 1
        except ValueError as error:
            sys.stderr.write(f"velocitron-viz: error: {error}\n")
            return 1

    if args.fence:
        dot = f"```dot\n{dot}```\n"

    try:
        if args.output is not None:
            args.output.write_text(dot, encoding="utf-8")
        else:
            sys.stdout.write(dot)
    except OSError:
        destination = args.output if args.output is not None else Path("<stdout>")
        sys.stderr.write(
            f"velocitron-viz: error: could not write output "
            f"{_json_quote(str(destination))}\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
