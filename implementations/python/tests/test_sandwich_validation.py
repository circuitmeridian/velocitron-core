"""Sandwich-rule validation co-evolution tests
(``[spec] [impl] sandwich-rule validation of net-referenced handlers``).

The operator's A1: *"disallow initialization of an engine if a handler is
referenced in the net but cannot be found"*. The engine stays net-agnostic at
construction (``Engine(registry, *, policy, ...)`` / ``run(net, marking)``), so
the sandwich rule is delivered as a **public, opt-in** ``Engine.validate(net)``
instance method that walks every net-declared handler ref (transition
``handler``, transition ``guard``, consume-arc named predicate ``handler``) and
raises ``HandlerNotFound`` on the first unresolvable ref — **before any
``run``**. ``run`` does NOT auto-call it (A4 = (ii)); the caller chooses.

This is a contract-amendment ``[spec] [impl]`` feature, not a lock-driven
spec-violation fix: the existing graceful-degradation contract (resolve-miss ⇒
transition failure / not-enabled / predicate-false, locked in
``test_firing_engine.py`` / ``test_enablement.py``) is **retained** on both the
direct primitive path and the ``run`` path. ``validate`` is the only seam where
``HandlerNotFound`` propagates uncaught.

Red phase: ``Engine.validate`` does not exist yet, so every test that invokes
``engine.validate(net)`` fails with ``AttributeError`` until the green step
adds the method. S7/S8 exercise surfaces that already exist (``__init__``
policy validation / direct-primitive degradation) and pin the scoping
boundary — they pass today and document that the amendment is additive.
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import (
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerNotFound, HandlerRegistry
from velocitron.schema import Marking, Net, Token


# ── Shared helpers ───────────────────────────────────────────────────────


def _tok(t: str = "tick", **data: Any) -> Token:
    """A minimal token of type ``t`` with payload ``data``."""
    return Token(type=t, data=dict(data))


def _net(
    name: str,
    places: list[str],
    transitions: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
    accepts: dict[str, list[str]] | None = None,
) -> Net:
    """Build a minimal net dict and parse it.

    Each place accepts a single type defaulting to its own name; override a
    place's accepted types via ``accepts``. Mirrors the shared helper in
    ``test_enablement.py`` so the sandwich tests' net dicts stay short while
    staying explicit about accepted types.
    """
    place_dicts = [{"name": p, "accepts": (accepts or {}).get(p, [p])} for p in places]
    return parse_net(
        {"name": name, "places": place_dicts, "transitions": transitions, "arcs": arcs}
    )


def _bare_engine() -> Engine:
    """An Engine with an empty registry — register only handlers a path invokes."""
    return Engine(HandlerRegistry())


# ── S1: validate raises on an unresolvable transition handler ref ────────


class TestSandwichTransitionHandlerRef:
    """S1 — ``validate(net)`` raises ``HandlerNotFound`` when a transition's
    ``handler`` ref is not registered. The opt-in seam surfaces the
    configuration error before any ``run``."""

    def test_validate_raises_on_unresolved_transition_handler(self):
        # given: a net with one transition whose handler is NOT registered, and
        # an engine with an empty registry
        net = _net(
            "sandwich-missing-transition",
            places=["in"],
            transitions=[{"name": "proc", "handler": "not_registered"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in"},
                },
            ],
        )
        engine = _bare_engine()
        # when: invoking the opt-in validate seam
        # then: HandlerNotFound is raised, carrying the missing handler name
        with pytest.raises(HandlerNotFound) as exc:
            engine.validate(net)
        assert exc.value.name == "not_registered"


# ── S2: validate raises on an unresolvable guard ref ─────────────────────


class TestSandwichGuardRef:
    """S2 — ``validate(net)`` raises ``HandlerNotFound`` when a transition's
    ``guard`` ref is not registered."""

    def test_validate_raises_on_unresolved_guard(self):
        # given: a net with a guarded transition whose guard is NOT registered;
        # the transition handler itself IS registered (so the miss is isolated
        # to the guard ref)
        net = _net(
            "sandwich-missing-guard",
            places=["in"],
            transitions=[
                {"name": "guarded", "handler": "proc", "guard": "missing_guard"}
            ],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "guarded"},
                    "consume": {"type": "in"},
                },
            ],
        )
        reg = HandlerRegistry()
        reg.register_transition("proc", _noop_transition)
        engine = Engine(reg)
        # when: invoking the opt-in validate seam
        # then: HandlerNotFound is raised, carrying the missing guard name
        with pytest.raises(HandlerNotFound) as exc:
            engine.validate(net)
        assert exc.value.name == "missing_guard"


# ── S3: validate raises on an unresolvable named predicate handler ref ────


class TestSandwichPredicateHandlerRef:
    """S3 — ``validate(net)`` raises ``HandlerNotFound`` when a consume arc
    names a predicate ``handler`` that is not registered. Distinct from a CEL
    predicate (S5), which is not registry-resolved."""

    def test_validate_raises_on_unresolved_named_predicate(self):
        # given: a net whose consume arc names a predicate handler that is NOT
        # registered; the transition handler IS registered (miss isolated to
        # the predicate ref)
        net = _net(
            "sandwich-missing-predicate",
            places=["in"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in", "predicate": {"handler": "missing_pred"}},
                },
            ],
        )
        reg = HandlerRegistry()
        reg.register_transition("proc", _noop_transition)
        engine = Engine(reg)
        # when: invoking the opt-in validate seam
        # then: HandlerNotFound is raised, carrying the missing predicate name
        with pytest.raises(HandlerNotFound) as exc:
            engine.validate(net)
        assert exc.value.name == "missing_pred"


# ── S4: validate returns None for a fully-registered net; run quiesces ────


class TestSandwichFullyRegistered:
    """S4 — ``validate(net)`` returns ``None`` (no raise) when every declared
    ref is registered, and a subsequent ``run`` proceeds to quiescence
    normally. Confirms validation is scoped to misses, not a blanket raise."""

    def test_validate_returns_none_and_run_quiesces(self):
        # given: a fully-registered net — a transition handler, a guard, and a
        # named predicate are all registered — with a single input token
        net = _net(
            "sandwich-fully-registered",
            places=["in", "out"],
            transitions=[{"name": "proc", "handler": "proc", "guard": "ok_guard"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in", "predicate": {"handler": "pass_pred"}},
                },
                {
                    "from": {"transition": "proc"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
            accepts={"in": ["in"], "out": ["out"]},
        )
        reg = HandlerRegistry()
        reg.register_transition("proc", _passthrough_transition)
        reg.register_guard("ok_guard", lambda inp: True)
        reg.register_predicate("pass_pred", lambda inp: True)
        engine = Engine(reg)
        marking = Marking({"in": [_tok("in")]})
        # when: invoking the opt-in validate seam, then running
        result = engine.validate(net)
        # then: validate returned None (no raise) — scoped to misses only
        assert result is None
        # and: run reaches quiescence normally — the token moved in -> out
        final = engine.run(net, marking)
        assert list(final.get("in", [])) == []
        assert len(final.get("out", [])) == 1


# ── S5: a CEL-predicate arc does not trigger registry resolution ──────────


class TestSandwichCelPredicateNotResolved:
    """S5 — a consume arc with ``predicate.cel`` set (no ``handler``) validates
    fine against a registry with no predicate handlers. CEL predicates are
    compile/eval, not registry-resolved, so they are out of the sandwich
    rule's scope."""

    def test_validate_skips_cel_predicate(self):
        # given: a net whose consume arc carries a CEL predicate (no handler),
        # with the transition handler registered but NO predicate handlers
        net = _net(
            "sandwich-cel-predicate",
            places=["in"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in", "predicate": {"cel": "true"}},
                },
            ],
        )
        reg = HandlerRegistry()
        reg.register_transition("proc", _noop_transition)
        engine = Engine(reg)
        # when: invoking the opt-in validate seam
        # then: no raise — the CEL predicate is not a registry ref
        assert engine.validate(net) is None


