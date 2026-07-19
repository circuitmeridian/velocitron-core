"""Native timed transitions (ADR 0018): the ``timer`` declaration + ``Engine.tick``.

The red-phase contract for the native timed-transition feature. A transition
may declare ``timer: {clock, cel, bind?}`` — a declarative temporal enablement
condition evaluated per candidate binding against the clock place's token
(reserved CEL variable ``clock``) plus explicitly bound input tokens (the
``bind`` variables). The deadline lives in the token; the engine holds no
timer state; time advances only via ``inject_token`` (ADR 0013), and
``Engine.tick`` = one ``inject_token(replace=True)`` + ``run`` is the
engine-owned re-evaluation loop.

Pinned verbs and topology (per the lock-the-coverage red discipline):

- invalid timer CEL fails **at parse** (``NetValidationError``), not at
  enablement (D6's compile-at-parse verb);
- the timer is evaluated **per candidate binding** — an unmatured token
  *earlier* in insertion order does not mask a matured later one (the
  multi-instance isolation topology: two deadline-carrying tokens, one clock);
- the timer is evaluated **before the guard** — an unmatured binding never
  reaches the guard;
- composition merge rewrites the timer's **place values** (alias-qualified and
  fusion-rewritten), never the CEL string.

Fails until the ``timer`` schema field, parser validation, engine enablement
clause, and ``Engine.tick`` land.
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import (
    GuardHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.composition import merge_nets
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import NetValidationError, parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token, Wire

from _cel_adapters import ADAPTER_IDS, adapters

# ── Shared helpers ──────────────────────────────────────────────────────


def _tok(t: str, **data: Any) -> Token:
    return Token(type=t, data=dict(data))


def _emit_to(dest: str, token: Token):
    def _handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        return {
            "status": "completed",
            "outputTokens": {dest: [token]},
            "error": None,
            "metadata": {},
        }

    return _handler


def _fail_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "failed",
        "outputTokens": {},
        "error": {"type": "SinkDown", "message": "sink unavailable"},
        "metadata": {},
    }


def _engine(journal: JsonlJournal | None = None, **kwargs: Any) -> Engine:
    """Engine with a ``clear`` handler emitting a ``done`` into ``cleared``.

    For tests whose path INVOKES the handler (``fire``/``tick``/``run``);
    enablement-only tests use ``_bare_engine`` (register only handlers the
    code path invokes — enablement never resolves the transition handler).
    """
    reg = HandlerRegistry()
    reg.register_transition("clear", _emit_to("cleared", _tok("done")))
    return Engine(reg, journal=journal, **kwargs)


def _bare_engine(cel_adapter: Any | None = None) -> Engine:
    """Engine with an empty registry, for enablement-only probes."""
    return Engine(HandlerRegistry(), cel_adapter=cel_adapter)


_COOLDOWN_TIMER = {
    "clock": "clock",
    "cel": "clock.now >= latch.fired_at + latch.cooldown_s",
    "bind": {"latch": "latch"},
}


def _cooldown_doc(timer: dict[str, Any] | None = _COOLDOWN_TIMER) -> dict[str, Any]:
    """The canonical timed net: ``clear`` consumes a latch, gated on a clock.

    The clock place is referenced ONLY by the timer declaration (no read arc):
    the timer itself is the clock dependency. Every place is resolved by the
    surface under test — clock by the timer, latch by the consume arc + bind,
    cleared by the produce arc the fired token is asserted into.
    """
    transition: dict[str, Any] = {"name": "clear", "handler": "clear"}
    if timer is not None:
        transition["timer"] = timer
    return {
        "name": "cooldown-net",
        "places": [
            {"name": "clock", "accepts": ["tick"]},
            {"name": "latch", "accepts": ["latch_tok"]},
            {"name": "cleared", "accepts": ["done"]},
        ],
        "transitions": [transition],
        "arcs": [
            {
                "from": {"place": "latch"},
                "to": {"transition": "clear"},
                "consume": {"type": "latch_tok"},
            },
            {
                "from": {"transition": "clear"},
                "to": {"place": "cleared"},
                "produce": {"type": "done", "destination": "cleared"},
            },
        ],
    }


def _cooldown_net(timer: dict[str, Any] | None = _COOLDOWN_TIMER) -> Net:
    return parse_net(_cooldown_doc(timer))


def _latch(fired_at: int, cooldown_s: int) -> Token:
    return _tok("latch_tok", fired_at=fired_at, cooldown_s=cooldown_s)


def _unmatured_marking() -> Marking:
    """The shared before-deadline shape: clock at 0 vs a latch maturing at 100."""
    return Marking({"clock": [_tok("tick", now=0)], "latch": [_latch(0, 100)]})


def _journal_records(journal: JsonlJournal) -> list[dict[str, Any]]:
    """The journal's buffered records (one seam for the private access)."""
    return journal._records  # pyright: ignore[reportPrivateUsage]


