"""Public Petri-net DSL loading, compilation, and canonical emission APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import math
from pathlib import Path
import re
from typing import Any, cast

from velocitron.parser import parse_net
from velocitron.schema import Net

from .compiler import (
    SourceInput,
    json_values_equal,
    lower_petrinet_text,
    resolve_contribution_ir,
)
from .diagnostics import Diagnostic, PetrinetDslError, SourcePosition, SourceSpan


def _aggregation_error(message: str, span: Mapping[str, Any]) -> PetrinetDslError:
    start = cast(Mapping[str, int], span["start"])
    end = cast(Mapping[str, int], span["end"])
    return PetrinetDslError(
        Diagnostic(
            "PN200",
            message,
            SourceSpan(
                str(span["source"]),
                SourcePosition(start["byteOffset"], start["line"], start["column"]),
                SourcePosition(end["byteOffset"], end["line"], end["column"]),
            ),
        )
    )


def compile_petrinet_sources(
    sources: Sequence[SourceInput],
    *,
    net_loader: Callable[[str], Mapping[str, Any] | Path | str] | None = None,
    origin: Path | None = None,
) -> dict[str, Any]:
    """Lower same-namespace sources in order, then resolve their aggregate once."""
    if not sources:
        raise ValueError("at least one SourceInput is required")
    if len(sources) == 1:
        source = sources[0]
        return resolve_contribution_ir(
            lower_petrinet_text(source.text, source.source_id),
            net_loader=net_loader,
            origin=origin,
        )

    seen_source_ids: set[str] = set()
    for source in sources:
        if source.source_id in seen_source_ids:
            position = {"byteOffset": 0, "line": 1, "column": 1}
            raise _aggregation_error(
                f"duplicate aggregate source id {source.source_id!r}",
                {
                    "source": source.source_id,
                    "start": position,
                    "end": position,
                },
            )
        seen_source_ids.add(source.source_id)

    lowered = [lower_petrinet_text(source.text, source.source_id) for source in sources]
    document_kind = lowered[0]["documentKind"]
    header_kind = (
        "document.net-header"
        if document_kind == "net"
        else "document.composition-header"
    )
    header_name_member = "name" if document_kind == "net" else "namespace"
    first_contributions = cast(list[dict[str, Any]], lowered[0]["contributions"])
    first_header = next(
        (
            contribution
            for contribution in first_contributions
            if contribution["kind"] == header_kind
        ),
        None,
    )
    header_name = (
        "unnamed" if first_header is None else first_header["value"][header_name_member]
    )

    aggregate_contributions: list[dict[str, Any]] = []
    for document in lowered:
        contributions = cast(list[dict[str, Any]], document["contributions"])
        position = {"byteOffset": 0, "line": 1, "column": 1}
        fallback_span: Mapping[str, Any] = {
            "source": cast(Mapping[str, str], document["document"])["id"],
            "start": position,
            "end": position,
        }
        if document["documentKind"] != document_kind:
            raise _aggregation_error(
                "aggregate sources must have the same document kind",
                cast(Mapping[str, Any], contributions[0]["span"])
                if contributions
                else fallback_span,
            )
        header = next(
            (
                contribution
                for contribution in contributions
                if contribution["kind"] == header_kind
            ),
            None,
        )
        document_name = (
            "unnamed" if header is None else header["value"][header_name_member]
        )
        if document_name != header_name:
            raise _aggregation_error(
                "aggregate sources must have the same decoded header name",
                cast(Mapping[str, Any], header["span"])
                if header is not None
                else (
                    cast(Mapping[str, Any], contributions[0]["span"])
                    if contributions
                    else fallback_span
                ),
            )
        for contribution in contributions:
            contribution["ordinal"] = len(aggregate_contributions)
            aggregate_contributions.append(contribution)

    aggregate = dict(lowered[0])
    aggregate["contributions"] = aggregate_contributions
    return resolve_contribution_ir(
        aggregate,
        source_ids=[source.source_id for source in sources],
        net_loader=net_loader,
        origin=origin,
    )


def compile_petrinet_text(
    text: str,
    source_name: str = "<memory>",
    *,
    net_loader: Callable[[str], Mapping[str, Any] | Path | str] | None = None,
    origin: Path | None = None,
) -> dict[str, Any]:
    """Compile DSL text to canonical net- or composition-shaped core JSON."""
    source_path = Path(source_name)
    source_id = source_path.name if source_path.is_absolute() else source_name
    return compile_petrinet_sources(
        [SourceInput(source_id, text)],
        net_loader=net_loader,
        origin=origin,
    )


def parse_petrinet_text(text: str, source_name: str = "<memory>") -> Net:
    """Compile DSL text then delegate all net semantics to :func:`parse_net`."""
    return parse_net(compile_petrinet_text(text, source_name))


def read_petrinet_text(path: Path | str) -> str:
    """Read UTF-8 DSL source without translating its accepted CRLF bytes."""
    with Path(path).open("r", encoding="utf-8", newline="") as source:
        return source.read()


def load_petrinet(path: Path | str) -> Net:
    """Read strict UTF-8 DSL text from a path and parse it into a validated Net."""
    source = Path(path)
    return parse_petrinet_text(read_petrinet_text(source), str(source))


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = frozenset(
    {
        "net",
        "handler",
        "guard",
        "order",
        "data",
        "predicate",
        "accepts",
        "capacityPerColorKey",
        "weight",
        "cel",
        "correlate",
        "marking",
        "initial",
        "true",
        "false",
        "null",
    }
)
_ALIAS_RESERVED = _RESERVED | {
    "composition",
    "use",
    "as",
    "wire",
    "port",
    "input",
    "output",
    "timer",
    "clock",
    "bind",
    "priority",
}


def _name(name: str) -> str:
    return (
        name
        if _IDENTIFIER.fullmatch(name) and name not in _RESERVED
        else render_canonical_json(name)
    )


def _valid_capacity_key(value: object) -> bool:
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, list) or not value:
        return False
    return all(
        isinstance(part, str) and bool(part) for part in cast(list[object], value)
    )


def _json_string(value: str) -> str:
    """Render one Unicode-scalar JSON string without ASCII escaping."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("JSON strings must contain only Unicode scalars") from error
    return json.dumps(value, ensure_ascii=False)


