"""The reference firing engine for velocitron nets.

Drives the full firing phase pipeline for a loaded :class:`Net`:

- **Enablement** — detect which transitions have a satisfiable binding in
  the current :class:`Marking`.
- **Binding** — deterministic first-enabled binding selection across the
  consume arcs' lexicographic token combinations.
- **Firing** — consume → invoke handler → deposit per the produce contract
  → record, with atomic rollback of the tentative consume on any failure.
- **Selection loop** — repeat enable → pick via firing policy → fire until
  quiescence or ``max_steps``.
- **CEL predicate evaluation** — exact string equalities recognized once; all
  other arc filters compiled once per expression and evaluated per token.
- **Timed transitions** — declarative ``timer`` enablement over a clock
  place's token (ADR 0018), with ``tick`` as the engine-owned
  advance-and-re-evaluate loop.
- **Deposit-violation handling** — configurable ``raise`` /
  ``record_then_raise`` / ``record_then_drop`` modes.

The journal is decoupled via hooks: the engine emits :class:`FiringRecord`
 objects with NO ``sequence`` — the journal implementation owns numbering.

References: spec/firing-semantics.md (D2, D3, D4, D6).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from itertools import combinations, product
from types import MappingProxyType
import re
import logging
import math
from typing import Any, NamedTuple, cast
from weakref import ReferenceType, ref

from pyrsistent import pvector

from .cel import CelAdapter, get_default_adapter
from .contract import (
    FiringContext,
    FiringPolicyInput,
    FiringTimestamps,
    GuardHandler,
    GuardHandlerInput,
    HandlerError,
    PredicateHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from .journal import FiringRecord, FiringStatus, InjectionKind, InjectionRecord, Journal
from .registry import DEFAULT_FIRING_POLICY, HandlerNotFound, HandlerRegistry
from .schema import (
    Arc,
    ConsumePattern,
    Marking,
    Net,
    Place,
    Predicate,
    ProduceTemplate,
    Timer,
    Token,
    Transition,
)

# The three legal deposit-violation modes.
_DEPOSIT_VIOLATION_MODES = ("raise", "record_then_raise", "record_then_drop")

# Deliberately narrower than CEL's full string grammar.  Only an unqualified
# identifier, equality, and a quoted string whose escapes are unambiguously
# quote/backslash escapes can bypass the adapter.  Every other valid CEL form
# stays on the backend path.
_STRING_EQUALITY = re.compile(
    r"""[ \t\r\n]*([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*==[ \t\r\n]*"""
    r"""(?:"((?:[^\x00-\x1f"\\]|\\["\\])*)"|'((?:[^\x00-\x1f'\\]|\\['\\])*)')"""
    r"""[ \t\r\n]*"""
)
# CEL's lexical grammar excludes KEYWORD from SELECTOR and RESERVED from IDENT.
_CEL_RESERVED_WORDS = frozenset(
    (
        "false in null true "
        "as break const continue else for function if import let loop "
        "package namespace return var void while"
    ).split()
)

_CEL_FAST_PATH_UNCACHED = object()
_LOGGER = logging.getLogger(__name__)


def _string_equality(expr: str) -> tuple[str, str] | None:
    """Extract the one CEL equality shape whose Python semantics are exact."""
    match = _STRING_EQUALITY.fullmatch(expr)
    if match is None:
        return None
    identifier = match.group(1)
    if identifier in _CEL_RESERVED_WORDS:
        return None
    encoded = match.group(2)
    if encoded is None:
        encoded = match.group(3)
    if "\\" not in encoded:
        return identifier, encoded

    decoded: list[str] = []
    index = 0
    while index < len(encoded):
        char = encoded[index]
        if char == "\\":
            index += 1
            char = encoded[index]
        decoded.append(char)
        index += 1
    return identifier, "".join(decoded)


class _Binding(NamedTuple):
    """A selected binding: the tokens the fire sees vs the tokens it removes.

    ``tokens`` is the FULL binding — consume- and read-mode arc tokens keyed by
    source place — which the guard, handler (``inputTokens``), and firing record
    all see. ``consumed`` is the consume-only subset the engine removes on fire;
    read-mode arcs are test-without-consume, so their tokens are in ``tokens``
    but never in ``consumed``. With no read arcs the two are equal, so the
    classical case is unchanged.

    References: spec/firing-semantics.md (a, b, D1); ADR 0012.
    """

    tokens: dict[str, list[Token]]
    consumed: dict[str, list[Token]]


class TimerMaturity(NamedTuple):
    """A future clock advance candidate declared by one timer binding."""

    transition: str
    clock: str
    at: float


class FiringReservation(NamedTuple):
    """A binding whose consume-mode tokens have been reserved for async work.

    ``Engine.fire`` remains the synchronous one-shot primitive.  Long-lived
    runtimes use this explicit two-phase seam instead: :meth:`Engine.reserve`
    selects a binding and removes only its consume-mode tokens, then
    :meth:`Engine.settle` either deposits a completed handler result or restores
    the reserved input on failure.  A runtime must admit only independent
    reservations, so no other in-flight operation can mutate the reservation's
    consumed places before it settles.
    """

    net: Net
    original_marking: Marking
    reserved_marking: Marking
    transition: str
    attempt: int
    context: FiringContext
    input_tokens: dict[str, list[Token]]
    consumed_tokens: dict[str, list[Token]]


class _TopologyIndex(NamedTuple):
    """Immutable, declaration-ordered lookup tables for one :class:`Net`."""

    transitions: tuple[Transition, ...]
    transition_by_name: Mapping[str, Transition]
    place_by_name: Mapping[str, Place]
    input_arcs: Mapping[tuple[str, str], tuple[Arc, ...]]
    binding_arcs: Mapping[str, tuple[Arc, ...]]
    produce_arcs: Mapping[str, tuple[Arc, ...]]
    candidate_mask_by_place: Mapping[str, int]
    always_candidate_mask: int


class DepositViolation(Exception):
    """Raised on a deposit-contract violation when configured to raise.

    A deposit violation is a programmer-bug signal: the transition handler
    returned tokens to a place with no matching produce template, or with the
    wrong token type. Carry a human-readable message.

    References: spec/firing-semantics.md (D3).
    """


class _ProduceCelError(Exception):
    """Internal: a produce template's ``cel`` failed at deposit (ADR 0023).

    An evaluation error or a non-object result while emitting a computed
    fallback. Never escapes ``fire``/``settle`` — both catch it and route the
    failure through the ordinary deposit-contract-violation handling (D3),
    carrying this exception's message as the violation detail.
    """


class UnknownTransitionError(Exception):
    """Raised when a transition name does not resolve to a declared transition.

    A public engine entry point that takes a transition name (``fire``,
    ``select_binding``) rejects a name absent from the net's declared
    transitions: an unknown name (a typo) is a **programmer bug**, surfaced as a
    raise rather than silently firing with an empty binding — the empty binding
    is the legitimate path for a *declared* source transition (no input arcs),
    and an undeclared name is indistinguishable from it inside
    ``_select_binding`` (both have no binding arcs), so the check must live at
    the name-taking public boundary.

    Distinct from :class:`HandlerNotFound` (a net-referenced handler ref missing
    from the registry, which the engine degrades to a *transition failure* — a
    ``failed`` record): here the transition itself is not in the net, so there
    is no transition to fail — the call is malformed. Mirrors
    ``inject_token``'s ``ValueError`` for an unknown place (a programmer bug the
    seam raises on, not a firing outcome).

    Carries the offending :attr:`transition` name and the :attr:`net` name.

    References: spec/firing-semantics.md (D10).
    """

    transition: str
    net: str

    def __init__(self, net: str, transition: str) -> None:
        super().__init__(f"net {net!r} has no transition named {transition!r}")
        self.net = net
        self.transition = transition


class Engine:
    """The reference firing engine for velocitron nets.

    Owns the firing phase pipeline end to end:

    - **Enablement** — which transitions have a satisfiable binding.
    - **Binding** — deterministic first-enabled binding selection.
    - **Firing** — consume → invoke → deposit → record, with atomic rollback
      of the tentative consume on any failure.
    - **Selection loop** — repeat until quiescence or ``max_steps``.
    - **CEL predicate evaluation** — cached exact-string fast path, otherwise
      compile-once and eval-per-token arc filters.
    - **Timed transitions** — declarative ``timer`` enablement + ``tick``
      (ADR 0018).
    - **Deposit-violation handling** — configurable raise / record / drop modes.

    The engine never assigns ``sequence`` to a record — that is the journal's
    concern.

    References: spec/firing-semantics.md (D4).
    """

    def __init__(
        self,
        registry: HandlerRegistry,
        *,
        policy: str = DEFAULT_FIRING_POLICY,
        journal: Journal | None = None,
        deposit_violation: str = "raise",
        cel_adapter: CelAdapter | None = None,
        max_consecutive_failures: int | None = None,
    ) -> None:
        if deposit_violation not in _DEPOSIT_VIOLATION_MODES:
            raise ValueError(
                f"deposit_violation must be one of {_DEPOSIT_VIOLATION_MODES}, "
                f"got {deposit_violation!r}"
            )
        # The opt-in failure budget (ADR 0015): None (default) = unlimited —
        # today's behavior, a persistently failing transition stays selectable
        # forever. An int >= 1 caps consecutive failed firings per transition
        # within a run; 0 would exhaust every transition before its first
        # fire, so it is a configuration error like a bad deposit_violation.
        if max_consecutive_failures is not None and max_consecutive_failures < 1:
            raise ValueError(
                "max_consecutive_failures must be >= 1 (or None for no "
                f"budget), got {max_consecutive_failures!r}"
            )
        # "raise" is the only legal mode when no journal is attached: the
        # record_then_* modes need somewhere to record the violation.
        if deposit_violation != "raise" and journal is None:
            raise ValueError(
                "deposit_violation='record_then_raise'/'record_then_drop' "
                "requires a journal"
            )
        # Validate the firing-policy ref at construction: it is engine
        # config (not net-referenced), known at __init__ with no transition
        # context to fail within. An unresolvable policy is a configuration
        # error raised here -- not a HandlerNotFound crashing out of `run`
        # on the first step. The resolve-miss-as-transition-failure rule
        # scopes to net-referenced handlers within fire/enablement.
        # References: spec/firing-semantics.md (e).
        registry.resolve_firing_policy(policy)
        self.registry = registry
        self.policy = policy
        self.journal = journal
        self.deposit_violation = deposit_violation
        self.max_consecutive_failures = max_consecutive_failures
        # CEL: one adapter, a compile-once-per-expression backend cache, and a
        # cached recognizer for the exact identifier == string-literal shape.
        # The adapter abstracts the CEL backend -- pure-Python ``cel-python``
        # (imported as ``celpy``, default), with ``cel-expr-python`` (C++) and
        # ``common-expression-language`` (Rust) as optional extras;
        # auto-detection prefers Rust → C++ → pure Python. Eval errors raise
        # CelEvalError, caught in _eval_predicate. D6.
        self._cel_adapter: CelAdapter = cel_adapter or get_default_adapter()
        self._cel_cache: dict[str, Any] = {}
        self._cel_string_equalities: dict[str, tuple[str, str] | None] = {}
        # Key by identity because Net's frozen dataclass contains unhashable
        # list fields. A weak reference avoids retaining every Net shown to a
        # long-lived Engine; checking its referent makes id reuse harmless.
        self._topology_cache: dict[int, tuple[ReferenceType[Net], _TopologyIndex]] = {}

    def _topology(self, net: Net) -> _TopologyIndex:
        """Return the immutable topology index belonging to ``net``."""
        key = id(net)
        cached = self._topology_cache.get(key)
        if cached is not None and cached[0]() is net:
            return cached[1]

        transitions = tuple(net.transitions)
        transition_by_name = {tr.name: tr for tr in transitions}
        place_by_name = {place.name: place for place in net.places}
        input_lists: dict[tuple[str, str], list[Arc]] = {}
        binding_lists: dict[str, list[Arc]] = {}
        produce_lists: dict[str, list[Arc]] = {}
        for arc in net.arcs:
            if arc.consume is not None and arc.to_transition is not None:
                input_lists.setdefault(
                    (arc.to_transition, arc.consume.mode), []
                ).append(arc)
                if arc.consume.mode in ("consume", "read"):
                    binding_lists.setdefault(arc.to_transition, []).append(arc)
            elif arc.produce is not None and arc.from_transition is not None:
                produce_lists.setdefault(arc.from_transition, []).append(arc)

        # Every binding arc must be satisfied, so one declaration-first source
        # place is a safe necessary condition for probing that transition.
        # Source/inhibit-only transitions have no such condition and remain
        # candidates on every pass. Bit positions preserve transition order
        # without caching any marking-dependent enablement result.
        candidate_mask_by_place: dict[str, int] = {}
        always_candidate_mask = 0
        for position, tr in enumerate(transitions):
            bit = 1 << position
            tr_binding_arcs = binding_lists.get(tr.name)
            if tr_binding_arcs:
                place = tr_binding_arcs[0].from_place
                assert place is not None
                candidate_mask_by_place[place] = (
                    candidate_mask_by_place.get(place, 0) | bit
                )
            else:
                always_candidate_mask |= bit

        topology = _TopologyIndex(
            transitions=transitions,
            transition_by_name=MappingProxyType(transition_by_name),
            place_by_name=MappingProxyType(place_by_name),
            input_arcs=MappingProxyType(
                {key: tuple(arcs) for key, arcs in input_lists.items()}
            ),
            binding_arcs=MappingProxyType(
                {name: tuple(arcs) for name, arcs in binding_lists.items()}
            ),
            produce_arcs=MappingProxyType(
                {name: tuple(arcs) for name, arcs in produce_lists.items()}
            ),
            candidate_mask_by_place=MappingProxyType(candidate_mask_by_place),
            always_candidate_mask=always_candidate_mask,
        )

        engine_ref = ref(self)

        def discard(dead_ref: ReferenceType[Net]) -> None:
            engine = engine_ref()
            if engine is None:
                return
            current = engine._topology_cache.get(key)
            if current is not None and current[0] is dead_ref:
                engine._topology_cache.pop(key, None)

        self._topology_cache[key] = (ref(net, discard), topology)
        return topology

    def _candidate_transitions(
        self, net: Net, marking: Marking
    ) -> Iterator[Transition]:
        """Yield transitions that can be enabled, in declaration order."""
        topology = self._topology(net)
        mask = topology.always_candidate_mask
        for place, place_mask in topology.candidate_mask_by_place.items():
            if marking.get(place, ()):
                mask |= place_mask
        while mask:
            bit = mask & -mask
            yield topology.transitions[bit.bit_length() - 1]
            mask ^= bit

    # ── CEL predicate evaluation ────────────────────────────────────────

    def _compile_cel(self, expr: str) -> Any:
        """Return the cached compiled CEL program for ``expr``.

        Compile at first use, not per token: a predicate's expression is
        stable across the tokens it filters. Runtime eval errors (undeclared
        refs, type errors) are handled by the caller, not here.
        """
        compiled = self._cel_cache.get(expr)
        if compiled is None:
            compiled = self._cel_adapter.compile(expr)  # pyright: ignore[reportUnknownMemberType]
            self._cel_cache[expr] = compiled
        return compiled

    def _eval_predicate(
        self, predicate: Predicate | None, token: Token, ctx: FiringContext
    ) -> bool:
        """Evaluate a single-token arc filter.

        ``None`` predicate -> True (unrestricted consume). A safe exact string
        equality may be evaluated directly; every other CEL predicate is
        evaluated against ``token.data`` through the adapter. A runtime eval
        error (undeclared ref, type error) yields False, not a crash. A named
        predicate handler is resolved and called; any failure (incl.
        :class:`HandlerNotFound`) yields False.

        References: spec/firing-semantics.md (D6).
        """
        if predicate is None:
            return True
        cel = predicate.cel
        if cel is not None:
            try:
                fast_path = self._cel_string_equalities[cel]
            except KeyError:
                fast_path = _string_equality(cel)
                self._cel_string_equalities[cel] = fast_path
            if fast_path is not None:
                identifier, expected = fast_path
                actual = token.data.get(identifier, _CEL_FAST_PATH_UNCACHED)
                # A present, exact string is the only data shape whose result
                # is backend-independent. Missing or differently typed values
                # retain the adapter's error/coercion behavior below.
                if type(actual) is str:
                    return actual == expected
            try:
                result = self._cel_adapter.eval(self._compile_cel(cel), token.data)  # pyright: ignore[reportUnknownMemberType]
            except Exception:  # noqa: BLE001 - any eval error => predicate false
                # Runtime eval error (adapter raises CelEvalError) =>
                # predicate false.
                return False
            return result is True
        if predicate.handler is not None:
            try:
                handler = self.registry.resolve_predicate(predicate.handler)
                result = handler(PredicateHandlerInput(token=token, firingContext=ctx))
            except Exception:  # noqa: BLE001 - any handler failure => predicate false, symmetric with CEL eval
                # Any handler failure (unresolved ref, runtime error) =>
                # predicate false, symmetric with a CEL eval error.
                return False
            return result is True
        # Neither cel nor handler set: unrestricted.
        return True

    def _timer_holds(
        self,
        timer: Timer,
        clock_data: dict[str, Any],
        binding: dict[str, list[Token]],
    ) -> bool:
        """Whether a timer's temporal condition holds for a candidate binding.

        Evaluates ``timer.cel`` against the closed environment: the reserved
        variable ``clock`` is the clock token's ``data`` and each ``bind``
        variable is the ``data`` of the first token bound from its place (the
        parser guarantees every bind place is a binding-arc source, so it is
        present in every candidate binding). A runtime eval error degrades to
        condition-false — the binding is simply not matured — symmetric with a
        predicate's eval error (D6). An instance method: it compiles and
        evaluates CEL.

        References: spec/firing-semantics.md (a, f); ADR 0018.
        """
        env: dict[str, Any] = {"clock": clock_data}
        for var, place in (timer.bind or {}).items():
            env[var] = binding[place][0].data
        try:
            result = self._cel_adapter.eval(self._compile_cel(timer.cel), env)  # pyright: ignore[reportUnknownMemberType]
        except Exception:  # noqa: BLE001 - any eval error => condition false
            # Runtime eval error (missing field, type mismatch) => not
            # matured, never a crash. (D6-symmetric.)
            return False
        return result is True

    def _timer_maturity(
        self,
        timer: Timer,
        clock_data: dict[str, Any],
        binding: dict[str, list[Token]],
        *,
        transition: str,
    ) -> float | None:
        """Evaluate one Runtime scheduling expression without changing enablement.

        ``Timer.cel`` remains the only timer enablement authority.  This
        companion expression only identifies the next future clock timestamp
        worth injecting for a structurally viable candidate binding.
        """
        assert timer.maturity is not None
        env: dict[str, Any] = {"clock": clock_data}
        for var, place in (timer.bind or {}).items():
            env[var] = binding[place][0].data
        try:
            raw = self._cel_adapter.eval(self._compile_cel(timer.maturity), env)  # pyright: ignore[reportUnknownMemberType]
            current = float(clock_data["now"])
            at = float(raw)
        except Exception as exc:  # noqa: BLE001 - scheduling stays non-spinning
            _LOGGER.warning(
                "timer maturity unschedulable",
                extra={
                    "transition": transition,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
            return None
        if isinstance(raw, bool) or not math.isfinite(at):
            _LOGGER.warning(
                "timer maturity unschedulable",
                extra={
                    "transition": transition,
                    "reason": f"expected a finite timestamp, got {raw!r}",
                },
            )
            return None
        if at <= current:
            # A maturity at or behind the clock is not a future wake candidate,
            # so it is excluded silently — like a ready timer (whose cel already
            # holds) is skipped upstream. Reaching here means the cel did *not*
            # hold, so advancing to `at` would not enable the transition anyway:
            # another guard blocks it (e.g. a poll whose deadline upper bound has
            # elapsed). Common on journal replay, where a restored clock token
            # can sit ahead of a restored token's stamped maturity.
            return None
        return at

    def timer_maturities(self, net: Net, marking: Marking) -> tuple[TimerMaturity, ...]:
        """Return every schedulable native-timer binding in declaration order.

        The Runtime consumes these advisory wake candidates; normal
        :meth:`enabled_transitions` remains the sole source of firing
        enablement, including ``Timer.cel`` and guards.
        """
        maturities: list[TimerMaturity] = []
        for transition in net.transitions:
            timer = transition.timer
            if timer is None or timer.maturity is None:
                continue
            clock_tokens = marking.get(timer.clock, [])
            if not clock_tokens:
                continue
            ctx = self._make_ctx(net, transition.name, attempt=0)
            if not self._inhibit_satisfied(net, transition.name, marking, ctx):
                continue
            binding_arcs = self._binding_arcs(net, transition.name)
            per_arc_candidates: list[list[tuple[Token, ...]]] = []
            for arc in binding_arcs:
                candidates = self._arc_candidates(arc, marking, ctx)
                if candidates is None:
                    break
                per_arc_candidates.append(candidates)
            else:
                for combo in product(*per_arc_candidates):
                    tokens = Engine._binding_from_combo(combo, binding_arcs)
                    if not self._binding_is_submultiset(tokens, marking):
                        continue
                    if not self._correlated_inhibit_satisfied(
                        net, transition.name, marking, tokens, ctx
                    ):
                        continue
                    if self._timer_holds(timer, clock_tokens[0].data, tokens):
                        continue
                    at = self._timer_maturity(
                        timer,
                        clock_tokens[0].data,
                        tokens,
                        transition=transition.name,
                    )
                    if at is not None:
                        maturities.append(
                            TimerMaturity(transition.name, timer.clock, at)
                        )
        return tuple(maturities)

    # ── Arc helpers ───────────────────────────────────────────────────────

    def _token_matches(
        self, consume: ConsumePattern, token: Token, ctx: FiringContext
    ) -> bool:
        """Whether a token matches a consume pattern's declared type + predicate.

        Shared by the consume-arc candidate filter (`_matching_tokens`) and the
        inhibit zero-test (`_inhibit_satisfied`): both ask the same per-token
        question (right type AND predicate passes), so the logic lives once here.
        """
        return token.type == consume.type and self._eval_predicate(
            consume.predicate, token, ctx
        )

    def _matching_tokens(
        self, arc: Arc, marking: Marking, ctx: FiringContext
    ) -> list[Token]:
        """Tokens in the arc's source place matching its type + predicate.

        Insertion (index) order preserved — the basis for lexicographic
        binding selection.
        """
        consume = arc.consume
        assert consume is not None
        place = arc.from_place
        assert place is not None
        return [
            tok
            for tok in marking.get(place, [])
            if self._token_matches(consume, tok, ctx)
        ]

    def _arc_candidates(
        self, arc: Arc, marking: Marking, ctx: FiringContext
    ) -> list[tuple[Token, ...]] | None:
        """Weight-combos of matching tokens for one binding arc, or None if
        the arc is unsatisfiable.

        The per-arc satisfiability probe, shared by consume- and read-mode arcs
        (both need ``weight`` matching tokens present): the arc needs at least
        ``weight`` tokens of the declared type passing its predicate. Its
        candidate bindings are the lexicographic ``weight``-combinations of
        those matching tokens, in index order — the per-arc input to the
        cartesian product in ``_select_binding``. A read arc is satisfied the
        same way; only firing differs (its tokens are not removed).

        References: spec/firing-semantics.md (§a C1); ADR 0012.
        """
        matching = self._matching_tokens(arc, marking, ctx)
        assert (
            arc.consume is not None
        )  # _arc_candidates is called only on consume/read arcs.
        weight = arc.consume.weight
        if len(matching) < weight:
            return None
        return list(combinations(matching, weight))

    def _input_arcs(self, net: Net, transition: str, mode: str) -> tuple[Arc, ...]:
        """Input arcs of ``transition`` in ``mode``, in declaration order."""
        return self._topology(net).input_arcs.get((transition, mode), ())

    def _binding_arcs(self, net: Net, transition: str) -> tuple[Arc, ...]:
        """Consume/read input arcs of ``transition``, in declaration order."""
        return self._topology(net).binding_arcs.get(transition, ())

    def _produce_arcs(self, net: Net, transition: str) -> tuple[Arc, ...]:
        """Produce arcs of ``transition``, in declaration order."""
        return self._topology(net).produce_arcs.get(transition, ())

    def _inhibit_satisfied(
        self, net: Net, transition: str, marking: Marking, ctx: FiringContext
    ) -> bool:
        """All UNCORRELATED inhibit arcs of ``transition`` pass their zero-test.

        An inhibit arc gates enablement on emptiness: it is satisfied iff NO
        token in its source place matches the arc's declared type + predicate.
        A single matching token on any inhibit arc fails the whole transition.
        Inhibit arcs consume nothing and never appear in a binding.

        Correlated inhibit arcs (``correlate`` set, ADR 0017) are skipped
        here: their zero-test references the candidate binding, so they are
        evaluated per binding by ``_correlated_inhibit_satisfied`` inside
        ``_select_binding``'s enumeration — this early whole-place check
        covers only the binding-independent (uncorrelated) arcs, keeping
        their cheap pre-binding evaluation unchanged.

        References: spec/firing-semantics.md (a, D7); ADR 0017.
        """
        for arc in self._input_arcs(net, transition, "inhibit"):
            consume = arc.consume
            place = arc.from_place
            assert consume is not None and place is not None
            if consume.correlate is not None:
                continue  # per-binding zero-test, evaluated in _select_binding
            if any(
                self._token_matches(consume, tok, ctx) for tok in marking.get(place, [])
            ):
                return False
        return True

    def _correlated_inhibit_satisfied(
        self,
        net: Net,
        transition: str,
        marking: Marking,
        binding: dict[str, list[Token]],
        ctx: FiringContext,
    ) -> bool:
        """All CORRELATED inhibit arcs of ``transition`` pass their per-binding
        zero-test against the candidate ``binding`` (the anti-join, ADR 0017).

        A correlated inhibit arc is satisfied iff NO token in its source place
        — of the declared type, passing the arc's single-token ``predicate``
        (which narrows the correlation candidates) — also satisfies its
        ``correlate`` CEL over ``{token: <candidate data>, binding:
        <place-keyed bound-token data>}``. Any correlated match blocks this
        binding (the caller skips it and advances to the next candidate, so
        the filter never reorders the enumeration).

        A ``correlate`` eval error (missing field, backend raise) fails
        CLOSED: the candidate token is treated as blocking. This is the guard's
        degrade-toward-not-enabled posture (D9), deliberately NOT D6's
        predicate-false rule — on an inhibit test, error-as-false would fail
        open and enable a transition whose safety test crashed.

        Uncorrelated inhibit arcs are not evaluated here; they are handled by
        the early whole-place ``_inhibit_satisfied`` check.

        References: spec/firing-semantics.md (a); ADR 0017.
        """
        binding_data: dict[str, list[dict[str, Any]]] | None = None
        for arc in self._input_arcs(net, transition, "inhibit"):
            consume = arc.consume
            place = arc.from_place
            assert consume is not None and place is not None
            if consume.correlate is None:
                continue  # whole-place zero-test, handled by _inhibit_satisfied
            compiled = self._compile_cel(consume.correlate)
            if binding_data is None:  # built once, shared across arcs
                binding_data = {
                    pl: [tok.data for tok in toks] for pl, toks in binding.items()
                }
            for tok in marking.get(place, []):
                if not self._token_matches(consume, tok, ctx):
                    continue
                try:
                    result = self._cel_adapter.eval(  # pyright: ignore[reportUnknownMemberType]
                        compiled, {"token": tok.data, "binding": binding_data}
                    )
                except Exception:  # noqa: BLE001 - eval error blocks the binding (fail-closed)
                    return False
                if result is not False:
                    return False
        return True

    def _make_ctx(self, net: Net, transition: str, attempt: int) -> FiringContext:
        """Build the closed four-field firing context for an attempt."""
        return FiringContext(
            firingId=f"{net.name}/{transition}/{attempt}",
            netId=net.name,
            attempt=attempt,
            timestamps=FiringTimestamps(
                fired_at=datetime.now(timezone.utc).isoformat()
            ),
        )

    def _find_transition(self, net: Net, transition: str) -> Transition | None:
        """Look up a transition by name; None if absent (defensive)."""
        return self._topology(net).transition_by_name.get(transition)

    def _require_transition(self, net: Net, transition: str) -> Transition:
        """Look up a declared transition by name, raising on an unknown name.

        The guard at every public entry point that takes a consumer-supplied
        transition name (``fire``, ``select_binding``): an undeclared name is
        indistinguishable from a declared source transition inside
        ``_select_binding`` (both have no binding arcs, so both would yield
        the empty binding), so the declaration check must live here, at the
        name-taking boundary, before any binding work. Navigates ``net``,
        hence an instance method like ``_find_transition``.

        Raises :class:`UnknownTransitionError` when ``transition`` is not
        declared in ``net``.

        References: spec/firing-semantics.md (D10).
        """
        tr = self._find_transition(net, transition)
        if tr is None:
            raise UnknownTransitionError(net.name, transition)
        return tr

    # ── Enablement + binding selection ───────────────────────────────────

    def select_binding(
        self,
        net: Net,
        transition: str,
        marking: Marking,
        *,
        attempt: int = 0,
    ) -> dict[str, list[Token]] | None:
        """Deterministic first-enabled binding, or None if not enabled.

        Returns the FULL binding (consume- and read-mode tokens, keyed by
        source place) that the guard/handler/record see; read tokens are part
        of the binding but are NOT removed on fire (that split lives in
        ``_Binding`` and is applied by ``fire``). Builds the firing context for
        this attempt and delegates to ``_select_binding``. ``fire`` shares one
        context across predicate, guard, and handler evaluation by calling
        ``_select_binding`` directly, so a firing attempt sees a single
        ``firingId`` / ``timestamps`` pair.

        An unknown ``transition`` name (absent from the net's declared
        transitions) raises :class:`UnknownTransitionError` — it must not fall
        into the empty-binding path a declared source transition legitimately
        uses.

        References: ADR 0012.
        """
        self._require_transition(net, transition)
        ctx = self._make_ctx(net, transition, attempt)
        selected = self._select_binding(net, transition, marking, ctx)
        return selected.tokens if selected is not None else None

    def _select_binding(
        self,
        net: Net,
        transition: str,
        marking: Marking,
        ctx: FiringContext,
    ) -> _Binding | None:
        """Deterministic first-enabled binding core, or None if not enabled.

        Inhibit arcs gate enablement (zero-test) and consume nothing. Binding
        arcs (consume- and read-mode) each require at least ``weight`` matching
        tokens; the binding is the cartesian product of per-arc lexicographic
        combinations, taken in net declaration order. When several binding arcs
        share a source place, the combined bound multiset must be a valid
        sub-multiset of that place — a token may not be bound to two arcs;
        invalid combos are skipped. This sub-multiset rule is also what enforces
        read/consume disjointness on a shared place: a token read by one arc
        cannot also be consumed by another, since both would draw from the same
        place's multiset. If a guard is present, the first valid binding it
        accepts is returned; otherwise the first valid product element is
        returned.

        The returned :class:`_Binding` carries the full bound token set
        (``tokens``, seen by guard/handler/record) and the consume-only subset
        (``consumed``, removed by ``fire``); read-mode tokens appear only in the
        former. A transition with no binding arcs (only inhibit arcs, or a
        source transition) yields a single empty binding: ``product()`` with no
        per-arc candidates produces exactly one empty tuple, so the loop below
        handles that case without a special branch.

        A guard that raises (an impure guard) degrades to not-enabled — never a
        runtime crash.

        References: spec/firing-semantics.md (D1, D6, D9); ADR 0002; ADR 0012.
        """
        # Uncorrelated inhibit arcs gate enablement BEFORE any binding work: a
        # matching token on any such arc fails the whole transition. Correlated
        # inhibit arcs (ADR 0017) reference the candidate binding, so they are
        # deferred to the per-binding check inside the product loop below.
        if not self._inhibit_satisfied(net, transition, marking, ctx):
            return None

        # Defensive lookup: every current caller reaches here with a declared
        # name (fire/select_binding guard via _require_transition;
        # enabled_transitions iterates net.transitions), so `tr is None` is
        # unreachable today — kept defensive, not asserted, because this
        # private core does not own the name-taking boundary (D10 puts the
        # unknown-name raise at the public entry points, not here).
        tr = self._find_transition(net, transition)

        # Timer (ADR 0018): resolve the clock token before any binding work.
        # An empty clock place means no time reference => not matured => not
        # enabled, regardless of the arcs. The first token in the clock place
        # is the reference (the singleton clock-advance pattern, ADR 0013,
        # keeps it at one in practice).
        timer = tr.timer if tr is not None else None
        clock_data: dict[str, Any] | None = None
        if timer is not None:
            clock_tokens = marking.get(timer.clock, [])
            if not clock_tokens:
                return None
            clock_data = clock_tokens[0].data

        binding_arcs = self._binding_arcs(net, transition)

        # Per-arc candidate selections (each a list of weight-tuples), in
        # lexicographic index order so the first is the lowest-index tokens.
        # An unsatisfiable arc (fewer than `weight` matching tokens) means the
        # transition is not enabled.
        per_arc_candidates: list[list[tuple[Token, ...]]] = []
        for arc in binding_arcs:
            cands = self._arc_candidates(arc, marking, ctx)
            if cands is None:
                return None
            per_arc_candidates.append(cands)

        # Resolve the guard once (if present). An unresolved guard ref means
        # the transition is not enabled regardless of the binding, so return
        # now — before the product / guard-evaluation loop below. (Per-arc
        # candidate enumeration above is retained; it is cheap and, like the
        # inhibit zero-test, evaluates only pure arc predicates.)
        guard_ref = tr.guard if tr is not None else None
        guard: GuardHandler | None = None
        if guard_ref is not None:
            try:
                guard = self.registry.resolve_guard(guard_ref)
            except HandlerNotFound:
                return None  # unresolved guard => not enabled

        # Cartesian product across binding arcs, in declaration order. With no
        # binding arcs this is a single empty binding (one iteration).
        for combo in product(*per_arc_candidates):
            tokens = Engine._binding_from_combo(combo, binding_arcs)
            # When several binding arcs share a source place, the combined
            # bound multiset must be a valid sub-multiset of that place — the
            # same token may not be bound to two arcs (that would let `fire`
            # under-consume and corrupt the marking, and is also the
            # read/consume disjointness rule). Skip invalid combos before
            # consulting the guard. (D1; ADR 0012.)
            if not self._binding_is_submultiset(tokens, marking):
                continue
            # Correlated inhibit arcs (the anti-join, ADR 0017) run their
            # per-binding zero-test here — after the structural sub-multiset
            # check, before the (possibly impure) guard. A blocked binding is
            # skipped, not a reorder: the next candidate in the same
            # enumeration order is tried, so first-surviving selection stays
            # deterministic.
            if not self._correlated_inhibit_satisfied(
                net, transition, marking, tokens, ctx
            ):
                continue
            # Timer (ADR 0018): evaluated per candidate binding, after the
            # correlated-inhibit zero-test and BEFORE the guard (pure before
            # possibly-impure). An unmatured binding is skipped — not the
            # whole transition — so binding enumeration finds a matured token
            # past an unmatured earlier one, which is what makes per-instance
            # deadline isolation automatic.
            if (
                timer is not None
                and clock_data is not None
                and not self._timer_holds(timer, clock_data, tokens)
            ):
                continue
            if guard is not None:
                try:
                    accepted = guard(
                        GuardHandlerInput(
                            transitionId=transition,
                            inputTokens=tokens,
                            firingContext=ctx,
                        )
                    )
                except Exception:  # noqa: BLE001 - impure guard raises => not-enabled, never a crash
                    # An impure guard that raises degrades to not-enabled,
                    # symmetric with a predicate handler's runtime error (which
                    # yields predicate false) and an unresolved guard ref
                    # (HandlerNotFound ⇒ not enabled). Never a runtime crash.
                    # (ADR 0002; D9.)
                    return None
                if accepted is not True:
                    continue
            # First valid binding (guard absent, or guard accepted): split into
            # the full bound set and the consume-only subset `fire` removes.
            return _Binding(
                tokens=tokens,
                consumed=Engine._consumed_from_combo(combo, binding_arcs),
            )
        # No combination satisfied the sub-multiset + timer + guard constraints.
        return None

    @staticmethod
    def _remove_each(available: list[Token], toks: list[Token]) -> bool:
        """Remove each token from ``available`` by equality, counting multiplicities.

        Token is frozen but carries an unhashable ``data`` dict, so multiset
        removal is simulated with ``list.remove`` (equality, not hashing).
        Returns False on the first token absent from ``available`` (leaving
        ``available`` partially mutated); True once every token is removed.
        The shared core of ``_binding_is_submultiset`` (a pre-fire validity
        check) and ``_consume`` (the actual marking mutation): both ask
        "can each bound token be removed once from its source place?".
        """
        for tok in toks:
            try:
                available.remove(tok)
            except ValueError:
                return False
        return True

    @staticmethod
    def _binding_is_submultiset(
        binding: dict[str, list[Token]], marking: Marking
    ) -> bool:
        """Whether each place's bound tokens are a valid sub-multiset of the
        place's current tokens.

        A binding is consumable iff every bound token can be removed from its
        source place by equality, counting multiplicities — the same operation
        ``_consume`` performs via ``_remove_each``. Pure data transform (no
        net/registry/CEL access), hence a ``staticmethod`` like its siblings.
        """
        for place, toks in binding.items():
            if not Engine._remove_each(list(marking.get(place, [])), toks):
                return False
        return True

    @staticmethod
    def _binding_from_combo(
        combo: tuple[tuple[Token, ...], ...], binding_arcs: Sequence[Arc]
    ) -> dict[str, list[Token]]:
        """Group a product-element's per-arc token tuples into a per-place binding.

        ``combo`` is one element of the cartesian product across binding arcs
        (consume- and read-mode, each slot a ``weight``-tuple of tokens for that
        arc, in declaration order). Tokens from arcs sharing a source place are
        concatenated, so the result is the full bound multiset per place —
        including read tokens — the input to the sub-multiset validity check and
        the guard. Pure data transform (no net/registry/CEL access), hence a
        ``staticmethod`` like its siblings (``_remove_each``,
        ``_binding_is_submultiset``).

        References: spec/firing-semantics.md (D1); ADR 0012.
        """
        binding: dict[str, list[Token]] = {}
        for i, arc in enumerate(binding_arcs):
            place = arc.from_place
            assert place is not None
            binding.setdefault(place, []).extend(combo[i])
        return binding

    @staticmethod
    def _consumed_from_combo(
        combo: tuple[tuple[Token, ...], ...], binding_arcs: Sequence[Arc]
    ) -> dict[str, list[Token]]:
        """The consume-only subset of a binding: the tokens ``fire`` removes.

        ``combo`` / ``binding_arcs`` are index-aligned exactly as in
        ``_binding_from_combo``. Read-mode arcs contribute to the full binding
        but are test-without-consume, so they are excluded here; only
        consume-mode arcs' tokens are grouped per source place. ``fire`` removes
        exactly this multiset, leaving read tokens in the marking. With no read
        arcs this equals ``_binding_from_combo``, so the classical case is
        unchanged. Pure data transform, hence a ``staticmethod`` like its
        siblings.

        References: spec/firing-semantics.md (b, D1); ADR 0012.
        """
        consumed: dict[str, list[Token]] = {}
        for i, arc in enumerate(binding_arcs):
            assert arc.consume is not None
            if arc.consume.mode != "consume":
                continue
            place = arc.from_place
            assert place is not None
            consumed.setdefault(place, []).extend(combo[i])
        return consumed

    def enabled_transitions(
        self, net: Net, marking: Marking, *, attempt: int = 0
    ) -> list[str]:
        """Transitions with a satisfiable binding, in declaration order.

        ``attempt`` is threaded to ``select_binding`` so an attempt-sensitive
        guard sees the same attempt the subsequent fire will use.

        References: spec/handler-contract.md; spec/firing-semantics.md (D9).
        """
        enabled: list[str] = []
        for tr in self._candidate_transitions(net, marking):
            ctx = self._make_ctx(net, tr.name, attempt)
            if self._select_binding(net, tr.name, marking, ctx) is not None:
                enabled.append(tr.name)
        return enabled

    # ── Firing: consume → invoke → deposit → record ──────────────────────

    @staticmethod
    def _record(
        ctx: FiringContext,
        net: Net,
        transition: str,
        attempt: int,
        status: FiringStatus,
        *,
        input_tokens: dict[str, list[Token]],
        output_tokens: dict[str, list[Token]],
        error: HandlerError | None,
        metadata: dict[str, Any],
    ) -> FiringRecord:
        """Build a ``FiringRecord`` with NO ``sequence``.

        Pure data transform -- it assembles the record from ``ctx`` and
        ``net.name`` without navigating ``net``/``registry``/CEL, hence a
        ``staticmethod`` like its ``_consume``/``_deposit`` siblings.

        References: spec/firing-semantics.md (D4).
        """
        return FiringRecord(
            firingId=ctx["firingId"],
            netId=net.name,
            transition=transition,
            attempt=attempt,
            status=status,
            inputTokens=input_tokens,
            outputTokens=output_tokens,
            error=error,
            metadata=metadata,
            timestamps=ctx["timestamps"],
        )

    def _emit_firing(self, record: FiringRecord) -> None:
        if self.journal is not None:
            self.journal.record_firing(record)

    def _emit_violation(self, record: FiringRecord) -> None:
        if self.journal is not None:
            self.journal.record_deposit_violation(record)

    def _emit_injection(self, record: InjectionRecord) -> None:
        if self.journal is not None:
            self.journal.record_injection(record)

    def _fail(
        self,
        ctx: FiringContext,
        net: Net,
        transition: str,
        attempt: int,
        error: HandlerError | None,
        metadata: dict[str, Any] | None = None,
    ) -> FiringRecord:
        """Build a failed record, emit it through the firing hook, and return it.

        The caller returns its unchanged marking alongside this record (atomic
        rollback — the tentative consume, if any, is discarded).

        Used for the not-enabled, handler-not-found, and handler-``failed``
        branches. The deposit-violation branch is NOT routed here: it emits
        exclusively through ``record_deposit_violation``, never
        ``record_firing``, so each attempt occupies one journal sequence slot.

        References: spec/firing-semantics.md (d).
        """
        record = self._record(
            ctx,
            net,
            transition,
            attempt,
            "failed",
            input_tokens={},
            output_tokens={},
            error=error,
            metadata=metadata or {},
        )
        self._emit_firing(record)
        return record

    @staticmethod
    def _consume(marking: Marking, binding: dict[str, list[Token]]) -> Marking:
        """Tentative consume: a new ``Marking`` with each bound token removed
        once from its source place; untouched places shared structurally.

        Removal uses ``_remove_each`` (Token equality — frozen but unhashable
        ``data``, so simulated multiset removal, matching
        ``_binding_is_submultiset``). The binding per place is exactly the
        multiset consumed from that place across all its arcs, so removing each
        bound token once reproduces the consume. Structural sharing is the
        atomicity seam: on any later failure the caller returns the original
        ``marking`` reference unchanged (it cannot have mutated). The binding
        is pre-validated by ``_binding_is_submultiset``, so removal always
        succeeds here.
        """
        new_marking = marking
        for place, toks in binding.items():
            available = list(marking.get(place, []))
            Engine._remove_each(available, toks)
            new_marking = new_marking.set(place, available)
        return new_marking

    @staticmethod
    def _produce_templates(
        produce_arcs: Sequence[Arc],
    ) -> tuple[ProduceTemplate, ...]:
        """Every produce template in arc declaration order.

        Parallel arcs may share a destination and type.  A tuple preserves
        every declaration without introducing destination-keyed overwrite
        semantics.
        """
        return tuple(cast(ProduceTemplate, arc.produce) for arc in produce_arcs)

    @staticmethod
    def _detect_violation(
        output_tokens: dict[str, list[Token]],
        templates: Sequence[ProduceTemplate],
    ) -> bool:
        """Detect a deposit-contract violation.

        A handler token violates the contract unless some template declares
        its exact destination and type.

        References: spec/firing-semantics.md (D3).
        """
        for dest, toks in output_tokens.items():
            for tok in toks:
                if not any(
                    template.destination == dest and template.type == tok.type
                    for template in templates
                ):
                    return True
        return False

    def _deposit(
        self,
        templates: Sequence[ProduceTemplate],
        output_tokens: dict[str, list[Token]],
        binding_tokens: Mapping[str, Sequence[Token]],
    ) -> dict[str, list[Token]]:
        """Deposit per produce template.

        Handler-supplied tokens are preserved once, in handler order.  For
        each declaration-ordered template whose destination/type pair has no
        handler token, literal ``data`` emits one fixed passthrough token and
        ``cel`` (ADR 0023) emits one computed token: the expression evaluated
        over ``{binding: <place-keyed bound-token data>}`` (the ADR 0017
        binding map). An eval error or non-object result raises
        :class:`_ProduceCelError`, which the callers route through the
        deposit-contract-violation handling (D3).

        An instance method, not a ``staticmethod`` like its pure-transform
        siblings (``_consume``, ``_apply_deposit``): evaluating a computed
        fallback resolves CEL through the engine's adapter and compile cache.

        References: spec/net-schema.md (Q3); ADR 0023.
        """
        deposited = {dest: list(toks) for dest, toks in output_tokens.items() if toks}
        binding_data: dict[str, list[dict[str, Any]]] | None = None
        for template in templates:
            handler_tokens = output_tokens.get(template.destination, ())
            if any(tok.type == template.type for tok in handler_tokens):
                continue
            if isinstance(template.data, dict):
                fixed = cast(dict[str, Any], template.data)
                deposited.setdefault(template.destination, []).append(
                    Token(type=template.type, data=deepcopy(fixed))
                )
            elif template.cel is not None:
                if binding_data is None:  # built once, shared across templates
                    binding_data = {
                        pl: [tok.data for tok in toks]
                        for pl, toks in binding_tokens.items()
                    }
                try:
                    result = self._cel_adapter.eval(  # pyright: ignore[reportUnknownMemberType]
                        self._compile_cel(template.cel),
                        {"binding": binding_data},
                    )
                except Exception as exc:  # noqa: BLE001 - eval error is a D3 violation
                    raise _ProduceCelError(
                        f"produce cel into {template.destination!r} failed to "
                        f"evaluate: {exc}"
                    ) from exc
                if not isinstance(result, dict):
                    raise _ProduceCelError(
                        f"produce cel into {template.destination!r} must yield "
                        f"a JSON object, got {type(result).__name__}"
                    )
                deposited.setdefault(template.destination, []).append(
                    Token(type=template.type, data=cast("dict[str, Any]", result))
                )
        return deposited

    @staticmethod
    def _apply_deposit(marking: Marking, deposited: dict[str, list[Token]]) -> Marking:
        """Apply deposited tokens to the marking, returning a new structurally-
        shared ``Marking`` (the deposit phase of ``fire``).

        Each destination's existing tokens are extended with the deposited
        tokens (insertion order preserved); untouched places are shared, not
        copied. Pure data transform (no net/registry/CEL access), hence a
        ``staticmethod`` like ``_consume``. Symmetric with ``_consume``: that
        removes bound tokens per place, this appends deposited tokens per place.
        """
        for dest, toks in deposited.items():
            existing = marking.get(dest, pvector())
            marking = marking.set(dest, existing.extend(toks))
        return marking

    def _handle_violation(
        self,
        ctx: FiringContext,
        net: Net,
        transition: str,
        attempt: int,
        out: TransitionHandlerOutput,
        marking: Marking,
        detail: str | None = None,
    ) -> tuple[Marking, FiringRecord]:
        """Handle a deposit-contract violation per the configured mode.

        The marking is UNCHANGED in every mode (atomic rollback of the
        tentative consume). The violation record routes exclusively through
        ``record_deposit_violation``, never ``record_firing``, so each attempt
        occupies one journal sequence slot. The three modes:

        - ``raise`` — raise :class:`DepositViolation` immediately.
        - ``record_then_raise`` — record the violation, then raise
          :class:`DepositViolation`.
        - ``record_then_drop`` — record the violation and return the original
          marking so the run loop continues.

        References: spec/firing-semantics.md (D3, d).
        """
        violation_msg = detail or (
            f"transition '{transition}' produced tokens that "
            f"violate its produce contract"
        )
        failed_record = self._record(
            ctx,
            net,
            transition,
            attempt,
            "failed",
            input_tokens={},
            output_tokens={},
            error=HandlerError(type="DepositViolation", message=violation_msg),
            metadata=out.get("metadata", {}),
        )
        if self.deposit_violation == "raise":
            raise DepositViolation(violation_msg)
        # record_then_*: emit through the deposit-violation hook only.
        self._emit_violation(failed_record)
        if self.deposit_violation == "record_then_raise":
            raise DepositViolation(violation_msg)
        # record_then_drop: record and return the original marking.
        return marking, failed_record

    # ── Async-runtime reservation seam ─────────────────────────────────

    def reserve(
        self,
        net: Net,
        marking: Marking,
        transition: str,
        *,
        attempt: int,
    ) -> FiringReservation | None:
        """Reserve one enabled firing without invoking its transition handler.

        The returned reservation owns the transition's consume-mode tokens;
        read-mode tokens remain in ``reserved_marking`` exactly as they do for
        :meth:`fire`.  No record is emitted until :meth:`settle` receives the
        handler's terminal result.  A caller that keeps several reservations
        live MUST admit only independent footprints: no other live reservation
        may read, consume, or produce into these consumed places.

        ``None`` means the transition was not enabled at this attempt.  Unknown
        transition names retain the public :meth:`fire` / :meth:`select_binding`
        programmer-error behavior.
        """
        self._require_transition(net, transition)
        ctx = self._make_ctx(net, transition, attempt)
        binding = self._select_binding(net, transition, marking, ctx)
        if binding is None:
            return None
        return FiringReservation(
            net=net,
            original_marking=marking,
            reserved_marking=self._consume(marking, binding.consumed),
            transition=transition,
            attempt=attempt,
            context=ctx,
            input_tokens=binding.tokens,
            consumed_tokens=binding.consumed,
        )

    @staticmethod
    def _restore_reservation(
        marking: Marking, reservation: FiringReservation
    ) -> Marking:
        """Restore each reserved input place to its exact pre-reservation state.

        This is safe when the caller observes :meth:`reserve`'s independent
        footprint rule.  That rule prevents another in-flight firing or source
        update from changing one of these places before settlement, preserving
        the synchronous ``fire`` rollback contract and token insertion order.
        """
        for place in reservation.consumed_tokens:
            marking = marking.set(place, reservation.original_marking.get(place, ()))
        return marking

    def settle(
        self,
        marking: Marking,
        reservation: FiringReservation,
        out: TransitionHandlerOutput,
    ) -> tuple[Marking, FiringRecord]:
        """Commit or roll back a previously reserved asynchronous firing.

        This is the second half of :meth:`reserve`.  It applies the ordinary
        handler-output and deposit contracts, emits exactly one terminal
        firing/deposit-violation record, and returns a new persistent marking.
        A failed result restores the reservation's consumed tokens rather than
        depositing output.  Synchronous :meth:`fire` remains unchanged.
        """
        net = reservation.net
        metadata = out.get("metadata", {})
        if out.get("status") == "failed":
            record = self._record(
                reservation.context,
                net,
                reservation.transition,
                reservation.attempt,
                "failed",
                input_tokens=reservation.input_tokens,
                output_tokens={},
                error=out.get("error"),
                metadata=metadata,
            )
            self._emit_firing(record)
            return self._restore_reservation(marking, reservation), record

        templates = self._produce_templates(
            self._produce_arcs(net, reservation.transition)
        )
        output_tokens = out.get("outputTokens", {}) or {}
        violation_msg: str | None = None
        deposited: dict[str, list[Token]] = {}
        if self._detect_violation(output_tokens, templates):
            violation_msg = (
                f"transition '{reservation.transition}' produced tokens that "
                "violate its produce contract"
            )
        else:
            try:
                deposited = self._deposit(
                    templates, output_tokens, reservation.input_tokens
                )
            except _ProduceCelError as exc:
                violation_msg = str(exc)
        if violation_msg is not None:
            record = self._record(
                reservation.context,
                net,
                reservation.transition,
                reservation.attempt,
                "failed",
                input_tokens=reservation.input_tokens,
                output_tokens={},
                error=HandlerError(
                    type="DepositViolation",
                    message=violation_msg,
                ),
                metadata=metadata,
            )
            if self.deposit_violation == "raise":
                raise DepositViolation(violation_msg)
            self._emit_violation(record)
            if self.deposit_violation == "record_then_raise":
                raise DepositViolation(violation_msg)
            return self._restore_reservation(marking, reservation), record

        committed = self._apply_deposit(marking, deposited)
        record = self._record(
            reservation.context,
            net,
            reservation.transition,
            reservation.attempt,
            "completed",
            input_tokens=reservation.input_tokens,
            output_tokens=deposited,
            error=None,
            metadata=metadata,
        )
        self._emit_firing(record)
        return committed, record

    def fire(
        self,
        net: Net,
        marking: Marking,
        transition: str,
        *,
        attempt: int,
    ) -> tuple[Marking, FiringRecord]:
        """Fire one transition: consume → invoke → deposit → record.

        On any failure (not-enabled, handler-not-found, handler-``failed``,
        or a deposit-contract violation under ``record_then_drop``) the
        marking is returned UNCHANGED — atomic rollback of the tentative
        consume. Deposit-violation handling is configurable.

        An unknown ``transition`` name (absent from the net's declared
        transitions) raises :class:`UnknownTransitionError` before any
        binding or firing work — a typo is a programmer bug, not a firing
        outcome; without the guard it would fall into the empty-binding
        path a declared source transition legitimately uses and silently
        execute a handler.

        References: spec/firing-semantics.md (b, D3).
        """
        tr = self._require_transition(net, transition)
        ctx = self._make_ctx(net, transition, attempt)
        binding = self._select_binding(net, transition, marking, ctx)
        if binding is None:
            return marking, self._fail(
                ctx,
                net,
                transition,
                attempt,
                HandlerError(
                    type="NotEnabled",
                    message=f"transition '{transition}' is not enabled",
                ),
            )

        # Tentative consume of the consume-mode subset only (structural
        # sharing); read-mode tokens stay in the marking (test-without-consume).
        # The original marking is dropped only on success. (ADR 0012.)
        new_marking = self._consume(marking, binding.consumed)

        # Resolve handler. HandlerNotFound is a transition failure, not a crash.
        # `tr` is a declared transition (guaranteed by _require_transition).
        handler_ref = tr.handler
        if handler_ref is None:
            return marking, self._fail(
                ctx,
                net,
                transition,
                attempt,
                HandlerError(
                    type="HandlerNotFound",
                    message=f"transition '{transition}' has no handler",
                ),
            )
        try:
            handler = self.registry.resolve_transition(handler_ref)
        except HandlerNotFound:
            return marking, self._fail(
                ctx,
                net,
                transition,
                attempt,
                HandlerError(
                    type="HandlerNotFound",
                    message=f"handler '{handler_ref}' not registered",
                ),
            )

        out: TransitionHandlerOutput = handler(
            TransitionHandlerInput(
                transitionId=transition,
                inputTokens=binding.tokens,
                firingContext=ctx,
            )
        )

        if out.get("status") == "failed":
            return marking, self._fail(
                ctx,
                net,
                transition,
                attempt,
                out.get("error"),
                out.get("metadata", {}),
            )

        # status == "completed": deposit per the produce contract.
        templates = self._produce_templates(self._produce_arcs(net, transition))
        output_tokens: dict[str, list[Token]] = out.get("outputTokens", {}) or {}

        if self._detect_violation(output_tokens, templates):
            return self._handle_violation(ctx, net, transition, attempt, out, marking)

        try:
            deposited = self._deposit(templates, output_tokens, binding.tokens)
        except _ProduceCelError as exc:
            return self._handle_violation(
                ctx, net, transition, attempt, out, marking, detail=str(exc)
            )
        new_marking = self._apply_deposit(new_marking, deposited)

        completed_record = self._record(
            ctx,
            net,
            transition,
            attempt,
            "completed",
            input_tokens=binding.tokens,
            output_tokens=deposited,
            error=None,
            metadata=out.get("metadata", {}),
        )
        self._emit_firing(completed_record)
        return new_marking, completed_record

    # ── Sandwich-rule validation (opt-in pre-run seam) ───────────────────

    def validate(self, net: Net) -> None:
        """Opt-in pre-run validation of every net-declared handler ref.

        The sandwich rule: surface a configuration error — a handler referenced
        in the net but unresolvable in the registry — *before* any ``run``. The
        engine is net-agnostic at construction (``Engine(registry, *, policy,
        ...)`` takes no ``net``), so the net meets the registry for the first
        time at ``run`` / ``validate``; this seam is the earliest boundary where
        the net exists.

        Walks the net in declaration order and resolves each net-referenced
        handler ref:

        - every transition ``handler`` (when present),
        - every transition ``guard`` (when present), and
        - every consume-arc named predicate ``handler`` (inline CEL predicates
          are compile/eval, not registry-resolved, and are skipped).

        Raises :class:`HandlerNotFound` on the **first** unresolvable ref
        (carrying its ``name``); returns ``None`` on success. Does not mutate
        state, fire, or emit journal records.

        ``run`` does **not** call this — the sandwich rule is opt-in. The caller
        invokes ``validate(net)`` at a time of their choosing (or not at all);
        ``run`` retains the locked graceful-degradation contract on the run path
        (a missing transition handler yields a ``failed`` record, not a raise).
        ``validate`` is the only seam where ``HandlerNotFound`` propagates
        uncaught.

        References: spec/firing-semantics.md (e); operator A1; A4 (ii).
        """
        for tr in net.transitions:
            if tr.handler is not None:
                self.registry.resolve_transition(tr.handler)
            if tr.guard is not None:
                self.registry.resolve_guard(tr.guard)
        for arc in net.arcs:
            if arc.consume is None or arc.consume.predicate is None:
                continue
            pred = arc.consume.predicate
            if pred.handler is not None:
                self.registry.resolve_predicate(pred.handler)

    # ── Environment-arrival seam (consumer token injection) ──────────────

    def inject_token(
        self,
        net: Net,
        marking: Marking,
        place: str,
        token: Token,
        *,
        attempt: int,
        replace: bool = False,
    ) -> tuple[Marking, InjectionRecord]:
        """Inject (or replace) a token in ``place``, recording the event.

        The general **environment-arrival seam** (ADR 0013, as amended): the
        one sanctioned, journaled, replay-deterministic way any external token
        enters a running net between firings — file arrivals, environment
        observations, external events, and clock/deadline tokens alike —
        WITHOUT the consumer reaching into the marking directly. The
        clock/timer case is the seam's motivating origin, and timing still
        enters a net only as token data — the engine never reads a wall clock;
        ``tick`` composes this method with ``run`` for native timed
        transitions (ADR 0018) — but nothing in the mechanism is
        time-specific. This method is the consumer's one write primitive:

        - ``replace=False`` (default) — **inject**: append ``token`` to
          ``place`` (the arrival pattern: a new ``deadline`` or observation
          token enables a gated transition).
        - ``replace=True`` — **update**: replace ``place``'s entire contents
          with ``[token]`` (the singleton clock-advance pattern: bump the one
          ``tick``/``clock`` token's ``now``). Intended for a place that holds a
          single clock/deadline token.

        Returns the new persistent :class:`Marking` (untouched places shared
        structurally) and the :class:`InjectionRecord`, which is emitted through
        the journal's ``record_injection`` hook (if a journal is attached) as an
        explicit entry sharing the firing sequence stream — so replay stays
        deterministic across injected time. The consumer re-runs
        ``enabled_transitions`` after injecting; this method does not drive the
        loop (that stays the consumer's / ``run``'s job).

        The token ``type`` is validated against the place's ``accepts`` (an
        unknown place or an unaccepted type is a programmer error →
        :class:`ValueError`), mirroring the deposit contract's type check —
        the seam cannot smuggle an ill-typed token past the net's structure.

        References: spec/firing-semantics.md (f); ADR 0013.
        """
        self._validate_injection(net, place, token)

        existing = list(marking.get(place, []))
        kind: InjectionKind
        if replace:
            kind = "update"
            replaced = existing
            new_marking = marking.set(place, [token])
        else:
            kind = "inject"
            replaced = []
            new_marking = marking.set(place, [*existing, token])

        record = InjectionRecord(
            injectionId=f"{net.name}/@inject/{place}/{attempt}",
            netId=net.name,
            place=place,
            attempt=attempt,
            kind=kind,
            tokens=[token],
            replaced=replaced,
            timestamps=FiringTimestamps(
                fired_at=datetime.now(timezone.utc).isoformat()
            ),
        )
        self._emit_injection(record)
        return new_marking, record

    def inject_tokens(
        self,
        net: Net,
        marking: Marking,
        placements: Sequence[tuple[str, Token]],
        *,
        attempt: int,
    ) -> tuple[Marking, list[InjectionRecord]]:
        """Inject a batch of ``(place, token)`` pairs in one journal-consistent step.

        The batch convenience over :meth:`inject_token` for the arrival
        pattern — several environment tokens land at once (a poll found three
        new observations). Append-only: every placement is an **inject**
        (``kind="inject"``); the singleton clock-advance/replace pattern stays
        on ``inject_token(replace=True)``.

        **All-or-nothing validation**: every placement is validated (unknown
        place, unaccepted token type → :class:`ValueError`) BEFORE any journal
        emission or marking change, so an invalid entry anywhere in the batch
        fails the whole batch with no side effects.

        **Journal shape**: one :class:`InjectionRecord` per token, emitted in
        placement order, so a stream-numbering journal (e.g.
        :class:`JsonlJournal`) gives them consecutive ``sequence`` slots —
        replay-compatible with per-injection tooling. Every record carries the
        batch's ``attempt`` and the unchanged single-injection ``injectionId``
        format; two same-place entries in one batch share an ``injectionId``
        and are disambiguated by ``sequence`` (the journal owns unique
        numbering, D4 — ``injectionId`` is deterministic, not a unique key).

        Returns the new persistent :class:`Marking` and the records in
        placement order.

        References: spec/firing-semantics.md (f); ADR 0013.
        """
        # Validate ALL placements before any journal emission or marking
        # change — the all-or-nothing contract.
        for place, token in placements:
            self._validate_injection(net, place, token)

        # Accumulate each place's additions before touching the persistent
        # marking.  Repeated ``Marking.set`` calls rebuild the outer persistent
        # map and, through ``inject_token``, repeatedly copy the growing
        # per-place vector.  Folding retains placement order within each place
        # while requiring exactly one persistent update per touched place.
        additions: dict[str, list[Token]] = {}
        for place, token in placements:
            tokens = additions.get(place)
            if tokens is None:
                additions[place] = [token]
            else:
                tokens.append(token)

        current = marking
        for place, tokens in additions.items():
            existing = marking.get(place)
            placed = pvector(tokens) if existing is None else existing.extend(tokens)
            current = current.set(place, placed)

        # Records and journal emissions remain per placement and in global
        # input order; only the marking construction above is folded.
        records: list[InjectionRecord] = []
        for place, token in placements:
            record = InjectionRecord(
                injectionId=f"{net.name}/@inject/{place}/{attempt}",
                netId=net.name,
                place=place,
                attempt=attempt,
                kind="inject",
                tokens=[token],
                replaced=[],
                timestamps=FiringTimestamps(
                    fired_at=datetime.now(timezone.utc).isoformat()
                ),
            )
            self._emit_injection(record)
            records.append(record)
        return current, records

    def _validate_injection(self, net: Net, place: str, token: Token) -> None:
        """Validate one injection placement against the net's structure.

        An unknown place or a token ``type`` the place's ``accepts`` rejects
        is a programmer error → :class:`ValueError` — the injection seam
        cannot smuggle an ill-typed token past the net's structure (mirrors
        the deposit contract's type check).
        """
        place_obj = self._topology(net).place_by_name.get(place)
        if place_obj is None:
            raise ValueError(
                f"token injection: net {net.name!r} has no place named {place!r}"
            )
        if token.type not in place_obj.accepts:
            raise ValueError(
                f"token injection: place {place!r} does not accept token type "
                f"{token.type!r} (accepts {place_obj.accepts!r})"
            )

    def tick(
        self,
        net: Net,
        marking: Marking,
        place: str,
        token: Token,
        *,
        attempt: int = 0,
        max_steps: int = 1000,
    ) -> Marking:
        """Advance the clock and fire everything it matured, to quiescence.

        The engine-owned re-evaluation loop for timed transitions (ADR 0018):
        one ``inject_token(replace=True)`` — the singleton clock-advance
        pattern (ADR 0013) — followed by ``run``. One advance can mature
        several deadlines; ``run``'s ordinary selection loop fires them all,
        under the configured firing policy (ADR 0014) and failure budget
        (ADR 0015). The injection and the firings share one journal sequence
        stream, so a tick-driven timeline replays deterministically. An
        unmatured timed transition is simply not enabled, so a tick short of
        every deadline reaches quiescence immediately — no spin.

        ``attempt`` feeds the injection's deterministic ``injectionId``; the
        inner ``run`` numbers its firing attempts from its own step counter,
        as every ``run`` does. Consumers needing append-mode injection (a
        deadline token rather than a clock advance) compose ``inject_token``
        + ``run`` directly.

        References: spec/firing-semantics.md (f); ADR 0018.
        """
        new_marking, _record = self.inject_token(
            net, marking, place, token, attempt=attempt, replace=True
        )
        return self.run(net, new_marking, max_steps=max_steps)

    # ── Selection loop ───────────────────────────────────────────────────

    def run(self, net: Net, marking: Marking, *, max_steps: int = 1000) -> Marking:
        """Fire transitions until quiescence or ``max_steps``.

        Each step: compute enabled transitions, ask the firing policy to pick
        one, fire it. ``attempt`` is the step counter, so firingIds are
        deterministic across replay runs. Stops when no transition is enabled
        or the policy returns None.

        The enablement probe and the fire share the same ``attempt`` (the step
        counter), so an attempt-sensitive guard sees a consistent attempt.

        **Failure budget** (opt-in, ADR 0015): with ``max_consecutive_failures``
        set, the loop counts each transition's consecutive ``failed`` firings
        (every ``failed`` record ``fire`` returns to this loop counts:
        handler-``failed``, resolve-miss, not-enabled, and a deposit violation
        under ``record_then_drop``). A transition whose count reaches the
        budget is **exhausted** — excluded from the enabled list handed to the
        policy, so selection moves past it. Any ``completed`` firing resets
        every count (the marking changed, so an exhausted transition's inputs
        may differ and it earns fresh retries). When every enabled transition
        is exhausted the run stops — quiescence-by-exhaustion — instead of
        burning the remaining steps. Counts are scoped to this ``run`` call
        and derived purely from the firing sequence, so replay determinism
        holds; failed fires still advance the step counter, keeping firingIds
        deterministic. ``fire``/``enabled_transitions`` primitives are
        untouched — the budget is a selection concern, like the policy.

        References: spec/firing-semantics.md (c, D5, D9, e); ADR 0014; ADR 0015.
        """
        # Declared Transition.priority per transition (net-static across the
        # run); the policy input carries the enabled subset each step, keyed
        # by exactly the enabledTransitions entries (absent declaration = 0),
        # so a priority-aware policy needs no access to the net. (ADR 0014.)
        declared_priority = {
            tr.name: tr.priority
            for tr in self._topology(net).transitions
            if tr.priority is not None
        }
        # Per-transition consecutive-failure counts for this run (ADR 0015):
        # incremented on a failed fire, cleared wholesale on any completed
        # fire. Threaded to every policy via consecutiveFailures (absent
        # history = 0) whether or not a budget is configured.
        failures: dict[str, int] = {}
        budget = self.max_consecutive_failures
        current: Marking = marking
        steps = 0
        while steps < max_steps:
            enabled = self.enabled_transitions(net, current, attempt=steps)
            if budget is not None:
                # Exhausted transitions (count >= budget) are hidden from the
                # policy — still enabled in the enablement sense, but not
                # selectable until a completed firing resets the counts.
                enabled = [name for name in enabled if failures.get(name, 0) < budget]
            if not enabled:
                break
            policy_handler = self.registry.resolve_firing_policy(self.policy)
            choice = policy_handler(
                FiringPolicyInput(
                    marking=current,
                    enabledTransitions=enabled,
                    priorities={
                        name: declared_priority.get(name, 0) for name in enabled
                    },
                    consecutiveFailures={
                        name: failures.get(name, 0) for name in enabled
                    },
                )
            )
            if choice is None:
                break
            current, record = self.fire(net, current, choice, attempt=steps)
            if record["status"] == "failed":
                failures[choice] = failures.get(choice, 0) + 1
            else:
                failures.clear()
            steps += 1
        return current
