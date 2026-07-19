"""Parse net and composition JSON documents into :class:`Net` and :class:`Composition` objects, with structural validation.

This module performs two passes per document:

- **Shape validation** against the packaged draft 2020-12 JSON Schemas
  loaded from :mod:`velocitron.schema_resources`.

- **Structural checks the schema cannot express**: unique place and
  transition names, port-type acceptance, arc direction vs. consume/produce,
  wire port resolution, and CEL predicate compilation.

What this module does NOT do:

- It is not a firing engine. The handler registry contract lives in
  :mod:`velocitron.contract` and :mod:`velocitron.registry`; the firing
  engine lives in :mod:`velocitron.engine`.

- It does not evaluate CEL predicates against token ``data``. CEL
  expressions are **compiled at parse time** so a syntax or compile error
  fails parsing as a :class:`NetValidationError`; a runtime evaluation error
  is deferred to the engine, which degrades it to predicate ``false`` rather
  than crashing.

References: spec/net.schema.json and spec/composition.schema.json (canonical schemas); D6.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from jsonschema import Draft202012Validator, ValidationError

from .cel import CelAdapter, get_default_adapter
from .schema import (
    Arc,
    CapacityPerColorKey,
    Composition,
    ConsumePattern,
    Marking,
    Net,
    NetRef,
    Place,
    Port,
    Predicate,
    ProduceTemplate,
    Timer,
    Token,
    Transition,
    Wire,
)
from .schema_resources import COMPOSITION_SCHEMA, NET_SCHEMA


class NetValidationError(Exception):
    """Raised when a net or composition document fails validation."""


# Aliases are restricted to simple identifiers (schema `pattern`) so the
# dotted `<alias>.<placeName>` form is unambiguous. The schema enforces this
# for *explicit* aliases; a derived default (from the net's `name`) bypasses
# the schema, so the parser is the authoritative enforcer.
# References: spec/composition.md (Aliasing — Why dotted); D4.
_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ── Source loading ──────────────────────────────────────────────────────


def _load_source(source: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    """Accept a mapping, a Path / str path to a JSON file, or a JSON string."""
    if isinstance(source, Mapping):
        return dict(source)
    if isinstance(source, Path):
        return json.loads(source.read_text())
    candidate = Path(source)
    if candidate.exists():
        return json.loads(candidate.read_text())
    return json.loads(source)


def _validate_against_schema(doc: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate ``doc`` against a draft 2020-12 schema; raise on shape failure."""
    try:
        Draft202012Validator(schema).validate(doc)  # type: ignore[reportUnknownMemberType]
    except ValidationError as exc:
        raise NetValidationError(str(exc)) from exc


# ── Dataclass builders ───────────────────────────────────────────────────


def _parse_token(token: dict[str, Any]) -> Token:
    return Token(type=token["type"], data=token.get("data", {}))


def _parse_capacity(capacity: dict[str, Any]) -> CapacityPerColorKey:
    # A single-field key normalizes to a 1-tuple so consumers see one shape.
    key = capacity["key"]
    keys = (key,) if isinstance(key, str) else tuple(key)
    return CapacityPerColorKey(keys=keys, max=capacity["max"])


def _parse_place(place: dict[str, Any]) -> Place:
    port: Port | None = None
    port_dict = place.get("port")
    if port_dict is not None:
        port = Port(direction=port_dict["direction"], type=port_dict["type"])
    capacity_dict = place.get("capacityPerColorKey")
    return Place(
        name=place["name"],
        accepts=list(place["accepts"]),
        port=port,
        description=place.get("description"),
        annotations=place.get("annotations"),
        capacity_per_color_key=(
            _parse_capacity(capacity_dict) if capacity_dict is not None else None
        ),
    )


def _parse_transition(transition: dict[str, Any]) -> Transition:
    timer: Timer | None = None
    timer_dict = transition.get("timer")
    if timer_dict is not None:
        timer = Timer(
            clock=timer_dict["clock"],
            cel=timer_dict["cel"],
            bind=timer_dict.get("bind"),
            maturity=timer_dict.get("maturity"),
        )
    return Transition(
        name=transition["name"],
        handler=transition.get("handler"),
        guard=transition.get("guard"),
        priority=transition.get("priority"),
        timer=timer,
        description=transition.get("description"),
        annotations=transition.get("annotations"),
    )