def _binary64(value: float) -> str:
    """Render a finite binary64 using the RFC 8785/ECMAScript thresholds."""
    if not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if value == 0:
        return "0"

    negative = value < 0
    rendered = repr(-value if negative else value).lower()
    coefficient, separator, raw_exponent = rendered.partition("e")
    integer, _, fraction = coefficient.partition(".")
    significand = (integer + fraction).lstrip("0")
    exponent = (int(raw_exponent) if separator else 0) - len(fraction)

    digits = significand.rstrip("0")
    exponent += len(significand) - len(digits)
    adjusted = exponent + len(digits) - 1

    if -6 <= adjusted < 21:
        decimal_at = len(digits) + exponent
        if decimal_at <= 0:
            magnitude = "0." + ("0" * -decimal_at) + digits
        elif decimal_at >= len(digits):
            magnitude = digits + ("0" * (decimal_at - len(digits)))
        else:
            magnitude = digits[:decimal_at] + "." + digits[decimal_at:]
    else:
        magnitude = digits[0]
        if len(digits) > 1:
            magnitude += "." + digits[1:]
        magnitude += "e" + str(adjusted)
    return ("-" if negative else "") + magnitude


def render_canonical_json(value: Any, *, indent: int | None = None) -> str:
    """Render JSON with one RFC 8785 primitive representation.

    Arrays retain semantic order. Object members use RFC 8785's UTF-16 sort
    order; indentation changes whitespace only, never primitive spellings.
    """
    if indent is not None and indent < 0:
        raise ValueError("indent must be nonnegative")

    def render(item: Any, level: int) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int):
            if abs(item) > 9_007_199_254_740_991:
                raise ValueError("JSON integer exceeds the safe IEEE-754 range")
            return str(item)
        if isinstance(item, float):
            return _binary64(item)
        if isinstance(item, str):
            return _json_string(item)
        if isinstance(item, list):
            values = [render(child, level + 1) for child in cast(list[Any], item)]
            if not values:
                return "[]"
            if indent is None:
                return "[" + ",".join(values) + "]"
            padding = " " * (indent * (level + 1))
            closing = " " * (indent * level)
            return (
                "[\n" + padding + (",\n" + padding).join(values) + "\n" + closing + "]"
            )
        if isinstance(item, Mapping):
            raw_object = cast(Mapping[Any, Any], item)
            if not all(isinstance(key, str) for key in raw_object):
                raise TypeError("JSON object keys must be strings")
            object_value = cast(Mapping[str, Any], raw_object)
            try:
                keys = sorted(object_value, key=lambda key: key.encode("utf-16be"))
            except UnicodeEncodeError as error:
                raise ValueError(
                    "JSON object keys must contain only Unicode scalars"
                ) from error
            members = [
                _json_string(key)
                + (":" if indent is None else ": ")
                + render(object_value[key], level + 1)
                for key in keys
            ]
            if not members:
                return "{}"
            if indent is None:
                return "{" + ",".join(members) + "}"
            padding = " " * (indent * (level + 1))
            closing = " " * (indent * level)
            return (
                "{\n" + padding + (",\n" + padding).join(members) + "\n" + closing + "}"
            )
        raise TypeError(f"not a JSON value: {type(item).__name__}")

    return render(value, 0)


def _json(value: Any) -> str:
    return render_canonical_json(value)


def _predicate_fact(predicate: object, *, handle: str) -> str:
    if not isinstance(predicate, Mapping):
        raise ValueError("not a resolved net document")
    typed_predicate = cast(Mapping[str, Any], predicate)
    if len(typed_predicate) != 1:
        raise ValueError("not a resolved net document")
    if "cel" in typed_predicate:
        kind = "cel"
    elif "handler" in typed_predicate:
        kind = "handler"
    else:
        raise ValueError("not a resolved net document")
    value = typed_predicate[kind]
    if not isinstance(value, str):
        raise ValueError("not a resolved net document")
    return f"@{handle} predicate {kind} {_json(value)}"


def _correlate_fact(correlate: object, *, handle: str) -> str:
    if not isinstance(correlate, Mapping):
        raise ValueError("not a resolved net document")
    typed_correlate = cast(Mapping[str, Any], correlate)
    cel = typed_correlate.get("cel")
    if set(typed_correlate) != {"cel"} or not isinstance(cel, str) or not cel:
        raise ValueError("not a resolved net document")
    return f"@{handle} correlate cel {_json(cel)}"


