"""Enablement contract coverage for ``spec/firing-semantics.md``.

The tests cover §(a) Enablement and the enablement probe in §(e) Selection.
Each test pins the surface at which an invariant applies, such as a CEL
evaluation error degrading during enablement, and constructs the topology
needed to distinguish related paths, such as an inhibit arc carrying a
predicate or a guard rejecting the first valid binding.

These tests exercise pure computation; no journal or firing fixture is needed.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

# ── Shared helpers ──────────────────────────────────────────────────────


def _net(
    name: str,
    places: list[str],
    transitions: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
    accepts: dict[str, list[str]] | None = None,
    *,
    cel_adapter: CelAdapter | None = None,
) -> Net:
    """Build a minimal net dict and parse it.

    Each place accepts a single type defaulting to its own name; override a
    place's accepted types via ``accepts``. Keeps the lock tests' net dicts
    short while staying explicit about accepted types.
    """
    place_dicts = [{"name": p, "accepts": (accepts or {}).get(p, [p])} for p in places]
    return parse_net(
        {
            "name": name,
            "places": place_dicts,
            "transitions": transitions,
            "arcs": arcs,
        },
        cel_adapter=cel_adapter,
    )


def _tok(t: str = "task", **data: Any) -> Token:
    """A token of type ``t`` with the given data fields."""
    return Token(type=t, data=dict(data))


def _bare_engine(*, cel_adapter: CelAdapter | None = None) -> Engine:
    """An engine with no registered handlers — for pure enablement probing.

    Enablement never invokes a transition handler, so the lock tests that probe
    enablement without guards/predicates all share this empty-registry engine.
    Mirrors the module-level shared-fixture convention (``_net``, ``_tok``).
    """
    return Engine(HandlerRegistry(), cel_adapter=cel_adapter)


def _engine_with_guards(
    *, cel_adapter: CelAdapter | None = None, **guards: Any
) -> Engine:
    """An engine with guard handlers registered by name.

    Symmetric to ``_bare_engine`` (no handlers): the lock tests that probe
    enablement *with* a guard share this builder, keeping the
    ``HandlerRegistry`` + ``register_guard`` + ``Engine`` boilerplate out of
    each test body.
    """
    reg = HandlerRegistry()
    for name, fn in guards.items():
        reg.register_guard(name, fn)
    return Engine(reg, cel_adapter=cel_adapter)


# ── C1: consume type filtering is distinct from predicate filtering ──────


class TestEnablementLockType:
    """C1 — a token of the *wrong type* in the source place does not satisfy a
    consume arc, independent of the predicate. The current suite relies on the
    planning slice whose places hold only one type, so type-vs-predicate was
    never isolated."""

    @staticmethod
    def _type_filter_net() -> Net:
        """The shared type-filter net: a consume arc wanting ``feature`` from a
        place that also accepts ``bug``."""
        return _net(
            "type-filter-net",
            places=["inbox"],
            transitions=[{"name": "take_feature", "handler": "take_feature"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "take_feature"},
                    "consume": {"type": "feature"},
                },
            ],
            accepts={"inbox": ["feature", "bug"]},
        )

    def test_wrong_type_token_does_not_satisfy_consume_arc(self):
        """A token whose type != the arc's declared type does not match, even
        with no predicate (absent predicate ⇒ any token *of the type*).

        Construction-bite: the ``token.type == consume.type`` clause in
        ``_token_matches`` is the sole barrier — dropping it lets a wrong-type
        token match the arc, failing this test."""
        # given: a net whose consume arc wants type "feature" from a place
        # that also accepts "bug", with an engine and a single *bug* token
        net = self._type_filter_net()
        engine = _bare_engine()
        # and: the source place holds only a bug token (wrong type, no predicate)
        marking = Marking({"inbox": [_tok("bug", id="b1")]})
        # when: querying enablement
        enabled = engine.enabled_transitions(net, marking)
        # then: take_feature is NOT enabled — the bug token fails the type test
        assert "take_feature" not in enabled
        # and: no binding exists for the wrong-type-only marking
        assert engine.select_binding(net, "take_feature", marking) is None

    def test_right_type_token_satisfies_consume_arc(self):
        """The same arc IS satisfied by a token of the declared type — the
        positive control proving the negative above is a type effect, not a
        wiring fault."""
        # given: the same net as above with an engine and a single *feature* token
        net = self._type_filter_net()
        engine = _bare_engine()
        # and: the source place holds a feature token (right type)
        marking = Marking({"inbox": [_tok("feature", id="f1")]})
        # when: querying enablement
        enabled = engine.enabled_transitions(net, marking)
        # then: take_feature IS enabled
        assert "take_feature" in enabled


# ── C2 / C3: D6 error degradation at the enablement surface ──────────────


class TestEnablementLockPredicateDegradation:
    """C2, C3(a), C3(b) — a predicate that *raises* (CEL eval error, named
    handler runtime error, or an unresolved named-handler ref) degrades to
    *not-enabled* at the ``enabled_transitions`` / ``select_binding`` surface,
    never a runtime crash. The existing suite pins valid-CEL-matches and
    valid-named-handler-matches but never the raise/miss paths at enablement."""

    @staticmethod
    def _predicate_net(
        predicate: dict[str, Any], *, cel_adapter: CelAdapter | None = None
    ) -> Net:
        """A net with one consume arc carrying the given predicate."""
        return _net(
            "pred-degrade-net",
            places=["inbox"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "msg", "predicate": predicate},
                },
            ],
            accepts={"inbox": ["msg"]},
            cel_adapter=cel_adapter,
        )

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_eval_error_degrades_to_not_enabled(self, adapter: CelAdapter) -> None:
        """C2 — a CEL predicate that raises at eval (an undeclared reference)
        ⇒ not-enabled, not a crash. The token is present; only the predicate's
        eval blows up.

        Construction-bite: the ``try/except`` around ``adapter.eval()`` in
        ``_eval_predicate`` is the sole barrier — removing it makes
        ``enabled_transitions`` / ``select_binding`` raise instead of
        returning not-enabled."""
        # given: a net whose consume arc has a CEL predicate referencing a field
        # the token does not carry, with an engine and a present token
        net = self._predicate_net({"cel": "priority > 5"}, cel_adapter=adapter)
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking({"inbox": [_tok("msg")]})  # no 'priority' field
        # when: querying enablement
        # then: proc is NOT enabled and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []
        # and: select_binding returns None, not a crash
        assert engine.select_binding(net, "proc", marking) is None

    def test_named_predicate_handler_raise_degrades_to_not_enabled(self):
        """C3(a) — a named predicate handler that raises ⇒ not-enabled, not a
        crash. Symmetric with D6 for CEL eval errors.

        Construction-bite: the ``try/except`` around the named-predicate
        resolve+call in ``_eval_predicate`` is the sole barrier — removing it
        makes ``enabled_transitions`` / ``select_binding`` raise."""
        # given: a net whose consume arc names a predicate handler that raises,
        # with an engine and a present token
        net = self._predicate_net({"handler": "flaky_pred"})
        reg = HandlerRegistry()

        def flaky(inp: Any) -> bool:
            raise RuntimeError("predicate blew up")

        reg.register_predicate("flaky_pred", flaky)
        engine = Engine(reg)
        marking = Marking({"inbox": [_tok("msg", ok=True)]})
        # when: querying enablement
        # then: proc is NOT enabled and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "proc", marking) is None

    def test_unresolved_predicate_handler_ref_degrades_to_not_enabled(self):
        """C3(b) — a named predicate ref that cannot be resolved
        (``resolve_predicate`` ⇒ ``HandlerNotFound``) ⇒ not-enabled, not a
        crash.

        Construction-bite: the ``try/except`` around ``resolve_predicate`` in
        ``_eval_predicate`` is the sole barrier — removing it lets the
        ``HandlerNotFound`` propagate and crash the enablement probe."""
        # given: a net whose consume arc names a predicate handler that is NOT
        # registered, with an engine and a present token
        net = self._predicate_net({"handler": "missing_pred"})
        engine = _bare_engine()
        marking = Marking({"inbox": [_tok("msg", ok=True)]})
        # when: querying enablement
        # then: proc is NOT enabled and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "proc", marking) is None


# ── C5: inhibit + predicate combinatorial zero-test ───────────────────────


class TestEnablementLockInhibitPredicate:
    """C5 — an inhibit arc carrying a predicate runs its zero-test over
    type+predicate, not type alone. The place can hold tokens of the right
    type and the inhibit arc is still satisfied when none of them pass the
    predicate."""

    @staticmethod
    def _inhibit_predicate_net() -> Net:
        """A net with a consume arc (``in``) and an inhibit arc (``gate``)
        whose zero-test carries a CEL predicate. ``work`` is enabled iff the
        consume arc is satisfied AND the inhibit arc's type+predicate zero-test
        passes (no matching token in ``gate``)."""
        return _net(
            "inhibit-pred-net",
            places=["in", "gate"],
            transitions=[{"name": "work", "handler": "work"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "work"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"place": "gate"},
                    "to": {"transition": "work"},
                    "consume": {
                        "type": "flag",
                        "mode": "inhibit",
                        "predicate": {"cel": "v > 5"},
                    },
                },
            ],
            accepts={"in": ["task"], "gate": ["flag"]},
        )

    def test_inhibit_satisfied_when_tokens_present_but_predicate_unmet(self):
        """The inhibit arc is satisfied when ``gate`` holds a token of the
        right type whose predicate *fails* — the zero-test is over
        type+predicate, so a non-matching token does not block.

        Reversion-verified bite: reverting ``_inhibit_satisfied`` to a
        type-only zero-test (dropping the predicate from ``_token_matches``)
        makes the gate token match and fails this test."""
        # given: the inhibit+predicate net with an engine, a satisfied consume
        # arc, and a gate token that is the right type but fails the predicate
        net = self._inhibit_predicate_net()
        engine = _bare_engine()
        marking = Marking(
            {
                "in": [_tok("task", id="t1")],
                "gate": [_tok("flag", v=1)],  # right type, predicate `v > 5` fails
            }
        )
        # when: querying enablement
        enabled = engine.enabled_transitions(net, marking)
        # then: work IS enabled — the gate token does not match (predicate fails)
        assert "work" in enabled
        assert engine.select_binding(net, "work", marking) is not None

    def test_inhibit_blocks_when_predicate_met(self):
        """The inhibit arc blocks when ``gate`` holds a token of the right type
        whose predicate *passes* — that token matches the zero-test."""
        # given: the inhibit+predicate net with an engine, a satisfied consume
        # arc, and a gate token that is the right type AND passes the predicate
        net = self._inhibit_predicate_net()
        engine = _bare_engine()
        marking = Marking(
            {
                "in": [_tok("task", id="t1")],
                "gate": [_tok("flag", v=9)],  # right type, predicate `v > 5` passes
            }
        )
        # when: querying enablement
        # then: work is NOT enabled — the gate token matches the inhibit zero-test
        assert "work" not in engine.enabled_transitions(net, marking)
        assert engine.select_binding(net, "work", marking) is None


# ── C7/C8: guard input shape — full consume binding, inhibit excluded ───


class TestEnablementLockGuardInputShape:
    """C7/C8 — the guard's ``inputTokens`` is the full binding across all
    consume-mode arcs (all ``weight`` tokens per arc) and EXCLUDES inhibit
    arcs (they contribute no tokens). No test pinned the binding shape the
    guard sees; existing guard tests use single-binding nets."""

    def test_guard_sees_all_consume_weight_and_excludes_inhibit(self):
        """A guard that returns True iff its ``inputTokens`` is exactly the
        consume binding (``in`` with 2 tokens, weight 2) and contains no
        inhibit place (``gate``) — so enablement holds only when the engine
        builds the binding per the spec.

        Construction-bite: ``_binding_from_combo`` builds the binding over
        consume arcs only (inhibit arcs consume nothing), so the guard never
        sees ``gate``; the consume-only binding construction is the sole
        barrier — leaking inhibit tokens into the binding fails the guard's
        ``keys() == ["in"]`` check."""
        # given: a net with a weight-2 consume arc (``in``), an inhibit arc
        # (``gate``), and a guard that validates the exact binding shape
        net = _net(
            "guard-shape-net",
            places=["in", "gate"],
            transitions=[
                {"name": "guarded", "handler": "guarded", "guard": "shape_guard"}
            ],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "guarded"},
                    "consume": {"type": "task", "weight": 2},
                },
                {
                    "from": {"place": "gate"},
                    "to": {"transition": "guarded"},
                    "consume": {"type": "flag", "mode": "inhibit"},
                },
            ],
            accepts={"in": ["task"], "gate": ["flag"]},
        )

        def shape_guard(inp: Any) -> bool:
            # The guard sees ONLY the consume binding: `in` with both weight
            # tokens, and `gate` (the inhibit place) absent.
            return (
                list(inp["inputTokens"].keys()) == ["in"]
                and len(inp["inputTokens"]["in"]) == 2
            )

        engine = _engine_with_guards(shape_guard=shape_guard)
        # and: the consume arc is satisfied (2 tokens) and the inhibit arc is
        # satisfied (gate empty)
        marking = Marking({"in": [_tok("task", id="a"), _tok("task", id="b")]})
        # when: querying enablement
        enabled = engine.enabled_transitions(net, marking)
        # then: guarded IS enabled — the guard accepted the spec-shaped binding
        assert "guarded" in enabled
        binding = engine.select_binding(net, "guarded", marking)
        assert binding is not None
        assert list(binding.keys()) == ["in"]
        assert len(binding["in"]) == 2


# ── C10: unresolved guard ref degrades to not-enabled ────────────────────


class TestEnablementLockGuardResolution:
    """C10 — an unresolved guard ref (``resolve_guard`` ⇒ ``HandlerNotFound``)
    degrades to not-enabled, not a crash. Parallel to C3(b) for predicates."""

    def test_unresolved_guard_ref_degrades_to_not_enabled(self):
        """A transition whose guard ref is not registered is not enabled, and
        probing it does not raise.

        Construction-bite: the ``try/except HandlerNotFound`` around
        ``resolve_guard`` in ``_select_binding`` is the sole barrier —
        removing it lets the ``HandlerNotFound`` propagate and crash the
        enablement probe."""
        # given: a net with a guarded transition whose guard is NOT registered,
        # a satisfied consume arc, and an engine
        net = _net(
            "unresolved-guard-net",
            places=["in"],
            transitions=[
                {"name": "guarded", "handler": "guarded", "guard": "missing_guard"}
            ],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "guarded"},
                    "consume": {"type": "task"},
                },
            ],
            accepts={"in": ["task"]},
        )
        engine = _bare_engine()
        marking = Marking({"in": [_tok("task", id="t1")]})
        # when: querying enablement
        # then: guarded is NOT enabled and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "guarded", marking) is None


# ── C11: attempt threaded to the guard at the enablement surface ─────────


class TestEnablementLockAttemptThreading:
    """C11 — ``enabled_transitions(net, marking, attempt=N)`` and
    ``select_binding(..., attempt=N)`` thread ``attempt`` to the guard at the
    *enablement surface*, not only through ``run``. A unit-level pin guards
    against a reversion that threads attempt in ``run`` only (the existing
    ``test_run_honors_attempt_sensitive_guard`` is end-to-end via ``run``)."""

    @staticmethod
    def _attempt_net() -> Net:
        """A consume-only net: structurally enabled whenever a token is
        present, so the guard is the only thing gating it."""
        return _net(
            "attempt-guard-net",
            places=["loop"],
            transitions=[
                {"name": "loop_t", "handler": "loop_t", "guard": "fresh_only"}
            ],
            arcs=[
                {
                    "from": {"place": "loop"},
                    "to": {"transition": "loop_t"},
                    "consume": {"type": "loop"},
                },
            ],
        )

    def test_enabled_transitions_threads_attempt_to_guard(self):
        """An attempt-sensitive guard (enables only at attempt==1) is
        consulted with the probe's ``attempt`` at the enablement surface.

        Construction-bite: the ``attempt`` threaded through
        ``enabled_transitions`` → ``select_binding`` → ``_make_ctx`` → guard
        is the sole barrier — a reversion that drops the threading (the guard
        always sees ``attempt=0``) fails this test."""
        # given: a consume-only net guarded by a guard that enables only at
        # attempt==1, with an engine and a present token
        net = self._attempt_net()
        engine = _engine_with_guards(
            fresh_only=lambda inp: inp["firingContext"]["attempt"] == 1  # pyright: ignore[reportUnknownLambdaType]
        )
        marking = Marking({"loop": [_tok("loop")]})
        # when: probing enablement at attempt=0 and at attempt=1
        enabled_at_0 = engine.enabled_transitions(net, marking, attempt=0)
        enabled_at_1 = engine.enabled_transitions(net, marking, attempt=1)
        # then: the guard sees the threaded attempt — disabled at 0, enabled at 1
        assert "loop_t" not in enabled_at_0
        assert "loop_t" in enabled_at_1

    def test_select_binding_threads_attempt_to_guard(self):
        """``select_binding`` likewise threads ``attempt`` to the guard.

        Construction-bite: same ``attempt`` threading as the
        ``enabled_transitions`` probe — dropping it makes the guard always see
        ``attempt=0`` and fails this test."""
        # given: the same attempt-sensitive consume-only net with an engine
        net = self._attempt_net()
        engine = _engine_with_guards(
            fresh_only=lambda inp: inp["firingContext"]["attempt"] == 1  # pyright: ignore[reportUnknownLambdaType]
        )
        marking = Marking({"loop": [_tok("loop")]})
        # when: selecting a binding at attempt=0 and at attempt=1
        # then: no binding at 0, a binding at 1 — the guard saw the attempt
        assert engine.select_binding(net, "loop_t", marking, attempt=0) is None
        assert engine.select_binding(net, "loop_t", marking, attempt=1) is not None


# ── C12: guard-rejected-first-binding advances to the next valid binding ─


class TestEnablementLockGuardAdvancesBinding:
    """C12 — D2's core: the engine selects the *first binding for which the
    guard returns true*. When the first valid binding is rejected by the
    guard, ``select_binding`` advances to the next valid binding rather than
    giving up. Existing guard tests use single-binding nets, so advancement
    was never exercised."""

    @staticmethod
    def _advance_binding_net(guard_name: str) -> Net:
        """The shared advance-binding net guarded by ``guard_name``."""
        return _net(
            "advance-binding-net",
            places=["in"],
            transitions=[{"name": "pick", "handler": "pick", "guard": guard_name}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "pick"},
                    "consume": {"type": "task"},
                },
            ],
            accepts={"in": ["task"]},
        )

    def test_select_binding_advances_past_guard_rejected_first_binding(self):
        """With two consumable tokens in lexicographic order [a, b] and a guard
        that accepts only `b`, ``select_binding`` returns the `b` binding — it
        did not stop at the rejected `a` binding.

        Reversion-verified bite: reverting ``_select_binding`` to return the
        first valid binding without consulting the guard returns the `a`
        binding and fails this test."""
        # given: a net with one weight-1 consume arc holding two tokens [a, b]
        # and a guard that accepts only the binding whose token id is 'b'
        net = self._advance_binding_net("only_b")

        def only_b(inp: Any) -> bool:
            bound = inp["inputTokens"]["in"]
            return len(bound) == 1 and bound[0].data.get("id") == "b"

        engine = _engine_with_guards(only_b=only_b)
        marking = Marking({"in": [_tok("task", id="a"), _tok("task", id="b")]})
        # when: selecting a binding for pick
        binding = engine.select_binding(net, "pick", marking)
        # then: the binding is `b` — the first guard-accepted binding, not the
        # first lexicographic binding (which the guard rejected)
        assert binding is not None
        assert [t.data["id"] for t in binding["in"]] == ["b"]
        # and: pick is enabled (some guard-accepted binding exists)
        assert "pick" in engine.enabled_transitions(net, marking)

    def test_no_binding_when_guard_rejects_all(self):
        """When the guard rejects every valid binding, the transition is not
        enabled — advancement is not infinite acceptance.

        Reversion-verified bite: same reversion as the advance test — skipping
        the guard returns the first binding instead of None and fails this
        test."""
        # given: the same net with a guard that accepts nothing
        net = self._advance_binding_net("never")
        engine = _engine_with_guards(never=lambda _inp: False)  # pyright: ignore[reportUnknownLambdaType]
        marking = Marking({"in": [_tok("task", id="a"), _tok("task", id="b")]})
        # when: selecting a binding for pick
        # then: no binding is accepted and pick is not enabled
        assert engine.select_binding(net, "pick", marking) is None
        assert "pick" not in engine.enabled_transitions(net, marking)


# ── C14: enabled_transitions returns ids in net declaration order ────────


class TestEnablementLockDeclarationOrder:
    """C14 — when multiple transitions are enabled, ``enabled_transitions``
    returns their ids in ``net.transitions`` declaration order, not sorted
    and not arbitrary."""

    def test_enabled_transitions_follow_declaration_order(self):
        """Three transitions declared [zeta, alpha, beta], all simultaneously
        enabled, come back in that order — not alphabetical.

        Construction-bite: ``enabled_transitions`` iterates
        ``net.transitions`` in declaration order — a reversion that sorts the
        result fails this test (would yield ``[alpha, beta, zeta]``)."""
        # given: a net with three transitions declared out of alphabetical
        # order, each with its own satisfied consume arc (no guard, no inhibit)
        net = _net(
            "decl-order-net",
            places=["pz", "pa", "pb"],
            transitions=[
                {"name": "zeta", "handler": "zeta"},
                {"name": "alpha", "handler": "alpha"},
                {"name": "beta", "handler": "beta"},
            ],
            arcs=[
                {
                    "from": {"place": "pz"},
                    "to": {"transition": "zeta"},
                    "consume": {"type": "pz"},
                },
                {
                    "from": {"place": "pa"},
                    "to": {"transition": "alpha"},
                    "consume": {"type": "pa"},
                },
                {
                    "from": {"place": "pb"},
                    "to": {"transition": "beta"},
                    "consume": {"type": "pb"},
                },
            ],
        )
        engine = _bare_engine()
        # and: every consume arc is satisfied
        marking = Marking(
            {
                "pz": [_tok("pz")],
                "pa": [_tok("pa")],
                "pb": [_tok("pb")],
            }
        )
        # when: querying enabled transitions
        enabled = engine.enabled_transitions(net, marking)
        # then: the order is the declaration order, not alphabetical
        assert enabled == ["zeta", "alpha", "beta"]


# ── C15: a source transition (no consume arcs) is always enabled ────────


class TestEnablementLockSourceTransition:
    """C15 — a transition with only produce arcs (no consume/inhibit/guard) is
    always enabled: ``product()`` with no per-arc candidates yields one empty
    binding. Not previously pinned."""

    @staticmethod
    def _source_net() -> Net:
        """The shared source net: a transition with only a produce arc."""
        return _net(
            "source-net",
            places=["out"],
            transitions=[{"name": "source", "handler": "source"}],
            arcs=[
                {
                    "from": {"transition": "source"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )

    def test_source_transition_with_no_consume_arcs_is_enabled(self):
        """A transition that only produces (a net source) is enabled against
        any marking, with an empty binding.

        Construction-bite: ``product()`` with no per-arc candidates yields
        exactly one empty binding — a reversion that special-cases "no consume
        arcs ⇒ not enabled" fails this test."""
        net = self._source_net()
        engine = _bare_engine()
        # and: an empty marking (nothing to consume, nothing to inhibit)
        marking = Marking({})
        # when: querying enablement and selecting a binding
        enabled = engine.enabled_transitions(net, marking)
        binding = engine.select_binding(net, "source", marking)
        # then: source IS enabled and its binding is the empty binding
        assert "source" in enabled
        assert binding == {}

    def test_source_transition_enabled_against_a_nonempty_marking(self):
        """The source transition is enabled regardless of marking contents —
        it consumes nothing, so unrelated tokens cannot gate it."""
        # given: the same source net with an engine and a marking holding
        # unrelated tokens in unrelated places
        net = self._source_net()
        engine = _bare_engine()
        marking = Marking({"out": [_tok("out", id="preexisting")]})
        # when: querying enablement
        # then: source is still enabled — it has no consume/inhibit arcs to gate it
        assert "source" in engine.enabled_transitions(net, marking)


