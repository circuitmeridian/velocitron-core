"""Behavioral coverage for the asynchronous Runtime vertical slice."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from velocitron.engine import Engine
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.journal import JsonlJournal
from velocitron.registry import HandlerRegistry
from velocitron.runtime import (
    HandlerSpec,
    Lane,
    Runtime,
    RuntimeContext,
    TokenArrival,
    TokenBatch,
    TokenSource,
)
from velocitron.schema import (
    Arc,
    ConsumePattern,
    Marking,
    Net,
    Place,
    ProduceTemplate,
    Timer,
    Token,
    Transition,
)


AsyncHandler = Callable[
    [TransitionHandlerInput, RuntimeContext], Awaitable[TransitionHandlerOutput]
]


def _journal_records(journal: JsonlJournal) -> list[dict[str, Any]]:
    return journal._records  # pyright: ignore[reportPrivateUsage]


def _token(type_: str, **data: Any) -> Token:
    return Token(type_, data)


def _net(
    *,
    transitions: list[Transition],
    places: list[Place],
    arcs: list[Arc],
    marking: Marking,
) -> Net:
    return Net("runtime", places, transitions, arcs, initial_marking=marking)


def _consume(place: str, transition: str, type_: str) -> Arc:
    return Arc(
        from_place=place,
        to_transition=transition,
        consume=ConsumePattern(type_, None, "consume"),
    )


def _produce(transition: str, place: str, type_: str) -> Arc:
    return Arc(
        from_transition=transition,
        to_place=place,
        produce=ProduceTemplate(type_, place),
    )


def _completed(place: str, token: Token) -> AsyncHandler:
    async def handler(
        inp: TransitionHandlerInput, ctx: RuntimeContext
    ) -> TransitionHandlerOutput:
        del inp, ctx
        return {
            "status": "completed",
            "outputTokens": {place: [token]},
            "error": None,
            "metadata": {},
        }

    return handler


class _ManualClock:
    """Deterministic Clock fixture: time moves only when the test advances it."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = now
        self.sleep_requests: list[float] = []
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self._now

    async def sleep_until(self, when: float) -> None:
        self.sleep_requests.append(when)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._sleepers.append((when, future))
        if when <= self._now:
            future.set_result(None)
        await future

    def advance(self, now: float) -> None:
        assert now >= self._now
        self._now = now
        for when, future in self._sleepers:
            if when <= now and not future.done():
                future.set_result(None)

    async def wait_for_sleeps(self, count: int) -> None:
        for _ in range(100):
            if len(self.sleep_requests) >= count:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"clock did not receive {count} sleep requests")