def _is_authoritative_coin_deposit(document: Mapping[str, Any]) -> bool:
    """Recognize the sole historical canonical template-name exception."""
    return document == {
        "name": "coin_deposit",
        "description": "Coin deposit",
        "places": [
            {"name": "coin_slot", "accepts": ["coin"]},
            {"name": "cash_box", "accepts": ["coin"]},
        ],
        "transitions": [{"name": "accept_coin", "handler": "accept_coin"}],
        "arcs": [
            {
                "from": {"place": "coin_slot"},
                "to": {"transition": "accept_coin"},
                "consume": {"type": "coin"},
            },
            {
                "from": {"transition": "accept_coin"},
                "to": {"place": "cash_box"},
                "produce": {"destination": "cash_box", "type": "coin"},
            },
        ],
        "initialMarking": {
            "coin_slot": [{"type": "coin", "data": {}}],
        },
    }


def _emit_composition(document: Mapping[str, Any]) -> str:
    if set(document) != {"nets", "wires"}:
        raise ValueError("not a resolved composition document")
    raw_nets, raw_wires = document["nets"], document["wires"]
    if not isinstance(raw_nets, list) or not isinstance(raw_wires, list):
        raise ValueError("not a resolved composition document")
    net_items = cast(list[object], raw_nets)
    wire_items = cast(list[object], raw_wires)
    aliases: set[str] = set()
    uses: list[str] = []
    for raw_entry in net_items:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("not a resolved composition document")
        entry = cast(Mapping[str, Any], raw_entry)
        ref, alias = entry.get("ref"), entry.get("alias")
        if (
            set(entry) != {"ref", "alias"}
            or not isinstance(ref, str)
            or not ref
            or not isinstance(alias, str)
            or not _IDENTIFIER.fullmatch(alias)
            or alias in _ALIAS_RESERVED
            or alias in aliases
        ):
            raise ValueError("not a resolved composition document")
        aliases.add(alias)
        uses.append(f"use {_json(ref)} as {alias}")
    wires: list[str] = []
    seen_wires: set[tuple[str, str, str, str]] = set()
    for raw_wire in wire_items:
        if not isinstance(raw_wire, Mapping):
            raise ValueError("not a resolved composition document")
        wire = cast(Mapping[str, Any], raw_wire)
        raw_from, raw_to = wire.get("from"), wire.get("to")
        if (
            set(wire) != {"from", "to"}
            or not isinstance(raw_from, Mapping)
            or not isinstance(raw_to, Mapping)
        ):
            raise ValueError("not a resolved composition document")
        typed_from = cast(Mapping[str, Any], raw_from)
        typed_to = cast(Mapping[str, Any], raw_to)
        endpoints: list[tuple[str, str]] = []
        for endpoint in (typed_from, typed_to):
            alias, port = endpoint.get("net"), endpoint.get("port")
            if (
                set(endpoint) != {"net", "port"}
                or not isinstance(alias, str)
                or alias not in aliases
                or not isinstance(port, str)
                or not port
            ):
                raise ValueError("not a resolved composition document")
            endpoints.append((alias, port))
        source, destination = endpoints
        key = (source[0], source[1], destination[0], destination[1])
        if key in seen_wires:
            raise ValueError("not a resolved composition document")
        seen_wires.add(key)
        wires.append(
            f"wire {source[0]}.({_name(source[1])}) -> "
            f"{destination[0]}.({_name(destination[1])})"
        )
    lines = ["composition composition", "", *uses, *wires, ""]
    return "\n".join(lines)


_DSL_METADATA_KEY = "petrinet.dsl/v1"


