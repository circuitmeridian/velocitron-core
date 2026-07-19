"""Behavioral coverage for Engine's cached static-topology lookups."""

from __future__ import annotations

from typing import Any

from velocitron.contract import (
    GuardHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token


def _token(token_type: str, **data: Any) -> Token:
    return Token(type=token_type, data=dict(data))


def test_run_preserves_mixed_arc_binding_and_rechecks_guard() -> None:
    # given: one transition combining consume, read, plain inhibit, correlated
    # inhibit, and produce arcs in a deliberately interleaved declaration
    net = parse_net(
        {
            "name": "mixed-topology",
            "places": [
                {"name": "jobs", "accepts": ["job"]},
                {"name": "config", "accepts": ["config"]},
                {"name": "paused", "accepts": ["pause"]},
                {"name": "locks", "accepts": ["lock"]},
                {"name": "done", "accepts": ["job"]},
            ],
            "transitions": [{"name": "finish", "handler": "finish", "guard": "permit"}],
            "arcs": [
                {
                    "from": {"place": "config"},
                    "to": {"transition": "finish"},
                    "consume": {"type": "config", "mode": "read"},
                },
                {
                    "from": {"place": "paused"},
                    "to": {"transition": "finish"},
                    "consume": {"type": "pause", "mode": "inhibit"},
                },
                {
                    "from": {"place": "jobs"},
                    "to": {"transition": "finish"},
                    "consume": {
                        "type": "job",
                        "predicate": {"cel": "ready == true"},
                    },
                },
                {
                    "from": {"place": "locks"},
                    "to": {"transition": "finish"},
                    "consume": {
                        "type": "lock",
                        "mode": "inhibit",
                        "correlate": {"cel": "token.job_id == binding.jobs[0].id"},
                    },
                },
                {
                    "from": {"transition": "finish"},
                    "to": {"place": "done"},
                    "produce": {"type": "job", "destination": "done"},
                },
            ],
        }
    )
    guard_inputs: list[dict[str, list[Token]]] = []
    registry = HandlerRegistry()

    def permit(inp: GuardHandlerInput) -> bool:
        guard_inputs.append(inp["inputTokens"])
        return True

    def finish(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        return {
            "status": "completed",
            "outputTokens": {"done": inp["inputTokens"]["jobs"]},
            "error": None,
            "metadata": {},
        }

    registry.register_guard("permit", permit)
    registry.register_transition("finish", finish)
    engine = Engine(registry)
    job = _token("job", id="j1", ready=True)
    config = _token("config", revision=3)
    unrelated_lock = _token("lock", job_id="another-job")
    marking = Marking({"jobs": [job], "config": [config], "locks": [unrelated_lock]})

    # when: enablement is probed and public fire rechecks it inside run
    enabled = engine.enabled_transitions(net, marking)
    result = engine.run(net, marking)

    # then: declaration-ordered mixed arcs retain their consume/read/inhibit roles
    assert enabled == ["finish"]
    assert list(result["jobs"]) == []
    assert list(result["config"]) == [config]
    assert list(result["locks"]) == [unrelated_lock]
    assert list(result["done"]) == [job]
    # and: no binding or dynamic guard result was reused across public fire
    assert len(guard_inputs) == 3
    assert all(list(binding) == ["config", "jobs"] for binding in guard_inputs)


def test_candidates_preserve_declaration_order_and_dynamic_no_input_guard() -> None:
    # given: declaration order differs from both place order and alphabetical order,
    # with an impure guarded transition that has no input arcs
    net = parse_net(
        {
            "name": "candidate-order",
            "places": [
                {"name": "beta_in", "accepts": ["item"]},
                {"name": "zeta_in", "accepts": ["item"]},
            ],
            "transitions": [
                {"name": "zeta", "handler": "unused"},
                {"name": "source", "handler": "unused", "guard": "alternating"},
                {"name": "beta", "handler": "unused"},
            ],
            "arcs": [
                {
                    "from": {"place": "beta_in"},
                    "to": {"transition": "beta"},
                    "consume": {"type": "item"},
                },
                {
                    "from": {"place": "zeta_in"},
                    "to": {"transition": "zeta"},
                    "consume": {"type": "item"},
                },
            ],
        }
    )
    calls = 0

    def alternating(inp: GuardHandlerInput) -> bool:
        nonlocal calls
        del inp
        calls += 1
        return calls % 2 == 1

    registry = HandlerRegistry()
    registry.register_guard("alternating", alternating)
    engine = Engine(registry)
    marking = Marking({"beta_in": [_token("item")], "zeta_in": [_token("item")]})

    # when: the same static topology is queried twice
    first = engine.enabled_transitions(net, marking)
    second = engine.enabled_transitions(net, marking)

    # then: static narrowing preserves declaration order and no-input candidacy
    assert first == ["zeta", "source", "beta"]
    assert second == ["zeta", "beta"]
    # and: the impure guard is evaluated anew rather than cached as enablement
    assert calls == 2


def test_one_engine_keeps_same_named_net_topologies_isolated() -> None:
    # given: two distinct Net instances deliberately sharing net and transition names
    def net(source: str, destination: str, token_type: str) -> Net:
        return parse_net(
            {
                "name": "same-name",
                "places": [
                    {"name": source, "accepts": [token_type]},
                    {"name": destination, "accepts": [token_type]},
                ],
                "transitions": [{"name": "move", "handler": "move"}],
                "arcs": [
                    {
                        "from": {"place": source},
                        "to": {"transition": "move"},
                        "consume": {"type": token_type},
                    },
                    {
                        "from": {"transition": "move"},
                        "to": {"place": destination},
                        "produce": {
                            "type": token_type,
                            "destination": destination,
                        },
                    },
                ],
            }
        )

    left_net = net("left", "left_done", "left_item")
    right_net = net("right", "right_done", "right_item")
    registry = HandlerRegistry()

    def move(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        source = next(iter(inp["inputTokens"]))
        destination = f"{source}_done"
        return {
            "status": "completed",
            "outputTokens": {destination: inp["inputTokens"][source]},
            "error": None,
            "metadata": {},
        }

    registry.register_transition("move", move)
    engine = Engine(registry)
    left = _token("left_item", id="left")
    right = _token("right_item", id="right")

    # when: the same Engine caches and runs both topologies in sequence
    left_result = engine.run(left_net, Marking({"left": [left]}))
    right_result = engine.run(right_net, Marking({"right": [right]}))
    injected, _record = engine.inject_token(
        right_net, right_result, "right", right, attempt=1
    )

    # then: transition, arc, produce, and place indexes never cross Net instances
    assert list(left_result["left_done"]) == [left]
    assert list(right_result["right_done"]) == [right]
    assert list(injected["right"]) == [right]
    assert "right_done" not in left_result
    assert "left_done" not in right_result
