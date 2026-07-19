"""Clock/timer seam tests (``Engine.inject_token`` + CEL over timestamp data).

The red-phase contract for the minimal timer/clock seam. When this seam
landed, velocitron had no native timed transitions (they arrived later — ADR
0016, built on this seam; see ``test_timed_transitions.py``); timing enters a
net as **token data** (a clock/tick or deadline token carrying epoch-seconds
fields) plus a thin runtime wrapper that advances the clock and re-checks
enablement. Before the seam, that wrapper had to reach into the marking
directly, and its clock advances were invisible to the journal — so replay
was not deterministic across injected time.

This pins the smallest engine seam that fixes both:

- **``Engine.inject_token``** — a sanctioned way for a consumer to inject a new
  token into a place (the deadline-token pattern) or replace a place's contents
  (``replace=True`` — the singleton clock-advance pattern), returning a new
  persistent :class:`Marking` and an injection record. Consumers use this
  instead of mutating the marking; the type is validated against the place's
  ``accepts``.
- **Journal recording** — each injection is emitted through a dedicated
  ``record_injection`` hook as an explicit journal entry (deterministic
  ``injectionId``), sharing one sequence stream with firings so replay is
  deterministic across injected time.
- **Enablement re-evaluation** — after an injection the consumer re-runs
  ``enabled_transitions``; a timed transition gated on a deadline/clock token
  flips to enabled once the token is injected/advanced.
- **CEL over timestamp fields (single-token)** — an epoch-seconds comparison
  like ``now - enqueued_at > 10`` or ``now >= deadline_at`` evaluated against a
  single token's ``data`` works identically across all three CEL backends. A
  CROSS-token comparison (a clock token vs a work token) is NOT expressible as a
  single-token predicate and stays a guard — pinned here as the boundary.

Fails until ``Engine.inject_token`` and the ``record_injection`` journal hook
land.
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.cel import CelEvalError
from velocitron.contract import (
    GuardHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

from _cel_adapters import ADAPTER_IDS, adapters

# ── Shared helpers ──────────────────────────────────────────────────────


def _net(
    name: str,
    places: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
) -> Net:
    return parse_net(
        {"name": name, "places": places, "transitions": transitions, "arcs": arcs}
    )


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


def _engine(journal: JsonlJournal | None = None, **transitions: Any) -> Engine:
    reg = HandlerRegistry()
    for name, fn in transitions.items():
        reg.register_transition(name, fn)
    return Engine(reg, journal=journal)


# ── inject_token: marking mechanics ──────────────────────────────────────


class TestInjectTokenMarking:
    """``inject_token`` adds or replaces a token and returns a new Marking."""

    @staticmethod
    def _clock_net() -> Net:
        return _net(
            "clock-net",
            places=[
                {"name": "clock", "accepts": ["tick"]},
                {"name": "work", "accepts": ["job"]},
            ],
            transitions=[{"name": "noop", "handler": "noop"}],
            arcs=[
                {
                    "from": {"place": "work"},
                    "to": {"transition": "noop"},
                    "consume": {"type": "job"},
                }
            ],
        )

    def test_inject_appends_token(self):
        # given: an empty clock place
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        marking = Marking({"work": [_tok("job")]})
        # when: injecting a tick
        new_marking, _record = engine.inject_token(
            net, marking, "clock", _tok("tick", now=100), attempt=0
        )
        # then: the tick lands in clock; the original marking is unchanged
        assert list(new_marking.get("clock", [])) == [_tok("tick", now=100)]
        assert list(marking.get("clock", [])) == []

    def test_inject_append_preserves_existing_tokens(self):
        # given: a clock place that already holds a tick
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        marking = Marking({"clock": [_tok("tick", now=100)]})
        # when: injecting a second tick without replace
        new_marking, record = engine.inject_token(
            net, marking, "clock", _tok("tick", now=200), attempt=0
        )
        # then: both ticks are present and the record is an "inject"
        assert list(new_marking.get("clock", [])) == [
            _tok("tick", now=100),
            _tok("tick", now=200),
        ]
        assert record["kind"] == "inject"
        assert record["replaced"] == []

    def test_inject_replace_replaces_place_contents(self):
        # given: a clock place holding a stale tick
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        marking = Marking({"clock": [_tok("tick", now=100)]})
        # when: advancing the clock with replace=True
        new_marking, record = engine.inject_token(
            net, marking, "clock", _tok("tick", now=200), attempt=1, replace=True
        )
        # then: only the advanced tick remains; the record is an "update"
        assert list(new_marking.get("clock", [])) == [_tok("tick", now=200)]
        assert record["kind"] == "update"
        assert record["replaced"] == [_tok("tick", now=100)]

    def test_inject_preserves_structural_sharing(self):
        # given: an unrelated place
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        job = _tok("job")
        marking = Marking({"work": [job]})
        # when: injecting into clock
        new_marking, _record = engine.inject_token(
            net, marking, "clock", _tok("tick", now=1), attempt=0
        )
        # then: the untouched place is shared, not copied
        assert new_marking.get("work") is marking.get("work")

    def test_inject_unknown_place_raises(self):
        # given: a place not in the net
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        # then: injecting into it is a programmer error
        with pytest.raises(ValueError):
            engine.inject_token(net, Marking(), "ghost", _tok("tick"), attempt=0)

    def test_inject_type_not_accepted_raises(self):
        # given: a token whose type the place does not accept
        net = self._clock_net()
        engine = _engine(noop=_emit_to("work", _tok("job")))
        # then: injecting it is a programmer error
        with pytest.raises(ValueError):
            engine.inject_token(net, Marking(), "clock", _tok("job"), attempt=0)


# ── inject_token: journal recording ──────────────────────────────────────


def _gate_net() -> Net:
    """A net whose ``escalate`` consumes a deadline token (absent by default) —
    the timed-transition-gated-on-injection shape used across several tests."""
    return _net(
        "gate-net",
        places=[
            {"name": "deadline_gate", "accepts": ["deadline"]},
            {"name": "work", "accepts": ["job"]},
            {"name": "escalated", "accepts": ["alert"]},
        ],
        transitions=[{"name": "escalate", "handler": "escalate"}],
        arcs=[
            {
                "from": {"place": "deadline_gate"},
                "to": {"transition": "escalate"},
                "consume": {"type": "deadline"},
            },
            {
                "from": {"place": "work"},
                "to": {"transition": "escalate"},
                "consume": {"type": "job"},
            },
            {
                "from": {"transition": "escalate"},
                "to": {"place": "escalated"},
                "produce": {"type": "alert", "destination": "escalated"},
            },
        ],
    )


class TestInjectTokenJournal:
    """Injections are explicit journal entries sharing the firing sequence."""

    def test_injection_recorded_with_deterministic_id(self):
        # given: a journal attached to the engine
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal, escalate=_emit_to("escalated", _tok("alert")))
        # when: injecting a deadline token
        _new, record = engine.inject_token(
            net, Marking(), "deadline_gate", _tok("deadline"), attempt=3
        )
        # then: the record has a deterministic injectionId and is journaled
        assert record["injectionId"] == "gate-net/@inject/deadline_gate/3"
        assert record["netId"] == "gate-net"
        assert record["place"] == "deadline_gate"
        records = journal._records  # pyright: ignore[reportPrivateUsage]
        assert records[-1]["injectionId"] == record["injectionId"]
        assert records[-1]["sequence"] == 0

    def test_injection_record_carries_first_class_attempt(self):
        """``attempt`` is a first-class ``InjectionRecord`` field, not only
        string-embedded in ``injectionId`` — so replay tooling reads it
        directly instead of parsing the id.

        Construction-bite: the engine populating ``record["attempt"]`` is the
        only barrier to a ``KeyError`` here; reverting the engine to omit the
        field (or dropping it from the ``InjectionRecord`` TypedDict) makes this
        test error on the subscript. The paired assertion pins that the
        first-class field equals the value the id embeds (the id format is
        unchanged for backward compat)."""
        # given: a journal attached to the engine
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal)
        # when: injecting a deadline token at a specific attempt
        _new, record = engine.inject_token(
            net, Marking(), "deadline_gate", _tok("deadline"), attempt=7
        )
        # then: attempt is a first-class field, equal to the id-embedded value
        assert record["attempt"] == 7
        assert record["injectionId"] == "gate-net/@inject/deadline_gate/7"

    def test_injection_and_firing_share_one_sequence_stream(self):
        # given: a journal, a work token, and a not-yet-enabled escalate
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal, escalate=_emit_to("escalated", _tok("alert")))
        marking = Marking({"work": [_tok("job")]})
        # when: inject the deadline (seq 0), then fire escalate (seq 1)
        marking, _rec = engine.inject_token(
            net, marking, "deadline_gate", _tok("deadline"), attempt=0
        )
        engine.fire(net, marking, "escalate", attempt=1)
        # then: the injection and the firing occupy consecutive sequence slots
        records = journal._records  # pyright: ignore[reportPrivateUsage]
        assert [r["sequence"] for r in records] == [0, 1]
        assert records[0]["injectionId"] == "gate-net/@inject/deadline_gate/0"
        assert records[1]["transition"] == "escalate"

    def test_inject_without_journal_still_returns_record(self):
        # given: no journal attached
        net = _gate_net()
        engine = _engine(escalate=_emit_to("escalated", _tok("alert")))
        # then: inject_token still returns a usable record (recording is optional)
        _new, record = engine.inject_token(
            net, Marking(), "deadline_gate", _tok("deadline"), attempt=0
        )
        assert record["place"] == "deadline_gate"


# ── inject_tokens: batch injection ────────────────────────────────────────


class TestInjectTokensBatch:
    """``inject_tokens`` injects a batch of (place, token) pairs in one
    journal-consistent step: all-or-nothing validation, one InjectionRecord
    per token on consecutive sequence slots, new persistent Marking back."""

    def test_batch_appends_every_token_and_returns_records(self):
        # given: a net with two injectable places and an empty marking
        net = _gate_net()
        engine = _engine()
        marking = Marking()
        # when: injecting a deadline and a job in one batch
        new_marking, records = engine.inject_tokens(
            net,
            marking,
            [("deadline_gate", _tok("deadline")), ("work", _tok("job"))],
            attempt=0,
        )
        # then: every token landed in its place; the input marking is unchanged
        assert list(new_marking.get("deadline_gate", [])) == [_tok("deadline")]
        assert list(new_marking.get("work", [])) == [_tok("job")]
        assert list(marking.get("deadline_gate", [])) == []
        # and: one record per token, in placement order, all "inject" appends
        assert [r["place"] for r in records] == ["deadline_gate", "work"]
        assert all(r["kind"] == "inject" for r in records)
        assert all(r["attempt"] == 0 for r in records)

    def test_batch_emits_one_record_per_token_on_consecutive_sequences(self):
        # given: a journal attached to the engine
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal)
        # when: injecting three tokens (two into the same place) in one batch
        _new, records = engine.inject_tokens(
            net,
            Marking(),
            [
                ("work", _tok("job", id=1)),
                ("work", _tok("job", id=2)),
                ("deadline_gate", _tok("deadline")),
            ],
            attempt=2,
        )
        # then: the journal holds one record per token on consecutive slots
        journaled = journal._records  # pyright: ignore[reportPrivateUsage]
        assert [r["sequence"] for r in journaled] == [0, 1, 2]
        assert [r["place"] for r in journaled] == ["work", "work", "deadline_gate"]
        # and: each record keeps the single-injection id format (backward
        # compat); same-place entries share an id, disambiguated by sequence
        assert records[0]["injectionId"] == "gate-net/@inject/work/2"
        assert records[2]["injectionId"] == "gate-net/@inject/deadline_gate/2"

    def test_batch_folds_interleaved_places_without_reordering_or_replacing(self):
        # given: append and replace have established existing contents, plus an
        # untouched place whose persistent vector must remain shared
        net = _gate_net()
        engine = _engine()
        marking, _ = engine.inject_token(
            net, Marking(), "work", _tok("job", id="old"), attempt=0
        )
        marking, _ = engine.inject_token(
            net,
            marking,
            "deadline_gate",
            _tok("deadline", id="replacement"),
            attempt=1,
            replace=True,
        )
        marking = marking.set("escalated", [_tok("alert", id="shared")])
        untouched = marking["escalated"]

        # when: placements for the two touched places are interleaved
        new_marking, records = engine.inject_tokens(
            net,
            marking,
            [
                ("work", _tok("job", id=1)),
                ("deadline_gate", _tok("deadline", id=1)),
                ("work", _tok("job", id=2)),
                ("deadline_gate", _tok("deadline", id=2)),
            ],
            attempt=7,
        )

        # then: each place retains its existing contents and receives its
        # placements in input order
        assert [token.data["id"] for token in new_marking["work"]] == [
            "old",
            1,
            2,
        ]
        assert [token.data["id"] for token in new_marking["deadline_gate"]] == [
            "replacement",
            1,
            2,
        ]
        # and: records remain one-per-placement in global input order
        assert [record["place"] for record in records] == [
            "work",
            "deadline_gate",
            "work",
            "deadline_gate",
        ]
        assert [record["tokens"][0].data["id"] for record in records] == [
            1,
            1,
            2,
            2,
        ]
        assert all(record["kind"] == "inject" for record in records)
        assert all(record["attempt"] == 7 for record in records)
        # and: the untouched per-place vector is structurally shared
        assert new_marking["escalated"] is untouched

    def test_batch_rejection_preserves_input_identity_and_emits_nothing(self):
        # given: a valid placement follows an invalid placement, guarding
        # against implementations that only validate a leading prefix
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal)
        marking = Marking(
            {
                "work": [_tok("job", id="shared")],
                "escalated": [_tok("alert", id="untouched")],
            }
        )
        work = marking["work"]
        untouched = marking["escalated"]

        # when/then: validation rejects the entire batch
        with pytest.raises(ValueError):
            engine.inject_tokens(
                net,
                marking,
                [
                    ("work", _tok("job", id=1)),
                    ("ghost", _tok("deadline")),
                    ("work", _tok("job", id=2)),
                ],
                attempt=3,
            )

        # and: neither the persistent input nor the journal was affected
        assert marking["work"] is work
        assert marking["escalated"] is untouched
        assert journal._records == []  # pyright: ignore[reportPrivateUsage]

    def test_batch_invalid_entry_fails_whole_batch_with_no_side_effects(self):
        """All-or-nothing: validation of EVERY placement happens BEFORE any
        journal emission or marking change — a valid entry ordered before the
        invalid one must leave no trace.

        Construction-bite: the up-front validation loop (before the placement
        loop) is the only barrier here; an implementation that validates
        per-entry while placing would journal the valid first entry before
        raising on the second, failing the empty-journal assertion."""
        # given: a journal and a batch whose FIRST entry is valid but whose
        # second names an unknown place
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal)
        marking = Marking({"work": [_tok("job")]})
        # when/then: the whole batch is rejected
        with pytest.raises(ValueError):
            engine.inject_tokens(
                net,
                marking,
                [("work", _tok("job")), ("ghost", _tok("deadline"))],
                attempt=0,
            )
        # and: nothing was journaled and the marking is untouched
        assert journal._records == []  # pyright: ignore[reportPrivateUsage]
        assert list(marking.get("work", [])) == [_tok("job")]

    def test_batch_type_mismatch_anywhere_fails_whole_batch(self):
        # given: a batch whose last entry's token type the place rejects
        journal = JsonlJournal()
        net = _gate_net()
        engine = _engine(journal=journal)
        # when/then: the whole batch is rejected with nothing journaled
        with pytest.raises(ValueError):
            engine.inject_tokens(
                net,
                Marking(),
                [("deadline_gate", _tok("deadline")), ("work", _tok("alert"))],
                attempt=0,
            )
        # and: nothing was journaled
        assert journal._records == []  # pyright: ignore[reportPrivateUsage]


# ── Injection drives enablement re-evaluation ────────────────────────────


class TestInjectionDrivesEnablement:
    """Injecting/advancing a token flips a timed transition's enablement."""

    def test_injected_deadline_enables_timed_transition(self):
        # given: a net whose escalate consumes a deadline that is absent
        net = _gate_net()
        engine = _engine(escalate=_emit_to("escalated", _tok("alert")))
        marking = Marking({"work": [_tok("job")]})
        # then: escalate is not enabled until the deadline token exists
        assert "escalate" not in engine.enabled_transitions(net, marking)
        # when: the wrapper injects the deadline token
        marking, _rec = engine.inject_token(
            net, marking, "deadline_gate", _tok("deadline"), attempt=0
        )
        # then: escalate is now enabled
        assert "escalate" in engine.enabled_transitions(net, marking)

    def test_clock_advance_flips_cross_token_guard(self):
        # given: a cooldown net — clear READS the clock and CONSUMES the latch;
        # a guard compares the (read) clock token against the (consumed) latch.
        net = _net(
            "cooldown",
            places=[
                {"name": "clock", "accepts": ["tick"]},
                {"name": "latch", "accepts": ["latch_tok"]},
                {"name": "cleared", "accepts": ["done"]},
            ],
            transitions=[
                {"name": "clear", "handler": "clear", "guard": "cooldown_elapsed"}
            ],
            arcs=[
                {
                    "from": {"place": "clock"},
                    "to": {"transition": "clear"},
                    "consume": {"type": "tick", "mode": "read"},
                },
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
        )

        def _cooldown_elapsed(inp: GuardHandlerInput) -> bool:
            # cross-token: the clock's now vs the latch's deadline (a guard,
            # because a single-token CEL predicate can't see two tokens).
            tick = inp["inputTokens"]["clock"][0]
            latch = inp["inputTokens"]["latch"][0]
            return tick.data["now"] >= latch.data["fired_at"] + latch.data["cooldown"]

        reg = HandlerRegistry()
        reg.register_transition("clear", _emit_to("cleared", _tok("done")))
        reg.register_guard("cooldown_elapsed", _cooldown_elapsed)
        engine = Engine(reg)

        marking = Marking(
            {
                "clock": [_tok("tick", now=0)],
                "latch": [_tok("latch_tok", fired_at=0, cooldown=100)],
            }
        )
        # then: before the cooldown elapses, clear is not enabled
        assert "clear" not in engine.enabled_transitions(net, marking)
        # when: the wrapper advances the clock past the cooldown (replace)
        marking, _rec = engine.inject_token(
            net, marking, "clock", _tok("tick", now=200), attempt=1, replace=True
        )
        # then: the guard now holds and clear is enabled; the clock still holds
        # exactly one (advanced) tick
        assert "clear" in engine.enabled_transitions(net, marking)
        assert list(marking.get("clock", [])) == [_tok("tick", now=200)]


# ── CEL over timestamp fields (single-token, cross-backend) ──────────────


class TestCelTimestampComparison:
    """Epoch-seconds comparisons on ONE token's data hold across all backends.

    Coverage lock on the existing CEL adapters for the timer use case: the
    engine already evaluates inline CEL against a single token's ``data`` (D6),
    and integer epoch-seconds arithmetic/comparison is backend-portable. This
    blesses the single-token timestamp pattern (``now - enqueued_at > 10``,
    ``now >= deadline_at``) that the clock seam enables, and pins the boundary:
    a CROSS-token comparison is not expressible as a single-token predicate and
    stays a guard (see ``test_clock_advance_flips_cross_token_guard``).

    Passes against the existing CEL impl — this is a lock, not a red.
    """

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_elapsed_beyond_deadline_is_true(self, adapter: Any):
        # given: one token carrying now + enqueued_at (20s elapsed)
        program = adapter.compile("now - enqueued_at > 10")
        # then: the 10s deadline is exceeded
        assert adapter.eval(program, {"now": 100, "enqueued_at": 80}) is True

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_elapsed_within_deadline_is_false(self, adapter: Any):
        # given: one token, only 5s elapsed
        program = adapter.compile("now - enqueued_at > 10")
        # then: still within the 10s deadline
        assert adapter.eval(program, {"now": 85, "enqueued_at": 80}) is False

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_deadline_reached_is_true(self, adapter: Any):
        program = adapter.compile("now >= deadline_at")
        assert adapter.eval(program, {"now": 100, "deadline_at": 100}) is True

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_deadline_not_yet_reached_is_false(self, adapter: Any):
        program = adapter.compile("now >= deadline_at")
        assert adapter.eval(program, {"now": 99, "deadline_at": 100}) is False

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_reference_to_absent_second_token_field_is_eval_error(self, adapter: Any):
        # given: a comparison naming a field the single token's data lacks —
        # what a "compare against another token" predicate would need
        program = adapter.compile("now >= other_deadline_at")
        # then: it is an eval error across every backend (the engine degrades
        # this to predicate-false, D6), which is why a cross-token compare must
        # be a guard over the full binding, not a single-token predicate
        with pytest.raises(CelEvalError):
            adapter.eval(program, {"now": 100})