def _unsupported_fields(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    extra = set(value) - allowed
    if extra:
        field = sorted(extra)[0]
        raise ValueError(f"{path} has unsupported field {field!r}")


def _reserved_payload(
    document: Mapping[str, Any],
    arcs: list[dict[str, Any]],
    places: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[int, str]]:
    raw_annotations = document.get("annotations")
    if raw_annotations is None:
        return None, {}
    if not isinstance(raw_annotations, Mapping):
        raise ValueError("not a resolved net document")
    annotations_map = cast(Mapping[str, Any], raw_annotations)
    raw_payload = annotations_map.get(_DSL_METADATA_KEY)
    if raw_payload is None:
        return None, {}
    path = "annotations['petrinet.dsl/v1']"
    if not isinstance(raw_payload, Mapping):
        raise ValueError(f"{path} must be an object")
    payload = cast(dict[str, Any], raw_payload)
    _unsupported_fields(
        payload, {"arcHandles", "markings", "views", "extensions"}, path
    )
    if set(payload) != {"arcHandles", "markings", "views", "extensions"}:
        raise ValueError(
            f"{path} must contain arcHandles, markings, views, and extensions"
        )
    if not all(isinstance(payload[key], Mapping) for key in payload):
        raise ValueError(f"{path} sections must be objects")

    handles_by_index: dict[int, str] = {}
    for handle, raw_handle in cast(Mapping[Any, Any], payload["arcHandles"]).items():
        handle_path = f"{path}.arcHandles.{handle}"
        if not isinstance(handle, str) or not isinstance(raw_handle, Mapping):
            raise ValueError(f"{handle_path} must be an object")
        handle_value = cast(Mapping[str, Any], raw_handle)
        _unsupported_fields(handle_value, {"index", "fingerprint"}, handle_path)
        if set(handle_value) != {"index", "fingerprint"}:
            raise ValueError(f"{handle_path} must contain index and fingerprint")
        index = handle_value["index"]
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"{handle_path}.index must be an integer")
        if index < 0 or index >= len(arcs):
            raise ValueError(
                f"{handle_path}.index {index} is outside arcs[0:{len(arcs)}]"
            )
        fingerprint = handle_value["fingerprint"]
        if not isinstance(fingerprint, Mapping):
            raise ValueError(f"{handle_path}.fingerprint must be an object")
        fingerprint_value = cast(Mapping[str, Any], fingerprint)
        _unsupported_fields(
            fingerprint_value,
            {"from", "to", "type", "mode"},
            f"{handle_path}.fingerprint",
        )
        arc = arcs[index]
        if "consume" in arc:
            actual = {
                "from": arc.get("from"),
                "to": arc.get("to"),
                "type": arc["consume"].get("type"),
                "mode": arc["consume"].get("mode", "consume"),
            }
        elif "produce" in arc:
            actual = {
                "from": arc.get("from"),
                "to": arc.get("to"),
                "type": arc["produce"].get("type"),
                "mode": "produce",
            }
        else:
            actual = {}
        if dict(fingerprint_value) != actual:
            raise ValueError(f"{handle_path}.fingerprint does not match arcs[{index}]")
        if index in handles_by_index:
            raise ValueError(f"{handle_path}.index duplicates another arc handle")
        handles_by_index[index] = handle

    place_names = {
        item.get("name") for item in places if isinstance(item.get("name"), str)
    }
    transition_names = {
        item.get("name") for item in transitions if isinstance(item.get("name"), str)
    }
    for marking_name, raw_marking in cast(
        Mapping[Any, Any], payload["markings"]
    ).items():
        marking_path = f"{path}.markings.{marking_name}"
        if not isinstance(marking_name, str) or not isinstance(raw_marking, Mapping):
            raise ValueError(f"{marking_path} must be an object")
        marking_value = cast(Mapping[str, Any], raw_marking)
        unknown: set[str] = set(marking_value) - place_names
        if unknown:
            field = sorted(unknown)[0]
            raise ValueError(f"{marking_path} has unsupported field {field!r}")
        for place, raw_tokens in marking_value.items():
            if not isinstance(raw_tokens, list):
                raise ValueError(f"{marking_path}.{place} is not a token array")
            for raw_token in cast(list[Any], raw_tokens):
                if not isinstance(raw_token, Mapping):
                    raise ValueError(f"{marking_path}.{place} is not a token array")
                token = cast(Mapping[str, Any], raw_token)
                if set(token) != {"type", "data"} or not isinstance(
                    token.get("type"), str
                ):
                    raise ValueError(f"{marking_path}.{place} is not a token array")

    for view_name, raw_view in cast(Mapping[Any, Any], payload["views"]).items():
        view_path = f"{path}.views.{view_name}"
        if not isinstance(view_name, str) or not isinstance(raw_view, Mapping):
            raise ValueError(f"{view_path} must be an object")
        view_value = cast(Mapping[str, Any], raw_view)
        _unsupported_fields(view_value, {"positions", "routes"}, view_path)
        if set(view_value) != {"positions", "routes"}:
            raise ValueError(f"{view_path} must contain positions and routes")
        positions, view_routes = view_value["positions"], view_value["routes"]
        if not isinstance(positions, Mapping) or not isinstance(view_routes, Mapping):
            raise ValueError(f"{view_path} positions and routes must be objects")
        position_values = cast(Mapping[str, Any], positions)
        route_values = cast(Mapping[str, Any], view_routes)
        for subject, raw_position in position_values.items():
            position_path = f"{view_path}.positions.{subject}"
            if not isinstance(raw_position, Mapping):
                raise ValueError(f"{position_path} is invalid")
            position = cast(Mapping[str, Any], raw_position)
            subject_parts = subject.split(":", 1)
            if (
                len(subject_parts) != 2
                or subject_parts[0]
                not in {
                    "place",
                    "transition",
                }
                or not subject_parts[1]
            ):
                raise ValueError(
                    f"{position_path} must target place:<name> or transition:<name>"
                )
            subject_kind, subject_name = subject_parts
            known_names = place_names if subject_kind == "place" else transition_names
            if subject_name not in known_names:
                raise ValueError(
                    f"{position_path} references unknown {subject_kind} {subject_name!r}"
                )
            _unsupported_fields(position, {"x", "y"}, position_path)
            if set(position) != {"x", "y"} or any(
                not isinstance(position.get(axis), (int, float))
                or isinstance(position.get(axis), bool)
                or not math.isfinite(position[axis])
                for axis in ("x", "y")
            ):
                raise ValueError(f"{position_path} is invalid")
        for handle, raw_route in route_values.items():
            route_path = f"{view_path}.routes.{handle}"
            if handle not in payload["arcHandles"]:
                raise ValueError(f"{route_path} references unknown arc handle")
            if not isinstance(raw_route, Mapping):
                raise ValueError(f"{route_path} must be an object")
            route = cast(Mapping[str, Any], raw_route)
            _unsupported_fields(route, {"style", "points"}, route_path)
            if (
                set(route) != {"style", "points"}
                or route.get("style") != "orthogonal"
                or not isinstance(route.get("points"), list)
                or not route["points"]
            ):
                raise ValueError(f"{route_path} is invalid")
            points = cast(list[Any], route["points"])
            for point_index, raw_point in enumerate(points):
                point_path = f"{route_path}.points[{point_index}]"
                if not isinstance(raw_point, Mapping):
                    raise ValueError(f"{point_path} is invalid")
                point = cast(Mapping[str, Any], raw_point)
                _unsupported_fields(point, {"x", "y"}, point_path)
                if set(point) != {"x", "y"} or any(
                    not isinstance(point.get(axis), (int, float))
                    or isinstance(point.get(axis), bool)
                    or not math.isfinite(point[axis])
                    for axis in ("x", "y")
                ):
                    raise ValueError(f"{point_path} is invalid")
    return payload, handles_by_index


