"""The declarative property pass (ADR 0019; spec/properties.md).

Marking- and replay-level verification for the walks adopters hand-roll:
capacity per color key, stuck-token detection at quiescence, key-correlated
conservation, marking invariants over counts, same-key witnesses, and
per-firing binding checks. The pass is a pure consumer of a :class:`Net`,
a marking, and journal record streams:

- **Non-behavioral** — the engine never reads a property; a violation is a
  *finding* in a :class:`PropertyReport`, never an engine error, and
  properties can never gate firing.
- **Engine-independent** — no handler registry; replay markings are
  reconstructed from the records alone via the D1 per-arc binding split
  (read-mode tokens stay, ADR 0012; ``failed`` records leave the marking
  unchanged; injections apply per ADR 0013).
- **Raises only on programmer error** — a replay-only property handed to
  :func:`check_marking`, or a record stream inconsistent with the
  net/marking (``ValueError``), never on a property violation.

References: spec/properties.md; docs/adr/0019.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Union

from .cel import CelAdapter, get_default_adapter
from .journal import FiringRecord, InjectionRecord
from .schema import Arc, Marking, Net, Token

# The two check scopes for stepwise properties: "always" = every
# intermediate marking of a replay; "quiescence" = the final marking only.
# check_marking applies stepwise properties to its single marking
# regardless of scope.
Scope = Literal["always", "quiescence"]

# A property key: one token-data field name, or a tuple of field names
# forming a composite key (e.g. ("account_id", "crawl_tag")).
Key = Union[str, tuple[str, ...]]


class _Absent:
    """Sentinel grouping tokens that lack a key field (spec/properties.md:
    partially-keyed tokens count against a bound rather than evading it)."""

    def __repr__(self) -> str:
        return "<absent>"


_ABSENT = _Absent()


# ── The property vocabulary ──────────────────────────────────────────────


@dataclass(frozen=True)
class AtMostN:
    """Stepwise (always): <= ``max`` tokens in ``place``, optionally per
    distinct value of ``key``. The net-declared ``capacityPerColorKey``
    bounds surface as instances of this kind via
    :func:`capacity_properties`."""

    place: str
    max: int
    key: Key | None = None


@dataclass(frozen=True)
class PlaceEmpty:
    """Stepwise (default: quiescence — the stuck-token witness): no token
    in ``place``; with ``cel``, no token whose data matches the single-token
    CEL predicate (arc-predicate semantics: an eval error means "does not
    match", mirroring D6)."""

    place: str
    cel: str | None = None
    scope: Scope = "quiescence"


@dataclass(frozen=True)
class EventuallyReaches:
    """Replay-only: every key value that ever ENTERS ``source`` (initial
    marking, firing deposit, or injection) is present in >= 1 of
    ``targets`` at the end of the replay."""

    source: str
    targets: tuple[str, ...]
    key: Key


@dataclass(frozen=True)
class MarkingInvariant:
    """Stepwise (default: always): a CEL predicate over per-place token
    counts, evaluated against ``{"count": {<place>: int}}`` with every
    declared place present (empty = 0). Anything other than a ``true``
    result — including an eval error — is a violation: an invariant that
    cannot be evaluated does not hold (deliberately stricter than D6, which
    keeps FIRING robust; a verification pass must not silently degrade)."""

    cel: str
    scope: Scope = "always"


@dataclass(frozen=True)
class KeyCorrelation:
    """Stepwise (default: always): every token in ``place`` has a same-key
    token in ``witness_place`` in the same marking."""

    place: str
    witness_place: str
    key: Key
    scope: Scope = "always"


@dataclass(frozen=True)
class FiringBinding:
    """Replay-only: checks each ``completed`` firing record of
    ``transition`` directly. Exactly one of ``key`` (all bound input tokens
    share one key value) or ``cel`` (every bound token's data satisfies a
    single-token CEL predicate; an eval error fails the predicate and is
    therefore a violation) must be given."""

    transition: str
    key: Key | None = None
    cel: str | None = None

    def __post_init__(self) -> None:
        if (self.key is None) == (self.cel is None):
            raise ValueError("FiringBinding requires exactly one of 'key' or 'cel'")


Property = Union[
    AtMostN,
    PlaceEmpty,
    EventuallyReaches,
    MarkingInvariant,
    KeyCorrelation,
    FiringBinding,
]

# Stepwise kinds are checked against markings; replay-only kinds are
# meaningful only over a record stream (check_marking rejects them).
_REPLAY_ONLY = (EventuallyReaches, FiringBinding)


# ── Reports ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PropertyViolation:
    """One finding: the kind, a message carrying the offending key value,
    the offending place (when the kind has one), and the 0-based record
    index whose post-state (or record) violates — ``None`` for the initial
    marking, a single-marking check, or an end-of-replay check."""

    kind: str
    message: str
    place: str | None = None
    step: int | None = None


@dataclass(frozen=True)
class PropertyReport:
    """The checker result: all violations, in deterministic walk order
    (step, then property declaration order, then place iteration order)."""

    violations: tuple[PropertyViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


# ── Key helpers ──────────────────────────────────────────────────────────


def _key_fields(key: Key) -> tuple[str, ...]:
    """Normalize a key declaration to its field-name tuple."""
    return (key,) if isinstance(key, str) else tuple(key)


def _key_value(token: Token, fields: tuple[str, ...]) -> tuple[Any, ...]:
    """A token's key value: its data values for the key fields, with the
    absent-marker for a missing field (so partially-keyed tokens group)."""
    return tuple(token.data.get(f, _ABSENT) for f in fields)


def _key_repr(value: tuple[Any, ...]) -> str:
    """Render a key value for messages and grouping (equality-safe: token
    data values may be unhashable, so groups are keyed by this repr)."""
    if len(value) == 1:
        return repr(value[0])
    return repr(value)


# ── Token coercion (records may be JSON-loaded) ──────────────────────────


def _coerce_token(token: Token | Mapping[str, Any]) -> Token:
    """Accept engine-emitted ``Token`` objects or JSON ``{"type", "data"}``
    mappings (e.g. records loaded back from a ``JsonlJournal`` file)."""
    if isinstance(token, Token):
        return token
    return Token(type=token["type"], data=dict(token.get("data", {})))


def _coerce_tokens(tokens: Iterable[Token | Mapping[str, Any]]) -> list[Token]:
    return [_coerce_token(t) for t in tokens]


# ── Public API ───────────────────────────────────────────────────────────


def capacity_properties(net: Net) -> list[AtMostN]:
    """The net's declared ``capacityPerColorKey`` bounds, as :class:`AtMostN`
    properties (checked automatically by both checkers)."""
    return [
        AtMostN(
            place=place.name,
            max=place.capacity_per_color_key.max,
            key=place.capacity_per_color_key.keys,
        )
        for place in net.places
        if place.capacity_per_color_key is not None
    ]


def check_marking(
    net: Net,
    marking: Marking | Mapping[str, Sequence[Token]],
    properties: Iterable[Property] = (),
    *,
    cel_adapter: CelAdapter | None = None,
) -> PropertyReport:
    """Check one marking: the net's capacity declarations plus every given
    stepwise property (scope is irrelevant for a single marking).

    A replay-only property (:class:`EventuallyReaches`,
    :class:`FiringBinding`) here is a programmer error → ``ValueError``.
    """
    props = list(properties)
    for prop in props:
        if isinstance(prop, _REPLAY_ONLY):
            raise ValueError(
                f"{type(prop).__name__} is a replay-only property; use check_replay"
            )
    checker = _Checker(net, cel_adapter)
    violations: list[PropertyViolation] = []
    for prop in [*capacity_properties(net), *props]:
        violations.extend(checker.check_stepwise(prop, marking, step=None))
    return PropertyReport(tuple(violations))


def check_replay(
    net: Net,
    initial_marking: Marking | Mapping[str, Sequence[Token]],
    records: Iterable[FiringRecord | InjectionRecord | Mapping[str, Any]],
    properties: Iterable[Property] = (),
    *,
    cel_adapter: CelAdapter | None = None,
) -> PropertyReport:
    """Reconstruct every intermediate marking along ``records`` and check:

    - ``scope="always"`` stepwise properties (and the net's capacity
      declarations) at the initial marking (``step=None``) and after every
      record (``step=<record index>``);
    - ``scope="quiescence"`` stepwise properties at the final marking;
    - replay-only properties over the stream (:class:`FiringBinding` per
      ``completed`` record; :class:`EventuallyReaches` at the end).

    A record stream inconsistent with the net/marking raises ``ValueError``
    (corruption, not a property violation).
    """
    props = list(properties)
    checker = _Checker(net, cel_adapter)
    walker = _ReplayWalker(net, initial_marking)

    always = [
        p
        for p in [*capacity_properties(net), *props]
        if not isinstance(p, _REPLAY_ONLY) and getattr(p, "scope", "always") == "always"
    ]
    quiescence = [
        p
        for p in props
        if not isinstance(p, _REPLAY_ONLY)
        and getattr(p, "scope", "always") == "quiescence"
    ]
    bindings = [p for p in props if isinstance(p, FiringBinding)]
    reaches = [p for p in props if isinstance(p, EventuallyReaches)]

    violations: list[PropertyViolation] = []
    entered: dict[int, set[str]] = {
        i: walker.entered_keys(prop) for i, prop in enumerate(reaches)
    }

    def _check_always(step: int | None) -> None:
        for prop in always:
            violations.extend(checker.check_stepwise(prop, walker.marking, step))

    _check_always(step=None)
    for step, record in enumerate(records):
        record = dict(record)
        walker.apply(record)
        _check_always(step)
        for prop in bindings:
            violations.extend(checker.check_binding(prop, record, step))
        for i, prop in enumerate(reaches):
            entered[i].update(walker.new_entries(prop, record))

    for prop in quiescence:
        violations.extend(checker.check_stepwise(prop, walker.marking, step=None))
    for i, prop in enumerate(reaches):
        violations.extend(checker.check_reaches(prop, entered[i], walker.marking))
    return PropertyReport(tuple(violations))


# ── The per-kind checker ─────────────────────────────────────────────────


class _Checker:
    """Evaluates one property kind against one marking or record.

    Owns the CEL compile cache (one compile per expression per checker
    call, mirroring the engine's compile-once convention).
    """

    def __init__(self, net: Net, cel_adapter: CelAdapter | None) -> None:
        self._net = net
        self._adapter = cel_adapter or get_default_adapter()
        self._cache: dict[str, Any] = {}

    # -- CEL plumbing ------------------------------------------------------

    def _compiled(self, expr: str) -> Any:
        compiled = self._cache.get(expr)
        if compiled is None:
            compiled = self._adapter.compile(expr)  # pyright: ignore[reportUnknownMemberType]
            self._cache[expr] = compiled
        return compiled

    def _token_matches(self, cel: str | None, token: Token) -> bool:
        """Single-token CEL predicate with arc-predicate semantics: an eval (or
        compile) error means the token does not match (D6 mirror)."""
        if cel is None:
            return True
        try:
            result = self._adapter.eval(self._compiled(cel), token.data)  # pyright: ignore[reportUnknownMemberType]
        except Exception:  # noqa: BLE001 - eval error => does not match (D6)
            return False
        return bool(result)

    def _invariant_holds(
        self, cel: str, marking: Mapping[str, Sequence[Token]]
    ) -> bool:
        """A counts invariant holds iff it evaluates to exactly ``true``;
        an eval/compile error or non-true result is a violation
        (spec/properties.md: stricter than D6 by design)."""
        counts = {
            place.name: len(marking.get(place.name, [])) for place in self._net.places
        }
        try:
            result = self._adapter.eval(self._compiled(cel), {"count": counts})  # pyright: ignore[reportUnknownMemberType]
        except Exception:  # noqa: BLE001 - unevaluable invariant => violation
            return False
        # `is True`, not bool(): anything other than a true result is a
        # violation (spec/properties.md). All shipped adapters normalize
        # results to native Python primitives, so a CEL true arrives here
        # as exactly `True`.
        return result is True

    # -- Stepwise kinds ----------------------------------------------------

    def check_stepwise(
        self,
        prop: Property,
        marking: Mapping[str, Sequence[Token]],
        step: int | None,
    ) -> list[PropertyViolation]:
        if isinstance(prop, AtMostN):
            return self._check_at_most_n(prop, marking, step)
        if isinstance(prop, PlaceEmpty):
            return self._check_place_empty(prop, marking, step)
        if isinstance(prop, MarkingInvariant):
            return self._check_invariant(prop, marking, step)
        if isinstance(prop, KeyCorrelation):
            return self._check_correlation(prop, marking, step)
        raise ValueError(f"not a stepwise property: {prop!r}")

    def _check_at_most_n(
        self, prop: AtMostN, marking: Mapping[str, Sequence[Token]], step: int | None
    ) -> list[PropertyViolation]:
        tokens = marking.get(prop.place, [])
        if prop.key is None:
            if len(tokens) <= prop.max:
                return []
            return [
                PropertyViolation(
                    kind="at-most-n",
                    message=(
                        f"place {prop.place!r} holds {len(tokens)} tokens; "
                        f"at most {prop.max} allowed"
                    ),
                    place=prop.place,
                    step=step,
                )
            ]
        fields = _key_fields(prop.key)
        groups: dict[str, int] = {}
        for token in tokens:
            key_repr = _key_repr(_key_value(token, fields))
            groups[key_repr] = groups.get(key_repr, 0) + 1
        return [
            PropertyViolation(
                kind="at-most-n",
                message=(
                    f"place {prop.place!r} holds {count} tokens for key "
                    f"{fields} = {key_repr}; at most {prop.max} allowed"
                ),
                place=prop.place,
                step=step,
            )
            for key_repr, count in groups.items()
            if count > prop.max
        ]

    def _check_place_empty(
        self, prop: PlaceEmpty, marking: Mapping[str, Sequence[Token]], step: int | None
    ) -> list[PropertyViolation]:
        matching = [
            t for t in marking.get(prop.place, []) if self._token_matches(prop.cel, t)
        ]
        if not matching:
            return []
        qualifier = f" matching {prop.cel!r}" if prop.cel is not None else ""
        return [
            PropertyViolation(
                kind="place-empty",
                message=(
                    f"place {prop.place!r} expected empty but holds "
                    f"{len(matching)} token(s){qualifier}"
                ),
                place=prop.place,
                step=step,
            )
        ]

    def _check_invariant(
        self,
        prop: MarkingInvariant,
        marking: Mapping[str, Sequence[Token]],
        step: int | None,
    ) -> list[PropertyViolation]:
        if self._invariant_holds(prop.cel, marking):
            return []
        return [
            PropertyViolation(
                kind="marking-invariant",
                message=f"invariant {prop.cel!r} does not hold",
                place=None,
                step=step,
            )
        ]

    def _check_correlation(
        self,
        prop: KeyCorrelation,
        marking: Mapping[str, Sequence[Token]],
        step: int | None,
    ) -> list[PropertyViolation]:
        fields = _key_fields(prop.key)
        witnesses = {
            _key_repr(_key_value(t, fields))
            for t in marking.get(prop.witness_place, [])
        }
        violations: list[PropertyViolation] = []
        for token in marking.get(prop.place, []):
            key_repr = _key_repr(_key_value(token, fields))
            if key_repr not in witnesses:
                violations.append(
                    PropertyViolation(
                        kind="key-correlation",
                        message=(
                            f"token in {prop.place!r} with key {fields} = "
                            f"{key_repr} has no same-key witness in "
                            f"{prop.witness_place!r}"
                        ),
                        place=prop.place,
                        step=step,
                    )
                )
        return violations

    # -- Replay-only kinds -------------------------------------------------

    def check_binding(
        self, prop: FiringBinding, record: Mapping[str, Any], step: int
    ) -> list[PropertyViolation]:
        if (
            "firingId" not in record
            or record.get("status") != "completed"
            or record.get("transition") != prop.transition
        ):
            return []
        bound = [
            token
            for tokens in record.get("inputTokens", {}).values()
            for token in _coerce_tokens(tokens)
        ]
        if prop.key is not None:
            fields = _key_fields(prop.key)
            distinct = {_key_repr(_key_value(t, fields)) for t in bound}
            if len(distinct) <= 1:
                return []
            return [
                PropertyViolation(
                    kind="firing-binding",
                    message=(
                        f"firing of {prop.transition!r} bound tokens of "
                        f"{len(distinct)} distinct keys {fields}: "
                        f"{sorted(distinct)}"
                    ),
                    place=None,
                    step=step,
                )
            ]
        failing = [t for t in bound if not self._token_matches(prop.cel, t)]
        if not failing:
            return []
        return [
            PropertyViolation(
                kind="firing-binding",
                message=(
                    f"firing of {prop.transition!r} bound {len(failing)} "
                    f"token(s) failing {prop.cel!r}"
                ),
                place=None,
                step=step,
            )
        ]

    def check_reaches(
        self,
        prop: EventuallyReaches,
        entered: set[str],
        final_marking: Mapping[str, Sequence[Token]],
    ) -> list[PropertyViolation]:
        fields = _key_fields(prop.key)
        reached = {
            _key_repr(_key_value(token, fields))
            for target in prop.targets
            for token in final_marking.get(target, [])
        }
        return [
            PropertyViolation(
                kind="eventually-reaches",
                message=(
                    f"key {fields} = {key_repr} entered {prop.source!r} but "
                    f"is in none of {prop.targets!r} at end of replay"
                ),
                place=prop.source,
                step=None,
            )
            for key_repr in sorted(entered)
            if key_repr not in reached
        ]


# ── Replay reconstruction ────────────────────────────────────────────────


class _ReplayWalker:
    """Reconstructs intermediate markings from a record stream alone.

    ``completed`` firing records: the consumed multiset is recovered by
    splitting each place's ``inputTokens`` back into per-arc slices (D1:
    per-arc ``weight`` tokens concatenated in arc-declaration order across
    the transition's consume- and read-mode arcs) and removing only the
    consume-mode slices (read tokens stay, ADR 0012); ``outputTokens`` are
    then appended. ``failed`` records leave the marking unchanged (atomic
    rollback). Injection records apply per ADR 0013 (inject appends,
    update replaces). Any inconsistency with the net/marking raises
    ``ValueError``.
    """

    def __init__(
        self, net: Net, initial_marking: Marking | Mapping[str, Sequence[Token]]
    ) -> None:
        self._net = net
        self.marking: dict[str, list[Token]] = {
            place: list(tokens) for place, tokens in initial_marking.items()
        }

    # -- Record application ------------------------------------------------

    def apply(self, record: Mapping[str, Any]) -> None:
        if "injectionId" in record:
            self._apply_injection(record)
        else:
            self._apply_firing(record)

    def _apply_injection(self, record: Mapping[str, Any]) -> None:
        place = record["place"]
        tokens = _coerce_tokens(record["tokens"])
        if record["kind"] == "update":
            self.marking[place] = tokens
        else:
            self.marking.setdefault(place, []).extend(tokens)

    def _apply_firing(self, record: Mapping[str, Any]) -> None:
        if record.get("status") != "completed":
            return  # atomic rollback: a failed attempt changed nothing
        input_tokens = {
            place: _coerce_tokens(tokens)
            for place, tokens in record.get("inputTokens", {}).items()
        }
        consumed = self._consumed_slices(record["transition"], input_tokens)
        for place, tokens in consumed.items():
            self._remove_each(place, tokens)
        for place, tokens in record.get("outputTokens", {}).items():
            self.marking.setdefault(place, []).extend(_coerce_tokens(tokens))

    # -- The D1 split ------------------------------------------------------

    def _binding_arcs(self, transition: str) -> list[Arc]:
        """Consume- and read-mode input arcs of ``transition``, in net
        declaration order — the order D1 concatenated the binding in."""
        return [
            arc
            for arc in self._net.arcs
            if arc.consume is not None
            and arc.consume.mode in ("consume", "read")
            and arc.to_transition == transition
        ]

    def _consumed_slices(
        self, transition: str, input_tokens: dict[str, list[Token]]
    ) -> dict[str, list[Token]]:
        """Split each place's recorded binding back into per-arc slices and
        keep only the consume-mode slices (the tokens the fire removed)."""
        offsets: dict[str, int] = {}
        consumed: dict[str, list[Token]] = {}
        for arc in self._binding_arcs(transition):
            place = arc.from_place
            assert place is not None and arc.consume is not None
            weight = arc.consume.weight
            start = offsets.get(place, 0)
            arc_slice = input_tokens.get(place, [])[start : start + weight]
            if len(arc_slice) != weight:
                raise ValueError(
                    f"record for transition {transition!r} carries fewer "
                    f"inputTokens for place {place!r} than the net's binding "
                    f"arcs require — the stream does not match this net"
                )
            offsets[place] = start + weight
            if arc.consume.mode == "consume":
                consumed.setdefault(place, []).extend(arc_slice)
        for place, tokens in input_tokens.items():
            if offsets.get(place, 0) != len(tokens):
                raise ValueError(
                    f"record for transition {transition!r} carries more "
                    f"inputTokens for place {place!r} than the net's binding "
                    f"arcs account for — the stream does not match this net"
                )
        return consumed

    # -- Equality-based multiset removal -----------------------------------

    def _remove_each(self, place: str, tokens: list[Token]) -> None:
        """Remove each token once by equality, counting multiplicities —
        ``Token.data`` is an unhashable dict, so multiset removal is
        list-remove by ``==``, never hash-based (the net-schema multiset
        rule; same approach as the engine's ``_remove_each``)."""
        available = self.marking.setdefault(place, [])
        for token in tokens:
            try:
                available.remove(token)
            except ValueError as exc:
                raise ValueError(
                    f"record consumed a token absent from place {place!r} — "
                    f"the stream does not match this net/marking"
                ) from exc

    # -- EventuallyReaches entry tracking ------------------------------------

    def entered_keys(self, prop: EventuallyReaches) -> set[str]:
        """Key-value reprs present in the source place right now (the
        initial entrants)."""
        fields = _key_fields(prop.key)
        return {
            _key_repr(_key_value(t, fields)) for t in self.marking.get(prop.source, [])
        }

    def new_entries(
        self, prop: EventuallyReaches, record: Mapping[str, Any]
    ) -> set[str]:
        """Key-value reprs the just-applied record moved INTO the source
        place (a completed firing's deposit, or an injection's tokens)."""
        fields = _key_fields(prop.key)
        arrivals: list[Token] = []
        if "injectionId" in record:
            if record["place"] == prop.source:
                arrivals = _coerce_tokens(record["tokens"])
        elif record.get("status") == "completed":
            arrivals = _coerce_tokens(
                record.get("outputTokens", {}).get(prop.source, [])
            )
        return {_key_repr(_key_value(t, fields)) for t in arrivals}
