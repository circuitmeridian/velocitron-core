"""Read-arc (``mode: "read"``, test-without-consume) tests.

The red-phase test contract for the read-arc feature. A consume arc may
declare ``mode: "read"`` alongside the existing ``"consume"`` and
``"inhibit"``: the transition is enabled only if a matching token exists (same
type + predicate matching as consume, generalized over ``weight``), the
matched token(s) CONTRIBUTE to the binding (guard/handler/record see them), but
are NOT removed from the place on firing.

These tests pin:

- **Parsing/validation** — ``mode: "read"`` parses to
  ``ConsumePattern(mode="read")``; ``weight`` and ``predicate`` are accepted on
  a read arc; the read type must be accepted by the source place.
- **Fixture nets** — four neutral read-arc nets (a library-hold checkout, a
  greenhouse sprinkler, an elevator dispatch, a turnstile gate) parse and
  validate after the change, each carrying at least one read arc. They stand in
  for the modeling-study nets, which stay out of this repo (kept
  business-agnostic per the examples policy).
- **Enablement** — a read arc gates enablement on presence (≥ ``weight``
  matching tokens), like consume; unlike consume it removes nothing.
- **Binding** — read-bound tokens appear in ``inputTokens`` (the binding the
  guard/handler/record see), keyed by source place.
- **Firing** — the read place is unchanged after firing; a pure-read
  transition fires and consumes nothing.
- **Interaction rules** — read + consume on the same place bind DISJOINT
  tokens (a token may not serve both); multiple read arcs; read + inhibit.

The test fails until ``mode: "read"`` lands in the schema enum, the parser
accepts it, and the engine implements the test-without-consume semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.parser import NetValidationError, parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

# ── Shared helpers ──────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures" / "readarc"


def _net(
    name: str,
    places: list[str],
    transitions: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
    accepts: dict[str, list[str]] | None = None,
) -> Net:
    """Build and parse a minimal net; each place accepts its own name by
    default (override via ``accepts``). Mirrors the ``test_enablement`` helper.
    """
    place_dicts = [{"name": p, "accepts": (accepts or {}).get(p, [p])} for p in places]
    return parse_net(
        {
            "name": name,
            "places": place_dicts,
            "transitions": transitions,
            "arcs": arcs,
        }
    )


def _tok(t: str, **data: Any) -> Token:
    return Token(type=t, data=dict(data))


_CAPTURED: list[dict[str, list[Token]]] = []


def _capturing_passthrough(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Record the binding the handler saw, deposit nothing."""
    _CAPTURED.append(dict(inp["inputTokens"]))
    return {"status": "completed", "outputTokens": {}, "error": None, "metadata": {}}


def _emit_to(dest: str, token: Token):
    """A handler that deposits one fixed ``token`` into ``dest``."""

    def _handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        _CAPTURED.append(dict(inp["inputTokens"]))
        return {
            "status": "completed",
            "outputTokens": {dest: [token]},
            "error": None,
            "metadata": {},
        }

    return _handler


def _engine(**transitions: Any) -> Engine:
    reg = HandlerRegistry()
    for name, fn in transitions.items():
        reg.register_transition(name, fn)
    return Engine(reg)


# ── Parsing & validation ─────────────────────────────────────────────────