def _parse_predicate(predicate: dict[str, Any]) -> Predicate | None:
    has_cel = predicate.get("cel") is not None
    has_handler = predicate.get("handler") is not None
    if has_cel and has_handler:
        raise NetValidationError(
            "predicate: 'cel' and 'handler' are mutually exclusive"
        )
    if not has_cel and not has_handler:
        return None
    return Predicate(cel=predicate.get("cel"), handler=predicate.get("handler"))


def _parse_consume(consume: dict[str, Any]) -> ConsumePattern:
    correlate_dict: dict[str, Any] = consume.get("correlate") or {}
    return ConsumePattern(
        type=consume["type"],
        predicate=_parse_predicate(consume.get("predicate") or {}),
        mode=consume.get("mode", "consume"),
        weight=consume.get("weight", 1),
        correlate=correlate_dict.get("cel"),
    )


def _parse_produce(produce: dict[str, Any]) -> ProduceTemplate:
    return ProduceTemplate(
        type=produce["type"],
        destination=produce["destination"],
        data=produce.get("data"),
        cel=produce.get("cel"),
    )


def _parse_arc(arc: dict[str, Any]) -> Arc:
    frm: dict[str, Any] = arc.get("from") or {}
    to: dict[str, Any] = arc.get("to") or {}
    consume_dict = arc.get("consume")
    produce_dict = arc.get("produce")
    return Arc(
        from_place=frm.get("place"),
        from_transition=frm.get("transition"),
        to_place=to.get("place"),
        to_transition=to.get("transition"),
        consume=_parse_consume(consume_dict) if consume_dict is not None else None,
        produce=_parse_produce(produce_dict) if produce_dict is not None else None,
        description=arc.get("description"),
        annotations=arc.get("annotations"),
    )


def _parse_marking(marking: dict[str, Any]) -> Marking:
    return Marking(
        {place: [_parse_token(t) for t in tokens] for place, tokens in marking.items()}
    )


# ── Structural validation ───────────────────────────────────────────────


def _unique_names(names: Iterable[str], kind: str) -> set[str]:
    """Collect names into a set, rejecting duplicates with a uniform message."""
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise NetValidationError(f"duplicate {kind} name: {name!r}")
        seen.add(name)
    return seen


def _validate_cel(
    expr: str, *, context: str, cel_adapter: CelAdapter | None = None
) -> None:
    """Compile a CEL predicate expression at parse time.

    A syntax or compile error fails parsing as a :class:`NetValidationError` —
    the net is malformed and cannot be loaded. Runtime evaluation errors are
    NOT raised here; they are deferred to the firing engine, which degrades
    them to predicate ``false`` rather than crashing. Specifically deferred:

    - undeclared references against a candidate token's ``data``

    - type mismatches at evaluation time.

    A fresh adapter is used for each call — the default auto-detected backend,
    or the injected ``cel_adapter``; the engine keeps its own compile cache
    keyed by expression.

    References: D6.
    """
    adapter = cel_adapter or get_default_adapter()
    try:
        adapter.compile(expr)  # pyright: ignore[reportUnknownMemberType]
    except (
        Exception
    ) as exc:  # backend compile error (CELParseError / RuntimeError / ValueError)
        raise NetValidationError(
            f"invalid CEL expression in {context}: {expr!r} ({exc})"
        ) from exc


def _validate_net(net: Net, cel_adapter: CelAdapter | None = None) -> None:
    place_names = _unique_names((p.name for p in net.places), "place")
    transition_names = _unique_names((t.name for t in net.transitions), "transition")

    # Port type must be one of the enclosing place's accepted types.
    for place in net.places:
        if place.port is not None and place.port.type not in place.accepts:
            raise NetValidationError(
                f"port type {place.port.type!r} not accepted by place "
                f"{place.name!r} (accepts {place.accepts!r})"
            )

    accepts_by_place = {p.name: p.accepts for p in net.places}

    for arc in net.arcs:
        consume = arc.consume
        produce = arc.produce

        if consume is not None and produce is not None:
            raise NetValidationError("arc cannot declare both consume and produce")
        if consume is not None:
            _validate_consume_arc(
                arc,
                consume,
                place_names,
                transition_names,
                accepts_by_place,
                cel_adapter=cel_adapter,
            )
        elif produce is not None:
            _validate_produce_arc(
                arc,
                produce,
                place_names,
                transition_names,
                accepts_by_place,
                cel_adapter=cel_adapter,
            )
        else:
            raise NetValidationError("arc must declare either consume or produce")

    # Timer validations (net-schema.md structural validations 9-11) need the
    # per-transition binding-arc sources, so they run after the arc checks.
    binding_sources: dict[str, set[str]] = {}
    for arc in net.arcs:
        if (
            arc.consume is not None
            and arc.consume.mode in ("consume", "read")
            and arc.to_transition is not None
            and arc.from_place is not None
        ):
            binding_sources.setdefault(arc.to_transition, set()).add(arc.from_place)
    for transition in net.transitions:
        if transition.timer is not None:
            _validate_timer(
                transition,
                transition.timer,
                place_names,
                binding_sources.get(transition.name, set()),
                cel_adapter=cel_adapter,
            )