async def _eventually(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


def test_runtime_rejects_handlerless_transitions_before_handler_map_validation() -> (
    None
):
    """Async execution requires every transition to bind a HandlerSpec."""
    # given: a handlerless core net and an unrelated handler map entry
    net = _net(
        transitions=[Transition("traditional")],
        places=[],
        arcs=[],
        marking=Marking(),
    )
    # when: constructing a Runtime
    # then: handlerlessness is reported deterministically before map mismatch
    with pytest.raises(
        ValueError,
        match=r"Runtime requires handlers; transitions without handlers: \['traditional'\]",
    ):
        Runtime(
            net=net,
            handlers={
                "extra": HandlerSpec(_completed("unused", _token("token")), "worker")
            },
            lanes={"worker": 1},
        )


def test_runtime_requires_maturity_for_native_timers() -> None:
    net = _net(
        transitions=[
            Transition(
                "timer",
                "timer",
                timer=Timer("clock", "clock.now >= 5"),
            )
        ],
        places=[Place("clock", ["tick"]), Place("done", ["done"])],
        arcs=[_produce("timer", "done", "done")],
        marking=Marking({"clock": [_token("tick", now=0)]}),
    )

    with pytest.raises(ValueError, match="timer.maturity"):
        Runtime(
            net=net,
            handlers={
                "timer": HandlerSpec(_completed("done", _token("done")), "timer")
            },
            lanes={"timer": 1},
        )


def test_timer_maturities_ignore_a_ready_timer_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A due timer is ready to fire, not an invalid scheduling candidate."""
    net = _net(
        transitions=[
            Transition(
                "due",
                "due",
                timer=Timer(
                    "clock",
                    "clock.now >= deadline.at",
                    {"deadline": "deadline"},
                    "deadline.at",
                ),
            )
        ],
        places=[Place("clock", ["tick"]), Place("deadline", ["deadline"])],
        arcs=[
            Arc(
                from_place="clock",
                to_transition="due",
                consume=ConsumePattern("tick", None, "read"),
            ),
            _consume("deadline", "due", "deadline"),
        ],
        marking=Marking(
            {
                "clock": [_token("tick", now=5)],
                "deadline": [_token("deadline", at=5)],
            }
        ),
    )
    assert net.initial_marking is not None

    caplog.set_level(logging.WARNING, logger="velocitron.engine")
    assert Engine(HandlerRegistry()).timer_maturities(net, net.initial_marking) == ()
    assert caplog.messages == []


def test_timer_maturity_behind_the_clock_is_excluded_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A poll whose deadline has elapsed: cel is false, maturity is in the past.

    Advancing to the maturity would not enable it (the deadline upper bound
    blocks it), so it is not a future wake candidate — excluded silently, not
    an unschedulable-maturity warning. This is the journal-replay case: a
    restored clock token can sit ahead of a token's stamped ``next_poll_at``.
    """
    # given: a poll timer gated on a lower bound (next_poll_at) and an upper
    # bound (deadline_at), with maturity at the lower bound, and a clock sitting
    # past both — the deadline has elapsed.
    net = _net(
        transitions=[
            Transition(
                "poll",
                "poll",
                timer=Timer(
                    "clock",
                    "clock.now >= work.next_poll_at && clock.now < work.deadline_at",
                    {"work": "waiting"},
                    "work.next_poll_at",
                ),
            )
        ],
        places=[Place("clock", ["tick"]), Place("waiting", ["work"])],
        arcs=[
            Arc(
                from_place="clock",
                to_transition="poll",
                consume=ConsumePattern("tick", None, "read"),
            ),
            Arc(
                from_place="waiting",
                to_transition="poll",
                consume=ConsumePattern("work", None, "read"),
            ),
        ],
        marking=Marking(
            {
                "clock": [_token("tick", now=5000)],
                "waiting": [_token("work", next_poll_at=30, deadline_at=3600)],
            }
        ),
    )
    assert net.initial_marking is not None

    # when: recomputing schedulable maturities
    caplog.set_level(logging.WARNING, logger="velocitron.engine")
    maturities = Engine(HandlerRegistry()).timer_maturities(
        net, net.initial_marking
    )

    # then: the past maturity is excluded, and no warning is logged
    assert maturities == ()
    assert caplog.messages == []


def test_runtime_past_deadline_poll_does_not_warn_and_timeout_supersedes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A live poll+timeout pair whose deadline has already elapsed.

    The Runtime's timer loop recomputes maturities on every mutation, so a
    lingering past-deadline wait would re-log "timer maturity unschedulable"
    each pass. The poll's cel is blocked by the deadline upper bound, so it is
    excluded silently; the deadline-gated timeout supersedes it. No warning, no
    spin.
    """
    async def scenario() -> None:
        # given: a poll (interval + deadline) and a deadline-gated timeout over
        # one waiting token, plus a blocker holding the shared lane so the
        # timeout cannot consume the token yet — the timer loop keeps observing
        # the elapsed wait. The clock sits past the deadline.
        clock = _ManualClock(now=5000)
        net = _net(
            # blocker is first so the first-found policy grabs the shared lane
            # before the timeout can, leaving the past-deadline token in place.
            transitions=[
                Transition("blocker", "blocker"),
                Transition(
                    "poll",
                    "poll",
                    timer=Timer(
                        "clock",
                        "clock.now >= work.next_poll_at && clock.now < work.deadline_at",
                        {"work": "waiting"},
                        "work.next_poll_at",
                    ),
                ),
                Transition(
                    "timeout",
                    "timeout",
                    timer=Timer(
                        "clock",
                        "clock.now >= work.deadline_at",
                        {"work": "waiting"},
                        "work.deadline_at",
                    ),
                ),
            ],
            places=[
                Place("clock", ["tick"]),
                Place("waiting", ["work"]),
                Place("done", ["done"]),
                Place("gate_in", ["gate"]),
                Place("gate_out", ["gate"]),
            ],
            arcs=[
                Arc(
                    from_place="clock",
                    to_transition="poll",
                    consume=ConsumePattern("tick", None, "read"),
                ),
                Arc(
                    from_place="waiting",
                    to_transition="poll",
                    consume=ConsumePattern("work", None, "read"),
                ),
                Arc(
                    from_place="clock",
                    to_transition="timeout",
                    consume=ConsumePattern("tick", None, "read"),
                ),
                _consume("waiting", "timeout", "work"),
                _produce("timeout", "done", "done"),
                _consume("gate_in", "blocker", "gate"),
                _produce("blocker", "gate_out", "gate"),
            ],
            marking=Marking(
                {
                    "clock": [_token("tick", now=5000)],
                    "waiting": [_token("work", next_poll_at=30, deadline_at=3600)],
                    "gate_in": [_token("gate")],
                }
            ),
        )
        poll_fired = 0
        blocker_running = asyncio.Event()
        release_blocker = asyncio.Event()

        async def poll_handler(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            nonlocal poll_fired
            poll_fired += 1
            return {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            }

        async def blocker_handler(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            blocker_running.set()
            await release_blocker.wait()
            return {
                "status": "completed",
                "outputTokens": {"gate_out": [_token("gate")]},
                "error": None,
                "metadata": {},
            }

        runtime = Runtime(
            net=net,
            handlers={
                "poll": HandlerSpec(poll_handler, "io"),
                "timeout": HandlerSpec(_completed("done", _token("done")), "io"),
                "blocker": HandlerSpec(blocker_handler, "io"),
            },
            lanes={"io": 1},
            clock=clock,
        )

        # when: the timer loop recomputes while the timeout is lane-blocked, so
        # the elapsed wait's poll binding is evaluated on the live path
        caplog.set_level(logging.WARNING, logger="velocitron.engine")
        await runtime.start()
        try:
            await asyncio.wait_for(blocker_running.wait(), timeout=1)
            for _ in range(10):
                await asyncio.sleep(0)
            # then: the lane-blocked, past-deadline poll logged nothing
            assert caplog.messages == []
            # release the lane so the timeout can supersede the poll
            release_blocker.set()
            await _eventually(lambda: len(runtime.marking.get("done", ())) == 1)
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            await runtime.stop()

        # then: the timeout superseded the poll — the poll never fired, the
        # waiting token is consumed, and nothing was logged as unschedulable
        assert poll_fired == 0
        assert list(runtime.marking.get("waiting", ())) == []
        assert len(runtime.marking.get("done", ())) == 1
        assert caplog.messages == []

    asyncio.run(scenario())


def test_reservation_settlement_preserves_sync_rollback_contract() -> None:
    net = _net(
        transitions=[Transition("work", "work")],
        places=[Place("in", ["job"]), Place("out", ["done"])],
        arcs=[_consume("in", "work", "job"), _produce("work", "out", "done")],
        marking=Marking({"in": [_token("job", id="a")]}),
    )
    engine = Engine(HandlerRegistry())

    assert net.initial_marking is not None
    reservation = engine.reserve(net, net.initial_marking, "work", attempt=0)

    assert reservation is not None
    assert list(reservation.reserved_marking.get("in", ())) == []
    marking, record = engine.settle(
        reservation.reserved_marking,
        reservation,
        {
            "status": "failed",
            "outputTokens": {},
            "error": {"type": "Down", "message": "unavailable"},
            "metadata": {},
        },
    )
    assert marking == net.initial_marking
    assert record["status"] == "failed"
    assert record["inputTokens"] == {"in": [_token("job", id="a")]}


def test_runtime_admits_independent_fanout_and_honors_single_flight_lane() -> None:
    async def scenario() -> None:
        transitions = [
            Transition("eink", "eink"),
            Transition("kitty", "kitty"),
            Transition("web", "web"),
        ]
        places = [
            Place("eink_jobs", ["job"]),
            Place("kitty_jobs", ["job"]),
            Place("web_jobs", ["job"]),
            Place("eink_done", ["done"]),
            Place("kitty_done", ["done"]),
            Place("web_done", ["done"]),
        ]
        arcs = [
            _consume("eink_jobs", "eink", "job"),
            _produce("eink", "eink_done", "done"),
            _consume("kitty_jobs", "kitty", "job"),
            _produce("kitty", "kitty_done", "done"),
            _consume("web_jobs", "web", "job"),
            _produce("web", "web_done", "done"),
        ]
        net = _net(
            transitions=transitions,
            places=places,
            arcs=arcs,
            marking=Marking(
                {
                    "eink_jobs": [
                        _token("job", id="first"),
                        _token("job", id="second"),
                    ],
                    "kitty_jobs": [_token("job", id="kitty")],
                    "web_jobs": [_token("job", id="web")],
                }
            ),
        )
        started: list[tuple[str, str]] = []
        fanout_started = asyncio.Event()
        release = asyncio.Event()

        def handler(name: str, output: str) -> AsyncHandler:
            async def run(
                inp: TransitionHandlerInput, ctx: RuntimeContext
            ) -> TransitionHandlerOutput:
                del ctx
                started.append(
                    (name, str(inp["inputTokens"][f"{name}_jobs"][0].data["id"]))
                )
                if len(started) == 3:
                    fanout_started.set()
                await release.wait()
                return {
                    "status": "completed",
                    "outputTokens": {output: [_token("done", name=name)]},
                    "error": None,
                    "metadata": {},
                }

            return run

        runtime = Runtime(
            net=net,
            handlers={
                "eink": HandlerSpec(handler("eink", "eink_done"), "panel"),
                "kitty": HandlerSpec(handler("kitty", "kitty_done"), "terminal"),
                "web": HandlerSpec(handler("web", "web_done"), "web"),
            },
            lanes={"panel": Lane(1), "terminal": Lane(1), "web": Lane(1)},
        )
        await runtime.start()
        try:
            await asyncio.wait_for(fanout_started.wait(), timeout=1)
            assert sorted(started) == [
                ("eink", "first"),
                ("kitty", "kitty"),
                ("web", "web"),
            ]
            release.set()
            await asyncio.wait_for(runtime.wait_idle(), timeout=1)
            assert [token.data["name"] for token in runtime.marking["eink_done"]] == [
                "eink",
                "eink",
            ]
            assert [token.data["name"] for token in runtime.marking["kitty_done"]] == [
                "kitty"
            ]
            assert [token.data["name"] for token in runtime.marking["web_done"]] == [
                "web"
            ]
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_runtime_schedules_earliest_timer_without_idle_polling() -> None:
    async def scenario() -> None:
        clock = _ManualClock()
        net = _net(
            transitions=[
                Transition(
                    "first",
                    "first",
                    timer=Timer(
                        "clock",
                        "clock.now >= deadline.at",
                        {"deadline": "first_deadline"},
                        "deadline.at",
                    ),
                ),
                Transition(
                    "later",
                    "later",
                    timer=Timer(
                        "clock",
                        "clock.now >= deadline.at",
                        {"deadline": "later_deadline"},
                        "deadline.at",
                    ),
                ),
            ],
            places=[
                Place("clock", ["tick"]),
                Place("first_deadline", ["deadline"]),
                Place("later_deadline", ["deadline"]),
                Place("done", ["done"]),
            ],
            arcs=[
                _consume("first_deadline", "first", "deadline"),
                _produce("first", "done", "done"),
                _consume("later_deadline", "later", "deadline"),
                _produce("later", "done", "done"),
            ],
            marking=Marking(
                {
                    "clock": [_token("tick", now=0)],
                    "first_deadline": [_token("deadline", at=5)],
                    "later_deadline": [_token("deadline", at=10)],
                }
            ),
        )
        journal = JsonlJournal()
        runtime = Runtime(
            net=net,
            handlers={
                "first": HandlerSpec(
                    _completed("done", _token("done", name="first")), "io"
                ),
                "later": HandlerSpec(
                    _completed("done", _token("done", name="later")), "io"
                ),
            },
            lanes={"io": 1},
            clock=clock,
            journal=journal,
        )
        await runtime.start()
        try:
            await clock.wait_for_sleeps(1)
            assert clock.sleep_requests == [5]
            assert _journal_records(journal) == []

            clock.advance(5)
            await _eventually(lambda: len(runtime.marking.get("done", ())) == 1)
            await clock.wait_for_sleeps(2)

            assert clock.sleep_requests == [5, 10]
            updates = [
                record
                for record in _journal_records(journal)
                if record.get("kind") == "update"
            ]
            assert [
                (record["place"], record["tokens"][0].data["now"]) for record in updates
            ] == [("clock", 5)]
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_source_batch_and_native_timer_progress_while_llm_is_in_flight() -> None:
    async def scenario() -> None:
        clock = _ManualClock()
        net = _net(
            transitions=[
                Transition("llm", "llm"),
                Transition("sample", "sample"),
                Transition(
                    "timer",
                    "timer",
                    timer=Timer(
                        "clock",
                        "clock.now >= deadline.at",
                        {"deadline": "deadline"},
                        "deadline.at",
                    ),
                ),
            ],
            places=[
                Place("request", ["request"]),
                Place("sample", ["sample"]),
                Place("deadline", ["deadline"]),
                Place("clock", ["tick"]),
                Place("llm_done", ["done"]),
                Place("sample_done", ["done"]),
                Place("timer_done", ["done"]),
            ],
            arcs=[
                _consume("request", "llm", "request"),
                _produce("llm", "llm_done", "done"),
                _consume("sample", "sample", "sample"),
                _produce("sample", "sample_done", "done"),
                _consume("deadline", "timer", "deadline"),
                _produce("timer", "timer_done", "done"),
            ],
            marking=Marking(
                {
                    "request": [_token("request")],
                    "deadline": [_token("deadline", at=3)],
                    "clock": [_token("tick", now=0)],
                }
            ),
        )
        llm_started = asyncio.Event()
        release_llm = asyncio.Event()

        async def llm(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del inp, ctx
            llm_started.set()
            await release_llm.wait()
            return {
                "status": "completed",
                "outputTokens": {"llm_done": [_token("done")]},
                "error": None,
                "metadata": {},
            }

        async def batches() -> AsyncIterator[TokenBatch]:
            yield TokenBatch((TokenArrival("sample", _token("sample", id=1)),))

        runtime = Runtime(
            net=net,
            handlers={
                "llm": HandlerSpec(llm, "llm"),
                "sample": HandlerSpec(_completed("sample_done", _token("done")), "io"),
                "timer": HandlerSpec(_completed("timer_done", _token("done")), "timer"),
            },
            lanes={"llm": 1, "io": 1, "timer": 1},
            sources=(TokenSource("sse", batches, "batch", 1),),
            clock=clock,
        )
        await runtime.start()
        try:
            await asyncio.wait_for(llm_started.wait(), timeout=1)
            await _eventually(lambda: runtime.marking.get("sample_done") is not None)
            await clock.wait_for_sleeps(1)
            clock.advance(3)
            await _eventually(lambda: runtime.marking.get("timer_done") is not None)
            assert runtime.marking.get("llm_done") is None
            release_llm.set()
            await asyncio.wait_for(runtime.wait_idle(), timeout=1)
            assert runtime.marking.get("llm_done") is not None
        finally:
            release_llm.set()
            await runtime.stop()

    asyncio.run(scenario())


def test_runtime_recomputes_after_timer_resetting_firing() -> None:
    async def scenario() -> None:
        clock = _ManualClock()
        net = _net(
            transitions=[
                Transition("reset", "reset"),
                Transition(
                    "timeout_fire",
                    "timeout_fire",
                    timer=Timer(
                        "clock",
                        "clock.now >= latch.at",
                        {"latch": "latch"},
                        "latch.at",
                    ),
                ),
            ],
            places=[
                Place("reset", ["reset"]),
                Place("latch", ["latch"]),
                Place("clock", ["tick"]),
                Place("done", ["done"]),
            ],
            arcs=[
                _consume("reset", "reset", "reset"),
                _consume("latch", "reset", "latch"),
                _produce("reset", "latch", "latch"),
                _consume("latch", "timeout_fire", "latch"),
                _produce("timeout_fire", "done", "done"),
            ],
            marking=Marking(
                {
                    "reset": [_token("reset")],
                    "latch": [_token("latch", at=10)],
                    "clock": [_token("tick", now=0)],
                }
            ),
        )

        async def reset(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del inp, ctx
            return {
                "status": "completed",
                "outputTokens": {"latch": [_token("latch", at=3)]},
                "error": None,
                "metadata": {},
            }

        runtime = Runtime(
            net=net,
            handlers={
                "reset": HandlerSpec(reset, "control"),
                "timeout_fire": HandlerSpec(
                    _completed("done", _token("done")), "timer"
                ),
            },
            lanes={"control": 1, "timer": 1},
            clock=clock,
        )
        await runtime.start()
        try:
            await clock.wait_for_sleeps(1)
            assert clock.sleep_requests == [3]
            clock.advance(3)
            await _eventually(lambda: runtime.marking.get("done") is not None)
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_runtime_advances_simultaneous_cadence_and_speaks_timers_lexically() -> None:
    async def scenario() -> None:
        clock = _ManualClock()
        net = _net(
            transitions=[
                Transition(
                    "on_tick",
                    "on_tick",
                    timer=Timer(
                        "cadence_clock",
                        "clock.now >= latch.fired_at + latch.cadence_s",
                        {"latch": "tick_latch"},
                        "latch.fired_at + latch.cadence_s",
                    ),
                ),
                Transition(
                    "timeout_fire",
                    "timeout_fire",
                    timer=Timer(
                        "speaks_clock",
                        "clock.now >= request.started_at + request.timeout_s",
                        {"request": "speak_window"},
                        "request.started_at + request.timeout_s",
                    ),
                ),
            ],
            places=[
                Place("cadence_clock", ["tick"]),
                Place("speaks_clock", ["tick"]),
                Place("tick_latch", ["latch"]),
                Place("speak_window", ["request"]),
                Place("done", ["done"]),
            ],
            arcs=[
                _consume("tick_latch", "on_tick", "latch"),
                _produce("on_tick", "done", "done"),
                _consume("speak_window", "timeout_fire", "request"),
                _produce("timeout_fire", "done", "done"),
            ],
            marking=Marking(
                {
                    "cadence_clock": [_token("tick", now=0)],
                    "speaks_clock": [_token("tick", now=0)],
                    "tick_latch": [_token("latch", fired_at=0, cadence_s=120)],
                    "speak_window": [_token("request", started_at=0, timeout_s=120)],
                }
            ),
        )
        journal = JsonlJournal()
        runtime = Runtime(
            net=net,
            handlers={
                "on_tick": HandlerSpec(
                    _completed("done", _token("done", name="tick")), "cadence"
                ),
                "timeout_fire": HandlerSpec(
                    _completed("done", _token("done", name="timeout")), "speaks"
                ),
            },
            lanes={"cadence": 1, "speaks": 1},
            clock=clock,
            journal=journal,
        )
        await runtime.start()
        try:
            await clock.wait_for_sleeps(1)
            assert clock.sleep_requests == [120]
            clock.advance(120)
            await _eventually(lambda: len(runtime.marking.get("done", ())) == 2)
            updates = [
                record
                for record in _journal_records(journal)
                if record.get("kind") == "update"
            ]
            assert [
                (record["place"], record["tokens"][0].data["now"]) for record in updates
            ] == [
                ("cadence_clock", 120),
                ("speaks_clock", 120),
            ]
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_timeout_cancels_scope_and_suppresses_late_blocking_completion() -> None:
    async def scenario() -> None:
        transitions = [Transition("llm", "llm"), Transition("timeout", "timeout")]
        places = [
            Place("request", ["request"]),
            Place("timeout", ["timeout"]),
            Place("result", ["result"]),
            Place("timed_out", ["timed_out"]),
        ]
        arcs = [
            _consume("request", "llm", "request"),
            _produce("llm", "result", "result"),
            _consume("timeout", "timeout", "timeout"),
            _produce("timeout", "timed_out", "timed_out"),
        ]
        net = _net(
            transitions=transitions,
            places=places,
            arcs=arcs,
            marking=Marking({"request": [_token("request", id="request-1")]}),
        )
        worker_started = asyncio.Event()
        worker_release = asyncio.Event()

        def blocking_llm(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del inp, ctx
            worker_started.set()
            # The thread keeps running after its Runtime task has been cancelled.
            # asyncio's Event is intentionally not awaited here; the worker only
            # needs a deterministic, externally controlled late-completion gate.
            while not worker_release.is_set():
                import time

                time.sleep(0.001)
            return {
                "status": "completed",
                "outputTokens": {"result": [_token("result")]},
                "error": None,
                "metadata": {},
            }

        async def timeout(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del inp
            cancellation = ctx.cancel(scope="request-1")
            assert cancellation.cancelled == 1
            return {
                "status": "completed",
                "outputTokens": {"timed_out": [_token("timed_out")]},
                "error": None,
                "metadata": {},
            }

        journal = JsonlJournal()
        runtime = Runtime(
            net=net,
            handlers={
                "llm": HandlerSpec(
                    blocking_llm,
                    "llm",
                    scope=lambda inp: str(inp["inputTokens"]["request"][0].data["id"]),
                    blocking=True,
                ),
                "timeout": HandlerSpec(timeout, "control"),
            },
            lanes={"llm": 1, "control": 1},
            journal=journal,
        )
        await runtime.start()
        try:
            await asyncio.wait_for(worker_started.wait(), timeout=1)
            await runtime.inject(TokenArrival("timeout", _token("timeout")))
            await asyncio.wait_for(runtime.wait_idle(), timeout=1)
            assert list(runtime.marking.get("result", ())) == []
            assert list(runtime.marking["request"]) == [
                _token("request", id="request-1")
            ]
            assert any(
                record.get("error", {}).get("type") == "Cancelled"
                for record in _journal_records(journal)
                if record.get("status") == "failed"
            )
            worker_release.set()
            for _ in range(100):
                if any(
                    record.get("event") == "suppressed"
                    for record in _journal_records(journal)
                ):
                    break
                await asyncio.sleep(0.005)
            assert any(
                record.get("event") == "suppressed"
                for record in _journal_records(journal)
            )
        finally:
            worker_release.set()
            await runtime.stop()

    asyncio.run(scenario())


def test_coalesce_source_keeps_latest_item_with_explicit_overflow_accounting() -> None:
    async def scenario() -> None:
        net = _net(
            transitions=[Transition("consume", "consume")],
            places=[Place("in", ["sample"]), Place("out", ["done"])],
            arcs=[
                _consume("in", "consume", "sample"),
                _produce("consume", "out", "done"),
            ],
            marking=Marking(),
        )

        async def updates() -> AsyncIterator[TokenArrival]:
            for value in (1, 2, 3):
                yield TokenArrival("in", _token("sample", value=value), key="co2")

        async def consume(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del ctx
            value = inp["inputTokens"]["in"][0].data["value"]
            return {
                "status": "completed",
                "outputTokens": {"out": [_token("done", value=value)]},
                "error": None,
                "metadata": {},
            }

        runtime = Runtime(
            net=net,
            handlers={"consume": HandlerSpec(consume, "io")},
            lanes={"io": 1},
            sources=(TokenSource("co2", updates, "coalesce", 1),),
        )
        await runtime.start()
        try:
            await asyncio.wait_for(runtime.wait_idle(), timeout=1)
            assert list(runtime.marking["out"]) == [_token("done", value=3)]
            assert runtime.source_health["co2"].coalesced == 2
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_source_policy_bounds_are_declared_at_construction() -> None:
    async def empty() -> AsyncIterator[TokenArrival]:
        if False:
            yield TokenArrival("ignored", _token("ignored"))

    with pytest.raises(ValueError, match="capacity=1"):
        TokenSource("latest", empty, "coalesce", 2)
    with pytest.raises(ValueError, match=">= 1"):
        TokenSource("lossless", empty, "lossless", 0)


def test_terminal_consume_can_preempt_active_read_arc() -> None:
    """A timeout may consume a read window while a blocking reader is running."""

    async def scenario() -> None:
        reader_started = asyncio.Event()
        release_reader = asyncio.Event()

        async def reader(
            inp: TransitionHandlerInput, ctx: RuntimeContext
        ) -> TransitionHandlerOutput:
            del inp, ctx
            reader_started.set()
            await release_reader.wait()
            return {
                "status": "completed",
                "outputTokens": {"read_done": [_token("done")]},
                "error": None,
                "metadata": {},
            }

        net = _net(
            transitions=[
                Transition("reader", "reader"),
                Transition("timeout", "timeout"),
            ],
            places=[
                Place("request", ["request"]),
                Place("window", ["window"]),
                Place("read_done", ["done"]),
                Place("timed_out", ["done"]),
            ],
            arcs=[
                _consume("request", "reader", "request"),
                Arc(
                    from_place="window",
                    to_transition="reader",
                    consume=ConsumePattern("window", None, "read"),
                ),
                _produce("reader", "read_done", "done"),
                _consume("window", "timeout", "window"),
                _produce("timeout", "timed_out", "done"),
            ],
            marking=Marking(
                {
                    "request": [_token("request")],
                    "window": [_token("window")],
                }
            ),
        )
        runtime = Runtime(
            net=net,
            handlers={
                "reader": HandlerSpec(reader, "llm"),
                "timeout": HandlerSpec(
                    _completed("timed_out", _token("done")), "control"
                ),
            },
            lanes={"llm": 1, "control": 1},
            policy="priority",
        )
        await runtime.start()
        try:
            await reader_started.wait()
            await _eventually(lambda: bool(runtime.marking.get("timed_out")))
            assert runtime.marking.get("read_done") is None
            release_reader.set()
            await runtime.wait_idle()
            assert list(runtime.marking["read_done"]) == [_token("done")]
        finally:
            await runtime.stop()

    asyncio.run(scenario())