class TestReadArcParsing:
    """``mode: "read"`` parses and validates like a consume arc bar removal."""

    def test_read_mode_parses(self):
        # given: a net whose single input arc declares mode "read"
        net = _net(
            "read-parse",
            places=["flag", "in", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "t"},
                    "consume": {"type": "in"},
                },
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        # then: the read arc carries mode "read"
        read_arc = next(a for a in net.arcs if a.from_place == "flag")
        assert read_arc.consume is not None
        assert read_arc.consume.mode == "read"

    def test_read_arc_accepts_weight(self):
        # given: a read arc declaring weight 2
        net = _net(
            "read-weight",
            places=["flag", "t"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read", "weight": 2},
                },
            ],
        )
        # then: weight is preserved on the read arc (weight applies to read)
        read_arc = next(a for a in net.arcs if a.from_place == "flag")
        assert read_arc.consume is not None
        assert read_arc.consume.weight == 2

    def test_read_arc_accepts_predicate(self):
        # given: a read arc with an inline CEL predicate
        net = _net(
            "read-pred",
            places=["flag", "t"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {
                        "type": "flag",
                        "mode": "read",
                        "predicate": {"cel": "kind == 'ripe'"},
                    },
                },
            ],
        )
        # then: the predicate is parsed and preserved
        read_arc = next(a for a in net.arcs if a.from_place == "flag")
        assert read_arc.consume is not None
        assert read_arc.consume.predicate is not None
        assert read_arc.consume.predicate.cel == "kind == 'ripe'"

    def test_read_arc_type_must_be_accepted_by_place(self):
        # given: a read arc whose type is not in the source place's accepts
        # then: validation raises
        with pytest.raises(NetValidationError):
            _net(
                "read-badtype",
                places=["flag", "t"],
                transitions=[{"name": "t", "handler": "t"}],
                arcs=[
                    {
                        "from": {"place": "flag"},
                        "to": {"transition": "t"},
                        "consume": {"type": "ghost", "mode": "read"},
                    },
                ],
                accepts={"flag": ["flag"]},
            )


# ── Fixture nets (acceptance criterion) ──────────────────────────────────


class TestReadArcFixturesParse:
    """Four neutral read-arc fixture nets parse and validate.

    Each stresses a facet of the read-arc surface: a read-only gate
    (library-hold), a read with a CEL predicate plus a second read arc
    (greenhouse-sprinkler), a read with ``weight`` > 1 (elevator-dispatch), and
    a read composed with an inhibit arc (turnstile-gate).
    """

    _FIXTURE_FILES = [
        "library-hold.readarc.json",
        "greenhouse-sprinkler.readarc.json",
        "elevator-dispatch.readarc.json",
        "turnstile-gate.readarc.json",
    ]

    @pytest.mark.parametrize("fixture", _FIXTURE_FILES)
    def test_fixture_parses_and_has_read_arc(self, fixture: str):
        # given: a neutral read-arc fixture net
        net = parse_net(_FIXTURES / fixture)
        # then: it parses and carries at least one read-mode arc
        read_arcs = [
            a for a in net.arcs if a.consume is not None and a.consume.mode == "read"
        ]
        assert read_arcs, f"{fixture} has no read arc"


# ── Enablement ───────────────────────────────────────────────────────────


class TestReadArcEnablement:
    """A read arc gates enablement on presence, like consume."""

    @staticmethod
    def _read_net() -> Net:
        return _net(
            "read-enable",
            places=["flag", "in", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "t"},
                    "consume": {"type": "in"},
                },
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )

    def test_missing_read_token_disables(self):
        # given: the read place is empty (consume place has a token)
        net = self._read_net()
        engine = _engine(t=_capturing_passthrough)
        marking = Marking({"in": [_tok("in", id="i")]})
        # then: t is not enabled — the read arc has no matching token
        assert "t" not in engine.enabled_transitions(net, marking)

    def test_present_read_token_enables(self):
        # given: both the read and consume places hold a token
        net = self._read_net()
        engine = _engine(t=_capturing_passthrough)
        marking = Marking({"in": [_tok("in", id="i")], "flag": [_tok("flag")]})
        # then: t is enabled
        assert "t" in engine.enabled_transitions(net, marking)

    def test_read_predicate_filters(self):
        # given: a read arc predicated on kind == 'ripe'
        net = _net(
            "read-pred-enable",
            places=["ready", "in", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "t"},
                    "consume": {"type": "in"},
                },
                {
                    "from": {"place": "ready"},
                    "to": {"transition": "t"},
                    "consume": {
                        "type": "ready",
                        "mode": "read",
                        "predicate": {"cel": "kind == 'ripe'"},
                    },
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        engine = _engine(t=_capturing_passthrough)
        # a non-matching ready token does NOT enable
        m_unripe = Marking({"in": [_tok("in")], "ready": [_tok("ready", kind="green")]})
        assert "t" not in engine.enabled_transitions(net, m_unripe)
        # a matching ready token enables
        m_ripe = Marking({"in": [_tok("in")], "ready": [_tok("ready", kind="ripe")]})
        assert "t" in engine.enabled_transitions(net, m_ripe)

    def test_read_weight_requires_that_many_matching_tokens(self):
        # given: a read arc of weight 2
        net = _net(
            "read-weight-enable",
            places=["flag", "t"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read", "weight": 2},
                },
            ],
        )
        engine = _engine(t=_capturing_passthrough)
        # one token: not enough
        assert "t" not in engine.enabled_transitions(
            net, Marking({"flag": [_tok("flag")]})
        )
        # two tokens: enabled
        two = Marking({"flag": [_tok("flag", i=0), _tok("flag", i=1)]})
        assert "t" in engine.enabled_transitions(net, two)