def _validate_timer(
    transition: Transition,
    timer: Timer,
    place_names: set[str],
    binding_sources: set[str],
    *,
    cel_adapter: CelAdapter | None = None,
) -> None:
    """Validate a transition's ``timer`` declaration (ADR 0018).

    Three checks, mirroring net-schema.md's structural validations 9-11:
    the clock place is declared; every ``bind`` value names a source place of
    one of the transition's consume- or read-mode arcs (so the variable always
    resolves to a bound token) and no ``bind`` key claims the reserved
    ``clock`` variable; and the timer's CEL compiles at parse (D6 — a compile
    error is a malformed net, never deferred to enablement).
    """
    if timer.clock not in place_names:
        raise NetValidationError(
            f"timer on transition {transition.name!r} references undeclared "
            f"clock place: {timer.clock!r}"
        )
    for var, place in (timer.bind or {}).items():
        if var == "clock":
            raise NetValidationError(
                f"timer on transition {transition.name!r}: bind variable "
                f"'clock' is reserved for the clock token"
            )
        if place not in binding_sources:
            raise NetValidationError(
                f"timer on transition {transition.name!r}: bind variable "
                f"{var!r} names place {place!r}, which is not a source place "
                f"of any consume- or read-mode arc of that transition"
            )
    _validate_cel(
        timer.cel,
        context=f"timer on transition {transition.name!r}",
        cel_adapter=cel_adapter,
    )
    if timer.maturity is not None:
        _validate_cel(
            timer.maturity,
            context=f"timer maturity on transition {transition.name!r}",
            cel_adapter=cel_adapter,
        )


def _validate_consume_arc(
    arc: Arc,
    consume: ConsumePattern,
    place_names: set[str],
    transition_names: set[str],
    accepts_by_place: dict[str, list[str]],
    *,
    cel_adapter: CelAdapter | None = None,
) -> None:
    # Direction: consume arcs are place → transition.
    if arc.from_place is None or arc.from_transition is not None:
        raise NetValidationError(
            "consume arc must originate at a place (place → transition)"
        )
    if arc.to_transition is None or arc.to_place is not None:
        raise NetValidationError(
            "consume arc must terminate at a transition (place → transition)"
        )
    # Endpoint resolution.
    if arc.from_place not in place_names:
        raise NetValidationError(f"arc references undeclared place: {arc.from_place!r}")
    if arc.to_transition not in transition_names:
        raise NetValidationError(
            f"arc references undeclared transition: {arc.to_transition!r}"
        )
    # Consume type must be accepted by the source place.
    if consume.type not in accepts_by_place[arc.from_place]:
        raise NetValidationError(
            f"consume type {consume.type!r} not accepted by place {arc.from_place!r}"
        )
    # Weight applies to consume and read (present-and-bound count); it is
    # meaningless on an inhibit arc, which zero-tests and consumes nothing.
    if consume.mode == "inhibit" and consume.weight != 1:
        raise NetValidationError(
            f"weight is not allowed on inhibit arcs (arc from {arc.from_place!r})"
        )
    if consume.weight < 1:
        raise NetValidationError(
            f"consume weight must be >= 1 (arc from {arc.from_place!r})"
        )
    # correlate is the binding-correlated zero-test (anti-join): only an
    # inhibit arc runs a zero-test, so it is meaningless on consume/read arcs
    # (their cross-token conditions over bound tokens are guard territory).
    # References: ADR 0017.
    if consume.correlate is not None and consume.mode != "inhibit":
        raise NetValidationError(
            f"correlate is only allowed on inhibit arcs "
            f"(arc from {arc.from_place!r} has mode {consume.mode!r})"
        )
    # CEL predicate (if any) must compile. A compile error is a malformed net;
    # runtime eval errors against token data are deferred to fire time.
    # References: D6.
    if consume.predicate is not None and consume.predicate.cel is not None:
        _validate_cel(
            consume.predicate.cel,
            context=f"consume predicate on arc from {arc.from_place!r}",
            cel_adapter=cel_adapter,
        )
    # correlate CEL (if any) must likewise compile at parse time; runtime
    # eval errors are deferred to the engine, which fails CLOSED (the
    # candidate binding is blocked). References: D6; ADR 0017.
    if consume.correlate is not None:
        _validate_cel(
            consume.correlate,
            context=f"correlate on inhibit arc from {arc.from_place!r}",
            cel_adapter=cel_adapter,
        )


