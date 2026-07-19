"""Firing-policy lock-the-coverage tests (``[lock] firing policy``).

The coverage-*lock* for ``spec/firing-semantics.md`` §(e) selection loop:
the firing policy chooses which enabled transition fires each step, and the
``None`` return stops the run. The selection-loop behavioral code already
exists on ``main``; per AGENTS.md this is a lock-the-coverage pass, not
red-then-green -- each test passes against the existing (correct) impl and was
verified to bite under a targeted reversion that breaks the invariant it pins.

Scope: the engine-integration surface (P1-P5) plus V1 -- the PLAN-sanctioned
behavioral change that ``Engine.__init__`` validates the firing-policy ref as
a configuration error (an unresolvable policy is engine config, not
net-referenced; there is no transition context to fail within, so the
misconfiguration surfaces at construction, not out of ``run``).
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import (
    FiringPolicyInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine
from velocitron.journal import FiringRecord, InjectionRecord, Journal
from velocitron.parser import parse_net
from velocitron.registry import (
    DEFAULT_FIRING_POLICY,
    HandlerNotFound,
    HandlerRegistry,
)
from velocitron.schema import Marking, Net, Token


# ── Shared helpers ───────────────────────────────────────────────────────


def _tok(t: str = "tick", **data: Any) -> Token:
    """A minimal token of type ``t`` with payload ``data``."""
    return Token(type=t, data=dict(data))


def _marking(**places: list[Token]) -> Marking:
    """A marking from ``place=tokens`` keyword pairs."""
    return Marking({place: list(toks) for place, toks in places.items()})


def _net(d: dict[str, Any]) -> Net:
    """Parse a net dict (thin alias for the parser)."""
    return parse_net(d)


def _bare_engine(
    *,
    journal: Journal | None = None,
    deposit_violation: str = "raise",
    policy: str = DEFAULT_FIRING_POLICY,
) -> Engine:
    """An Engine with an empty registry -- register only handlers the path invokes."""
    return Engine(
        HandlerRegistry(),
        journal=journal,
        deposit_violation=deposit_violation,
        policy=policy,
    )


def _engine(
    reg: HandlerRegistry,
    *,
    journal: Journal | None = None,
    deposit_violation: str = "raise",
    policy: str = DEFAULT_FIRING_POLICY,
) -> Engine:
    """An Engine over ``reg`` with the given journal / violation mode / policy."""
    return Engine(
        reg,
        journal=journal,
        deposit_violation=deposit_violation,
        policy=policy,
    )


def _reg(*pairs: tuple[str, Any]) -> HandlerRegistry:
    """A registry from ``(name, handler)`` pairs of transition handlers."""
    reg = HandlerRegistry()
    for name, handler in pairs:
        reg.register_transition(name, handler)
    return reg


class _CapturingJournal:
    """In-memory Journal: captures records, assigns no ``sequence`` (D4)."""

    def __init__(self) -> None:
        self.firings: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self.injections: list[dict[str, Any]] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.firings.append(dict(record))

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self.violations.append(dict(record))

    def record_injection(self, record: InjectionRecord) -> None:
        self.injections.append(dict(record))


# ── P1: the policy's returned id drives which transition fires ────────────
# Two transitions (ta, tb) each independently enabled on a single `in` token;
# a `pick_last` policy chooses tb, so out_b lands and out_a stays empty.

_P1_NET = _net(
    {
        "name": "p1-policy-choice",
        "places": [
            {"name": "in", "accepts": ["tick"]},
            {"name": "out_a", "accepts": ["tick"]},
            {"name": "out_b", "accepts": ["tick"]},
        ],
        "transitions": [
            {"name": "ta", "handler": "ta"},
            {"name": "tb", "handler": "tb"},
        ],
        "arcs": [
            {
                "from": {"place": "in"},
                "to": {"transition": "ta"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "ta"},
                "to": {"place": "out_a"},
                "produce": {"type": "tick", "destination": "out_a"},
            },
            {
                "from": {"place": "in"},
                "to": {"transition": "tb"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "tb"},
                "to": {"place": "out_b"},
                "produce": {"type": "tick", "destination": "out_b"},
            },
        ],
    }
)


def _ta(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"out_a": inp["inputTokens"].get("in", [])},
        "error": None,
        "metadata": {},
    }


def _tb(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"out_b": inp["inputTokens"].get("in", [])},
        "error": None,
        "metadata": {},
    }


def _pick_last(inp: FiringPolicyInput) -> str | None:
    """Choose the LAST enabled transition (reverse of the default first-found)."""
    enabled = inp["enabledTransitions"]
    return enabled[-1] if enabled else None


# ── P2: None => quiescence even when transitions are enabled ─────────────
# A self-loop that is always enabled; a `never_fire` policy returns None and
# the run must stop immediately with zero firings and the marking untouched.

_P2_NET = _net(
    {
        "name": "p2-none-quiescence",
        "places": [{"name": "loop", "accepts": ["tick"]}],
        "transitions": [{"name": "loop_t", "handler": "loop_t"}],
        "arcs": [
            {
                "from": {"place": "loop"},
                "to": {"transition": "loop_t"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "loop_t"},
                "to": {"place": "loop"},
                "produce": {"type": "tick", "destination": "loop"},
            },
        ],
    }
)


def _loop_pass(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"loop": inp["inputTokens"].get("loop", [])},
        "error": None,
        "metadata": {},
    }


def _never_fire(inp: FiringPolicyInput) -> str | None:
    """Never fire: always returns None to stop the run."""
    return None


# ── P3: enabledTransitions arrive in net declaration order (not sorted) ──
# Two independent transitions declared [zeta, alpha] (reverse-alphabetic), each
# consuming its own input and producing its own output. Shared with P5 (same
# shape; P5 asserts the default policy's replay order over it). The policy
# captures the list it receives and P3 asserts it is the declaration order.

_ORDER_NET = _net(
    {
        "name": "ordered-zeta-alpha",
        "places": [
            {"name": "in_z", "accepts": ["tick"]},
            {"name": "in_a", "accepts": ["tick"]},
            {"name": "out_z", "accepts": ["tick"]},
            {"name": "out_a", "accepts": ["tick"]},
        ],
        "transitions": [
            {"name": "zeta", "handler": "zeta"},
            {"name": "alpha", "handler": "alpha"},
        ],
        "arcs": [
            {
                "from": {"place": "in_z"},
                "to": {"transition": "zeta"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "zeta"},
                "to": {"place": "out_z"},
                "produce": {"type": "tick", "destination": "out_z"},
            },
            {
                "from": {"place": "in_a"},
                "to": {"transition": "alpha"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "alpha"},
                "to": {"place": "out_a"},
                "produce": {"type": "tick", "destination": "out_a"},
            },
        ],
    }
)


def _zeta(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"out_z": inp["inputTokens"].get("in_z", [])},
        "error": None,
        "metadata": {},
    }


def _alpha(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"out_a": inp["inputTokens"].get("in_a", [])},
        "error": None,
        "metadata": {},
    }


_P3_seen: list[list[str]] = []


def _capture_order(inp: FiringPolicyInput) -> str | None:
    """Capture the enabledTransitions list, then pick the first."""
    _P3_seen.append(list(inp["enabledTransitions"]))
    enabled = inp["enabledTransitions"]
    return enabled[0] if enabled else None


# ── P4: the policy receives the CURRENT marking, not the initial ──────────
# src --(t_fill)--> p --(t_drain)--> done. At step 0 only t_fill is enabled
# (p empty); a `gate_on_p` policy fires t_drain only when it sees p populated
# in the CURRENT marking, so done lands at step 1.

_P4_NET = _net(
    {
        "name": "p4-current-marking",
        "places": [
            {"name": "src", "accepts": ["tick"]},
            {"name": "p", "accepts": ["tick"]},
            {"name": "done", "accepts": ["tick"]},
        ],
        "transitions": [
            {"name": "t_fill", "handler": "t_fill"},
            {"name": "t_drain", "handler": "t_drain"},
        ],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t_fill"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "t_fill"},
                "to": {"place": "p"},
                "produce": {"type": "tick", "destination": "p"},
            },
            {
                "from": {"place": "p"},
                "to": {"transition": "t_drain"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "t_drain"},
                "to": {"place": "done"},
                "produce": {"type": "tick", "destination": "done"},
            },
        ],
    }
)


def _fill(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"p": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


def _drain(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "completed",
        "outputTokens": {"done": inp["inputTokens"].get("p", [])},
        "error": None,
        "metadata": {},
    }


def _gate_on_p(inp: FiringPolicyInput) -> str | None:
    """Fire t_drain only when the CURRENT marking shows p populated."""
    enabled = inp["enabledTransitions"]
    if not enabled:
        return None
    if "t_drain" in enabled:
        return "t_drain" if inp["marking"].get("p") else None
    return enabled[0]


# ── P5: default first-found is deterministic / replayable (D5) ───────────
# Reuses `_ORDER_NET` (the [zeta, alpha] pair from P3): two runs under the
# DEFAULT policy produce identical, declaration-order firing sequences.
def _records_without_timestamps(journal: _CapturingJournal) -> list[dict[str, Any]]:
    """Firing records with the non-deterministic `timestamps` field stripped,
    so two replay runs can be compared record-for-record (D5)."""
    out: list[dict[str, Any]] = []
    for rec in journal.firings:
        copy = dict(rec)
        copy.pop("timestamps", None)
        out.append(copy)
    return out


class TestPolicySelectionSurface:
    """P1-P5: the engine-integration surface of the firing policy."""

    def test_p1_policy_returned_id_drives_which_transition_fires(self):
        """P1 — the policy's returned id drives which transition fires.

        Invariant: the engine fires the transition whose id the policy
        returns, not a fixed one. Bite (reversion-verified): in
        ``engine.py`` ``run``, reverting
        ``self.fire(net, current, choice, attempt=steps)`` to
        ``self.fire(net, current, enabled[0], attempt=steps)`` (ignore the
        policy's ``choice``, fire first-found instead) makes ``ta`` fire
        (declaration order) and land a token in ``out_a``; the
        ``out_b``-has-one-tick assertion fails (confirmed: ``AssertionError``
        — ``out_b`` empty, ``out_a`` populated)."""
        # given a net with two independently-enabled transitions and a
        # pick_last policy that chooses the last enabled
        reg = _reg(("ta", _ta), ("tb", _tb))
        reg.register_firing_policy("pick_last", _pick_last)
        engine = _engine(reg, policy="pick_last")
        # when running one step
        final = engine.run(_P1_NET, _marking(**{"in": [_tok()]}), max_steps=10)
        # then the policy's choice (tb) fired: out_b landed, out_a empty
        assert list(final.get("out_b", [])) == [_tok()]
        assert list(final.get("out_a", [])) == []

    def test_p2_none_return_means_quiescence_even_when_enabled(self):
        """P2 — a policy returning None stops the run even with enabled
        transitions.

        Invariant: ``choice is None`` is the quiescence signal — the run
        stops, it does not fire on. Bite (reversion-verified): in
        ``engine.py`` ``run``, reverting ``if choice is None: break`` to
        ``if choice is None: choice = enabled[0]`` (drop the stop — coerce
        the stop-signal into firing the first enabled instead) makes the
        always-enabled self-loop fire every step until ``max_steps``; the
        ``len(journal.firings) == 0`` assertion fails (confirmed:
        ``AssertionError: assert 10 == 0`` — ten firings recorded, the
        final marking mutated). The literal ``break``-to-``continue``
        reversion instead hangs (``continue`` neither fires nor advances
        ``steps``), so the coercion is the terminable realization of
        "drop the stop" that yields the documented firings-gt-0 failure."""
        # given a self-loop net that is always enabled and a never_fire policy
        reg = _reg(("loop_t", _loop_pass))
        reg.register_firing_policy("never_fire", _never_fire)
        journal = _CapturingJournal()
        engine = _engine(reg, policy="never_fire", journal=journal)
        # when running
        final = engine.run(_P2_NET, _marking(loop=[_tok()]), max_steps=10)
        # then no firing happened and the marking is untouched
        assert len(journal.firings) == 0
        assert list(final.get("loop", [])) == [_tok()]

    def test_p3_policy_receives_enabled_in_declaration_order_not_sorted(self):
        """P3 — the policy receives ``enabledTransitions`` in net declaration
        order, not sorted.

        Invariant: the engine does not re-sort the enabled list before
        handing it to the policy. Bite (reversion-verified): in
        ``engine.py`` ``run``, reverting
        ``FiringPolicyInput(marking=current, enabledTransitions=enabled)``
        to ``enabledTransitions=sorted(enabled)`` makes the policy receive
        ``["alpha", "zeta"]``; the ``_P3_seen[0] == ["zeta", "alpha"]``
        assertion fails (confirmed: ``AssertionError`` — captured list is
        the sorted order, not the declaration order)."""
        # given a net declaring [zeta, alpha] (reverse-alphabetic) and a
        # capture_order policy that records the list it receives
        reg = _reg(("zeta", _zeta), ("alpha", _alpha))
        reg.register_firing_policy("capture_order", _capture_order)
        engine = _engine(reg, policy="capture_order")
        _P3_seen.clear()
        # when running one step
        engine.run(
            _ORDER_NET,
            _marking(in_z=[_tok()], in_a=[_tok()]),
            max_steps=1,
        )
        # then the policy saw the enabled list in declaration order
        assert _P3_seen[0] == ["zeta", "alpha"]

    def test_p4_policy_receives_current_marking_not_initial(self):
        """P4 — the policy receives the CURRENT marking (post previous fire),
        not the initial marking passed to ``run``.

        Invariant: each step's ``FiringPolicyInput.marking`` is the
        evolving ``current`` marking, so a policy gating on a place
        populated by a prior step sees the update. Bite (reversion-
        verified): in ``engine.py`` ``run``, reverting
        ``FiringPolicyInput(marking=current, enabledTransitions=enabled)``
        to ``marking=marking`` (re-pass the INITIAL marking each step)
        makes the policy see p empty at step 1, return None, and stop
        before t_drain fires; the ``done``-has-one-tick assertion fails
        (confirmed: ``AssertionError`` — ``done`` empty, the drain never
        fired)."""
        # given src -> p -> done with t_drain gated on a populated p
        reg = _reg(("t_fill", _fill), ("t_drain", _drain))
        reg.register_firing_policy("gate_on_p", _gate_on_p)
        engine = _engine(reg, policy="gate_on_p")
        # when running
        final = engine.run(_P4_NET, _marking(src=[_tok()]), max_steps=10)
        # then t_drain fired at step 1 (policy saw p populated in current)
        assert list(final.get("done", [])) == [_tok()]

    def test_p5_default_first_found_is_deterministic_and_replayable(self):
        """P5 — the default first-found policy is deterministic and replayable
        at the selection level (D5).

        Invariant: two runs under the DEFAULT policy over the same net
        produce the same declaration-order firing sequence, record for
        record (excluding timestamps). Bite (reversion-verified): in
        ``registry.py`` ``_first_found``, reverting
        ``return enabled[0] if enabled else None`` to
        ``return enabled[-1] if enabled else None`` makes step 0 pick
        ``alpha`` and step 1 pick ``zeta``; the
        ``[r["transition"] for r in j1.firings] == ["zeta", "alpha"]``
        assertion fails (confirmed: ``AssertionError`` — sequence is
        ``["alpha", "zeta"]``). The run1==run2 equality still holds (both
        use the reverted policy); the bite is the sequence-order assertion."""
        # given two fresh registries with only the transition handlers
        reg1 = _reg(("zeta", _zeta), ("alpha", _alpha))
        reg2 = _reg(("zeta", _zeta), ("alpha", _alpha))
        j1 = _CapturingJournal()
        j2 = _CapturingJournal()
        engine1 = Engine(reg1, journal=j1)
        engine2 = Engine(reg2, journal=j2)
        marking = _marking(in_z=[_tok()], in_a=[_tok()])
        # when running both under the default first-found policy
        engine1.run(_ORDER_NET, marking, max_steps=10)
        engine2.run(_ORDER_NET, marking, max_steps=10)
        # then (a) run1 sequence is declaration order
        seq1 = [r["transition"] for r in j1.firings]
        assert seq1 == ["zeta", "alpha"]
        # (b) run2 sequence matches
        seq2 = [r["transition"] for r in j2.firings]
        assert seq2 == ["zeta", "alpha"]
        # (c) the two journals are equal record-for-record (excluding timestamps)
        assert _records_without_timestamps(j1) == _records_without_timestamps(j2)


class TestFiringPolicyConfigValidation:
    """V1: an unresolvable firing-policy ref is a configuration error raised
    at ``Engine.__init__``, not a ``HandlerNotFound`` crashing out of ``run``."""

    def test_v1_unresolvable_policy_raises_at_engine_init(self):
        """V1 — an unresolvable firing-policy ref raises ``HandlerNotFound`` at
        ``Engine.__init__``, not out of ``run``; a resolvable policy still
        constructs.

        Invariant: the firing-policy ref is engine config (not
        net-referenced), known at construction with no transition context to
        fail within; an unresolvable policy is a configuration error raised
        at ``__init__``. Bite (reversion-verified): in ``engine.py``
        ``__init__``, deleting the ``registry.resolve_firing_policy(policy)``
        validation line makes ``Engine(HandlerRegistry(),
        policy="not-registered")`` construct silently; the
        ``pytest.raises(HandlerNotFound)`` assertion fails (confirmed:
        ``Failed: DID NOT RAISE <class 'velocitron.registry.HandlerNotFound'>``
        — the engine constructs with an unresolvable policy, deferring the
        crash to ``run``). The companion assertions (default + registered
        custom policy construct without raising) do NOT bite under that
        reversion — they confirm the validation is scoped to misses only, not
        over-eager; the biting assertion is the ``pytest.raises`` above."""
        # given / when / then an unregistered policy raises at construction
        with pytest.raises(HandlerNotFound):
            _bare_engine(policy="not-registered")
        # and a resolvable policy still constructs (validation scoped to misses)
        _bare_engine()
        reg = HandlerRegistry()
        reg.register_firing_policy("custom", _never_fire)
        Engine(reg, policy="custom")


# ── P6: the built-in priority policy (ADR 0014) ───────────────────────────
# Three transitions all enabled on independent inputs: ta declares no
# priority (=> 0), tb and tc both declare 5, and the high-priority pair is
# declared AFTER ta so declaration order actively works against them — the
# priority, not the list position, must win. Fixed produce-template data
# (`"data": {}`) lets one pass-through handler serve all three.

_P6_NET = _net(
    {
        "name": "p6-priority",
        "places": [
            {"name": "in_a", "accepts": ["tick"]},
            {"name": "in_b", "accepts": ["tick"]},
            {"name": "in_c", "accepts": ["tick"]},
            {"name": "out_a", "accepts": ["tick"]},
            {"name": "out_b", "accepts": ["tick"]},
            {"name": "out_c", "accepts": ["tick"]},
        ],
        "transitions": [
            {"name": "ta", "handler": "emit"},
            {"name": "tb", "handler": "emit", "priority": 5},
            {"name": "tc", "handler": "emit", "priority": 5},
        ],
        "arcs": [
            {
                "from": {"place": "in_a"},
                "to": {"transition": "ta"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "ta"},
                "to": {"place": "out_a"},
                "produce": {"type": "tick", "destination": "out_a", "data": {}},
            },
            {
                "from": {"place": "in_b"},
                "to": {"transition": "tb"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "tb"},
                "to": {"place": "out_b"},
                "produce": {"type": "tick", "destination": "out_b", "data": {}},
            },
            {
                "from": {"place": "in_c"},
                "to": {"transition": "tc"},
                "consume": {"type": "tick"},
            },
            {
                "from": {"transition": "tc"},
                "to": {"place": "out_c"},
                "produce": {"type": "tick", "destination": "out_c", "data": {}},
            },
        ],
    }
)


def _emit(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Pass-through: the fixed produce-template data supplies the outputs."""
    return {"status": "completed", "outputTokens": {}, "error": None, "metadata": {}}


def _p6_marking() -> Marking:
    return _marking(in_a=[_tok()], in_b=[_tok()], in_c=[_tok()])


def _p6_registry() -> HandlerRegistry:
    return _reg(("emit", _emit))


class TestPriorityFiringPolicy:
    """P6: the built-in ``priority`` policy — registered out of the box,
    highest declared ``Transition.priority`` wins, ties and the all-default
    case fall back to declaration order, and the engine threads
    ``priorities`` to every policy (ADR 0014)."""

    def test_p6_priority_policy_registered_on_fresh_registry(self):
        """P6 — ``priority`` resolves on a fresh registry and constructs an
        Engine, exactly like the default (ADR 0014: built-in, opt-in)."""
        # given a fresh registry
        reg = HandlerRegistry()
        # when / then the built-in name resolves and the engine constructs
        reg.resolve_firing_policy("priority")
        Engine(reg, policy="priority")

    def test_p6_highest_declared_priority_outranks_declaration_order(self):
        """P6 — with ta (priority absent => 0) declared FIRST and tb
        (priority 5) enabled together, ``policy="priority"`` fires tb on the
        first step; the default first-found fires ta over the identical
        marking. The pair proves the priority, not list position, decides."""
        # given both engines over the same net and marking
        prio_engine = _engine(_p6_registry(), policy="priority")
        default_engine = _engine(_p6_registry())
        marking = _p6_marking()
        # when each runs a single step
        prio_after = prio_engine.run(_P6_NET, marking, max_steps=1)
        default_after = default_engine.run(_P6_NET, marking, max_steps=1)
        # then the priority policy fired tb (out_b) and first-found fired ta
        assert list(prio_after.get("out_b", [])) and not prio_after.get("out_a")
        assert list(default_after.get("out_a", [])) and not default_after.get("out_b")

    def test_p6_ties_fall_back_to_declaration_order(self):
        """P6 — tb and tc both declare priority 5; the tie resolves to tb,
        the first maximal entry in declaration order (deterministic,
        replayable — the first-found fallback inside the policy)."""
        # given the priority engine and all three transitions enabled
        engine = _engine(_p6_registry(), policy="priority")
        # when one step fires
        after = engine.run(_P6_NET, _p6_marking(), max_steps=1)
        # then the earlier-declared of the tied pair fired
        assert list(after.get("out_b", [])) and not after.get("out_c")

    def test_p6_run_to_quiescence_fires_by_descending_priority(self):
        """P6 — a full run consumes all three inputs in priority order:
        tb, tc (the tied 5s in declaration order), then ta (default 0)."""
        # given a capturing journal on the priority engine
        journal = _CapturingJournal()
        engine = _engine(_p6_registry(), journal=journal, policy="priority")
        # when the run reaches quiescence
        after = engine.run(_P6_NET, _p6_marking(), max_steps=10)
        # then every output landed and the firing order was tb, tc, ta
        assert [r["transition"] for r in journal.firings] == ["tb", "tc", "ta"]
        assert all(list(after.get(p, [])) for p in ("out_a", "out_b", "out_c"))

    def test_p6_policy_input_carries_priorities_for_enabled(self):
        """P6 — ``FiringPolicyInput.priorities`` is keyed by exactly the
        ``enabledTransitions`` entries, absent declarations mapped to 0, so
        any policy (custom included) sees the declared priorities without
        net access (ADR 0014)."""
        # given a custom policy that captures its input then stops the run
        captured: list[FiringPolicyInput] = []

        def capture(inp: FiringPolicyInput) -> str | None:
            captured.append(inp)
            return None

        reg = _p6_registry()
        reg.register_firing_policy("capture", capture)
        engine = _engine(reg, policy="capture")
        # when the run consults the policy once
        engine.run(_P6_NET, _p6_marking(), max_steps=5)
        # then priorities mirror enabledTransitions with declared values
        (inp,) = captured
        assert inp["priorities"] == {"ta": 0, "tb": 5, "tc": 5}
        assert set(inp["priorities"]) == set(inp["enabledTransitions"])