# ── Parsing ──────────────────────────────────────────────────────────────


class TestTimerParsing:
    """The ``timer`` declaration parses onto ``Transition.timer``."""

    def test_timer_parsed_onto_transition(self):
        # given: a net whose transition declares a full timer
        net = _cooldown_net()
        # then: the parsed transition carries the declaration
        timer = net.transitions[0].timer
        assert timer is not None
        assert timer.clock == "clock"
        # and: the CEL string and bind map round-trip verbatim
        assert timer.cel == "clock.now >= latch.fired_at + latch.cooldown_s"
        assert timer.bind == {"latch": "latch"}

    def test_timer_absent_is_none(self):
        # given: a net whose transition declares no timer
        net = _cooldown_net(timer=None)
        # then: the field defaults to None (all existing nets unchanged)
        assert net.transitions[0].timer is None

    def test_bind_is_optional(self):
        # given: a clock-only timer (condition over the clock token alone)
        net = _cooldown_net(timer={"clock": "clock", "cel": "clock.now >= 100"})
        # then: bind parses as None
        timer = net.transitions[0].timer
        assert timer is not None
        assert timer.bind is None

    def test_maturity_is_optional_runtime_scheduler_metadata(self):
        # given: a timer declaring the next candidate maturity in the same CEL environment
        net = _cooldown_net(
            timer={
                "clock": "clock",
                "cel": "clock.now >= latch.fired_at + latch.cooldown_s",
                "bind": {"latch": "latch"},
                "maturity": "latch.fired_at + latch.cooldown_s",
            }
        )
        # then: parsing preserves the scheduling expression without changing cel
        timer = net.transitions[0].timer
        assert timer is not None
        assert timer.maturity == "latch.fired_at + latch.cooldown_s"


class TestTimerParseValidation:
    """Structural validations 9-11 (net-schema.md) reject at PARSE time."""

    def test_unknown_clock_place_fails_at_parse(self):
        # given: a timer naming a place the net does not declare
        doc = _cooldown_doc(timer={"clock": "ghost", "cel": "clock.now >= 100"})
        # then: parsing fails (validation 9)
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_bind_place_without_binding_arc_fails_at_parse(self):
        # given: a bind value naming a declared place that feeds the
        # transition through NO consume- or read-mode arc (cleared is only a
        # produce destination)
        doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= item.deadline",
                "bind": {"item": "cleared"},
            }
        )
        # then: parsing fails (validation 10 — the variable could never
        # resolve to a bound token)
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_bind_place_fed_only_by_inhibit_arc_fails_at_parse(self):
        # given: a bind value naming a place connected to the transition ONLY
        # via an inhibit arc — inhibit arcs contribute no tokens to the
        # binding, so the variable could never resolve
        doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= item.deadline",
                "bind": {"item": "gate"},
            }
        )
        doc["places"].append({"name": "gate", "accepts": ["deadline"]})
        doc["arcs"].append(
            {
                "from": {"place": "gate"},
                "to": {"transition": "clear"},
                "consume": {"type": "deadline", "mode": "inhibit"},
            }
        )
        # then: parsing fails (validation 10 pins consume/read-mode-only)
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_bind_key_clock_is_reserved(self):
        # given: a bind map claiming the reserved 'clock' variable
        doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= 100",
                "bind": {"clock": "latch"},
            }
        )
        # then: parsing fails (validation 10)
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_invalid_timer_cel_fails_at_parse(self):
        # given: a timer whose CEL is a syntax error
        doc = _cooldown_doc(timer={"clock": "clock", "cel": "clock.now >="})
        # then: parsing fails (validation 11 — compile at parse, D6; the net
        # never loads, so the failure cannot be deferred to enablement)
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_invalid_timer_maturity_cel_fails_at_parse(self):
        # given: a malformed scheduler expression
        doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= 100",
                "maturity": "latch.fired_at +",
            }
        )
        # then: it is invalid net structure, not a deferred Runtime failure
        with pytest.raises(NetValidationError):
            parse_net(doc)

    def test_bind_key_must_be_identifier(self):
        # given: a bind key that is not a simple identifier
        doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= 100",
                "bind": {"not-an-identifier": "latch"},
            }
        )
        # then: the JSON schema's patternProperties rejects it at parse
        with pytest.raises(NetValidationError):
            parse_net(doc)

    @pytest.mark.parametrize("missing", ["clock", "cel"])
    def test_timer_requires_clock_and_cel(self, missing: str):
        # given: a timer missing a required field
        timer = {"clock": "clock", "cel": "clock.now >= 100"}
        del timer[missing]
        doc = _cooldown_doc(timer=timer)
        # then: the JSON schema rejects it at parse
        with pytest.raises(NetValidationError):
            parse_net(doc)