def _validate_produce_arc(
    arc: Arc,
    produce: ProduceTemplate,
    place_names: set[str],
    transition_names: set[str],
    accepts_by_place: dict[str, list[str]],
    cel_adapter: CelAdapter | None = None,
) -> None:
    # Direction: produce arcs are transition → place.
    if arc.from_transition is None or arc.from_place is not None:
        raise NetValidationError(
            "produce arc must originate at a transition (transition → place)"
        )
    if arc.to_place is None or arc.to_transition is not None:
        raise NetValidationError(
            "produce arc must terminate at a place (transition → place)"
        )
    # Endpoint resolution.
    if arc.from_transition not in transition_names:
        raise NetValidationError(
            f"arc references undeclared transition: {arc.from_transition!r}"
        )
    if arc.to_place not in place_names:
        raise NetValidationError(f"arc references undeclared place: {arc.to_place!r}")
    # Produce destination must equal the arc's `to` place.
    if produce.destination != arc.to_place:
        raise NetValidationError(
            f"produce destination {produce.destination!r} must equal "
            f"arc to-place {arc.to_place!r}"
        )
    # Produce type must be accepted by the destination place.
    if produce.type not in accepts_by_place[arc.to_place]:
        raise NetValidationError(
            f"produce type {produce.type!r} not accepted by place {arc.to_place!r}"
        )
    # Rule 14 (ADR 0023): at most one of literal data and computed cel; the
    # cel must compile at parse like every other inline expression (D6).
    if produce.cel is not None:
        if produce.data is not None:
            raise NetValidationError(
                f"produce template into {arc.to_place!r} declares both "
                "'data' and 'cel'; they are mutually exclusive"
            )
        _validate_cel(
            produce.cel,
            context=f"produce template into {arc.to_place!r}",
            cel_adapter=cel_adapter,
        )


def _find_port(net: Net, port_name: str) -> Port | None:
    """Locate a port facet by place name (ports are place facets)."""
    for place in net.places:
        if place.name == port_name and place.port is not None:
            return place.port
    return None


def _resolve_wire_port(
    alias: str,
    port_name: str,
    role: str,
    direction: str,
    alias_to_net: dict[str, Net],
) -> Port:
    """Resolve a wire endpoint's port and enforce its direction.

    ``role`` ("source"/"target") names the endpoint in error messages; the
    port must declare ``direction`` ("output"/"input") to match the wire's
    flow.
    """
    if alias not in alias_to_net:
        raise NetValidationError(f"wire references unknown net alias: {alias!r}")
    port = _find_port(alias_to_net[alias], port_name)
    if port is None:
        raise NetValidationError(f"net {alias!r} has no port named {port_name!r}")
    if port.direction != direction:
        raise NetValidationError(
            f"wire {role} port {alias!r}.{port_name!r} is not an {direction} port"
        )
    return port


def _validate_wires(wires: list[Wire], alias_to_net: dict[str, Net]) -> None:
    for wire in wires:
        from_port = _resolve_wire_port(
            wire.from_net, wire.from_port, "source", "output", alias_to_net
        )
        to_port = _resolve_wire_port(
            wire.to_net, wire.to_port, "target", "input", alias_to_net
        )
        if from_port.type != to_port.type:
            raise NetValidationError(
                f"wire type mismatch: {wire.from_net!r}.{wire.from_port!r} "
                f"type {from_port.type!r} != {wire.to_net!r}.{wire.to_port!r} "
                f"type {to_port.type!r}"
            )


