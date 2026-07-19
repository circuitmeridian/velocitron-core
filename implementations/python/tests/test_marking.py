"""Red-phase tests for the ``[impl] marking data structure`` feature.

The feature replaces the plain mutable alias ``Marking = dict[str, list[Token]]``
(``schema.py``) with an **immutable, persistent (structurally-shared)** data
structure — ``PMap[str, PVector[Token]]`` from ``pyrsistent`` — adopted
wherever the marking flows as engine runtime state.

This module pins the two load-bearing invariants that distinguish the upgrade
from the status quo (copy-on-write *by convention*):

1. **Immutability is structural, not conventional.** A ``Marking`` rejects
   in-place mutation of both its outer map (assigning a new place) and its
   inner per-place token collection (appending a token). Today the alias is a
   plain ``dict`` of ``list``s, so both writes succeed and mutate — these
   tests fail until the immutable type lands.

2. **Structural sharing replaces full copy.** A ``fire`` that touches one
   place leaves every *untouched* place's token collection ``is``-identical
   (the same ``PVector`` object) in the result — the persistent-structure
   payoff. Today ``_consume`` shallow-copies every place into a fresh list,
   so untouched places are new objects and the ``is`` check fails.

Both assertions are the "actual point" of the feature (per the plan), not a
verify-and-lock: they upgrade the existing atomic-rollback guarantee from
discipline to enforcement. They use BDD given/when/then per ``AGENTS.md``.
"""

from __future__ import annotations

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Token


# ── Fixture net: a move that touches src/sink, leaves bystander alone ────

# A minimal three-place net. ``move`` consumes a ``task`` from ``src`` and
# deposits it in ``sink``; ``bystander`` is declared but no arc touches it, so
# firing ``move`` must structurally share ``bystander`` rather than copy it.

_SHARE_NET: dict[str, object] = {
    "name": "marking-share-net",
    "places": [
        {"name": "src", "accepts": ["task"]},
        {"name": "sink", "accepts": ["task"]},
        {"name": "bystander", "accepts": ["task"]},
    ],
    "transitions": [{"name": "move", "handler": "move"}],
    "arcs": [
        {
            "from": {"place": "src"},
            "to": {"transition": "move"},
            "consume": {"type": "task"},
        },
        {
            "from": {"transition": "move"},
            "to": {"place": "sink"},
            "produce": {"type": "task", "destination": "sink"},
        },
    ],
}


def _task_token(tid: str = "t1") -> Token:
    """A minimal task token."""
    return Token(type="task", data={"id": tid})


def _passthrough_registry() -> HandlerRegistry:
    """A registry whose ``move`` handler passes its consumed token through to
    ``sink``."""
    reg = HandlerRegistry()

    def move(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        return {
            "status": "completed",
            "outputTokens": {"sink": inp["inputTokens"].get("src", [])},
            "error": None,
            "metadata": {},
        }

    reg.register_transition("move", move)
    return reg


# ── Immutability invariant (the outer map and inner collection) ──────────


class TestMarkingImmutability:
    """A ``Marking`` cannot be mutated in place — structural immutability."""

    def test_per_place_token_collection_rejects_in_place_mutation(self):
        """Appending to a place's token collection does not mutate the
        ``Marking``; the original collection is unchanged (D2/D6)."""
        # given: a Marking constructed from the natural dict-of-lists shape
        marking = Marking({"src": [_task_token("t1")]})
        snapshot = list(marking["src"])
        # when: attempting to mutate the per-place token collection in place
        marking["src"].append(_task_token("t2"))
        # then: the original collection is unchanged (immutability enforced)
        assert list(marking["src"]) == snapshot

    def test_outer_map_rejects_place_assignment(self):
        """Assigning a new place into a ``Marking`` is rejected; the map is
        immutable."""
        # given: a Marking with a single place
        marking = Marking({"src": [_task_token()]})
        # when: attempting to assign a new place into the marking
        try:
            marking["new_place"] = [_task_token()]  # pyright: ignore[reportIndexIssue]
        except TypeError:
            pass  # an immutable map raises on item assignment
        # then: the new place was not added (immutability enforced)
        assert "new_place" not in marking


# ── Structural sharing invariant (the persistent-structure payoff) ──────


class TestMarkingStructuralSharing:
    """A ``fire`` shares untouched places structurally with the input marking
    — no full copy."""

    def test_fire_shares_untouched_places_is_identical(self):
        """Firing a transition that touches ``src``/``sink`` leaves
        ``bystander`` ``is``-identical (the same collection object) in the
        result, not a copy (D2: structural sharing replaces copy-on-write)."""
        # given: the share-net with a passthrough move handler and a marking
        #        holding a token in both the consumed place and an untouched one
        net = parse_net(_SHARE_NET)
        engine = Engine(_passthrough_registry())
        bystander_token = _task_token("t2")
        marking = Marking({"src": [_task_token("t1")], "bystander": [bystander_token]})
        # when: firing move (consumes src, deposits sink; bystander untouched)
        result, _record = engine.fire(net, marking, "move", attempt=0)
        # then: the untouched place is the SAME collection object (shared, not
        # copied) — the regression guard against an accidental full-copy revert
        assert result["bystander"] is marking["bystander"]
        # and: its value is preserved unchanged
        assert list(result["bystander"]) == [bystander_token]

    def test_run_shares_untouched_place_across_multi_step_run(self):
        """A multi-step ``run`` keeps a never-touched place ``is``-identical to
        the input marking's collection — locking D2's run-level payoff (no
        up-front copy in ``run``).

        The single-fire ``is``-test shares with ``current``, not the input, so
        it cannot catch a reversion that re-introduces an up-front full copy
        only in ``run``. This test fires ``move`` twice (two tokens in ``src``)
        and asserts the input's ``bystander`` collection survives the whole
        run as the same object."""
        # given: the share-net with a passthrough move handler and a marking
        #        with two tokens to consume in src plus an untouched bystander
        net = parse_net(_SHARE_NET)
        engine = Engine(_passthrough_registry())
        bystander_token = _task_token("t2")
        marking = Marking(
            {
                "src": [_task_token("a1"), _task_token("a2")],
                "bystander": [bystander_token],
            }
        )
        # when: running to quiescence (move fires twice, draining src)
        final = engine.run(net, marking)
        # then: the never-touched place is the SAME collection object as the
        # input's — the run-level regression guard against an up-front copy
        assert final["bystander"] is marking["bystander"]
        # and: src is drained and sink holds both consumed tokens
        assert list(final["src"]) == []
        assert list(final["sink"]) == [_task_token("a1"), _task_token("a2")]
        # and: the input marking itself is unchanged (immutability, D2)
        assert list(marking["src"]) == [_task_token("a1"), _task_token("a2")]
