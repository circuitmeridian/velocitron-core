"""Merged-net firing tests for the composition-safe handler contract.

The composition merge (``merge_nets``) rewrites place names — every place is
alias-qualified (``<alias>.<name>``) and wired port-places are fused into a
place named after the source output port — so a handler keyed on literal place
names breaks under every merge. The composition-safe contract
(``spec/handler-contract.md``, "Composition-safe handlers") is the idiom that
survives: read ``inputTokens`` by token *type*, produce ``outputTokens``
against the transition's *resolved* produce destinations (bound at
registration time), never hardcoded pre-merge names.

The pre-existing ``test_composition_merge.py`` coverage is structural (M8
fires a merged net, but its handlers hardcode the merged names — the very
coupling this contract exists to remove). These tests are the first in-repo
coverage that drives ``Engine.fire``/``Engine.run`` over a merged net with
handlers written *per the documented contract*, on the canonical composition
shape: two instances of one small worker net fan-out wired onto a shared
source place.

Net-shape note (minimal-net convention): the worker net carries exactly the
surface under test — one input port (wired, hence fused), one work transition
(consume + produce arcs, both resolved by ``fire``), one destination place
(asserted on). The source net is a single output-port place with no
transitions — the structurally-required fan-out anchor for the two wires,
nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.composition import merge_nets
from velocitron.engine import DepositViolation, Engine
from velocitron.journal import FiringRecord, InjectionRecord
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token, Wire

# The fused shared place: named after the sole source output port.
_FUSED = "src.feed"


def _task(**data: Any) -> Token:
    """A ``task`` token with payload ``data``."""
    return Token(type="task", data=dict(data))


def _worker_net(instance: str) -> Net:
    """One worker instance: ``in`` (input port) → ``work`` → ``done``.

    The handler ref is instance-scoped (``work@<instance>``) per the
    composition-safe idiom: each instance's handler is bound to its own
    resolved output destination at registration time, so two instances of the
    same net coexist in one registry.
    """
    return parse_net(
        {
            "name": f"worker-{instance}",
            "places": [
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {"name": "done", "accepts": ["task"]},
            ],
            "transitions": [{"name": "work", "handler": f"work@{instance}"}],
            "arcs": [
                {
                    "from": {"place": "in"},
                    "to": {"transition": "work"},
                    "consume": {"type": "task"},
                },
                {
                    "from": {"transition": "work"},
                    "to": {"place": "done"},
                    "produce": {"type": "task", "destination": "done"},
                },
            ],
        }
    )


# The shared source: one output port, no transitions — a valid fusion anchor
# (tokens enter the fused place via the initial marking in these tests).
_SOURCE = parse_net(
    {
        "name": "src",
        "places": [
            {
                "name": "feed",
                "accepts": ["task"],
                "port": {"direction": "output", "type": "task"},
            }
        ],
        "transitions": [],
        "arcs": [],
    }
)


def _merged() -> Net:
    """Two worker instances fan-out wired onto the shared source place."""
    alias_to_net = {
        "src": _SOURCE,
        "a": _worker_net("a"),
        "b": _worker_net("b"),
    }
    wires = [
        Wire(from_net="src", from_port="feed", to_net="a", to_port="in"),
        Wire(from_net="src", from_port="feed", to_net="b", to_port="in"),
    ]
    return merge_nets(alias_to_net, wires)


def _make_work_handler(instance: str, done_place: str):
    """A composition-safe transition handler bound to one instance.

    Per the contract: inputs are selected by token *type* (scanning
    ``inputTokens.values()``, never indexing by place name — the key is the
    fused place's merge-assigned name); the output is keyed by the *resolved*
    destination ``done_place``, bound here at registration time.
    """

    def work(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        tasks = [
            tok
            for toks in inp["inputTokens"].values()
            for tok in toks
            if tok.type == "task"
        ]
        out = Token(type="task", data={**tasks[0].data, "worker": instance})
        return {
            "status": "completed",
            "outputTokens": {done_place: [out]},
            "error": None,
            "metadata": {},
        }

    return work


def _contract_registry() -> HandlerRegistry:
    """Both instances' handlers, each bound to its resolved destination."""
    registry = HandlerRegistry()
    registry.register_transition("work@a", _make_work_handler("a", "a.done"))
    registry.register_transition("work@b", _make_work_handler("b", "b.done"))
    return registry


class _CapturingJournal:
    """In-memory Journal: captures firing records for merged-name assertions.

    Implements all three protocol methods (required by ``Engine``'s ``Journal``
    type); only ``record_firing`` is exercised by these tests' completed fires.
    """

    def __init__(self) -> None:
        self.firings: list[FiringRecord] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.firings.append(record)

    def record_deposit_violation(
        self, record: FiringRecord
    ) -> None:  # pragma: no cover - protocol completeness
        raise AssertionError("no deposit violation expected")

    def record_injection(
        self, record: InjectionRecord
    ) -> None:  # pragma: no cover - protocol completeness
        raise AssertionError("no injection expected")


class TestMergedNetTokenFlow:
    """A contract-conforming handler survives the merge: a token in the fused
    shared place flows through an instance's transition to that instance's
    ``done`` place under ``Engine.run``.

    Bite mechanism (construction-bite): the handlers never mention the fused
    place's name — reading by token type is the only thing that lets them see
    the binding at all, since post-merge the binding key is ``src.feed``, a
    name that exists only after ``merge_nets`` runs.
    """

    def test_token_in_fused_place_flows_to_instance_done_via_run(self):
        # given: the merged net and contract-conforming handlers
        merged = _merged()
        engine = Engine(_contract_registry())
        # and: one task token sitting in the fused shared place
        marking = Marking({_FUSED: [_task(id=1)]})

        # when: running to quiescence
        final = engine.run(merged, marking, max_steps=10)

        # then: the token flowed through instance "a" — the two instances
        #       conflict over the one token; the default first-found policy
        #       resolves conflict by declaration order (ADR 0005), and "a" is
        #       merged before "b", so this winner is spec-derived, not
        #       incidental
        done_a = list(final.get("a.done", []))
        assert len(done_a) == 1
        assert done_a[0].data == {"id": 1, "worker": "a"}
        # and: the fused place is drained (the two instances contend; one wins)
        assert len(final.get(_FUSED, [])) == 0
        # and: the losing instance saw nothing
        assert len(final.get("b.done", [])) == 0


class TestMergedBindingKeys:
    """The binding a merged-net handler receives is keyed by the *fused* place
    name — the merge-rewritten key, not the constituent's local port name.

    Bite mechanism (reversion-verified): disabling ``merge_nets``' fusion
    rewrite (reverting ``_rw`` to return the qualified name unrewritten)
    fails this test. That one reversion is representative across all five
    classes in this module — they share the merged-net fixture, and each
    fails under it (at merged-net validation: the un-rewritten arcs
    reference the fused-away ``a.in``/``b.in`` places).
    """

    def test_handler_binding_keyed_by_fused_place_name(self):
        # given: the merged net
        merged = _merged()
        # and: a capturing contract-conforming handler for instance a
        captured: dict[str, Any] = {}
        registry = _contract_registry()

        def capturing_work(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            captured["keys"] = set(inp["inputTokens"].keys())
            captured["transitionId"] = inp["transitionId"]
            return _make_work_handler("a", "a.done")(inp)

        registry.register_transition("work@a", capturing_work)
        engine = Engine(registry)
        marking = Marking({_FUSED: [_task(id=7)]})

        # when: firing instance a's (alias-qualified) transition
        _, record = engine.fire(merged, marking, "a.work", attempt=0)

        # then: the fire completed
        assert record["status"] == "completed"
        # and: the binding is keyed by the fused place name, not "in"/"a.in"
        assert captured["keys"] == {_FUSED}
        # and: the transition-local view carries the qualified transition name
        assert captured["transitionId"] == "a.work"


class TestPerInstanceIsolation:
    """Each instance's transition fires against the shared fused place, but
    per-instance state stays isolated: each ``done`` place receives exactly
    its own instance's output.

    Bite mechanism (construction-bite): the registration-time destination
    binding (``a.done`` vs ``b.done``) is the only thing separating the two
    instances' outputs — both handlers are the same factory over the same
    fused input.
    """

    def test_instances_consume_shared_place_but_keep_isolated_state(self):
        # given: the merged net, contract handlers, two tokens in the fused place
        merged = _merged()
        engine = Engine(_contract_registry())
        marking = Marking({_FUSED: [_task(id=1), _task(id=2)]})

        # when: firing each instance's transition once
        marking, rec_a = engine.fire(merged, marking, "a.work", attempt=0)
        marking, rec_b = engine.fire(merged, marking, "b.work", attempt=1)

        # then: both fires completed
        assert rec_a["status"] == "completed"
        assert rec_b["status"] == "completed"
        # and: each instance's done place holds exactly its own output
        done_a = list(marking.get("a.done", []))
        done_b = list(marking.get("b.done", []))
        assert [t.data["worker"] for t in done_a] == ["a"]
        assert [t.data["worker"] for t in done_b] == ["b"]
        # and: the shared fused place is fully drained
        assert len(marking.get(_FUSED, [])) == 0


class TestJournalRecordsMergedNames:
    """The firing journal records the *merged* names: alias-qualified
    transitions, fused input keys, and rewritten output destinations.

    Bite mechanism (construction-bite): the engine builds the record from the
    net it fires — the merged one — so any un-rewritten name in a record
    would mean ``fire`` resolved a constituent name the merged net does not
    carry.
    """

    def test_journal_records_carry_merged_names(self):
        # given: the merged net, contract handlers, and a capturing journal
        merged = _merged()
        journal = _CapturingJournal()
        engine = Engine(_contract_registry(), journal=journal)
        marking = Marking({_FUSED: [_task(id=1), _task(id=2)]})

        # when: firing both instances' transitions
        marking, _ = engine.fire(merged, marking, "a.work", attempt=0)
        marking, _ = engine.fire(merged, marking, "b.work", attempt=1)

        # then: the journal recorded both fires under alias-qualified names
        assert [r["transition"] for r in journal.firings] == ["a.work", "b.work"]
        # and: input tokens are keyed by the fused place name
        assert set(journal.firings[0]["inputTokens"].keys()) == {_FUSED}
        assert set(journal.firings[1]["inputTokens"].keys()) == {_FUSED}
        # and: output tokens are keyed by the rewritten (qualified) destinations
        assert set(journal.firings[0]["outputTokens"].keys()) == {"a.done"}
        assert set(journal.firings[1]["outputTokens"].keys()) == {"b.done"}
        # and: the records carry the merged net's id
        assert {r["netId"] for r in journal.firings} == {merged.name}


class TestPlaceNameCoupledHandlerBreaks:
    """A house-style handler keyed on literal pre-merge place names completes
    standalone but breaks on the merged net — the executable form of the
    friction the composition-safe contract removes.

    Bite mechanism (construction-bite): the merge's name rewriting is the
    only difference between the two halves — the same handler object fires
    both nets. Post-merge it reads nothing (its input key no longer exists)
    and its hardcoded ``"done"`` destination matches no rewritten produce
    template, tripping the deposit contract (``DepositViolation``).
    """

    def test_hardcoded_place_names_complete_standalone_but_violate_post_merge(self):
        # given: a house-style handler coupled to the worker net's local names
        def house_style(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            tasks = inp["inputTokens"].get("in", [])  # pre-merge key
            return {
                "status": "completed",
                "outputTokens": {"done": [_task(echoed=len(tasks))]},  # pre-merge name
                "error": None,
                "metadata": {},
            }

        registry = HandlerRegistry()
        registry.register_transition("work@a", house_style)
        engine = Engine(registry)

        # when: firing the standalone (un-merged) worker net
        standalone = _worker_net("a")
        final, record = engine.fire(
            standalone, Marking({"in": [_task()]}), "work", attempt=0
        )

        # then: standalone, the local names resolve and the fire completes
        assert record["status"] == "completed"
        assert [t.data["echoed"] for t in final.get("done", [])] == [1]

        # when: firing the same handler on the merged net
        merged = _merged()
        marking = Marking({_FUSED: [_task()]})

        # then: the hardcoded destination violates the rewritten produce contract
        with pytest.raises(DepositViolation):
            engine.fire(merged, marking, "a.work", attempt=0)