# ── Public API ───────────────────────────────────────────────────────────
def parse_net(
    source: Mapping[str, Any] | Path | str,
    *,
    cel_adapter: CelAdapter | None = None,
) -> Net:
    """Parse and validate a net document into a :class:`Net`.

    ``source`` may be an already-loaded dict, a path to a JSON file, or a raw
    JSON string. The document is shape-validated against the net JSON Schema
    and then structurally checked (unique names, port acceptance, arc
    direction, CEL compilation). When ``cel_adapter`` is omitted the
    auto-detected default backend is used.
    """
    doc = _load_source(source)
    _validate_against_schema(doc, NET_SCHEMA)

    places = [_parse_place(p) for p in doc["places"]]
    transitions = [_parse_transition(t) for t in doc["transitions"]]
    arcs = [_parse_arc(a) for a in doc["arcs"]]

    initial_marking: Marking | None = None
    marking_doc = doc.get("initialMarking")
    if marking_doc is not None:
        initial_marking = _parse_marking(marking_doc)

    net = Net(
        name=doc["name"],
        places=places,
        transitions=transitions,
        arcs=arcs,
        initial_marking=initial_marking,
        description=doc.get("description"),
        annotations=doc.get("annotations"),
    )
    _validate_net(net, cel_adapter=cel_adapter)
    return net


def parse_composition(
    source: Mapping[str, Any] | Path | str,
    *,
    net_loader: Callable[[str], Mapping[str, Any] | Path | str] | None = None,
    origin: Path | None = None,
) -> Composition:
    """Parse and validate a composition and each referenced net.

    Relative references in an in-memory document require an explicit
    ``origin`` unless ``net_loader`` resolves the literal reference itself.
    A filesystem composition source supplies its parent automatically.
    Loader results remain source material and always pass through
    :func:`parse_net`.
    """
    source_path: Path | None = None
    if isinstance(source, Path):
        source_path = source
    elif isinstance(source, str):
        candidate = Path(source)
        if candidate.exists():
            source_path = candidate
    if origin is None and source_path is not None:
        origin = source_path.parent

    doc = _load_source(source)
    _validate_against_schema(doc, COMPOSITION_SCHEMA)

    def load_ref(ref: str) -> Mapping[str, Any] | Path | str:
        if net_loader is not None:
            try:
                return net_loader(ref)
            except Exception as error:
                raise NetValidationError(
                    f"composition loader failed for reference {ref!r}: {error}"
                ) from error
        ref_path = Path(ref)
        if not ref_path.is_absolute():
            if origin is None:
                raise NetValidationError(
                    f"relative composition net reference {ref!r} requires origin"
                )
            ref_path = origin / ref_path
        if ref_path.suffix.lower() != ".json":
            raise NetValidationError(
                f"unsupported composition net reference extension: {ref!r}"
            )
        return ref_path

    nets: list[NetRef] = []
    alias_to_net: dict[str, Net] = {}
    for entry in doc["nets"]:
        ref = entry["ref"]
        loaded = load_ref(ref)
        if isinstance(loaded, (Net, Composition)):
            raise NetValidationError(
                f"composition loader for {ref!r} must return net source material"
            )
        try:
            parsed = parse_net(loaded)
        except (OSError, UnicodeError, ValueError, NetValidationError) as error:
            raise NetValidationError(
                f"invalid referenced net {ref!r}: {error}"
            ) from error
        alias = entry.get("alias")
        if alias is None:
            # Default to the referenced net's `name` when no explicit alias
            # is given.
            alias = parsed.name
            # A derived alias bypasses the schema's `pattern`, so enforce the
            # identifier invariant here: a non-identifier net name (e.g.
            # "prod.line") would make `<alias>.<placeName>` ambiguous.
            # References: spec/composition.md (default aliasing; aliasing rationale).
            if not _ALIAS_PATTERN.fullmatch(alias):
                raise NetValidationError(
                    f"derived composition alias {alias!r} (from net name) is not a "
                    f"simple identifier; supply an explicit `alias` "
                    f"(pattern {_ALIAS_PATTERN.pattern})"
                )
        if alias in alias_to_net:
            raise NetValidationError(f"duplicate composition alias: {alias!r}")
        alias_to_net[alias] = parsed
        nets.append(NetRef(ref=ref, alias=alias))

    wires: list[Wire] = []
    for w in doc["wires"]:
        frm = w["from"]
        to = w["to"]
        wires.append(
            Wire(
                from_net=frm["net"],
                from_port=frm["port"],
                to_net=to["net"],
                to_port=to["port"],
            )
        )

    _validate_wires(wires, alias_to_net)

    return Composition(nets=nets, wires=wires, parsed_nets=alias_to_net)
