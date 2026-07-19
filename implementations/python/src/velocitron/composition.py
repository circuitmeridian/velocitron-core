"""The runtime composition merge engine.

Produces the combined :class:`Net` from a validated :class:`Composition`:
alias-qualifies every place/transition/arc endpoint, fuses wired port-places
into single shared places (place-fusion realization), rewrites arc endpoints
and produce destinations, and re-exposes unwired ports as the composition's
own boundary ports. The output is one :class:`Net`, verifiable as a single
Petri net and runnable by :class:`Engine`.

This module is the merge transform only — parsing and validation live in
:mod:`velocitron.parser` (the locked ``parse_composition`` surface). The
merge is a distinct, file-free transform over already-parsed nets.

References: spec/composition.md; ADR 0004.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .parser import _validate_net, parse_net  # pyright: ignore[reportPrivateUsage]
from .schema import (
    Arc,
    Composition,
    Marking,
    Net,
    Place,
    ProduceTemplate,
    Timer,
    Token,
    Transition,
    Wire,
)


def _map_timer_places(
    timer: Timer | None, rename: Callable[[str], str]
) -> Timer | None:
    """Apply ``rename`` to a timer's place references (clock + bind values).

    The timer's CEL string is NEVER touched — it references only the reserved
    ``clock`` variable and the ``bind`` aliases, which is what makes a timed
    transition composition-safe (ADR 0018): the merge rewrites the place
    *values* exactly as it rewrites arc endpoints (alias-qualification here,
    fusion rewriting via the rewrite map), and the expression survives.
    """
    if timer is None:
        return None
    return Timer(
        clock=rename(timer.clock),
        cel=timer.cel,
        bind=(
            {var: rename(place) for var, place in timer.bind.items()}
            if timer.bind is not None
            else None
        ),
        maturity=timer.maturity,
    )


class _UnionFind:
    """Minimal union-find over port-place qualified names (N-ary fusion).

    Fan-out (one output wired to several inputs) and fan-in (several outputs
    wired to one input) both collapse to a single equivalence class, hence a
    single shared place.
    """

    def __init__(self, names: set[str]) -> None:
        self._parent: dict[str, str] = {n: n for n in names}

    def find(self, name: str) -> str:
        root = name
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[name] != root:
            self._parent[name], name = root, self._parent[name]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _fused_annotations(
    members: list[str], qname_to_place: dict[str, Place]
) -> dict[str, Any]:
    """Annotations for a fused place (spec/composition.md "Fused-place
    annotations").

    Member ports' annotations merge output (source) ports before input
    ports — the fused place is named after its sources, and their
    annotations take the same precedence — each group in sorted
    qualified-name order, the earliest member winning conflicting keys.
    The ``fusion: true`` tag is set last, overriding any member value, so
    the viz renderer's fusion-place styling triggers on every fused place.
    """

    def _is_output(name: str) -> bool:
        port = qname_to_place[name].port
        return port is not None and port.direction == "output"

    ordered = sorted(m for m in members if _is_output(m))
    ordered += sorted(m for m in members if not _is_output(m))
    annotations: dict[str, Any] = {}
    for m in ordered:
        for key, value in (qname_to_place[m].annotations or {}).items():
            annotations.setdefault(key, value)
    annotations["fusion"] = True
    return annotations


def merge_nets(alias_to_net: dict[str, Net], wires: list[Wire]) -> Net:
    """Merge nets under aliases, fusing wired ports into shared places.

    A pure, file-free transform that returns one :class:`Net` structurally
    valid (passes :func:`velocitron.parser._validate_net`) and runnable by
    :class:`velocitron.engine.Engine`.

    Steps:

    1. Qualify every Place and Transition name with its net alias, and index
       Port facets by qualified name.

    2. Build port-fusion equivalence classes from the Wires via union-find.
       A Wire joins an output Port to an input Port; only wired Ports
       participate, so an unwired Port stays a boundary.

    3. Realize each equivalence class as one fused Place, named after its
       sorted output-Port qualified names, and build a rewrite map from every
       member to the fused name. The fused Place is tagged
       ``annotations.fusion = true`` and carries the member Ports'
       annotations, output (source) Ports merging before input Ports, each
       group in sorted qualified-name order (earliest member wins
       conflicting keys; the fusion tag is set last and always wins).

    4. Rewrite Arc endpoints and ProduceTemplate destinations to the
       qualified or fused names, preserving the Arc-centric representation.

    5. Compose initial markings: qualify keys and merge tokens that land on
       the same fused Place.

    6. Validate the composed Net so it is verifiable as one net and executable.
    """
    # ── 1. Qualify places and transitions; index port places by qualified name.
    qualified_places: list[Place] = []
    qname_to_place: dict[str, Place] = {}
    qualified_transitions: list[Transition] = []
    for alias, net in alias_to_net.items():
        for place in net.places:
            qname = f"{alias}.{place.name}"
            # description/annotations carry through qualification — they are
            # doc-only (ADR 0011) but consumers (e.g. the viz renderer's
            # fusion-place styling) read them off the merged net. Fused
            # places (step 3 below) get their own annotations: the member
            # ports' annotations merged plus the `fusion: true` tag.
            qp = Place(
                name=qname,
                accepts=list(place.accepts),
                port=place.port,
                description=place.description,
                annotations=place.annotations,
            )
            qualified_places.append(qp)
            qname_to_place[qname] = qp
        for t in net.transitions:
            qualified_transitions.append(
                Transition(
                    name=f"{alias}.{t.name}",
                    handler=t.handler,
                    guard=t.guard,
                    priority=t.priority,
                    # Timer place references qualify like arc endpoints; the
                    # fusion rewrite is applied in step 4, once the rewrite
                    # map exists. The CEL string is never touched (ADR 0018).
                    timer=_map_timer_places(t.timer, lambda n, a=alias: f"{a}.{n}"),
                    description=t.description,
                    annotations=t.annotations,
                )
            )

    def _q(alias: str, name: str | None) -> str | None:
        return f"{alias}.{name}" if name is not None else None

    # ── 2. Build port-fusion equivalence classes via union-find.
    # A wire joins an output port (from) to an input port (to); fusion is the
    # equivalence relation induced by all wires. Only wired port names
    # participate — an unwired port stays a boundary (no class, no fusion).
    wire_endpoints = [
        (f"{w.from_net}.{w.from_port}", f"{w.to_net}.{w.to_port}") for w in wires
    ]
    wired = {ep for pair in wire_endpoints for ep in pair}

    uf = _UnionFind(wired)
    for src, dst in wire_endpoints:
        uf.union(src, dst)

    classes: dict[str, list[str]] = {}
    for name in sorted(wired):
        classes.setdefault(uf.find(name), []).append(name)

    # rewrite_map: every member of a fused class → the fused place name. The
    # fused name is the sorted `__`-concatenation of the class's OUTPUT
    # (source) port qualified names — the place is named after what deposits
    # into it. Input ports are class members but never appear in the name.
    # Deterministic regardless of wire ordering; handles fan-in (multiple
    # sources) without a tiebreaker.
    rewrite_map: dict[str, str] = {}
    fused_places: list[Place] = []
    for members in classes.values():
        source_ports = sorted(
            m
            for m in members
            if (port := qname_to_place[m].port) is not None
            and port.direction == "output"
        )
        fused_name = "__".join(source_ports)
        accepts = list(
            dict.fromkeys(a for m in members for a in qname_to_place[m].accepts)
        )
        annotations = _fused_annotations(members, qname_to_place)
        fused_places.append(
            Place(name=fused_name, accepts=accepts, port=None, annotations=annotations)
        )
        for m in members:
            rewrite_map[m] = fused_name

    # ── 3. Assemble places: unwired/non-port qualified places + fused places.
    places: list[Place] = [qp for qp in qualified_places if qp.name not in rewrite_map]
    places.extend(fused_places)

    # ── 4. Rewrite arc endpoints and produce destinations to qualified/fused.
    def _rw(name: str | None) -> str | None:
        if name is None:
            return None
        return rewrite_map.get(name, name)

    # Timer place references follow the same fusion rewrite as arc endpoints
    # (a wired clock port fuses into the shared place the timer must read).
    qualified_transitions = [
        t
        if t.timer is None
        else replace(
            t, timer=_map_timer_places(t.timer, lambda n: rewrite_map.get(n, n))
        )
        for t in qualified_transitions
    ]

    arcs: list[Arc] = []
    for alias, net in alias_to_net.items():
        for arc in net.arcs:
            produce = None
            if arc.produce is not None:
                produce = ProduceTemplate(
                    type=arc.produce.type,
                    destination=rewrite_map.get(
                        f"{alias}.{arc.produce.destination}",
                        f"{alias}.{arc.produce.destination}",
                    ),
                    data=arc.produce.data,
                )
            arcs.append(
                Arc(
                    from_place=_rw(_q(alias, arc.from_place)),
                    from_transition=_q(alias, arc.from_transition),
                    to_place=_rw(_q(alias, arc.to_place)),
                    to_transition=_q(alias, arc.to_transition),
                    consume=arc.consume,
                    produce=produce,
                )
            )

    # ── 5. Compose initial markings: qualify keys, merge fused-place keys.
    composed: dict[str, list[Token]] = {}
    has_marking = False
    for alias, net in alias_to_net.items():
        if net.initial_marking is None:
            continue
        has_marking = True
        for key, tokens in net.initial_marking.items():
            qkey = f"{alias}.{key}"
            target = rewrite_map.get(qkey, qkey)
            composed.setdefault(target, []).extend(tokens)
    initial_marking = Marking(composed) if has_marking else None

    result = Net(
        name="composition",
        places=places,
        transitions=qualified_transitions,
        arcs=arcs,
        initial_marking=initial_marking,
    )

    # ── 6. Validate the composed net — "verifiable as one net", executable.
    _validate_net(result)
    return result


def merge_composition(composition: Composition) -> Net:
    """Merge a validated :class:`Composition` into a single :class:`Net`.

    Uses ``composition.parsed_nets`` (populated by
    :func:`velocitron.parser.parse_composition`) when present, closing the
    validate→merge TOCTOU window at no extra parsing cost; falls back to
    re-parsing each ``NetRef.ref`` via :func:`parse_net` for
    directly-constructed ``Composition`` objects where ``parsed_nets`` is
    ``None``. Keys by the already-resolved ``NetRef.alias`` and delegates to
    :func:`merge_nets`.
    """
    if composition.parsed_nets is not None:
        alias_to_net: dict[str, Net] = composition.parsed_nets
    else:
        alias_to_net = {}
        for ref in composition.nets:
            parsed = parse_net(ref.ref)
            alias_to_net[ref.alias if ref.alias is not None else parsed.name] = parsed
    return merge_nets(alias_to_net, composition.wires)
