"""Tests for the firing engine, journal hooks, and firing semantics.

The suite exercises enablement detection, firing
(consume/deposit/passthrough/atomic rollback), the selection loop, the journal
hook contract, ``JsonlJournal`` round-trip, replay determinism, and
deposit-violation handling under all three configurations. It also covers the
``weight`` field on consume patterns (D7).

The worked net is shared with parser coverage and includes ordinary and
inhibit consume arcs, produce arcs, handler references, and a gating pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter

# Handler contract types used by the firing fixtures.
from velocitron.contract import (
    FiringPolicyInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)

# Engine and journal surfaces exercised by this suite.
from velocitron.engine import Engine
from velocitron.journal import (
    FiringRecord,
    FiringStatus,
    InjectionRecord,
    Journal,
    JsonlJournal,
)
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import (
    ConsumePattern,
    Marking,
    Net,
    Token,
)

# ── Shared worked-net fixture data ──────────────────────────────────────

# The fixture covers regular places, consume arcs (default + inhibit),
# produce arcs, handler refs, and a gating pattern.

PLANNING_SLICE: dict[str, Any] = {
    "name": "planning-slice",
    "places": [
        {"name": "backlog", "accepts": ["feature"]},
        {"name": "plan_needed", "accepts": ["feature"]},
        {"name": "plan_drafted", "accepts": ["feature"]},
        {"name": "qa_check", "accepts": ["feature"]},
        {"name": "done", "accepts": ["feature"]},
        {"name": "git_tree_diff", "accepts": ["git_status"]},
    ],
    "transitions": [
        {"name": "start_feature", "handler": "start_feature"},
        {"name": "write_plan", "handler": "write_plan"},
        {"name": "commit_plan", "handler": "commit_plan"},
    ],
    "arcs": [
        # start_feature: consume backlog, inhibit done, produce plan_needed
        {
            "from": {"place": "backlog"},
            "to": {"transition": "start_feature"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "done"},
            "to": {"transition": "start_feature"},
            "consume": {"type": "feature", "mode": "inhibit"},
        },
        {
            "from": {"transition": "start_feature"},
            "to": {"place": "plan_needed"},
            "produce": {"type": "feature", "destination": "plan_needed"},
        },
        # write_plan: consume plan_needed, inhibit git_tree_diff,
        #             produce plan_drafted + git_tree_diff (dirty)
        {
            "from": {"place": "plan_needed"},
            "to": {"transition": "write_plan"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "git_tree_diff"},
            "to": {"transition": "write_plan"},
            "consume": {"type": "git_status", "mode": "inhibit"},
        },
        {
            "from": {"transition": "write_plan"},
            "to": {"place": "plan_drafted"},
            "produce": {"type": "feature", "destination": "plan_drafted"},
        },
        {
            "from": {"transition": "write_plan"},
            "to": {"place": "git_tree_diff"},
            "produce": {"type": "git_status", "destination": "git_tree_diff"},
        },
        # commit_plan: consume plan_drafted + git_tree_diff (dirty),
        #              produce qa_check (clean)
        {
            "from": {"place": "plan_drafted"},
            "to": {"transition": "commit_plan"},
            "consume": {"type": "feature"},
        },
        {
            "from": {"place": "git_tree_diff"},
            "to": {"transition": "commit_plan"},
            "consume": {"type": "git_status"},
        },
        {
            "from": {"transition": "commit_plan"},
            "to": {"place": "qa_check"},
            "produce": {"type": "feature", "destination": "qa_check"},
        },
    ],
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _feature_token(fid: str = "f1") -> Token:
    """A minimal feature token."""
    return Token(type="feature", data={"id": fid})


def _git_token() -> Token:
    """A git_status token (dirty tree)."""
    return Token(type="git_status", data={})


def _registry_with_planning_handlers() -> HandlerRegistry:
    """A registry with handlers for the planning slice transitions."""
    reg = HandlerRegistry()

    def start_feature(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        # Pass through: produce a feature token in plan_needed.
        return {
            "status": "completed",
            "outputTokens": {"plan_needed": inp["inputTokens"].get("backlog", [])},
            "error": None,
            "metadata": {},
        }

    def write_plan(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        # Produce a feature in plan_drafted + git_status in git_tree_diff.
        feat = inp["inputTokens"].get("plan_needed", [])
        return {
            "status": "completed",
            "outputTokens": {
                "plan_drafted": feat,
                "git_tree_diff": [_git_token()],
            },
            "error": None,
            "metadata": {},
        }

    def commit_plan(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        # Consume-only: no output tokens (the git diff is cleaned by consuming it).
        return {
            "status": "completed",
            "outputTokens": {"qa_check": inp["inputTokens"].get("plan_drafted", [])},
            "error": None,
            "metadata": {},
        }

    reg.register_transition("start_feature", start_feature)
    reg.register_transition("write_plan", write_plan)
    reg.register_transition("commit_plan", commit_plan)
    return reg


def _initial_marking() -> Marking:
    """A marking with one feature token in backlog, nothing elsewhere."""
    return Marking({"backlog": [_feature_token()]})


class _CapturingJournal:
    """An in-memory Journal implementation for testing the hook contract.

    Captures every record the engine emits; assigns no ``sequence`` (that
    is the journal implementation's concern, per D4).
    """

    def __init__(self) -> None:
        self.firings: list[FiringRecord] = []
        self.violations: list[FiringRecord] = []
        self.injections: list[InjectionRecord] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.firings.append(record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self.violations.append(record)

    def record_injection(self, record: InjectionRecord) -> None:
        self.injections.append(record)


# ── ConsumePattern weight field (D7: additive, narrow) ──────────────────


class TestConsumeWeight:
    """The optional ``weight`` field on the consume pattern (D7)."""

    def test_weight_defaults_to_one(self):
        """``ConsumePattern`` has ``weight`` defaulting to ``1`` when absent."""
        # given: the ConsumePattern schema exposes an optional weight field
        # when: constructing a pattern with no explicit weight
        cp = ConsumePattern(type="feature", predicate=None, mode="consume")
        # then: weight defaults to 1
        assert cp.weight == 1

    def test_weight_explicit_on_consume(self):
        # given: the ConsumePattern schema accepts an explicit weight
        # when: constructing a consume pattern with weight=2
        cp = ConsumePattern(type="feature", predicate=None, mode="consume", weight=2)
        # then: the weight is 2
        assert cp.weight == 2

    def test_weight_parsed_from_json(self):
        """A net with ``weight: 2`` on a consume arc parses to weight=2."""
        # given: a net dict with a weight=2 consume arc
        net_dict = {
            "name": "weight-net",
            "places": [
                {"name": "src", "accepts": ["task"]},
                {"name": "dst", "accepts": ["task"]},
            ],
            "transitions": [{"name": "move", "handler": "move"}],
            "arcs": [
                {
                    "from": {"place": "src"},
                    "to": {"transition": "move"},
                    "consume": {"type": "task", "weight": 2},
                },
                {
                    "from": {"transition": "move"},
                    "to": {"place": "dst"},
                    "produce": {"type": "task", "destination": "dst"},
                },
            ],
        }
        # when: parsing the net dict
        net = parse_net(net_dict)
        consume_arc = next(
            a for a in net.arcs if a.consume is not None and a.from_place == "src"
        )
        # then: the parsed consume arc carries weight 2
        assert consume_arc.consume is not None
        assert consume_arc.consume.weight == 2

    def test_weight_rejected_on_inhibit(self):
        """``weight`` on an inhibit arc is a parser error (validated)."""
        # given: a net dict placing weight on an inhibit arc
        net_dict = {
            "name": "inhibit-weight-net",
            "places": [
                {"name": "gate", "accepts": ["task"]},
                {"name": "dst", "accepts": ["task"]},
            ],
            "transitions": [{"name": "t", "handler": "t"}],
            "arcs": [
                {
                    "from": {"place": "gate"},
                    "to": {"transition": "t"},
                    "consume": {"type": "task", "mode": "inhibit", "weight": 2},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "dst"},
                    "produce": {"type": "task", "destination": "dst"},
                },
            ],
        }
        # when: parsing the net dict
        # then: a validation error is raised
        with pytest.raises(Exception):
            parse_net(net_dict)


# ── Enablement ──────────────────────────────────────────────────────────


class TestEnablement:
    """``enabled_transitions`` — all input arcs satisfiable AND guard true."""

    def test_consume_arc_satisfied(self):
        """A transition with a satisfiable consume arc is enabled."""
        # given: a parsed planning slice, a planning-handler registry, and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: querying enabled transitions
        enabled = engine.enabled_transitions(net, marking)
        # then: start_feature is enabled
        # start_feature: backlog has a feature, done is empty (inhibit satisfied).
        assert "start_feature" in enabled

    def test_consume_arc_not_satisfied(self):
        """A transition whose consume arc is unsatisfied is not enabled."""
        # given: a parsed planning slice with an engine and an empty marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # Empty backlog → start_feature's consume arc is not satisfied.
        marking: Marking = Marking({})
        # when: querying enabled transitions
        enabled = engine.enabled_transitions(net, marking)
        # then: start_feature is not enabled
        assert "start_feature" not in enabled

    def test_inhibit_arc_blocks_when_token_present(self):
        """An inhibit arc is a zero-test: a matching token blocks enablement."""
        # given: a parsed planning slice with an engine
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # and: a marking with a token in done, violating start_feature's inhibit arc
        # Put a token in done → start_feature's inhibit arc on done is violated.
        marking = Marking(
            {
                "backlog": [_feature_token()],
                "done": [_feature_token("done-1")],
            }
        )
        # when: querying enabled transitions
        enabled = engine.enabled_transitions(net, marking)
        # then: start_feature is not enabled
        assert "start_feature" not in enabled

    def test_inhibit_arc_satisfied_when_empty(self):
        """An inhibit arc is satisfied when the source place has no matching token."""
        # given: a parsed planning slice with an engine and a backlog-only marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = Marking({"backlog": [_feature_token()]})
        # when: querying enabled transitions
        enabled = engine.enabled_transitions(net, marking)
        # then: start_feature is enabled
        assert "start_feature" in enabled

    def test_weight_gt_one_requires_multiple_tokens(self):
        """``weight: 2`` requires at least 2 matching tokens in the source place."""
        # given: a net whose consume arc has weight=2, with an engine
        net_dict = {
            "name": "weight-enable-net",
            "places": [
                {"name": "storage", "accepts": ["abstract"]},
                {"name": "distributed", "accepts": ["abstract"]},
            ],
            "transitions": [{"name": "distribute", "handler": "distribute"}],
            "arcs": [
                {
                    "from": {"place": "storage"},
                    "to": {"transition": "distribute"},
                    "consume": {"type": "abstract", "weight": 2},
                },
                {
                    "from": {"transition": "distribute"},
                    "to": {"place": "distributed"},
                    "produce": {"type": "abstract", "destination": "distributed"},
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        engine = Engine(reg)

        # when: the source place holds a single token
        # Only one token → not enough for weight=2.
        one_token = Marking({"storage": [Token(type="abstract", data={"n": 1})]})
        # then: distribute is not enabled
        assert "distribute" not in engine.enabled_transitions(net, one_token)

        # when: the source place holds two tokens
        # Two tokens → enabled.
        two_tokens = Marking(
            {
                "storage": [
                    Token(type="abstract", data={"n": 1}),
                    Token(type="abstract", data={"n": 2}),
                ]
            }
        )
        # then: distribute is enabled
        assert "distribute" in engine.enabled_transitions(net, two_tokens)

    def test_guard_true_enables(self):
        """A guard returning True does not block enablement."""
        # given: a net with a guarded transition and an always-true guard handler
        net_dict = {
            "name": "guard-net",
            "places": [
                {"name": "in_place", "accepts": ["task"]},
                {"name": "out_place", "accepts": ["task"]},
            ],
            "transitions": [
                {"name": "guarded_t", "handler": "guarded_t", "guard": "always_true"},
            ],
            "arcs": [
                {
                    "from": {"place": "in_place"},
                    "to": {"transition": "guarded_t"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "guarded_t"},
                    "to": {"place": "out_place"},
                    "produce": {"type": "task", "destination": "out_place"},
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        reg.register_guard("always_true", lambda inp: True)
        reg.register_transition(
            "guarded_t",
            lambda inp: {
                "status": "completed",
                "outputTokens": {"out_place": inp["inputTokens"].get("in_place", [])},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)
        marking = Marking({"in_place": [Token(type="task", data={})]})
        # when: querying enabled transitions
        # then: guarded_t is enabled
        assert "guarded_t" in engine.enabled_transitions(net, marking)

    def test_guard_false_disables(self):
        """A guard returning False disables the transition."""
        # given: a net with a guarded transition and an always-false guard handler
        net_dict = {
            "name": "guard-false-net",
            "places": [
                {"name": "in_place", "accepts": ["task"]},
                {"name": "out_place", "accepts": ["task"]},
            ],
            "transitions": [
                {"name": "guarded_t", "handler": "guarded_t", "guard": "always_false"},
            ],
            "arcs": [
                {
                    "from": {"place": "in_place"},
                    "to": {"transition": "guarded_t"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "guarded_t"},
                    "to": {"place": "out_place"},
                    "produce": {"type": "task", "destination": "out_place"},
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        reg.register_guard("always_false", lambda inp: False)
        reg.register_transition(
            "guarded_t",
            lambda inp: {
                "status": "completed",
                "outputTokens": {"out_place": inp["inputTokens"].get("in_place", [])},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)
        marking = Marking({"in_place": [Token(type="task", data={})]})
        # when: querying enabled transitions
        # then: guarded_t is not enabled
        assert "guarded_t" not in engine.enabled_transitions(net, marking)

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_predicate_filters_tokens(self, adapter: CelAdapter) -> None:
        """A CEL predicate on a consume arc filters which tokens match."""
        # given: a net with a CEL predicate consume arc and a process_high handler
        net_dict = {
            "name": "cel-predicate-net",
            "places": [
                {"name": "inbox", "accepts": ["msg"]},
                {"name": "processed", "accepts": ["msg"]},
            ],
            "transitions": [{"name": "process_high", "handler": "process_high"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "process_high"},
                    "consume": {"type": "msg", "predicate": {"cel": "priority > 5"}},
                },
                {
                    "from": {"transition": "process_high"},
                    "to": {"place": "processed"},
                    "produce": {"type": "msg", "destination": "processed"},
                },
            ],
        }
        net = parse_net(net_dict, cel_adapter=adapter)
        reg = HandlerRegistry()
        reg.register_transition(
            "process_high",
            lambda inp: {
                "status": "completed",
                "outputTokens": {"processed": inp["inputTokens"].get("inbox", [])},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg, cel_adapter=adapter)

        # when: the inbox holds a low-priority token
        # Low-priority token → predicate fails → not enabled.
        low = Marking({"inbox": [Token(type="msg", data={"priority": 3})]})
        # then: process_high is not enabled
        assert "process_high" not in engine.enabled_transitions(net, low)

        # when: the inbox holds a high-priority token
        # High-priority token → predicate passes → enabled.
        high = Marking({"inbox": [Token(type="msg", data={"priority": 9})]})
        # then: process_high is enabled
        assert "process_high" in engine.enabled_transitions(net, high)

    def test_named_predicate_handler_filters_tokens(self):
        """A named predicate handler on a consume arc filters tokens."""
        # given: a net with a named-predicate consume arc and a process_valid handler
        net_dict = {
            "name": "named-pred-net",
            "places": [
                {"name": "inbox", "accepts": ["msg"]},
                {"name": "processed", "accepts": ["msg"]},
            ],
            "transitions": [{"name": "process_valid", "handler": "process_valid"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "process_valid"},
                    "consume": {"type": "msg", "predicate": {"handler": "is_valid"}},
                },
                {
                    "from": {"transition": "process_valid"},
                    "to": {"place": "processed"},
                    "produce": {"type": "msg", "destination": "processed"},
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        reg.register_predicate(
            "is_valid", lambda inp: inp["token"].data.get("ok") is True
        )
        reg.register_transition(
            "process_valid",
            lambda inp: {
                "status": "completed",
                "outputTokens": {"processed": inp["inputTokens"].get("inbox", [])},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)

        # when: the inbox holds an invalid token
        invalid = Marking({"inbox": [Token(type="msg", data={"ok": False})]})
        # then: process_valid is not enabled
        assert "process_valid" not in engine.enabled_transitions(net, invalid)

        # when: the inbox holds a valid token
        valid = Marking({"inbox": [Token(type="msg", data={"ok": True})]})
        # then: process_valid is enabled
        assert "process_valid" in engine.enabled_transitions(net, valid)


# ── Enablement guard robustness (discovered work) ────────────────────────


class TestEnablementGuardRobustness:
    """Two spec gaps the judge surfaced after the firing-semantics feature
    landed (BACKLOG.md):

    1. Guard-throw handling — a guard may be impure (ADR 0002), so it may
       raise. The engine must degrade that to *not-enabled*, never crash —
       symmetric with predicate-handler exceptions (D6) and an unresolved
       guard ref (``resolve_guard`` ``HandlerNotFound`` ⇒ not-enabled).
    2. Attempt mismatch — ``handler-contract.md`` sanctions ``attempt`` to
       tell a fresh fire from a net-modeled retry, so enablement probing
       must honor the same ``attempt`` the fire will use. Today
       ``enabled_transitions`` probes with ``attempt=0`` while ``run`` fires
       with ``attempt=steps``, so an attempt-sensitive guard flips between
       probe and fire and ``run`` spins failing.
    """

    @staticmethod
    def _loop_net(guard: str | None = None) -> Net:
        """A self-loop net: one transition consuming a ``tick`` from ``loop``
        and producing one back. Structurally always enabled, so the guard is
        the only thing gating it — isolating guard behavior from arc
        satisfiability."""
        transition: dict[str, Any] = {"name": "loop_t", "handler": "loop_t"}
        if guard is not None:
            transition["guard"] = guard
        return parse_net(
            {
                "name": "loop-net",
                "places": [{"name": "loop", "accepts": ["tick"]}],
                "transitions": [transition],
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

    @staticmethod
    def _pass_through(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        """Echo the consumed ``tick`` back to ``loop`` — keeps the loop enabled."""
        return {
            "status": "completed",
            "outputTokens": {"loop": inp["inputTokens"].get("loop", [])},
            "error": None,
            "metadata": {},
        }

    @staticmethod
    def _raising_guard(inp: Any) -> bool:
        raise RuntimeError("guard blew up")

    def test_throwing_guard_degrades_to_not_enabled(self):
        """An impure guard that raises is treated as not-enabled, not a crash."""
        # given: a self-loop net with a guard that raises
        net = self._loop_net(guard="flaky")
        reg = HandlerRegistry()
        reg.register_guard("flaky", self._raising_guard)
        reg.register_transition("loop_t", self._pass_through)
        engine = Engine(reg)
        marking = Marking({"loop": [Token(type="tick", data={})]})
        # when: querying enabled transitions
        # then: the transition is not enabled and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []

    def test_fire_treats_throwing_guard_as_not_enabled(self):
        """A direct ``fire`` against a throwing guard returns a failed record
        with the marking unchanged, not a crash."""
        # given: a self-loop net with a guard that raises
        net = self._loop_net(guard="flaky")
        reg = HandlerRegistry()
        reg.register_guard("flaky", self._raising_guard)
        reg.register_transition("loop_t", self._pass_through)
        engine = Engine(reg)
        marking = Marking({"loop": [Token(type="tick", data={})]})
        # when: firing the transition directly
        new_marking, record = engine.fire(net, marking, "loop_t", attempt=0)
        # then: the fire fails (not enabled) and the marking is unchanged
        assert record["status"] == "failed"
        assert new_marking == marking

    def test_run_honors_attempt_sensitive_guard(self):
        """``run`` probes enablement with the same ``attempt`` it fires with.

        A guard that enables only on a fresh fire (``attempt == 0``) must fire
        exactly once, then be not-enabled at ``attempt == 1`` and stop — not
        spin emitting ``NotEnabled`` failures for ``max_steps`` because the
        probe used ``attempt == 0`` while the fire used ``attempt == steps``.
        """
        # given: a self-loop net guarded by a fresh-fire-only guard
        net = self._loop_net(guard="fresh_only")
        reg = HandlerRegistry()
        reg.register_guard(
            "fresh_only", lambda inp: inp["firingContext"]["attempt"] == 0
        )
        reg.register_transition("loop_t", self._pass_through)
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal)
        marking = Marking({"loop": [Token(type="tick", data={})]})
        # when: running the loop
        engine.run(net, marking, max_steps=5)
        # then: the transition fires once (the fresh fire), then is not
        # enabled at attempt==1 and the run stops — exactly one completed record.
        assert len(journal.firings) == 1
        assert journal.firings[0]["status"] == "completed"


# ── Binding selection ──────────────────────────────────────────────────


class TestBindingSelection:
    """``select_binding`` — deterministic first-enabled binding (D2)."""

    def test_returns_binding_when_enabled(self):
        # given: a parsed planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: selecting a binding for start_feature
        binding = engine.select_binding(net, "start_feature", marking)
        # then: the binding maps backlog to the single feature token
        # The binding is a dict keyed by source place, with token lists.
        assert binding is not None
        assert "backlog" in binding
        assert len(binding["backlog"]) == 1
        assert binding["backlog"][0].data["id"] == "f1"

    def test_returns_none_when_not_enabled(self):
        # given: a parsed planning slice with an engine
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # when: selecting a binding for start_feature against an empty marking
        # Empty marking → start_feature not enabled → no binding.
        binding = engine.select_binding(net, "start_feature", Marking())
        # then: no binding is returned
        assert binding is None

    def test_weight_binding_selects_first_n_tokens(self):
        """``weight: 2`` selects the first 2 matching tokens in insertion order."""
        # given: a net with a weight=2 consume arc, an engine, and three tokens in storage
        net_dict = {
            "name": "weight-binding-net",
            "places": [
                {"name": "storage", "accepts": ["abstract"]},
                {"name": "distributed", "accepts": ["abstract"]},
            ],
            "transitions": [{"name": "distribute", "handler": "distribute"}],
            "arcs": [
                {
                    "from": {"place": "storage"},
                    "to": {"transition": "distribute"},
                    "consume": {"type": "abstract", "weight": 2},
                },
                {
                    "from": {"transition": "distribute"},
                    "to": {"place": "distributed"},
                    "produce": {"type": "abstract", "destination": "distributed"},
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        engine = Engine(reg)
        marking = Marking(
            {
                "storage": [
                    Token(type="abstract", data={"n": 1}),
                    Token(type="abstract", data={"n": 2}),
                    Token(type="abstract", data={"n": 3}),
                ]
            }
        )
        # when: selecting a binding for distribute
        binding = engine.select_binding(net, "distribute", marking)
        # then: the first two tokens in insertion order are bound
        assert binding is not None
        # First 2 tokens in insertion order.
        assert [t.data["n"] for t in binding["storage"]] == [1, 2]

    def _two_arcs_one_place_net(self) -> Net:
        """A net whose transition has TWO consume arcs from the SAME source
        place (each ``weight: 1``) plus a produce arc — the D1 shared-place
        scenario. ``select_binding`` must treat the combined consumed multiset
        as a single sub-multiset of the place, never binding one token twice.
        """
        net_dict = {
            "name": "shared-place-net",
            "places": [
                {"name": "src", "accepts": ["task"]},
                {"name": "dst", "accepts": ["task"]},
            ],
            "transitions": [{"name": "merge", "handler": "merge"}],
            "arcs": [
                # Two consume arcs from the same place to the same transition.
                {
                    "from": {"place": "src"},
                    "to": {"transition": "merge"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"place": "src"},
                    "to": {"transition": "merge"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "merge"},
                    "to": {"place": "dst"},
                    "produce": {"type": "task", "destination": "dst"},
                },
            ],
        }
        return parse_net(net_dict)

    def test_shared_place_single_token_not_enabled(self):
        """Two consume arcs share a place holding ONE token: the combined
        binding would need two tokens, so the transition is NOT enabled.
        Without sub-multiset validation the same token would be bound twice
        and ``fire`` would silently under-consume (marking corruption)."""
        # given: a shared-place net (two consume arcs from one place) with a single token
        net = self._two_arcs_one_place_net()
        reg = HandlerRegistry()
        engine = Engine(reg)
        marking = Marking({"src": [Token(type="task", data={"id": "only"})]})
        # when: selecting a binding and querying enablement
        # then: no binding exists and merge is not enabled
        # Not enabled: cannot bind two distinct tokens from one.
        assert engine.select_binding(net, "merge", marking) is None
        assert "merge" not in engine.enabled_transitions(net, marking)

    def test_shared_place_two_tokens_binds_distinct(self):
        """Two consume arcs share a place holding TWO tokens: the binding binds
        both, each once — a valid sub-multiset. Firing consumes both."""
        # given: a shared-place net with two tokens and a merge handler
        net = self._two_arcs_one_place_net()
        reg = HandlerRegistry()
        reg.register_transition(
            "merge",
            lambda inp: {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)
        t1 = Token(type="task", data={"id": "a"})
        t2 = Token(type="task", data={"id": "b"})
        marking = Marking({"src": [t1, t2]})
        # when: selecting a binding for merge
        binding = engine.select_binding(net, "merge", marking)
        # then: both tokens are bound, each exactly once
        assert binding is not None
        # Both tokens bound, each exactly once — no double-binding.
        assert sorted(tok.data["id"] for tok in binding["src"]) == ["a", "b"]
        # when: firing merge
        # Firing consumes both (src emptied), no under-consumption.
        new_marking, record = engine.fire(net, marking, "merge", attempt=0)
        # then: src is emptied and the record is completed
        assert new_marking.get("src", []) == []
        assert record["status"] == "completed"


# ── Firing ──────────────────────────────────────────────────────────────


class TestFiring:
    """``fire`` — consume → invoke → deposit → record (b), atomic rollback (c)."""

    def test_consume_and_deposit(self):
        """Firing consumes from input place and deposits to output place."""
        # given: a parsed planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: firing start_feature
        new_marking, record = engine.fire(net, marking, "start_feature", attempt=0)

        # then: backlog is consumed, plan_needed is deposited, and the record is completed
        # Consumed from backlog.
        assert new_marking.get("backlog", []) == []
        # Deposited to plan_needed.
        assert len(new_marking.get("plan_needed", [])) == 1
        assert new_marking["plan_needed"][0].data["id"] == "f1"
        # Record reflects success.
        assert record["status"] == "completed"
        assert record["error"] is None

    def test_inhibit_arc_consumes_nothing(self):
        """An inhibit arc gates enablement but consumes nothing on fire."""
        # given: a parsed planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: firing start_feature
        new_marking, _ = engine.fire(net, marking, "start_feature", attempt=0)
        # then: the inhibited place (done) stays empty
        # start_feature inhibits done; done was empty and stays empty.
        assert new_marking.get("done", []) == []

    def test_passthrough_produce_template(self):
        """A produce template with literal ``data`` and no handler-supplied token
        emits the template's fixed token (passthrough, net-schema.md Q3)."""
        # given: a passthrough net whose produce template has literal data and a handler returning no tokens
        net_dict = {
            "name": "passthrough-net",
            "places": [
                {"name": "in_place", "accepts": ["task"]},
                {"name": "out_place", "accepts": ["task"]},
            ],
            "transitions": [{"name": "passthrough_t", "handler": "passthrough_t"}],
            "arcs": [
                {
                    "from": {"place": "in_place"},
                    "to": {"transition": "passthrough_t"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "passthrough_t"},
                    "to": {"place": "out_place"},
                    "produce": {
                        "type": "task",
                        "destination": "out_place",
                        "data": {"fixed": True},
                    },
                },
            ],
        }
        net = parse_net(net_dict)
        reg = HandlerRegistry()
        # Handler returns no tokens → engine uses the template's literal data.
        reg.register_transition(
            "passthrough_t",
            lambda inp: {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)
        marking = Marking({"in_place": [Token(type="task", data={"orig": True})]})
        # when: firing passthrough_t
        new_marking, _ = engine.fire(net, marking, "passthrough_t", attempt=0)
        # then: the template's fixed token lands in out_place
        # The fixed token from the template lands in out_place.
        assert len(new_marking.get("out_place", [])) == 1
        assert new_marking["out_place"][0].data == {"fixed": True}

    def test_failed_handler_does_not_consume(self):
        """On ``status: "failed"``, the marking is unchanged (atomic rollback)."""
        # given: a planning slice whose start_feature handler is overridden to fail
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        # Override start_feature to fail.
        reg.register_transition(
            "start_feature",
            lambda inp: {
                "status": "failed",
                "outputTokens": {},
                "error": {"type": "TestError", "message": "intentional failure"},
                "metadata": {},
            },
        )
        engine = Engine(reg)
        marking = _initial_marking()
        original_backlog = list(marking["backlog"])

        # when: firing start_feature
        new_marking, record = engine.fire(net, marking, "start_feature", attempt=0)

        # then: the marking is unchanged and the record reflects failure
        # Marking unchanged — backlog still has its token.
        assert new_marking["backlog"] == original_backlog
        # Record reflects failure.
        assert record["status"] == "failed"
        assert record["error"] is not None
        assert record["error"]["type"] == "TestError"

    def test_net_modeled_retry_after_failure(self):
        """Spec (c): retry is net-modeled. After a failed firing the marking is
        unchanged, so the transition remains enabled and the net drives the next
        attempt — never the handler internally (handler-contract decision 4)."""
        # given: a planning slice with a stateful handler that fails once then succeeds
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        # A stateful handler: fails on the first attempt, succeeds thereafter.
        calls = {"n": 0}

        def flaky_start(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "status": "failed",
                    "outputTokens": {},
                    "error": {"type": "Transient", "message": "flaky"},
                    "metadata": {},
                }
            return {
                "status": "completed",
                "outputTokens": {"plan_needed": inp["inputTokens"].get("backlog", [])},
                "error": None,
                "metadata": {},
            }

        reg.register_transition("start_feature", flaky_start)
        engine = Engine(reg)
        marking = _initial_marking()
        original_backlog = list(marking["backlog"])

        # when: the first attempt fires
        # First attempt fails — marking unchanged (atomic rollback of consume).
        m1, rec1 = engine.fire(net, marking, "start_feature", attempt=0)
        # then: it fails and the marking is unchanged (atomic rollback of consume)
        assert rec1["status"] == "failed"
        assert m1["backlog"] == original_backlog
        assert m1.get("plan_needed", []) == []

        # The net models retry: with the marking unchanged, start_feature is
        # still enabled (backlog holds its token; done is empty so the inhibit
        # arc is satisfied). The engine re-fires — no handler-internal retry.
        # when: checking enablement against the unchanged marking
        # then: start_feature remains enabled (the net drives the retry)
        assert "start_feature" in engine.enabled_transitions(net, m1)

        # when: the second attempt fires
        # Second attempt succeeds — the same token is consumed and deposited.
        m2, rec2 = engine.fire(net, m1, "start_feature", attempt=1)
        # then: it succeeds, consuming and depositing the same token
        assert rec2["status"] == "completed"
        assert m2["backlog"] == []
        assert len(m2["plan_needed"]) == 1

    def test_resolve_miss_is_transition_failure(self):
        """An unregistered handler ref is a transition failure, not a crash."""
        # given: a planning slice with an engine whose registry has no transition handlers
        net = parse_net(PLANNING_SLICE)
        # Registry with no transition handlers → resolve-miss.
        reg = HandlerRegistry()
        engine = Engine(reg)
        marking = _initial_marking()
        original_backlog = list(marking["backlog"])

        # when: firing start_feature
        new_marking, record = engine.fire(net, marking, "start_feature", attempt=0)

        # then: the marking is unchanged and the record reflects failure
        # Marking unchanged (rollback).
        assert new_marking["backlog"] == original_backlog
        # Record reflects failure.
        assert record["status"] == "failed"
        assert record["error"] is not None

    def test_handlerless_transition_fails_atomically_without_registry_resolution(self):
        """A handlerless transition has structure but no firing behavior."""
        # given: an enabled handlerless transition and a same-name registry entry
        net = parse_net(
            {
                "name": "traditional",
                "places": [{"name": "input", "accepts": ["token"]}],
                "transitions": [{"name": "move"}],
                "arcs": [
                    {
                        "from": {"place": "input"},
                        "to": {"transition": "move"},
                        "consume": {"type": "token"},
                    }
                ],
            }
        )
        registry = HandlerRegistry()
        registry.register_transition(
            "move",
            lambda inp: {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(registry)
        marking = Marking({"input": [Token(type="token", data={"id": 1})]})
        # when: explicitly firing the handlerless transition
        new_marking, record = engine.fire(net, marking, "move", attempt=0)
        # then: no same-name fallback runs and the failed attempt is atomic
        assert new_marking is marking
        assert record["status"] == "failed"
        assert record["error"] == {
            "type": "HandlerNotFound",
            "message": "transition 'move' has no handler",
        }

        # when: the same handlerless transition is structurally disabled
        disabled = Marking()
        disabled_marking, disabled_record = engine.fire(
            net, disabled, "move", attempt=1
        )
        # then: existing NotEnabled precedence remains unchanged
        assert disabled_marking is disabled
        assert disabled_record["error"] == {
            "type": "NotEnabled",
            "message": "transition 'move' is not enabled",
        }

    def test_validate_skips_handlerless_transitions(self):
        """Validation resolves declared refs only."""
        # given: one handlerless transition and one explicitly handled transition
        net = parse_net(
            {
                "name": "mixed",
                "places": [],
                "transitions": [
                    {"name": "structural"},
                    {"name": "behavioral", "handler": "behavioral@demo"},
                ],
                "arcs": [],
            }
        )
        registry = HandlerRegistry()
        registry.register_transition(
            "behavioral@demo",
            lambda inp: {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            },
        )
        # when: validating the mixed net
        # then: the absent ref is skipped and the declared ref resolves
        assert Engine(registry).validate(net) is None

    def test_empty_output_tokens(self):
        """A consume-only transition (empty ``outputTokens``) is valid."""
        # given: a planning slice whose commit_plan returns empty output, with a marking where commit_plan is enabled
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        # Override commit_plan to return empty output (consume-only).
        reg.register_transition(
            "commit_plan",
            lambda inp: {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            },
        )
        engine = Engine(reg)
        # Set up a marking where commit_plan is enabled.
        marking = Marking(
            {
                "plan_drafted": [_feature_token()],
                "git_tree_diff": [_git_token()],
            }
        )
        # when: firing commit_plan
        new_marking, record = engine.fire(net, marking, "commit_plan", attempt=0)
        # then: both input places are consumed and the record is completed
        # Consumed from both input places.
        assert new_marking.get("plan_drafted", []) == []
        assert new_marking.get("git_tree_diff", []) == []
        # No output deposited.
        assert record["status"] == "completed"

    def test_firing_record_has_no_sequence(self):
        """The engine-emitted ``FiringRecord`` carries no ``sequence`` (D4)."""
        # given: a planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: firing start_feature
        _, record = engine.fire(net, marking, "start_feature", attempt=0)
        # then: the record carries no sequence
        assert "sequence" not in record

    def test_firing_record_has_firing_id(self):
        """The record carries ``firingId`` (per-attempt logical id, D4)."""
        # given: a planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: firing start_feature
        _, record = engine.fire(net, marking, "start_feature", attempt=0)
        # then: the record carries a non-empty firingId
        assert "firingId" in record
        assert isinstance(record["firingId"], str)
        assert len(record["firingId"]) > 0

    def test_firing_record_has_metadata_and_timestamps(self):
        """The record carries ``metadata`` and ``timestamps``."""
        # given: a planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        marking = _initial_marking()
        # when: firing start_feature
        _, record = engine.fire(net, marking, "start_feature", attempt=0)
        # then: the record carries metadata and timestamps
        assert "metadata" in record
        assert "timestamps" in record


# ── Selection loop (run) ────────────────────────────────────────────────


class TestRunLoop:
    """``run`` — the selection loop (e), default + custom policy."""

    def test_run_with_default_policy_fires_to_completion(self):
        """The default first-found policy drives the planning slice to quiescence."""
        # given: a parsed planning slice with an engine
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # when: running to completion
        final = engine.run(net, _initial_marking(), max_steps=100)
        # then: the feature reaches qa_check and git_tree_diff is clean
        # After start_feature → write_plan → commit_plan, the feature lands
        # in qa_check, git_tree_diff is clean.
        assert len(final.get("qa_check", [])) == 1
        assert final.get("git_tree_diff", []) == []

    def test_run_stops_on_quiescence(self):
        """When no transition is enabled, ``run`` stops (policy returns None)."""
        # given: a parsed planning slice with an engine and an empty marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # when: running with an empty marking
        # Empty marking → nothing enabled → immediate quiescence.
        final = engine.run(net, Marking(), max_steps=10)
        # then: the run stops at quiescence with no tokens
        assert final == {} or all(v == [] for v in final.values())

    def test_run_respects_max_steps(self):
        """``run`` stops after ``max_steps`` even if transitions remain enabled."""
        # given: a parsed planning slice with an engine and an initial marking
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)
        # when: running with max_steps=0
        # max_steps=0 → no firing at all.
        final = engine.run(net, _initial_marking(), max_steps=0)
        # then: no firing occurs and the marking is unchanged
        # Marking unchanged.
        assert len(final.get("backlog", [])) == 1

    def test_custom_firing_policy(self):
        """A custom firing policy selects which transition to fire."""
        # given: a parsed planning slice with a custom last-enabled firing policy
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()

        call_log: list[str] = []

        def custom_policy(inp: FiringPolicyInput) -> str | None:
            call_log.append(f"policy-called-with-{len(inp['enabledTransitions'])}")
            # Always pick the last enabled transition (reverse of first-found).
            if inp["enabledTransitions"]:
                return inp["enabledTransitions"][-1]
            return None

        reg.register_firing_policy("custom-last", custom_policy)
        engine = Engine(reg, policy="custom-last")
        # when: running the net
        # The engine should use our custom policy.
        engine.run(net, _initial_marking(), max_steps=10)
        # then: the custom policy was invoked
        # Policy was called at least once.
        assert len(call_log) > 0


# ── Journal hook contract ───────────────────────────────────────────────


class TestJournalHook:
    """The journal is decoupled from the engine via hooks (D4)."""

    def test_no_journal_engine_still_fires(self):
        """The engine fires correctly with no journal attached (D4)."""
        # given: a parsed planning slice with an engine and no journal
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        engine = Engine(reg)  # journal=None
        # when: running to completion
        final = engine.run(net, _initial_marking(), max_steps=100)
        # then: the feature still reaches qa_check
        # Still works — feature reaches qa_check.
        assert len(final.get("qa_check", [])) == 1

    def test_journal_attached_emits_records(self):
        """With a journal attached, every firing emits a record."""
        # given: a parsed planning slice with an engine and a capturing journal
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal)
        # when: running to completion
        engine.run(net, _initial_marking(), max_steps=100)
        # then: three firings are recorded, all completed
        # Three transitions fire: start_feature, write_plan, commit_plan.
        assert len(journal.firings) == 3
        # All records are completed.
        assert all(r["status"] == "completed" for r in journal.firings)

    def test_journal_records_failed_firing(self):
        """A failed firing emits a record with ``status: "failed"``."""
        # given: a planning slice whose start_feature handler fails, with a capturing journal
        net = parse_net(PLANNING_SLICE)
        reg = _registry_with_planning_handlers()
        reg.register_transition(
            "start_feature",
            lambda inp: {
                "status": "failed",
                "outputTokens": {},
                "error": {"type": "Err", "message": "fail"},
                "metadata": {},
            },
        )
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal)
        # when: firing start_feature
        engine.fire(net, _initial_marking(), "start_feature", attempt=0)
        # then: exactly one failed record is captured
        assert len(journal.firings) == 1
        assert journal.firings[0]["status"] == "failed"

    def test_journal_protocol_is_satisfiable(self):
        """A class implementing ``record_firing`` + ``record_deposit_violation``
        satisfies the ``Journal`` protocol."""
        # given: a class implementing record_firing and record_deposit_violation
        # when: treating an instance as a Journal
        # _CapturingJournal already does this; verify it's recognized as a Journal.
        j: Journal = _CapturingJournal()
        # then: the Journal protocol methods are present
        j.record_firing.__call__  # has the method
        j.record_deposit_violation.__call__  # has the method


# ── JsonlJournal ────────────────────────────────────────────────────────


class TestJsonlJournal:
    """The default journal implementation: timestamped .jsonl, monotonic sequence."""

    def test_jsonl_journal_assigns_sequence(self):
        """``JsonlJournal`` assigns a monotonic 0-based ``sequence`` (D4)."""
        # given: a JsonlJournal
        j = JsonlJournal()
        # when: recording two firings
        # Records have a sequence assigned by the journal, not the engine.
        j.record_firing(_make_record("t1", "completed"))
        j.record_firing(_make_record("t2", "completed"))
        # then: sequences are assigned 0 and 1 by the journal
        lines = j._records  # type: ignore[attr-defined]
        assert lines[0]["sequence"] == 0
        assert lines[1]["sequence"] == 1

    def test_jsonl_journal_writes_file(self, tmp_path: Path):
        """``JsonlJournal`` appends records to a timestamped .jsonl file."""
        # given: a JsonlJournal backed by a tmp_path prefix
        prefix = str(tmp_path / "firings")
        j = JsonlJournal(prefix=prefix)
        # when: recording a completed and a failed firing, then flushing
        j.record_firing(_make_record("t1", "completed"))
        j.record_firing(_make_record("t2", "failed"))
        j.flush()

        # then: a single .jsonl file holds both records with monotonic sequence
        # Find the written .jsonl file.
        files = list(tmp_path.glob("firings-*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text()
        records = [json.loads(ln) for ln in content.strip().splitlines()]
        assert len(records) == 2
        assert records[0]["transition"] == "t1"
        assert records[0]["status"] == "completed"
        assert records[1]["transition"] == "t2"
        assert records[1]["status"] == "failed"
        # Sequence is monotonic.
        # and: sequence is monotonic
        assert records[0]["sequence"] == 0
        assert records[1]["sequence"] == 1

    def test_jsonl_journal_round_trip(self, tmp_path: Path):
        """Round-trip: records written to .jsonl can be read back and compared."""
        # given: a JsonlJournal backed by a tmp_path prefix, recording one firing with input data
        prefix = str(tmp_path / "rt")
        j = JsonlJournal(prefix=prefix)
        j.record_firing(_make_record("t1", "completed", input_data={"in": "a"}))
        # when: flushing and reading the file back
        j.flush()

        # then: the round-tripped record matches
        files = list(tmp_path.glob("rt-*.jsonl"))
        content = files[0].read_text()
        records = [json.loads(ln) for ln in content.strip().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["transition"] == "t1"
        assert r["status"] == "completed"
        assert r["sequence"] == 0


# ── Replay determinism (D5) ─────────────────────────────────────────────


class TestReplayDeterminism:
    """Replay = re-run with identical handlers + journal, compare records (D5)."""

    def test_replay_produces_equal_journal_modulo_timestamps(self):
        """Two runs with identical handlers produce equal journals (excluding
        ``timestamps``, which are metadata-only and non-deterministic)."""
        # given: a parsed planning slice
        net = parse_net(PLANNING_SLICE)

        # and: a first run with identical handlers and a capturing journal
        # Run 1.
        reg1 = _registry_with_planning_handlers()
        j1 = _CapturingJournal()
        engine1 = Engine(reg1, journal=j1)
        engine1.run(net, _initial_marking(), max_steps=100)

        # and: a second run with identical handlers and a capturing journal
        # Run 2 — identical handlers.
        reg2 = _registry_with_planning_handlers()
        j2 = _CapturingJournal()
        engine2 = Engine(reg2, journal=j2)
        engine2.run(net, _initial_marking(), max_steps=100)

        # when: comparing the two journals record-for-record, excluding timestamps
        # Compare record-for-record, excluding timestamps.
        # then: the records are equal
        assert len(j1.firings) == len(j2.firings)
        for r1, r2 in zip(j1.firings, j2.firings):
            r1_cmp = {k: v for k, v in r1.items() if k != "timestamps"}
            r2_cmp = {k: v for k, v in r2.items() if k != "timestamps"}
            assert r1_cmp == r2_cmp, f"Records differ: {r1_cmp} vs {r2_cmp}"


# ── Deposit-contract violation handling (D3) ────────────────────────────


class TestDepositViolation:
    """Deposit-contract violation handling is configurable at engine
    instantiation (D3): ``raise`` (no journal), ``record_then_raise``,
    ``record_then_drop``."""

    def _make_violation_net(self) -> Net:
        """A net where the handler returns a token for a place with no matching
        produce template — a deposit-contract violation."""
        net_dict = {
            "name": "violation-net",
            "places": [
                {"name": "in_place", "accepts": ["task"]},
                {"name": "out_place", "accepts": ["task"]},
                {"name": "unexpected", "accepts": ["task"]},
            ],
            "transitions": [{"name": "bad_handler_t", "handler": "bad_handler_t"}],
            "arcs": [
                {
                    "from": {"place": "in_place"},
                    "to": {"transition": "bad_handler_t"},
                    "consume": {"type": "task"},
                },
                # Only produce to out_place — no produce template for "unexpected".
                {
                    "from": {"transition": "bad_handler_t"},
                    "to": {"place": "out_place"},
                    "produce": {"type": "task", "destination": "out_place"},
                },
            ],
        }
        return parse_net(net_dict)

    def _make_bad_handler_registry(self) -> HandlerRegistry:
        """A registry whose handler returns a token to 'unexpected' (no template)."""
        reg = HandlerRegistry()
        reg.register_transition(
            "bad_handler_t",
            lambda inp: {
                "status": "completed",
                # Returns a token for 'unexpected' — no produce template exists.
                "outputTokens": {"unexpected": [Token(type="task", data={})]},
                "error": None,
                "metadata": {},
            },
        )
        return reg

    def test_violation_not_routed_through_record_firing(self):
        """Pinning the spec (d) routing: a deposit-contract violation attempt
        is routed EXCLUSIVELY through ``record_deposit_violation`` —
        ``record_firing`` is NOT called for that attempt (each attempt occupies
        one sequence slot, not two). Holds under both recordable modes."""
        # given: a violation net, a bad-handler registry, and an input token
        net = self._make_violation_net()
        reg = self._make_bad_handler_registry()
        marking = Marking({"in_place": [Token(type="task", data={})]})

        for mode in ("record_then_raise", "record_then_drop"):
            # given: a capturing journal and an engine in the current mode
            journal = _CapturingJournal()
            engine = Engine(reg, journal=journal, deposit_violation=mode)
            # when: firing the bad-handler transition
            if mode == "record_then_raise":
                with pytest.raises(Exception):
                    engine.fire(net, marking, "bad_handler_t", attempt=0)
            else:
                engine.fire(net, marking, "bad_handler_t", attempt=0)
            # then: the violation is routed to record_deposit_violation, not record_firing
            # The violation is captured by the violation hook, NOT the firing
            # hook — record_firing sees zero records for this attempt.
            assert len(journal.violations) == 1, mode
            assert journal.firings == [], mode

    def test_raise_with_no_journal(self):
        """With no journal, the default is to raise (programmer-bug signal)."""
        # given: a violation net with a bad-handler registry and no journal
        net = self._make_violation_net()
        reg = self._make_bad_handler_registry()
        engine = Engine(reg)  # journal=None, deposit_violation="raise"
        marking = Marking({"in_place": [Token(type="task", data={})]})
        # when: firing the bad-handler transition
        # then: an exception is raised
        with pytest.raises(Exception):
            engine.fire(net, marking, "bad_handler_t", attempt=0)

    def test_raise_is_only_legal_when_no_journal(self):
        """``record_then_raise`` or ``record_then_drop`` require a journal."""
        # given: a violation net with a bad-handler registry
        self._make_violation_net()
        reg = self._make_bad_handler_registry()
        # when: constructing an engine with record_then_raise and no journal
        # then: construction is rejected
        # record_then_raise with no journal should be rejected at construction.
        with pytest.raises(Exception):
            Engine(reg, journal=None, deposit_violation="record_then_raise")

    def test_record_then_raise_with_journal(self):
        """With a journal, ``record_then_raise`` records the violation then raises."""
        # given: a violation net with a bad-handler registry and a capturing journal
        net = self._make_violation_net()
        reg = self._make_bad_handler_registry()
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal, deposit_violation="record_then_raise")
        marking = Marking({"in_place": [Token(type="task", data={})]})
        # when: firing the bad-handler transition
        # then: an exception is raised and the violation is recorded before raising
        with pytest.raises(Exception):
            engine.fire(net, marking, "bad_handler_t", attempt=0)
        # The violation was recorded before raising.
        assert len(journal.violations) == 1

    def test_record_then_drop_with_journal(self):
        """With a journal, ``record_then_drop`` records the violation and continues
        with the marking unchanged (no raise)."""
        # given: a violation net with a bad-handler registry and a capturing journal
        net = self._make_violation_net()
        reg = self._make_bad_handler_registry()
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal, deposit_violation="record_then_drop")
        marking = Marking({"in_place": [Token(type="task", data={})]})
        original = list(marking["in_place"])

        # when: firing the bad-handler transition
        new_marking, record = engine.fire(net, marking, "bad_handler_t", attempt=0)
        # then: no raise, the marking is unchanged, the violation is recorded, and the record is failed
        # No raise — marking unchanged (atomic rollback).
        assert new_marking["in_place"] == original
        # Violation recorded.
        assert len(journal.violations) == 1
        # Record reflects failure.
        assert record["status"] == "failed"

    def test_record_then_drop_continues_run_loop(self):
        """``record_then_drop`` lets the run loop continue (marking unchanged)."""
        # given: a violation net with a bad-handler registry and a capturing journal
        net = self._make_violation_net()
        reg = self._make_bad_handler_registry()
        journal = _CapturingJournal()
        engine = Engine(reg, journal=journal, deposit_violation="record_then_drop")
        marking = Marking({"in_place": [Token(type="task", data={})]})
        # when: running the loop for one step
        # run should not raise; it records the violation and stops (quiescence
        # after the violated transition since the token remains in in_place
        # and the same transition will violate again — max_steps bounds it).
        engine.run(net, marking, max_steps=1)
        # then: the violation is recorded and the run continues without raising
        assert len(journal.violations) == 1


# ── Helpers for journal tests ───────────────────────────────────────────


def _make_record(
    transition: str,
    status: str,
    *,
    input_data: dict[str, Any] | None = None,
) -> FiringRecord:
    """Construct a minimal ``FiringRecord`` for journal tests."""
    return FiringRecord(  # type: ignore[typeddict-item]
        firingId=f"test-net/{transition}/0",
        netId="test-net",
        transition=transition,
        attempt=0,
        status=cast(FiringStatus, status),
        inputTokens={} if input_data is None else input_data,
        outputTokens={},
        error=None,
        metadata={},
        timestamps={"fired_at": "2026-01-01T00:00:00Z"},
    )