# ── Binding & firing (test-without-consume) ──────────────────────────────


class TestReadArcFiring:
    """Read-bound tokens contribute to the binding but are not consumed."""

    def setup_method(self):
        _CAPTURED.clear()

    @staticmethod
    def _read_net() -> Net:
        return _net(
            "read-fire",
            places=["flag", "in", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "in"},
                    "to": {"transition": "t"},
                    "consume": {"type": "in"},
                },
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )

    def test_read_token_in_binding(self):
        # given: a firing over a read + consume net
        net = self._read_net()
        engine = _engine(t=_emit_to("out", _tok("out", ok=True)))
        flag = _tok("flag", label="up")
        marking = Marking({"in": [_tok("in", id="i")], "flag": [flag]})
        # when: firing t
        engine.fire(net, marking, "t", attempt=0)
        # then: the handler saw the read token in its binding, keyed by place
        assert len(_CAPTURED) == 1
        seen = _CAPTURED[0]
        assert seen.get("flag") == [flag]
        assert seen.get("in") == [_tok("in", id="i")]

    def test_read_token_not_consumed_on_fire(self):
        # given: a firing over a read + consume net
        net = self._read_net()
        engine = _engine(t=_emit_to("out", _tok("out", ok=True)))
        flag = _tok("flag", label="up")
        marking = Marking({"in": [_tok("in", id="i")], "flag": [flag]})
        # when: firing t
        new_marking, record = engine.fire(net, marking, "t", attempt=0)
        # then: the read place is UNCHANGED, the consume place is drained
        assert list(new_marking.get("flag", [])) == [flag]
        assert list(new_marking.get("in", [])) == []
        # and: the output was deposited
        assert record["status"] == "completed"
        assert list(new_marking.get("out", [])) == [_tok("out", ok=True)]

    def test_read_token_recorded_in_firing_input(self):
        # given: a firing over a read + consume net
        net = self._read_net()
        engine = _engine(t=_emit_to("out", _tok("out", ok=True)))
        flag = _tok("flag", label="up")
        marking = Marking({"in": [_tok("in", id="i")], "flag": [flag]})
        # when: firing t
        _new, record = engine.fire(net, marking, "t", attempt=0)
        # then: the firing record's inputTokens include the read token
        # (read tokens affect the binding, so replay stays deterministic)
        assert record["inputTokens"].get("flag") == [flag]

    def test_pure_read_transition_fires_and_consumes_nothing(self):
        # given: a transition whose only input is a read arc
        net = _net(
            "pure-read",
            places=["flag", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flag"},
                    "to": {"transition": "t"},
                    "consume": {"type": "flag", "mode": "read"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        engine = _engine(t=_emit_to("out", _tok("out")))
        flag = _tok("flag")
        marking = Marking({"flag": [flag]})
        # when: firing t
        new_marking, record = engine.fire(net, marking, "t", attempt=0)
        # then: nothing was consumed; the flag persists; the output landed
        assert record["status"] == "completed"
        assert list(new_marking.get("flag", [])) == [flag]
        assert list(new_marking.get("out", [])) == [_tok("out")]


# ── Interaction rules ────────────────────────────────────────────────────


class TestReadArcInteractions:
    """read + consume disjointness, multiple read arcs, read + inhibit."""

    def setup_method(self):
        _CAPTURED.clear()

    @staticmethod
    def _read_and_consume_same_place_net() -> Net:
        """A transition that both READS and CONSUMES from one place ``p``."""
        return _net(
            "read-consume-same",
            places=["p", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "p"},
                    "to": {"transition": "t"},
                    "consume": {"type": "p", "mode": "read"},
                },
                {
                    "from": {"place": "p"},
                    "to": {"transition": "t"},
                    "consume": {"type": "p"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )

    def test_read_and_consume_same_place_need_distinct_tokens(self):
        # given: one token in a place that a transition both reads and consumes
        net = self._read_and_consume_same_place_net()
        engine = _engine(t=_emit_to("out", _tok("out")))
        # a single token cannot satisfy both the read and the consume (disjoint)
        assert "t" not in engine.enabled_transitions(
            net, Marking({"p": [_tok("p", i=0)]})
        )

    def test_read_and_consume_same_place_two_tokens_consumes_one(self):
        # given: two tokens in the read+consume place
        net = self._read_and_consume_same_place_net()
        engine = _engine(t=_emit_to("out", _tok("out")))
        marking = Marking({"p": [_tok("p", i=0), _tok("p", i=1)]})
        # then: enabled; firing consumes exactly ONE token, the read leaves one
        assert "t" in engine.enabled_transitions(net, marking)
        new_marking, record = engine.fire(net, marking, "t", attempt=0)
        assert record["status"] == "completed"
        assert len(new_marking.get("p", [])) == 1

    def test_multiple_read_arcs_all_required_none_consumed(self):
        # given: a transition that reads from TWO places
        net = _net(
            "multi-read",
            places=["a", "b", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "a"},
                    "to": {"transition": "t"},
                    "consume": {"type": "a", "mode": "read"},
                },
                {
                    "from": {"place": "b"},
                    "to": {"transition": "t"},
                    "consume": {"type": "b", "mode": "read"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        engine = _engine(t=_emit_to("out", _tok("out")))
        ta, tb = _tok("a"), _tok("b")
        # only one present: not enabled
        assert "t" not in engine.enabled_transitions(net, Marking({"a": [ta]}))
        # both present: enabled, and neither is consumed
        marking = Marking({"a": [ta], "b": [tb]})
        assert "t" in engine.enabled_transitions(net, marking)
        new_marking, _record = engine.fire(net, marking, "t", attempt=0)
        assert list(new_marking.get("a", [])) == [ta]
        assert list(new_marking.get("b", [])) == [tb]

    def test_read_and_inhibit_same_place_never_enabled(self):
        # given: a transition that both reads AND inhibits the same place/type
        net = _net(
            "read-inhibit-same",
            places=["g", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "g"},
                    "to": {"transition": "t"},
                    "consume": {"type": "g", "mode": "read"},
                },
                {
                    "from": {"place": "g"},
                    "to": {"transition": "t"},
                    "consume": {"type": "g", "mode": "inhibit"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        engine = _engine(t=_emit_to("out", _tok("out")))
        # token present: inhibit fails; token absent: read fails — never enabled
        assert "t" not in engine.enabled_transitions(net, Marking({"g": [_tok("g")]}))
        assert "t" not in engine.enabled_transitions(net, Marking())

    def test_read_and_inhibit_different_places(self):
        # given: read place g1 (require present), inhibit place g2 (require absent)
        net = _net(
            "read-inhibit-diff",
            places=["g1", "g2", "out"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "g1"},
                    "to": {"transition": "t"},
                    "consume": {"type": "g1", "mode": "read"},
                },
                {
                    "from": {"place": "g2"},
                    "to": {"transition": "t"},
                    "consume": {"type": "g2", "mode": "inhibit"},
                },
                {
                    "from": {"transition": "t"},
                    "to": {"place": "out"},
                    "produce": {"type": "out", "destination": "out"},
                },
            ],
        )
        engine = _engine(t=_emit_to("out", _tok("out")))
        # g1 present, g2 absent: enabled; the read token survives firing
        marking = Marking({"g1": [_tok("g1")]})
        assert "t" in engine.enabled_transitions(net, marking)
        new_marking, _record = engine.fire(net, marking, "t", attempt=0)
        assert list(new_marking.get("g1", [])) == [_tok("g1")]
        # g2 populated: inhibit blocks
        assert "t" not in engine.enabled_transitions(
            net, Marking({"g1": [_tok("g1")], "g2": [_tok("g2")]})
        )