# ── Time-gated enablement ────────────────────────────────────────────────


class TestTimedEnablement:
    """A timed transition is enabled only when its condition holds."""

    def test_not_enabled_before_deadline(self):
        # given: a latch whose cooldown has not elapsed against the clock
        net = _cooldown_net()
        engine = _bare_engine()
        marking = _unmatured_marking()
        # then: the transition is not enabled (arcs alone would enable it)
        assert "clear" not in engine.enabled_transitions(net, marking)

    def test_enabled_after_clock_advance(self):
        # given: an unmatured timed transition
        net = _cooldown_net()
        engine = _bare_engine()
        marking = _unmatured_marking()
        # when: the clock advances past the deadline (the ADR 0013 seam)
        marking, _rec = engine.inject_token(
            net, marking, "clock", _tok("tick", now=150), attempt=0, replace=True
        )
        # then: the transition matured
        assert "clear" in engine.enabled_transitions(net, marking)

    def test_empty_clock_place_means_not_enabled(self):
        # given: a satisfiable consume arc but NO token in the clock place
        net = _cooldown_net()
        engine = _bare_engine()
        marking = Marking({"latch": [_latch(0, 0)]})
        # then: no time reference => not matured => not enabled
        assert "clear" not in engine.enabled_transitions(net, marking)

    def test_timer_eval_error_degrades_to_not_enabled(self):
        """A runtime CEL eval error yields condition-false, never a crash.

        Construction-bite: the try/except around the timer's CEL evaluation is
        the sole barrier to ``enabled_transitions`` raising here — the latch
        token lacks the ``fired_at``/``cooldown_s`` fields the expression
        names, which is an eval error in every backend.
        """
        # given: a bound token missing the fields the timer expression names
        net = _cooldown_net()
        engine = _bare_engine()
        marking = Marking(
            {"clock": [_tok("tick", now=1000)], "latch": [_tok("latch_tok")]}
        )
        # then: the eval error degrades to not-enabled (D6-symmetric)
        assert "clear" not in engine.enabled_transitions(net, marking)

    def test_fire_unmatured_yields_not_enabled_failure(self):
        # given: an unmatured timed transition (a bare engine suffices: the
        # not-enabled failure lands before any handler resolution)
        net = _cooldown_net()
        engine = _bare_engine()
        marking = _unmatured_marking()
        # when: firing it directly anyway
        new_marking, record = engine.fire(net, marking, "clear", attempt=0)
        # then: a failed NotEnabled record, marking unchanged (atomic)
        assert record["status"] == "failed"
        assert record["error"] is not None and record["error"]["type"] == "NotEnabled"
        assert new_marking is marking

    def test_clock_only_timer_needs_no_bind(self):
        # given: a timer over the clock token alone
        net = _cooldown_net(timer={"clock": "clock", "cel": "clock.now >= 100"})
        engine = _bare_engine()
        early = Marking({"clock": [_tok("tick", now=50)], "latch": [_latch(0, 0)]})
        late = Marking({"clock": [_tok("tick", now=150)], "latch": [_latch(0, 0)]})
        # then: enablement follows the clock alone
        assert "clear" not in engine.enabled_transitions(net, early)
        assert "clear" in engine.enabled_transitions(net, late)

    def test_timer_evaluated_before_guard(self):
        """An unmatured binding never reaches the guard (pure before impure)."""
        # given: a timed + guarded transition and a guard that records calls
        doc = _cooldown_doc()
        doc["transitions"][0]["guard"] = "spy_guard"
        net = parse_net(doc)
        calls: list[dict[str, list[Token]]] = []

        def _spy_guard(inp: GuardHandlerInput) -> bool:
            calls.append(inp["inputTokens"])
            return True

        # and: only the guard is registered — an enablement probe never
        # resolves the transition handler
        reg = HandlerRegistry()
        reg.register_guard("spy_guard", _spy_guard)
        engine = Engine(reg)

        # when: probing enablement with an unmatured binding
        unmatured = _unmatured_marking()
        enabled_before = engine.enabled_transitions(net, unmatured)

        # then: not enabled, and the guard was never consulted — the timer
        # gated first (pure before possibly-impure)
        assert "clear" not in enabled_before
        assert calls == []

        # when: probing with a matured binding
        matured = Marking({"clock": [_tok("tick", now=200)], "latch": [_latch(0, 100)]})
        enabled_after = engine.enabled_transitions(net, matured)

        # then: enabled, and the guard saw exactly the matured binding
        assert "clear" in enabled_after
        assert calls == [{"latch": [_latch(0, 100)]}]