# ── Strict boolean condition results ────────────────────────────────────


class _ConstantCelAdapter:
    """Minimal CEL adapter returning one controlled runtime result."""

    def __init__(self, result: Any) -> None:
        self.result = result

    def compile(self, expr: str) -> str:
        return expr

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        return self.result


class TestStrictBooleanConditions:
    """Every enablement condition accepts only the exact boolean ``True``."""

    def test_inline_cel_predicate_rejects_truthy_non_boolean(self):
        """A CEL predicate result of NaN is condition-false.

        Reversion bite: replacing the exact-``True`` check with Python
        ``bool(result)`` enables ``proc`` because Python treats NaN as truthy,
        while JavaScript treats NaN as falsy.
        """
        # given: a satisfied consume arc whose CEL backend returns non-boolean NaN
        adapter = _ConstantCelAdapter(float("nan"))
        net = _net(
            "strict-cel-predicate",
            places=["inbox"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {
                        "type": "msg",
                        "predicate": {"cel": "value > 0"},
                    },
                }
            ],
            accepts={"inbox": ["msg"]},
            cel_adapter=adapter,
        )
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking({"inbox": [_tok("msg", value=1)]})
        # when: enablement evaluates the non-boolean predicate result
        enabled = engine.enabled_transitions(net, marking)
        # then: only exact True passes
        assert enabled == []

    def test_named_predicate_rejects_truthy_non_boolean(self):
        """A named predicate result of NaN is condition-false.

        Reversion bite: restoring ``bool(result)`` accepts Python-truthy NaN
        (unlike JavaScript) and incorrectly enables ``proc``.
        """
        # given: a named predicate that violates its boolean return contract
        net = _net(
            "strict-named-predicate",
            places=["inbox"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {
                        "type": "msg",
                        "predicate": {"handler": "non_boolean"},
                    },
                }
            ],
            accepts={"inbox": ["msg"]},
        )
        registry = HandlerRegistry()

        def non_boolean_predicate(_inp: Any) -> Any:
            return float("nan")

        registry.register_predicate("non_boolean", cast(Any, non_boolean_predicate))
        engine = Engine(registry)
        marking = Marking({"inbox": [_tok("msg")]})
        # when: enablement evaluates the named predicate
        enabled = engine.enabled_transitions(net, marking)
        # then: its truthy non-boolean result does not match the token
        assert enabled == []

    def test_guard_rejects_truthy_non_boolean(self):
        """A guard result of NaN rejects its candidate binding.

        Reversion bite: the former ``if not accepted`` host-truthiness check
        accepts Python-truthy NaN (unlike JavaScript) and selects the binding.
        """
        # given: an otherwise enabled transition with a non-boolean guard
        net = _net(
            "strict-guard",
            places=["inbox"],
            transitions=[{"name": "proc", "handler": "proc", "guard": "non_boolean"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "msg"},
                }
            ],
            accepts={"inbox": ["msg"]},
        )

        def non_boolean_guard(_inp: Any) -> Any:
            return float("nan")

        engine = _engine_with_guards(non_boolean=non_boolean_guard)
        marking = Marking({"inbox": [_tok("msg")]})
        # when: selecting a binding through the guard
        binding = engine.select_binding(net, "proc", marking)
        # then: the non-boolean result degrades to not-enabled
        assert binding is None

    def test_timer_condition_rejects_truthy_non_boolean(self):
        """A timer CEL result of NaN leaves the binding unmatured.

        Reversion bite: restoring ``bool(result)`` treats Python-truthy NaN
        (unlike JavaScript) as a mature timer and enables ``timed``.
        """
        # given: a timed transition whose CEL backend returns non-boolean NaN
        adapter = _ConstantCelAdapter(float("nan"))
        net = _net(
            "strict-timer",
            places=["clock", "inbox"],
            transitions=[
                {
                    "name": "timed",
                    "handler": "timed",
                    "timer": {
                        "clock": "clock",
                        "cel": "clock.now >= item.due",
                        "bind": {"item": "inbox"},
                    },
                }
            ],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "timed"},
                    "consume": {"type": "msg"},
                }
            ],
            accepts={"clock": ["clock"], "inbox": ["msg"]},
            cel_adapter=adapter,
        )
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking(
            {
                "clock": [_tok("clock", now=10)],
                "inbox": [_tok("msg", due=5)],
            }
        )
        # when: timer enablement evaluates the non-boolean result
        enabled = engine.enabled_transitions(net, marking)
        # then: the candidate remains unmatured
        assert enabled == []

    def test_correlated_inhibit_non_boolean_fails_closed(self):
        """A non-boolean correlate result blocks the candidate binding.

        Reversion bite: restoring ``bool(result)`` treats Python-falsy empty
        list as no match and enables ``proc``; JavaScript would treat the same
        empty list as truthy. Strict typing removes that host-dependent branch.
        """
        # given: a correlated inhibitor whose CEL backend returns an empty list
        adapter = _ConstantCelAdapter([])
        net = _net(
            "strict-correlate",
            places=["inbox", "gate"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "msg"},
                },
                {
                    "from": {"place": "gate"},
                    "to": {"transition": "proc"},
                    "consume": {
                        "type": "flag",
                        "mode": "inhibit",
                        "correlate": {"cel": "token.id == binding.inbox[0].id"},
                    },
                },
            ],
            accepts={"inbox": ["msg"], "gate": ["flag"]},
            cel_adapter=adapter,
        )
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking(
            {
                "inbox": [_tok("msg", id="x")],
                "gate": [_tok("flag", id="x")],
            }
        )
        # when: correlation evaluates to a non-boolean
        binding = engine.select_binding(net, "proc", marking)
        # then: the condition error fails closed, rejecting the binding
        assert binding is None