def _compact_arc_indexes(
    places: Sequence[Mapping[str, object]],
    arcs: Sequence[Mapping[str, object]],
    *,
    compact: bool,
) -> set[int]:
    """Choose only default arcs whose color remains uniquely recoverable."""
    if not compact:
        return set()

    sole_color_by_place: dict[str, str] = {}
    anchored_colors_by_place: dict[str, set[str]] = {}

    for place in places:
        name, raw_accepts = place.get("name"), place.get("accepts")
        if not isinstance(name, str) or not isinstance(raw_accepts, list):
            continue
        accepts = cast(list[object], raw_accepts)
        accepted = [color for color in accepts if isinstance(color, str)]
        if len(accepted) == 1 and len(accepts) == 1:
            sole_color_by_place[name] = accepted[0]

    eligible: dict[int, tuple[str, str]] = {}
    for index, arc in enumerate(arcs):
        raw_inscription: object
        raw_endpoint: object
        default = False
        if "consume" in arc:
            raw_endpoint = arc.get("from")
            raw_inscription = arc.get("consume")
            if isinstance(raw_inscription, Mapping):
                inscription = cast(Mapping[str, object], raw_inscription)
                weight = inscription.get("weight", 1)
                default = bool(
                    inscription.get("mode", "consume") == "consume"
                    and weight == 1
                    and not isinstance(weight, bool)
                    and "predicate" not in inscription
                    and "data" not in inscription
                    and "correlate" not in inscription
                )
        elif "produce" in arc:
            raw_endpoint = arc.get("to")
            raw_inscription = arc.get("produce")
            if isinstance(raw_inscription, Mapping):
                inscription = cast(Mapping[str, object], raw_inscription)
                default = not any(
                    field in inscription
                    for field in (
                        "weight",
                        "predicate",
                        "data",
                        "cel",
                        "correlate",
                        "mode",
                    )
                )
        else:
            continue
        if not isinstance(raw_endpoint, Mapping) or not isinstance(
            raw_inscription, Mapping
        ):
            continue
        endpoint = cast(Mapping[str, object], raw_endpoint)
        inscription = cast(Mapping[str, object], raw_inscription)
        place = endpoint.get("place")
        color = inscription.get("type")
        if not isinstance(place, str) or not isinstance(color, str):
            continue
        if default and sole_color_by_place.get(place) == color:
            eligible[index] = (place, color)
        else:
            anchored_colors_by_place.setdefault(place, set()).add(color)

    elided: set[int] = set()
    for index, (place, color) in eligible.items():
        place_anchors = anchored_colors_by_place.setdefault(place, set())
        if color in place_anchors:
            elided.add(index)
            continue
        place_anchors.add(color)
    return elided


def _topology_place_accepts(
    arcs: Sequence[Mapping[str, object]],
) -> list[tuple[str, list[str]]]:
    """Reconstruct place order and accepted colors contributed by topology."""
    ordered: list[tuple[str, list[str]]] = []
    by_name: dict[str, list[str]] = {}
    for arc in arcs:
        if "consume" in arc:
            endpoint, inscription = arc.get("from"), arc.get("consume")
        elif "produce" in arc:
            endpoint, inscription = arc.get("to"), arc.get("produce")
        else:
            raise ValueError("not a resolved net document")
        if not isinstance(endpoint, Mapping) or not isinstance(inscription, Mapping):
            raise ValueError("not a resolved net document")
        typed_endpoint = cast(Mapping[str, object], endpoint)
        typed_inscription = cast(Mapping[str, object], inscription)
        place, color = typed_endpoint.get("place"), typed_inscription.get("type")
        if not isinstance(place, str) or not isinstance(color, str):
            raise ValueError("not a resolved net document")
        colors = by_name.get(place)
        if colors is None:
            colors = []
            by_name[place] = colors
            ordered.append((place, cast(list[str], colors)))
        if color not in colors:
            colors.append(color)
    return ordered


