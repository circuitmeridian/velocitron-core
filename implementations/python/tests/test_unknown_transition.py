"""An unknown transition name is a raised error, not a silent empty firing.

A consumer integration surfaced that ``Engine.fire(net, marking, "typo_name")``
with a name absent from the net's declared transitions did NOT error:
``_binding_arcs`` finds no arcs, so ``_select_binding`` yields a single empty
binding — the same code path that legitimately serves a *declared* source
transition (no input arcs) — and the engine proceeds to fire under the typo'd
name. A typo silently executes side effects (or reports a misleading
``HandlerNotFound``) instead of failing.

These tests pin the fix: every public engine entry point that takes a
transition name (``fire``, ``select_binding``) raises
``UnknownTransitionError`` when the name does not resolve to a *declared*
transition, while the legitimate declared-source-transition empty-binding path
keeps working. ``enabled_transitions`` iterates declared transitions only and
is unaffected; ``run`` fires the firing policy's choice, so a rogue policy
returning an undeclared name surfaces as the same raise.

References: spec/firing-semantics.md (D10).
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import (
    FiringPolicyInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine, UnknownTransitionError
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net

# ── Shared fixture: a net with one declared source transition ───────────

# ``source`` is a declared, produce-only transition (no input arcs) — the
# legitimate empty-binding path. ``typo_name`` is NOT declared here; it is the
# name a consumer mistypes. The produce arc + ``out`` place are structurally
# required (they make ``source`` a produce-only source transition rather than
# an isolated node, mirroring the C15 enablement lock net); nothing is
# asserted on ``out`` — the handler returns no output tokens and the template
# carries no literal data, so no token is ever deposited there.
SOURCE_NET_DOC: dict[str, Any] = {
    "name": "source-net",
    "places": [{"name": "out", "accepts": ["out"]}],
    "transitions": [{"name": "source", "handler": "source"}],
    "arcs": [
        {
            "from": {"transition": "source"},
            "to": {"place": "out"},
            "produce": {"type": "out", "destination": "out"},
        },
    ],
}


def _source_net() -> Net:
    return parse_net(SOURCE_NET_DOC)


def _completed(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """A transition handler that always completes (no output tokens)."""
    return {"status": "completed", "outputTokens": {}, "error": None, "metadata": {}}


def _engine_with_source_handler() -> Engine:
    registry = HandlerRegistry()
    registry.register_transition("source", _completed)
    return Engine(registry)


# ── fire: unknown name raises ───────────────────────────────────────────


def test_fire_unknown_transition_raises():
    """``fire`` with a name absent from the net's declared transitions raises
    ``UnknownTransitionError``, not a firing.

    Construction-bite: the ``_require_transition`` guard at the top of ``fire``
    is the only barrier to the unknown name reaching ``_select_binding``'s
    empty-binding path. Removing the guard makes ``fire`` proceed and return a
    ``FiringRecord`` (a ``HandlerNotFound`` failure, or a ``completed`` fire if
    a handler happens to be registered under the typo'd name) instead of
    raising — this ``pytest.raises`` then fails."""
    # given: a net whose declared transitions do NOT include "typo_name"
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when / then: firing the typo'd name raises UnknownTransitionError
    with pytest.raises(UnknownTransitionError):
        engine.fire(net, marking, "typo_name", attempt=0)


def test_fire_unknown_transition_error_carries_net_and_transition():
    """The raised ``UnknownTransitionError`` carries the offending
    ``transition`` name and the ``net`` name for a clear diagnostic.

    Construction-bite: the error attributes are populated by
    ``UnknownTransitionError.__init__``; a reversion raising a bare
    ``Exception`` (or omitting the attributes) fails these attribute
    assertions."""
    # given: the source net and an engine
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when: firing an unknown name
    with pytest.raises(UnknownTransitionError) as excinfo:
        engine.fire(net, marking, "typo_name", attempt=0)

    # then: the error names the transition and the net
    assert excinfo.value.transition == "typo_name"
    assert excinfo.value.net == "source-net"


# ── select_binding: unknown name raises ─────────────────────────────────


def test_select_binding_unknown_transition_raises():
    """``select_binding`` with an undeclared name raises
    ``UnknownTransitionError`` rather than returning the empty binding.

    Construction-bite: without the ``_require_transition`` guard,
    ``select_binding`` returns ``{}`` (the empty binding a declared source
    transition legitimately yields), so this ``pytest.raises`` fails; the guard
    is the sole barrier."""
    # given: the source net; "typo_name" is undeclared
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when / then: selecting a binding for the typo'd name raises
    with pytest.raises(UnknownTransitionError):
        engine.select_binding(net, "typo_name", marking)


# ── Regression: the legitimate declared-source path still works ─────────


def test_declared_source_transition_still_fires_with_empty_binding():
    """A *declared* source transition (no input arcs) still fires to
    ``completed`` with an empty binding — the fix must not break the
    legitimate empty-binding path.

    Construction-bite: this is the path the unknown-name guard must NOT reject.
    A guard that keyed off "no binding arcs" instead of "name not declared"
    would raise here and fail the fire."""
    # given: the source net with its handler registered
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when: firing the declared source transition
    _new_marking, record = engine.fire(net, marking, "source", attempt=0)

    # then: it completes with an empty input binding
    assert record["status"] == "completed"
    assert record["inputTokens"] == {}


def test_declared_source_transition_select_binding_returns_empty():
    """``select_binding`` for a declared source transition returns the empty
    binding ``{}`` (enabled), not a raise — the presence-of-declaration check
    is what distinguishes it from an unknown name."""
    # given: the source net
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when: selecting a binding for the declared source transition
    binding = engine.select_binding(net, "source", marking)

    # then: the empty binding is returned (enabled), not None and not a raise
    assert binding == {}


# ── Regression: declared-only / policy-driven surfaces ──────────────────


def test_enabled_transitions_and_run_iterate_declared_transitions_only():
    """``enabled_transitions`` builds its names from ``net.transitions`` and
    ``run`` (under the built-in policy) picks from ``enabledTransitions``, so
    neither can present an undeclared name to the guard; both complete
    normally for the source net.

    This is a scoping regression check, not a guard-placement bite: these
    surfaces never carry consumer-supplied names, so the guard added to the
    name-taking entry points cannot affect them."""
    # given: the source net and an engine
    net = _source_net()
    engine = _engine_with_source_handler()
    marking = Marking({})

    # when: probing enablement and running the loop
    enabled = engine.enabled_transitions(net, marking)
    final = engine.run(net, marking, max_steps=3)

    # then: the declared source transition is enabled
    assert "source" in enabled
    # and: the run completes; nothing is ever deposited (the handler returns
    # no output tokens and the template carries no literal data), so the
    # final marking is still empty
    assert dict(final) == {}


def test_run_raises_when_policy_returns_undeclared_name():
    """A custom firing policy that returns a name not declared in the net
    surfaces as ``UnknownTransitionError`` out of ``run`` — previously a
    silent empty-binding firing under the typo'd name.

    Construction-bite: ``run`` passes the policy's choice straight to
    ``fire``, so ``fire``'s ``_require_transition`` guard is the sole barrier
    between a rogue policy and the empty-binding path; removing the guard
    makes ``run`` fire under the undeclared name and this ``pytest.raises``
    fails."""

    # given: a rogue policy that ignores enabledTransitions entirely
    def _rogue(inp: FiringPolicyInput) -> str | None:
        return "typo_name"

    # and: an engine configured with the rogue policy
    registry = HandlerRegistry()
    registry.register_transition("source", _completed)
    registry.register_firing_policy("rogue", _rogue)
    engine = Engine(registry, policy="rogue")
    net = _source_net()
    marking = Marking({})

    # when / then: the run surfaces the rogue choice as a raise
    with pytest.raises(UnknownTransitionError):
        engine.run(net, marking, max_steps=3)