# ── Multi-instance deadline isolation ────────────────────────────────────


class TestMultiInstanceIsolation:
    """Two tokens with distinct token-carried deadlines against one clock."""

    def test_matured_token_selected_past_unmatured_earlier_one(self):
        # given: the UNMATURED latch first in insertion order, the matured one
        # second — an implementation that evaluates the timer only against the
        # first candidate binding fails here (per-binding evaluation topology)
        net = _cooldown_net()
        engine = _bare_engine()
        marking = Marking(
            {
                "clock": [_tok("tick", now=100)],
                "latch": [_latch(0, 200), _latch(0, 50)],
            }
        )
        # when: probing enablement and selecting the binding
        enabled = engine.enabled_transitions(net, marking)
        binding = engine.select_binding(net, "clear", marking)
        # then: the transition is enabled and binds the matured token
        assert "clear" in enabled
        assert binding == {"latch": [_latch(0, 50)]}

    def test_fire_consumes_only_the_matured_token(self):
        # given: one matured and one unmatured latch
        net = _cooldown_net()
        engine = _engine()
        marking = Marking(
            {
                "clock": [_tok("tick", now=100)],
                "latch": [_latch(0, 200), _latch(0, 50)],
            }
        )
        # when: firing
        marking, record = engine.fire(net, marking, "clear", attempt=0)
        # then: the matured token is consumed; the unmatured one remains
        assert record["status"] == "completed"
        assert list(marking.get("latch", [])) == [_latch(0, 200)]
        # and: the clock token is untouched (the timer reads, never consumes)
        assert list(marking.get("clock", [])) == [_tok("tick", now=100)]
        # when: the clock advances past the remaining deadline
        marking, _rec = engine.inject_token(
            net, marking, "clock", _tok("tick", now=250), attempt=1, replace=True
        )
        # then: the second instance matures independently
        assert "clear" in engine.enabled_transitions(net, marking)


# ── Engine-owned re-evaluation: tick ─────────────────────────────────────