# ── S6: run does NOT auto-validate (the (ii) scoping test) ────────────────


class TestSandwichRunDoesNotAutoValidate:
    """S6 — ``run`` does NOT auto-call ``validate(net)`` (A4 = (ii)). A net
    carrying an unregistered transition handler but where that transition is
    NOT enabled (its consume arc is unsatisfied by the marking) reaches
    quiescence without raising and without ever invoking validation. Marks the
    boundary between the opt-in seam and the run path."""

    def test_run_skips_whole_net_validation(self):
        # given: a net with an unregistered transition handler whose consume arc
        # is UNSATISFIED by the marking (empty `in`), so the transition is not
        # enabled and `run` never resolves its handler
        net = _net(
            "sandwich-run-no-autovalidate",
            places=["in", "out"],
            transitions=[{"name": "proc", "handler": "not_registered"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in"},
                },
                {
                    "from": {"transition": "proc"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
            accepts={"in": ["in"], "out": ["out"]},
        )
        engine = _bare_engine()
        marking = Marking({})  # empty — `in` is empty, proc is not enabled
        # when: running (no validate() call)
        # then: run reaches quiescence without raising — it did not auto-validate
        final = engine.run(net, marking)
        assert list(final.get("in", [])) == []
        assert list(final.get("out", [])) == []


# ── S7: the firing-policy __init__ validation is not regressed ────────────


class TestSandwichPolicyInitNotRegressed:
    """S7 — the firing-policy ``Engine.__init__`` validation (V1 from
    ``(firing-policy)``) is not regressed by the sandwich rule. An unresolvable
    policy still raises at construction; the sandwich rule is additive, not a
    replacement."""

    def test_unresolvable_policy_still_raises_at_init(self):
        # given / when / then: an unregistered policy raises at construction
        with pytest.raises(HandlerNotFound):
            Engine(HandlerRegistry(), policy="not-registered")
        # and: the default policy still constructs (validation scoped to misses)
        _bare_engine()


# ── S8: the direct primitive path still degrades gracefully ───────────────


class TestSandwichDirectPrimitiveDegradation:
    """S8 — the direct primitive path (``fire`` / ``enabled_transitions`` /
    ``select_binding``) still degrades gracefully on a resolve-miss. The
    sandwich rule is opt-in; the locked graceful-degradation contract survives
    for the direct primitives AND the ``run`` path. Re-asserts the scoping
    decision at the amended boundary."""

    def test_fire_resolve_miss_is_transition_failure_not_crash(self):
        # given: a net with an unregistered transition handler, a satisfied
        # consume arc, and an engine
        net = _net(
            "sandwich-fire-degrade",
            places=["in"],
            transitions=[{"name": "proc", "handler": "not_registered"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in"},
                },
            ],
        )
        engine = _bare_engine()
        marking = Marking({"in": [_tok("in")]})
        # when: firing proc directly
        new_marking, record = engine.fire(net, marking, "proc", attempt=0)
        # then: a failed record (not a raise); marking unchanged (rollback)
        assert record["status"] == "failed"
        assert list(new_marking["in"]) == [_tok("in")]

    def test_enablement_resolve_miss_degrades_to_not_enabled(self):
        # given: a net with an unregistered guard ref, a satisfied consume arc,
        # and an engine
        net = _net(
            "sandwich-enablement-degrade",
            places=["in"],
            transitions=[
                {"name": "guarded", "handler": "proc", "guard": "missing_guard"}
            ],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "guarded"},
                    "consume": {"type": "in"},
                },
            ],
        )
        engine = _bare_engine()
        marking = Marking({"in": [_tok("in")]})
        # when: querying enablement
        # then: not enabled and no raise (guard resolve-miss ⇒ not-enabled)
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "guarded", marking) is None

    def test_predicate_resolve_miss_yields_predicate_false(self):
        # given: a net whose consume arc names a predicate handler that is NOT
        # registered, with a present token
        net = _net(
            "sandwich-predicate-degrade",
            places=["in"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "in", "predicate": {"handler": "missing_pred"}},
                },
            ],
        )
        engine = _bare_engine()
        marking = Marking({"in": [_tok("in")]})
        # when: querying enablement
        # then: not enabled and no raise (predicate resolve-miss ⇒ predicate-false)
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "proc", marking) is None


# ── Module-level handler fixtures ────────────────────────────────────────


def _noop_transition(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """A registered-but-no-op transition handler for nets that only need the
    ref to resolve (validate does not invoke handlers)."""
    return {
        "status": "completed",
        "outputTokens": {},
        "error": None,
        "metadata": {},
    }


def _passthrough_transition(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """A transition handler that deposits one token to `out` (for S4's run)."""
    return {
        "status": "completed",
        "outputTokens": {"out": [Token(type="out", data={})]},
        "error": None,
        "metadata": {},
    }