def emit_petrinet(document: Mapping[str, Any], compact: bool = False) -> str:
    """Emit deterministic DSL, optionally eliding only inferable default colors."""
    if "nets" in document:
        return _emit_composition(document)
    name = document.get("name")
    places = document.get("places")
    transitions = document.get("transitions")
    arcs = document.get("arcs")
    marking = document.get("initialMarking", {})
    if (
        not isinstance(name, str)
        or not isinstance(places, list)
        or not isinstance(transitions, list)
        or not isinstance(arcs, list)
        or not isinstance(marking, Mapping)
    ):
        raise ValueError("not a resolved net document")

    typed_places = cast(list[dict[str, Any]], places)
    typed_transitions = cast(list[dict[str, Any]], transitions)
    typed_arcs = cast(list[dict[str, Any]], arcs)
    typed_marking = cast(dict[str, Any], marking)
    compact_arc_indexes = _compact_arc_indexes(
        typed_places, typed_arcs, compact=compact
    )
    metadata, metadata_handles = _reserved_payload(
        document, typed_arcs, typed_places, typed_transitions
    )
    description = document.get("description")
    header = f"net {_name(name)}" + (
        f" {_json(description)}" if isinstance(description, str) else ""
    )
    documentation_facts: list[str] = []
    raw_document_annotations = document.get("annotations")
    if raw_document_annotations is not None:
        annotation_values = cast(Mapping[str, Any], raw_document_annotations)
        for key, value in sorted(annotation_values.items()):
            if key != _DSL_METADATA_KEY:
                documentation_facts.append(
                    f"net annotation {_name(key)} {_json(value)}"
                )

    transition_names: list[str] = []
    handlers: list[str] = []
    guards: list[str] = []
    timer_facts: list[str] = []
    priority_facts: list[str] = []
    for item in typed_transitions:
        transition = item.get("name")
        if not isinstance(transition, str):
            raise ValueError("not a resolved net document")
        transition_names.append(transition)
        if "handler" in item:
            handler = item["handler"]
            if not isinstance(handler, str) or not handler:
                raise ValueError("not a resolved net document")
            handlers.append(f"[{_name(transition)}] handler {_json(handler)}")
        if "guard" in item:
            guard = item["guard"]
            if not isinstance(guard, str) or not guard:
                raise ValueError("not a resolved net document")
            guards.append(f"[{_name(transition)}] guard {_json(guard)}")
        raw_timer = item.get("timer")
        if raw_timer is not None:
            if not isinstance(raw_timer, Mapping):
                raise ValueError("not a resolved net document")
            timer = cast(Mapping[str, Any], raw_timer)
            clock = timer.get("clock")
            cel = timer.get("cel")
            maturity = timer.get("maturity")
            if (
                set(timer) - {"clock", "cel", "bind", "maturity"}
                or not isinstance(clock, str)
                or not clock
                or not isinstance(cel, str)
                or not cel
                or (
                    maturity is not None
                    and (not isinstance(maturity, str) or not maturity)
                )
            ):
                raise ValueError("not a resolved net document")
            timer_facts.append(
                f"[{_name(transition)}] timer clock ({_name(clock)}) cel {_json(cel)}"
            )
            if maturity is not None:
                timer_facts.append(
                    f"[{_name(transition)}] timer maturity cel {_json(maturity)}"
                )
            raw_binds = timer.get("bind")
            if raw_binds is not None:
                if not isinstance(raw_binds, Mapping):
                    raise ValueError("not a resolved net document")
                binds = cast(Mapping[Any, Any], raw_binds)
                if any(
                    not isinstance(bind_name, str)
                    or not bind_name
                    or not isinstance(place, str)
                    or not place
                    for bind_name, place in binds.items()
                ):
                    raise ValueError("not a resolved net document")
                timer_facts.extend(
                    f"[{_name(transition)}] timer bind {_name(bind_name)} "
                    f"({_name(cast(str, binds[bind_name]))})"
                    for bind_name in sorted(cast(dict[str, Any], binds))
                )
        if "priority" in item:
            priority = item["priority"]
            if (
                not isinstance(priority, int)
                or isinstance(priority, bool)
                or priority < 0
            ):
                raise ValueError("not a resolved net document")
            if priority:
                priority_facts.append(f"[{_name(transition)}] priority {priority}")
        if isinstance(item.get("description"), str):
            documentation_facts.append(
                f"[{_name(transition)}] description {_json(item['description'])}"
            )
        raw_annotations = item.get("annotations")
        if raw_annotations is not None:
            if not isinstance(raw_annotations, Mapping):
                raise ValueError("not a resolved net document")
            annotation_values = cast(Mapping[str, Any], raw_annotations)
            documentation_facts.extend(
                f"[{_name(transition)}] annotation {_name(key)} {_json(value)}"
                for key, value in sorted(annotation_values.items())
            )

    declared_places: list[tuple[str, list[str]]] = []
    for item in typed_places:
        place, raw_accepts = item.get("name"), item.get("accepts")
        if (
            not isinstance(place, str)
            or not isinstance(raw_accepts, list)
            or not raw_accepts
            or any(
                not isinstance(color, str) or not color
                for color in cast(list[object], raw_accepts)
            )
            or len(cast(list[object], raw_accepts))
            != len(set(cast(list[object], raw_accepts)))
        ):
            raise ValueError("not a resolved net document")
        declared_places.append((place, cast(list[str], raw_accepts)))
    place_accept_facts = (
        [
            f"({_name(place)}) accepts [{', '.join(_name(color) for color in colors)}]"
            for place, colors in declared_places
        ]
        if _topology_place_accepts(typed_arcs) != declared_places
        else []
    )

    port_facts: list[str] = []
    for item in typed_places:
        place = item.get("name")
        if not isinstance(place, str):
            raise ValueError("not a resolved net document")
        raw_port = item.get("port")
        if raw_port is not None:
            if not isinstance(raw_port, Mapping):
                raise ValueError("not a resolved net document")
            port = cast(Mapping[str, Any], raw_port)
            direction, type_ = port.get("direction"), port.get("type")
            accepts = item.get("accepts")
            if (
                set(port) != {"direction", "type"}
                or direction not in {"input", "output"}
                or not isinstance(type_, str)
                or not isinstance(accepts, list)
                or type_ not in accepts
            ):
                raise ValueError("not a resolved net document")
            port_facts.append(f"({_name(place)}) port {direction} {_name(type_)}")
        if isinstance(item.get("description"), str):
            documentation_facts.append(
                f"({_name(place)}) description {_json(item['description'])}"
            )
        raw_annotations = item.get("annotations")
        if raw_annotations is not None:
            if not isinstance(raw_annotations, Mapping):
                raise ValueError("not a resolved net document")
            annotation_values = cast(Mapping[str, Any], raw_annotations)
            documentation_facts.extend(
                f"({_name(place)}) annotation {_name(key)} {_json(value)}"
                for key, value in sorted(annotation_values.items())
            )
    capacity_facts: list[str] = []
    for item in typed_places:
        place = item.get("name")
        if not isinstance(place, str):
            raise ValueError("not a resolved net document")
        raw_capacity = item.get("capacityPerColorKey")
        if raw_capacity is None:
            continue
        if not isinstance(raw_capacity, Mapping):
            raise ValueError("not a resolved net document")
        capacity = cast(Mapping[str, Any], raw_capacity)
        key, maximum = capacity.get("key"), capacity.get("max")
        valid_key = _valid_capacity_key(key)
        if (
            set(capacity) != {"key", "max"}
            or not valid_key
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum < 1
        ):
            raise ValueError("not a resolved net document")
        capacity_facts.append(f"({_name(place)}) capacityPerColorKey {_json(capacity)}")

    chains: list[str] = []
    data_facts: list[str] = []
    weight_facts: list[str] = []
    predicate_facts: list[str] = []
    correlate_facts: list[str] = []
    first_arc_transitions: list[str] = []
    index = 0
    while index < len(typed_arcs):
        arc = typed_arcs[index]
        try:
            if "consume" in arc:
                place = arc["from"]["place"]
                transition = arc["to"]["transition"]
                raw_consume = arc["consume"]
                if not isinstance(raw_consume, Mapping):
                    raise TypeError
                consume = cast(Mapping[str, Any], raw_consume)
                color = consume["type"]
                mode = consume.get("mode", "consume")
                operator = {
                    "consume": "->",
                    "read": "->?",
                    "inhibit": "->0",
                }.get(mode)
                if (
                    not all(
                        isinstance(item, str) for item in (place, transition, color)
                    )
                    or operator is None
                ):
                    raise TypeError
                predicate = consume.get("predicate")
                has_correlate = "correlate" in consume
                correlate = consume.get("correlate")
                if has_correlate and (correlate is None or mode != "inhibit"):
                    raise TypeError
                weight = consume.get("weight", 1)
                if (
                    not isinstance(weight, int)
                    or isinstance(weight, bool)
                    or weight < 1
                    or mode == "inhibit"
                    and weight > 1
                ):
                    raise TypeError
                input_segment = (
                    "->"
                    if index in compact_arc_indexes
                    else f"-{_name(color)}{operator}"
                )
                chain = f"({_name(place)}) {input_segment} [{_name(transition)}]"
                first_arc_transitions.append(transition)
                produce = None
                produce_index = None
                if (
                    metadata is None
                    and mode == "consume"
                    and predicate is None
                    and weight == 1
                    and index + 1 < len(typed_arcs)
                ):
                    following = typed_arcs[index + 1]
                    if (
                        following.get("from", {}).get("transition") == transition
                        and "produce" in following
                    ):
                        destination = following["to"]["place"]
                        produce = following["produce"]
                        output_color = produce["type"]
                        if not isinstance(destination, str) or not isinstance(
                            output_color, str
                        ):
                            raise TypeError
                        output_segment = (
                            "->"
                            if index + 1 in compact_arc_indexes
                            else f"-{_name(output_color)}->"
                        )
                        chain += f" {output_segment} ({_name(destination)})"
                        produce_index = index + 1
                needs_weight_handle = weight > 1
                handle_index = (
                    index
                    if index in metadata_handles
                    or has_correlate
                    or needs_weight_handle
                    or predicate is not None
                    else produce_index
                    if produce is not None and ("data" in produce or "cel" in produce)
                    else None
                )
                if handle_index is not None:
                    handle = metadata_handles.get(handle_index, f"arc_{handle_index}")
                    chain = f"@{_name(handle)}: " + chain
                    if produce is not None and "data" in produce:
                        data_facts.append(f"@{handle} data {_json(produce['data'])}")
                    if produce is not None and "cel" in produce:
                        data_facts.append(f"@{handle} data cel {_json(produce['cel'])}")
                    if predicate is not None:
                        predicate_facts.append(
                            _predicate_fact(predicate, handle=handle)
                        )
                    if correlate is not None:
                        correlate_facts.append(
                            _correlate_fact(correlate, handle=handle)
                        )
                    if needs_weight_handle:
                        weight_facts.append(f"@{handle} weight {weight}")
                if index in metadata_handles:
                    handle = metadata_handles[index]
                    description_value = arc.get("description")
                    if isinstance(description_value, str):
                        documentation_facts.append(
                            f"@{_name(handle)} description {_json(description_value)}"
                        )
                    raw_annotations = arc.get("annotations")
                    if raw_annotations is not None:
                        if not isinstance(raw_annotations, Mapping):
                            raise TypeError
                        annotation_values = cast(Mapping[str, Any], raw_annotations)
                        documentation_facts.extend(
                            f"@{_name(handle)} annotation {_name(key)} {_json(value)}"
                            for key, value in sorted(annotation_values.items())
                        )
                if produce_index is not None:
                    index = produce_index
            elif "produce" in arc:
                transition = arc["from"]["transition"]
                destination = arc["to"]["place"]
                produce = arc["produce"]
                color = produce["type"]
                if not all(
                    isinstance(item, str) for item in (transition, destination, color)
                ):
                    raise TypeError
                output_segment = (
                    "->" if index in compact_arc_indexes else f"-{_name(color)}->"
                )
                chain = f"[{_name(transition)}] {output_segment} ({_name(destination)})"
                if "data" in produce or "cel" in produce or index in metadata_handles:
                    handle = metadata_handles.get(index, f"arc_{index}")
                    chain = f"@{_name(handle)}: " + chain
                    if "data" in produce:
                        data_facts.append(
                            f"@{_name(handle)} data {_json(produce['data'])}"
                        )
                    if "cel" in produce:
                        data_facts.append(
                            f"@{_name(handle)} data cel {_json(produce['cel'])}"
                        )
            else:
                raise TypeError
        except (KeyError, TypeError) as error:
            raise ValueError("not a resolved net document") from error
        chains.append(chain)
        index += 1

    appearance_order = list(dict.fromkeys(first_arc_transitions))
    order_facts = (
        [
            f"[{_name(transition)}] order {rank}"
            for rank, transition in enumerate(transition_names, 1)
        ]
        if appearance_order != transition_names
        else []
    )

    place_names = [cast(str, item["name"]) for item in typed_places]
    if any(place not in place_names for place in typed_marking):
        raise ValueError("not a resolved net document")

    generic_token: dict[str, Any] = {"type": "token", "data": {}}
    token_templates: list[dict[str, Any]] = []
    marking_runs: list[tuple[str, str, int, int | None]] = []

    def add_marking(marking_name: str, raw_marking: Mapping[str, Any]) -> None:
        for place in place_names:
            if place not in raw_marking:
                continue
            raw_tokens = raw_marking[place]
            if not isinstance(raw_tokens, list):
                raise ValueError("not a resolved net document")
            tokens = cast(list[Any], raw_tokens)
            offset = 0
            while offset < len(tokens):
                raw_token = tokens[offset]
                if not isinstance(raw_token, Mapping):
                    raise ValueError("not a resolved net document")
                token = cast(dict[str, Any], raw_token)
                if set(token) != {"type", "data"} or not isinstance(
                    token.get("type"), str
                ):
                    raise ValueError("not a resolved net document")
                run_length = 1
                while offset + run_length < len(tokens) and json_values_equal(
                    tokens[offset + run_length], token
                ):
                    run_length += 1
                if json_values_equal(token, generic_token):
                    marking_runs.append((marking_name, place, run_length, None))
                    offset += run_length
                    continue
                template_index = next(
                    (
                        candidate_index
                        for candidate_index, candidate in enumerate(token_templates)
                        if json_values_equal(candidate, token)
                    ),
                    -1,
                )
                if template_index < 0:
                    token_templates.append(token)
                    template_index = len(token_templates) - 1
                marking_runs.append((marking_name, place, run_length, template_index))
                offset += run_length

    add_marking("initial", typed_marking)
    if metadata is not None:
        for marking_name, raw_marking in metadata["markings"].items():
            add_marking(cast(str, marking_name), cast(Mapping[str, Any], raw_marking))

    template_name = (
        "inserted_coin" if _is_authoritative_coin_deposit(document) else "token_0"
    )

    def emitted_template_name(index: int) -> str:
        return template_name if len(token_templates) == 1 else f"token_{index}"

    def marking_fact(
        marking_name: str,
        place: str,
        run_length: int,
        template_index: int | None,
    ) -> str:
        prefix = (
            f"marking {marking_name if marking_name == 'initial' else _name(marking_name)} "
            f"({_name(place)}) <- "
        )
        if template_index is None:
            return f"{prefix}{run_length}"
        repetition = f"{run_length} * " if run_length > 1 else ""
        return f"{prefix}{repetition}${emitted_template_name(template_index)}"

    marking_facts = [
        marking_fact(marking_name, place, run_length, template_index)
        for marking_name, place, run_length, template_index in marking_runs
    ]
    template_facts = [
        f"${emitted_template_name(index)}: "
        f"{_name(cast(str, token['type']))} {_json(token['data'])}"
        for index, token in enumerate(token_templates)
    ]

    documentation_facts.sort(
        key=lambda fact: (
            0
            if fact.startswith("net ")
            else 1
            if fact.startswith("(")
            else 2
            if fact.startswith("[")
            else 3
        )
    )
    view_facts: list[str] = []
    if metadata is not None:
        for view_name, view in sorted(metadata["views"].items()):
            ordered_positions = sorted(
                view["positions"].items(),
                key=lambda item: (item[1]["x"], item[1]["y"], item[0]),
            )
            for subject, position in ordered_positions:
                subject_type, subject_name = cast(str, subject).split(":", 1)
                rendered_subject = (
                    f"({_name(subject_name)})"
                    if subject_type == "place"
                    else f"[{_name(subject_name)}]"
                )
                view_facts.append(
                    f"view {_name(cast(str, view_name))} position "
                    f"{rendered_subject} at {_json(position)}"
                )
            for handle, route in sorted(view["routes"].items()):
                view_facts.append(
                    f"view {_name(cast(str, view_name))} route @{_name(cast(str, handle))} "
                    f"orthogonal {_json(route['points'])}"
                )
        view_facts.append(f"extensions {_json(metadata['extensions'])}")

    common_tail = [
        timer_facts,
        priority_facts,
        order_facts,
        capacity_facts,
        weight_facts,
        data_facts,
        predicate_facts,
        correlate_facts,
    ]
    sections = (
        [
            [header],
            place_accept_facts,
            chains,
            [*handlers, *guards],
            port_facts,
            *common_tail,
            [*marking_facts, *template_facts],
            documentation_facts,
            view_facts,
        ]
        if metadata is not None
        else [
            [header],
            place_accept_facts,
            chains,
            port_facts,
            [*handlers, *guards],
            *common_tail,
            [*marking_facts, *template_facts],
            documentation_facts,
        ]
    )
    lines: list[str] = []
    for section in sections:
        if not section:
            continue
        if lines:
            lines.append("")
        lines.extend(section)
    lines.append("")
    return "\n".join(lines)