class TestTick:
    """``tick`` = advance the clock + run to quiescence, engine-owned."""

    def test_tick_fires_matured_transition(self):
        # given: an unmatured timed net
        net = _cooldown_net()
        engine = _engine()
        marking = _unmatured_marking()
        # when: one tick past the deadline — no consumer-side loop
        marking = engine.tick(net, marking, "clock", _tok("tick", now=150))
        # then: the matured transition fired to quiescence
        assert list(marking.get("latch", [])) == []
        assert list(marking.get("cleared", [])) == [_tok("done")]
        # and: the clock was advanced by replacement (singleton pattern)
        assert list(marking.get("clock", [])) == [_tok("tick", now=150)]

    def test_one_tick_matures_several_deadlines(self):
        # given: two latches whose deadlines both fall inside one advance
        net = _cooldown_net()
        engine = _engine()
        marking = Marking(
            {
                "clock": [_tok("tick", now=0)],
                "latch": [_latch(0, 50), _latch(0, 80)],
            }
        )
        # when: a single tick past both
        marking = engine.tick(net, marking, "clock", _tok("tick", now=100))
        # then: both instances fired (one advance, looped re-evaluation)
        assert list(marking.get("latch", [])) == []
        assert list(marking.get("cleared", [])) == [_tok("done"), _tok("done")]

    def test_tick_before_deadline_fires_nothing_and_does_not_spin(self):
        # given: a journal to observe the event stream
        journal = JsonlJournal()
        net = _cooldown_net()
        engine = _engine(journal=journal)
        marking = _unmatured_marking()
        # when: a tick short of the deadline
        marking = engine.tick(net, marking, "clock", _tok("tick", now=50))
        # then: nothing fired — the unmatured transition is NOT enabled, so
        # run reaches quiescence immediately instead of spinning to max_steps
        assert list(marking.get("latch", [])) == [_latch(0, 100)]
        records = _journal_records(journal)
        assert len(records) == 1
        assert records[0]["injectionId"] == "cooldown-net/@inject/clock/0"

    def test_tick_journal_shares_one_sequence_stream(self):
        # given: a journal
        journal = JsonlJournal()
        net = _cooldown_net()
        engine = _engine(journal=journal)
        marking = _unmatured_marking()
        # when: a maturing tick
        engine.tick(net, marking, "clock", _tok("tick", now=150))
        # then: the injection and the firing occupy consecutive sequence slots
        records = _journal_records(journal)
        assert [r["sequence"] for r in records] == [0, 1]
        assert records[0]["kind"] == "update"
        assert records[1]["transition"] == "clear"

    def test_tick_inherits_failure_budget(self):
        # given: a matured timed transition whose handler persistently fails,
        # under an opt-in failure budget (ADR 0015)
        journal = JsonlJournal()
        net = _cooldown_net()
        reg = HandlerRegistry()
        reg.register_transition("clear", _fail_handler)
        engine = Engine(reg, journal=journal, max_consecutive_failures=2)
        marking = _unmatured_marking()
        # when: a maturing tick
        marking = engine.tick(net, marking, "clock", _tok("tick", now=150))
        # then: the transition exhausted after exactly 2 failed firings
        # (quiescence-by-exhaustion, not a spin to max_steps)
        records = _journal_records(journal)
        failed = [r for r in records if r.get("status") == "failed"]
        assert len(failed) == 2
        # and: atomic rollback kept the latch in place
        assert list(marking.get("latch", [])) == [_latch(0, 100)]


# ── Replay determinism ───────────────────────────────────────────────────


def _records_without_timestamps(journal: JsonlJournal) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in record.items() if k != "timestamps"}
        for record in _journal_records(journal)
    ]


class TestReplayDeterminism:
    """Same net + same injected time = the same journal (D5, ADR 0013)."""

    def test_identical_tick_drives_produce_identical_journals(self):
        # given: two independent engines with independent journals
        def _drive(journal: JsonlJournal) -> None:
            net = _cooldown_net()
            engine = _engine(journal=journal)
            marking = _unmatured_marking()
            # when: an identical drive — a short tick, then a maturing tick
            marking = engine.tick(net, marking, "clock", _tok("tick", now=50))
            engine.tick(net, marking, "clock", _tok("tick", now=150), attempt=1)

        j1, j2 = JsonlJournal(), JsonlJournal()
        _drive(j1)
        _drive(j2)
        # then: the journals agree record-for-record, excluding timestamps
        assert _records_without_timestamps(j1) == _records_without_timestamps(j2)
        # and: the stream carries both injections and the one firing
        kinds = [
            r.get("kind", r.get("transition")) for r in _records_without_timestamps(j1)
        ]
        assert kinds == ["update", "update", "clear"]


