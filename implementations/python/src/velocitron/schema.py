"""Frozen dataclasses for the Petri net JSON schema.

Pure data: no behavior, no validation logic. Validation lives in
``velocitron.parser``. Field names are part of the public contract and are read
by attribute in the test suite — do not rename.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from pyrsistent import PMap, PVector, pmap, pvector


@dataclass(frozen=True)
class Token:
    """A colored token: a type string plus a JSON-object payload."""

    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Port:
    """A place facet marking the place as a composition boundary."""

    direction: Literal["input", "output"]
    type: str


@dataclass(frozen=True)
class CapacityPerColorKey:
    """A place's non-behavioral verification bound (ADR 0019).

    At most ``max`` tokens per distinct value of the token-data key(s)
    ``keys`` (a single-field key parses to a 1-tuple). The engine never
    reads this — it does not gate enablement, binding, firing, or deposit;
    it is consumed only by the declarative property pass
    (:mod:`velocitron.properties`).
    """

    keys: tuple[str, ...]
    max: int


@dataclass(frozen=True)
class Place:
    """A place: name, accepted token types, and an optional port facet."""

    name: str
    accepts: list[str]
    port: Port | None = None
    description: str | None = None
    annotations: dict[str, Any] | None = None
    capacity_per_color_key: CapacityPerColorKey | None = None


@dataclass(frozen=True)
class Timer:
    """A transition's declarative temporal enablement condition (ADR 0018).

    ``clock`` names the clock place whose first token is the time reference
    (exposed to ``cel`` and optional ``maturity`` as the reserved variable
    ``clock``); ``cel`` is the authoritative temporal enablement condition,
    compiled at parse (D6); ``bind`` optionally maps CEL variable names to
    binding places, exposing the candidate binding's tokens to both
    expressions. ``maturity`` is the optional Runtime scheduling expression:
    it declares the next monotonic timestamp at which that candidate may
    mature. The deadline lives in token data — the engine holds no timer state.
    """

    clock: str
    cel: str
    bind: dict[str, str] | None = None
    maturity: str | None = None


@dataclass(frozen=True)
class Transition:
    """A transition: name, optional handler ref, optional guard ref, optional
    priority (honored by the built-in ``priority`` firing policy, ADR 0014),
    optional timer (ADR 0018)."""

    name: str
    handler: str | None = None
    guard: str | None = None
    priority: int | None = None
    timer: Timer | None = None
    description: str | None = None
    annotations: dict[str, Any] | None = None


@dataclass(frozen=True)
class Predicate:
    """A single-token arc filter: inline CEL OR a named pure predicate handler.

    Exactly one of ``cel`` / ``handler`` is set; the other is ``None``.
    """

    cel: str | None
    handler: str | None


@dataclass(frozen=True)
class ConsumePattern:
    """An arc's consume contract: token type, optional predicate, and mode.

    ``mode`` is one of:

    - ``"consume"`` (default) — remove ``weight`` matching tokens on fire.
    - ``"inhibit"`` — a zero-test requiring the absence of a matching token;
      consumes nothing and never contributes to the binding.
    - ``"read"`` — test-without-consume: gates enablement on the presence of
      ``weight`` matching tokens (like consume) and those tokens contribute to
      the binding (guard/handler/firing record see them), but they are NOT
      removed on fire.

    ``weight`` is the number of matching tokens the arc requires (default 1 =
    classical one-token-per-arc). It applies to ``consume`` (that many removed)
    and ``read`` (that many present and bound, none removed); it is ignored
    (and rejected by the parser) on inhibit arcs, which consume nothing by
    definition.

    ``correlate`` (inhibit arcs only, ADR 0017) is the CEL source of a
    binding-correlated zero-test (anti-join): the transition is enabled under
    a candidate binding only if NO matching token in the source place
    satisfies the expression over ``{token: <candidate data>, binding:
    <place-keyed bound-token data>}``. ``None`` (the default) keeps the plain
    whole-place zero-test. Rejected by the parser on consume/read arcs.
    """

    type: str
    predicate: Predicate | None
    mode: Literal["consume", "inhibit", "read"]
    weight: int = 1
    correlate: str | None = None


@dataclass(frozen=True)
class ProduceTemplate:
    """An arc's produce contract: a routing declaration, not the tokens.

    Declares the output ``type`` and ``destination`` place; the handler
    supplies the actual tokens. Optional literal ``data`` lets a passthrough
    or routing transition emit a fixed token without a registered handler.
    Optional ``cel`` (ADR 0023, mutually exclusive with ``data``) is the
    computed variant: evaluated at deposit over the ADR 0017 ``binding``
    map for a pair the handler left uncovered.
    """

    type: str
    destination: str
    data: object | None = None
    cel: str | None = None


@dataclass(frozen=True)
class Arc:
    """An arc-centric edge.

    A consume arc sets ``from_place`` + ``to_transition`` and ``consume``
    (``produce`` is ``None``). A produce arc sets ``from_transition`` +
    ``to_place`` and ``produce`` (``consume`` is ``None``).
    """

    from_place: str | None = None
    from_transition: str | None = None
    to_place: str | None = None
    to_transition: str | None = None
    consume: ConsumePattern | None = None
    produce: ProduceTemplate | None = None
    description: str | None = None
    annotations: dict[str, Any] | None = None


class Marking(Mapping[str, PVector[Token]]):
    """Immutable, persistent, structurally-shared mapping over places.

    A thin wrapper over ``PMap[str, PVector[Token]]`` (pyrsistent): the outer
    map is a persistent map keyed by place, and each per-place collection is a
    persistent vector (``PVector``) rather than a persistent set. The
    ``PVector`` choice is deliberate, for two reasons:

    - ``Token`` is unhashable (a frozen dataclass carrying a ``dict``
      payload), so a hash-keyed ``PSet`` cannot hold tokens at all.

    - Per-place insertion order is load-bearing: deterministic binding
      selection walks each place's tokens lexicographically by insertion
      order so firings replay identically.

    The read API is the full ``collections.abc.Mapping`` surface (``[]``,
    ``.get``, ``in``, ``.items()``, ``.values()``, ``len``, iteration),
    inherited for free. ``Marking`` is unhashable (because ``Token`` is),
    matching the equality-based multiset contract.

    Mutation goes through ``.set(key, iterable)``, which returns a NEW
    ``Marking`` that shares untouched places structurally; in-place writes
    raise (``PMap``/``PVector`` are immutable). The inherited
    ``Mapping.__eq__`` compares content, so a ``Marking`` equals a plain
    ``dict`` of lists when their contents match (``PVector`` compares equal
    to a ``list``).

    References: spec/firing-semantics.md (D2); AGENTS.md.
    """

    __slots__ = ("_pmap",)

    def __init__(
        self, data: Mapping[str, Iterable[Token]] | "Marking" | None = None
    ) -> None:
        if data is None:
            self._pmap: PMap[str, PVector[Token]] = pmap()
        elif isinstance(data, Marking):
            # Re-wrapping a Marking shares its underlying pmap, preserving
            # structural sharing across reconstruction.
            self._pmap = data._pmap
        else:
            self._pmap = pmap(
                {
                    k: v if isinstance(v, PVector) else pvector(v)
                    for k, v in data.items()
                }
            )

    @classmethod
    def _from_pmap(cls, pmap_: PMap[str, PVector[Token]]) -> "Marking":
        """Internal reconstruction seam: wrap an already-built ``PMap``.

        Wraps the pmap without re-coercing its values. Used by ``set`` so
        persistent updates route through here rather than the public
        constructor (which takes the dict-of-lists shape); the pyrsistent
        layer is an implementation detail.

        References: spec/firing-semantics.md (D4).
        """
        m = cls.__new__(cls)
        m._pmap = pmap_
        return m

    def __getitem__(self, key: str) -> PVector[Token]:
        return self._pmap[key]

    def __iter__(self):
        return iter(self._pmap)

    def __len__(self) -> int:
        return len(self._pmap)

    def set(self, key: str, value: Iterable[Token] | PVector[Token]) -> "Marking":
        """Return a new ``Marking`` with ``key`` set to ``value`` (coerced to
        a ``PVector``); untouched places are shared structurally."""
        pv = value if isinstance(value, PVector) else pvector(value)
        return type(self)._from_pmap(self._pmap.set(key, pv))

    def __repr__(self) -> str:
        return f"Marking({dict(self._pmap)!r})"


@dataclass(frozen=True)
class Net:
    """A complete Petri net: places, transitions, arc-centric arcs, marking."""

    name: str
    places: list[Place]
    transitions: list[Transition]
    arcs: list[Arc]
    initial_marking: Marking | None = None
    description: str | None = None
    annotations: dict[str, Any] | None = None


@dataclass(frozen=True)
class Wire:
    """A composition wire: an output port of one net to an input port of another."""

    from_net: str
    from_port: str
    to_net: str
    to_port: str


@dataclass(frozen=True)
class NetRef:
    """A reference to a net document within a composition, with an optional alias."""

    ref: str
    alias: str | None = None


@dataclass(frozen=True)
class Composition:
    """A composition document: net references and the wires connecting their ports."""

    nets: list[NetRef]
    wires: list[Wire]
    parsed_nets: dict[str, Net] | None = None