# ── Backend portability ──────────────────────────────────────────────────


class TestTimerBackendPortability:
    """The nested-map timer environment evaluates identically across all
    three CEL backends (the pure-Python backend requires the adapter to
    convert activations to celtypes — this parametrization is what forces
    that conversion)."""

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_timed_enablement_flips_on_every_backend(self, adapter: Any):
        # given: an engine pinned to one backend
        net = _cooldown_net()
        engine = _bare_engine(cel_adapter=adapter)
        early = _unmatured_marking()
        late = Marking({"clock": [_tok("tick", now=150)], "latch": [_latch(0, 100)]})
        # then: the cross-token nested-map condition gates identically
        assert "clear" not in engine.enabled_transitions(net, early)
        assert "clear" in engine.enabled_transitions(net, late)


# ── Composition merge ────────────────────────────────────────────────────


class TestCompositionMergeRewritesTimer:
    """merge_nets rewrites timer place values, never the CEL string."""

    def test_merge_alias_qualifies_timer_places(self):
        # given: a timed net merged under an alias, no wires
        net = _cooldown_net(
            timer={
                "clock": "clock",
                "cel": "clock.now >= latch.fired_at + latch.cooldown_s",
                "bind": {"latch": "latch"},
                "maturity": "latch.fired_at + latch.cooldown_s",
            }
        )
        # when: merging
        merged = merge_nets({"chip": net}, wires=[])
        # then: the timer's place values are alias-qualified
        timer = merged.transitions[0].timer
        assert timer is not None
        assert timer.clock == "chip.clock"
        assert timer.bind == {"latch": "chip.latch"}
        # and: the CEL string is untouched (the composition-safety point of
        # the bind indirection)
        assert timer.cel == "clock.now >= latch.fired_at + latch.cooldown_s"
        assert timer.maturity == "latch.fired_at + latch.cooldown_s"

    def test_merged_timed_net_fires_against_qualified_marking(self):
        # given: the merged net and a marking under qualified place names
        # (the handler deposits by the merge-resolved destination name)
        net = _cooldown_net()
        merged = merge_nets({"chip": net}, wires=[])
        reg = HandlerRegistry()
        reg.register_transition("clear", _emit_to("chip.cleared", _tok("done")))
        engine = Engine(reg)
        marking = Marking(
            {
                "chip.clock": [_tok("tick", now=150)],
                "chip.latch": [_latch(0, 100)],
            }
        )
        # then: the merged timed transition matures and fires end to end
        assert "chip.clear" in engine.enabled_transitions(merged, marking)
        marking, record = engine.fire(merged, marking, "chip.clear", attempt=0)
        assert record["status"] == "completed"
        assert list(marking.get("chip.cleared", [])) == [_tok("done")]

    def test_fusion_rewrites_timer_clock_to_fused_place(self):
        # given: a source net exposing a clock through an output port, wired
        # to a chip whose timed transition names its input clock port
        src = parse_net(
            {
                "name": "src",
                "places": [
                    {
                        "name": "clock_out",
                        "accepts": ["tick"],
                        "port": {"direction": "output", "type": "tick"},
                    }
                ],
                "transitions": [],
                "arcs": [],
            }
        )
        chip_doc = _cooldown_doc(
            timer={
                "clock": "clock",
                "cel": "clock.now >= latch.fired_at + latch.cooldown_s",
                "bind": {"latch": "latch"},
            }
        )
        chip_doc["places"][0]["port"] = {"direction": "input", "type": "tick"}
        chip = parse_net(chip_doc)
        # when: merging with the clock wire
        merged = merge_nets(
            {"src": src, "chip": chip},
            wires=[
                Wire(
                    from_net="src",
                    from_port="clock_out",
                    to_net="chip",
                    to_port="clock",
                )
            ],
        )
        # then: the fused place is named after the source port, and the
        # chip's timer follows the fusion rewrite (like arc endpoints)
        timer = merged.transitions[0].timer
        assert timer is not None
        assert timer.clock == "src.clock_out"
        assert timer.bind == {"latch": "chip.latch"}
