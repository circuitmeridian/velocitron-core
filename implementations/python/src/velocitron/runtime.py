"""Long-lived asynchronous execution for a :class:`velocitron.schema.Net`.

``Runtime`` owns the mutable *reference* to the net's immutable ``Marking``.
It reserves a firing's consume-mode inputs before starting asynchronous work,
then commits or rolls that reservation back when work reaches a terminal
outcome.  This lets independent firings make progress concurrently without
letting conflicting firings overlap.
"""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterable,
    Awaitable,
    Callable,
    Hashable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
import inspect
from time import monotonic
from typing import Any, Literal, Protocol

from .contract import (
    FiringPolicyInput,
    HandlerError,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from .engine import Engine, FiringReservation
from .journal import Journal, RuntimeRecord
from .registry import HandlerRegistry
from .schema import Marking, Net, Token, Transition


class Clock(Protocol):
    """The Runtime-owned monotonic clock used by native timer scheduling."""

    def now(self) -> float: ...

    async def sleep_until(self, when: float) -> None: ...


class _MonotonicClock:
    """Production clock backed by the process monotonic clock."""

    def now(self) -> float:
        return monotonic()

    async def sleep_until(self, when: float) -> None:
        await asyncio.sleep(max(0.0, when - self.now()))


SourcePolicy = Literal["lossless", "batch", "coalesce"]


class AsyncTransitionHandler(Protocol):
    """An awaitable transition handler receiving its input and Runtime context."""

    def __call__(
        self, inp: TransitionHandlerInput, ctx: "RuntimeContext", /
    ) -> Awaitable[TransitionHandlerOutput]: ...


@dataclass(frozen=True)
class Lane:
    """A named bounded execution capacity."""

    capacity: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"lane capacity must be >= 1, got {self.capacity}")


ScopeResolver = Callable[[TransitionHandlerInput], str | None]


@dataclass(frozen=True)
class HandlerSpec:
    """Runtime-specific declaration for one net transition handler reference.

    ``blocking=True`` runs ``handler`` in a bounded worker thread.  Cancelling
    the firing immediately suppresses its eventual result; Python cannot stop
    an already-running worker thread, so its late terminal result receives a
    runtime lifecycle journal record instead of depositing tokens.
    """

    handler: AsyncTransitionHandler | Callable[..., TransitionHandlerOutput]
    lane: str
    scope: ScopeResolver | None = None
    blocking: bool = False


@dataclass(frozen=True)
class TokenArrival:
    """One external token placement owned by a :class:`TokenSource`."""

    place: str
    token: Token
    replace: bool = False
    key: Hashable | None = None


@dataclass(frozen=True)
class TokenBatch:
    """An all-or-nothing sequence of append-only source placements."""

    placements: tuple[TokenArrival, ...]

    def __post_init__(self) -> None:
        if not self.placements:
            raise ValueError("a token batch must contain at least one placement")
        if any(placement.replace for placement in self.placements):
            raise ValueError(
                "token batches are append-only; use TokenArrival(replace=True)"
            )


SourceItem = TokenArrival | TokenBatch
SourceFactory = Callable[[], AsyncIterable[SourceItem]]


@dataclass(frozen=True)
class TokenSource:
    """A reconnectable, explicitly bounded producer of environment arrivals.

    The source factory owns transport details (authentication, parsing, cursor
    and reconnect protocol).  Runtime owns its bounded mailbox, lifecycle,
    retry backoff, and admission to the marking.

    ``lossless`` and ``batch`` apply backpressure when the mailbox is full.
    ``batch`` requires :class:`TokenBatch` values.  ``coalesce`` is deliberately
    a single-slot latest-value mailbox: a full slot is replaced only by an item
    with the same non-``None`` key; a different key backpressures rather than
    silently dropping state.
    """

    name: str
    open: SourceFactory
    policy: SourcePolicy
    capacity: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"source {self.name!r} capacity must be >= 1")
        if self.policy == "coalesce" and self.capacity != 1:
            raise ValueError("coalesce sources require capacity=1")


@dataclass
class SourceHealth:
    """Observable runtime state for one supervised token source."""

    state: Literal["starting", "running", "backing_off", "stopped"] = "starting"
    restarts: int = 0
    coalesced: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class Cancellation:
    """The explicit outcome of ``ctx.cancel(scope=...)``."""

    scope: str
    cancelled: int


@dataclass(frozen=True)
class _Footprint:
    consume: frozenset[str]
    read: frozenset[str]
    write: frozenset[str]


@dataclass
class _ActiveFiring:
    reservation: FiringReservation
    spec: HandlerSpec
    footprint: _Footprint
    scope: str | None
    task: asyncio.Task[None]


class _Mailbox:
    """One source's bounded mailbox with policy-specific overflow semantics."""

    def __init__(self, source: TokenSource, health: SourceHealth) -> None:
        self.source = source
        self.health = health
        self.queue: asyncio.Queue[SourceItem] = asyncio.Queue(maxsize=source.capacity)

    async def put(self, item: SourceItem) -> None:
        if self.source.policy == "batch" and not isinstance(item, TokenBatch):
            raise TypeError(
                f"batch source {self.source.name!r} emitted a non-batch item"
            )
        if self.source.policy != "coalesce":
            await self.queue.put(item)
            return

        if not isinstance(item, TokenArrival) or item.key is None:
            raise TypeError(
                f"coalesce source {self.source.name!r} must emit TokenArrival with a key"
            )
        if not self.queue.full():
            self.queue.put_nowait(item)
            return

        previous = self.queue.get_nowait()
        self.queue.task_done()
        assert isinstance(previous, TokenArrival)
        if previous.key == item.key:
            self.queue.put_nowait(item)
            self.health.coalesced += 1
            return

        # A coalesce source owns one latest value per key.  A different key is
        # not eligible for replacement, so put the pending value back and apply
        # ordinary bounded backpressure instead of silently losing either one.
        self.queue.put_nowait(previous)
        await self.queue.put(item)


class RuntimeContext:
    """Per-firing control context supplied to an async Runtime handler."""

    def __init__(self, runtime: "Runtime", scope: str | None) -> None:
        self._runtime = runtime
        self.scope = scope

    def cancel(self, *, scope: str) -> Cancellation:
        """Cancel every other active firing in ``scope`` and report the count."""
        return self._runtime.cancel_scope(scope)


class Runtime:
    """Own a net's long-lived asynchronous execution.

    ``handlers`` is keyed by transition handler reference, not transition name,
    and every transition must declare one because asynchronous execution
    requires a :class:`HandlerSpec`. ``lanes`` supplies every handler spec's
    bounded capacity. The optional ``registry`` retains synchronous guard,
    predicate, and firing-policy resolution; Runtime never registers async
    handlers into it or calls :meth:`Engine.fire`.
    """

    def __init__(
        self,
        *,
        net: Net,
        handlers: Mapping[str, HandlerSpec],
        lanes: Mapping[str, Lane | int],
        sources: Sequence[TokenSource] = (),
        clock: Clock | Literal["monotonic"] = "monotonic",
        journal: Journal | None = None,
        registry: HandlerRegistry | None = None,
        marking: Marking | None = None,
        policy: str = "first-found",
        max_consecutive_failures: int | None = None,
        source_backoff: float = 0.1,
    ) -> None:
        if clock == "monotonic":
            self.clock: Clock = _MonotonicClock()
        else:
            self.clock = clock
            if not callable(getattr(clock, "now", None)) or not callable(
                getattr(clock, "sleep_until", None)
            ):
                raise ValueError("clock must be 'monotonic' or implement Clock")
        if source_backoff <= 0:
            raise ValueError("source_backoff must be > 0")
        if max_consecutive_failures is not None and max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1 or None")

        self.net = net
        self.handlers = dict(handlers)
        self.lanes = {
            name: lane if isinstance(lane, Lane) else Lane(lane)
            for name, lane in lanes.items()
        }
        self.sources = tuple(sources)
        self.journal = journal
        self.registry = registry or HandlerRegistry()
        self.engine = Engine(
            self.registry,
            policy=policy,
            journal=journal,
            deposit_violation="record_then_drop" if journal is not None else "raise",
        )
        self.marking = marking or net.initial_marking or Marking()
        self.policy = policy
        self.max_consecutive_failures = max_consecutive_failures
        self.source_backoff = source_backoff

        handlerless = [
            transition.name
            for transition in net.transitions
            if transition.handler is None
        ]
        if handlerless:
            raise ValueError(
                "Runtime requires handlers; transitions without handlers: "
                f"{handlerless!r}"
            )

        transition_refs = {
            transition.handler
            for transition in net.transitions
            if transition.handler is not None
        }
        missing = transition_refs - self.handlers.keys()
        extra = self.handlers.keys() - transition_refs
        if missing or extra:
            raise ValueError(
                "handler specs must match the net's handler refs exactly; "
                f"missing={sorted(missing)!r}, extra={sorted(extra)!r}"
            )
        unknown_lanes = {
            spec.lane for spec in self.handlers.values()
        } - self.lanes.keys()
        if unknown_lanes:
            raise ValueError(
                f"handler specs reference unknown lanes: {sorted(unknown_lanes)!r}"
            )
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("token-source names must be unique")

        self._started = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._changed = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active: dict[asyncio.Task[None], _ActiveFiring] = {}
        self._lane_in_use = {name: 0 for name in self.lanes}
        self._failures: dict[str, int] = {}
        self._blocked_transitions: set[str] = set()
        missing_maturity = sorted(
            transition.name
            for transition in self.net.transitions
            if transition.timer is not None and transition.timer.maturity is None
        )
        if missing_maturity:
            raise ValueError(
                "Runtime requires timer.maturity for every native timer: "
                f"{missing_maturity!r}"
            )
        self._attempt = 0
        self._injection_attempt = 0
        self.source_health = {source.name: SourceHealth() for source in self.sources}
        self._mailboxes: dict[str, _Mailbox] = {}
        self._timer_changed = asyncio.Event()

    async def start(self) -> None:
        """Start source/timer supervision and admit currently enabled work."""
        if self._started:
            raise RuntimeError("Runtime is already started")
        self._started = True
        self._bootstrap_timer_clocks()
        self._admit()
        for source in self.sources:
            mailbox = _Mailbox(source, self.source_health[source.name])
            self._mailboxes[source.name] = mailbox
            self._track(asyncio.create_task(self._supervise_source(source, mailbox)))
            self._track(asyncio.create_task(self._dispatch_source(mailbox)))
        if self._timer_clock_places():
            self._track(asyncio.create_task(self._run_timers()))

    async def run(self) -> None:
        """Run until :meth:`stop` or task cancellation ends this Runtime."""
        await self.start()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop supervision and cancel all work that has not settled."""
        if self._stopping:
            return
        self._stopping = True
        self._stop_event.set()
        current = asyncio.current_task()
        for task in tuple(self._tasks):
            if task is not current:
                task.cancel()
        for task in tuple(self._active):
            if task is not current:
                task.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.gather(*tuple(self._active), return_exceptions=True)
        for health in self.source_health.values():
            health.state = "stopped"
        self._signal()

    async def inject(self, arrival: TokenArrival) -> None:
        """Inject one environment arrival after respecting in-flight footprints."""
        self._require_started()
        await self._apply_arrival(arrival)

    async def inject_batch(self, batch: TokenBatch) -> None:
        """Inject an all-or-nothing source batch after respecting footprints."""
        self._require_started()
        await self._apply_arrival(batch)

    async def wait_idle(self) -> None:
        """Wait until no handler is active and every source mailbox is empty."""
        self._require_started()
        await asyncio.sleep(0)
        while self._active or any(
            not mailbox.queue.empty() for mailbox in self._mailboxes.values()
        ):
            self._changed.clear()
            await self._changed.wait()

    def _require_started(self) -> None:
        if not self._started or self._stopping:
            raise RuntimeError("Runtime is not running")

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _signal(self) -> None:
        self._changed.set()
        self._timer_changed.set()

    def _timer_clock_places(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    transition.timer.clock
                    for transition in self.net.transitions
                    if transition.timer
                }
            )
        )

    def _bootstrap_timer_clocks(self) -> None:
        places = {place.name: place for place in self.net.places}
        now = self.clock.now()
        for clock_place in self._timer_clock_places():
            if self.marking.get(clock_place):
                continue
            place = places[clock_place]
            if not place.accepts:
                raise ValueError(
                    f"timer clock place {clock_place!r} accepts no token type"
                )
            self._inject_now(
                TokenArrival(clock_place, Token(place.accepts[0], {"now": now}))
            )

    async def _run_timers(self) -> None:
        """Sleep until the next declared maturity or any marking mutation."""
        while not self._stopping:
            self._timer_changed.clear()
            maturities = self.engine.timer_maturities(self.net, self.marking)
            now = self.clock.now()
            due_places = tuple(
                sorted({item.clock for item in maturities if item.at <= now})
            )
            if due_places:
                await self._advance_timer_clocks(due_places, now)
                continue
            next_wake = min((item.at for item in maturities), default=None)
            if self._timer_changed.is_set():
                continue
            await self._wait_for_timer_change(next_wake)

    async def _advance_timer_clocks(self, places: tuple[str, ...], now: float) -> None:
        """Replace every due clock with one timestamp before admitting work."""
        while any(self._write_conflicts(place) for place in places):
            self._changed.clear()
            await self._changed.wait()
        changed: set[str] = set()
        for place in places:
            existing = self.marking.get(place, ())
            if not existing:
                continue
            token = existing[0]
            self._inject_now(
                TokenArrival(
                    place,
                    Token(token.type, {**token.data, "now": now}),
                    replace=True,
                )
            )
            changed.add(place)
        if changed:
            self._unblock_for(changed)
            self._admit()
            self._signal()

    async def _wait_for_timer_change(self, when: float | None) -> None:
        if when is None:
            await self._timer_changed.wait()
            return
        sleeper = asyncio.create_task(self.clock.sleep_until(when))
        changed = asyncio.create_task(self._timer_changed.wait())
        try:
            _done, _pending = await asyncio.wait(
                (sleeper, changed), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (sleeper, changed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, changed, return_exceptions=True)

    async def _supervise_source(self, source: TokenSource, mailbox: _Mailbox) -> None:
        health = self.source_health[source.name]
        while not self._stopping:
            try:
                health.state = "running"
                async for item in source.open():
                    await mailbox.put(item)
                    self._signal()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - source boundary contains transport failures
                health.state = "backing_off"
                health.restarts += 1
                health.last_error = f"{type(exc).__name__}: {exc}"
                self._record_runtime(
                    "source_failed", source=source.name, detail=health.last_error
                )
                await asyncio.sleep(self.source_backoff)

    async def _dispatch_source(self, mailbox: _Mailbox) -> None:
        while not self._stopping:
            item = await mailbox.queue.get()
            try:
                await self._apply_arrival(item)
            finally:
                mailbox.queue.task_done()
                self._signal()

    async def _apply_arrival(self, item: SourceItem) -> None:
        places = (
            {item.place}
            if isinstance(item, TokenArrival)
            else {arrival.place for arrival in item.placements}
        )
        while any(self._write_conflicts(place) for place in places):
            self._changed.clear()
            await self._changed.wait()
        if isinstance(item, TokenArrival):
            self._inject_now(item)
        else:
            self.marking, _records = self.engine.inject_tokens(
                self.net,
                self.marking,
                [(arrival.place, arrival.token) for arrival in item.placements],
                attempt=self._next_injection_attempt(),
            )
        self._unblock_for(places)
        self._admit()
        self._signal()

    def _unblock_for(self, changed_places: set[str]) -> None:
        """Retry a failed transition only when one of its inputs changes."""
        for transition in self.net.transitions:
            if transition.name not in self._blocked_transitions:
                continue
            footprint = self._footprint(transition)
            if changed_places & (footprint.consume | footprint.read):
                self._blocked_transitions.remove(transition.name)

    def _inject_now(self, arrival: TokenArrival) -> None:
        self.marking, _record = self.engine.inject_token(
            self.net,
            self.marking,
            arrival.place,
            arrival.token,
            attempt=self._next_injection_attempt(),
            replace=arrival.replace,
        )

    def _next_injection_attempt(self) -> int:
        attempt = self._injection_attempt
        self._injection_attempt += 1
        return attempt

    def _next_attempt(self) -> int:
        attempt = self._attempt
        self._attempt += 1
        return attempt

    def _footprint(self, transition: Transition) -> _Footprint:
        consume: set[str] = set()
        read: set[str] = set()
        write: set[str] = set()
        for arc in self.net.arcs:
            if arc.to_transition == transition.name and arc.consume is not None:
                if arc.consume.mode == "consume":
                    assert arc.from_place is not None
                    consume.add(arc.from_place)
                elif arc.consume.mode == "read":
                    assert arc.from_place is not None
                    read.add(arc.from_place)
            elif arc.from_transition == transition.name and arc.produce is not None:
                write.add(arc.produce.destination)
        return _Footprint(frozenset(consume), frozenset(read), frozenset(write))

    @staticmethod
    def _footprints_conflict(left: _Footprint, right: _Footprint) -> bool:
        """Return whether overlapping firings mutate a common dependency.

        A read arc observes a token without reserving or changing it.  A
        terminal transition may therefore consume that observed token while
        the reader's side effect is still in flight; this is the required
        timeout-race shape.  Writes remain exclusive with reads because a
        reader must not observe a value concurrently being replaced.
        """
        return bool(
            left.consume & (right.consume | right.write)
            or left.write & (right.consume | right.read | right.write)
            or right.consume & left.write
            or right.write & (left.consume | left.read | left.write)
        )

    def _write_conflicts(self, place: str) -> bool:
        arrival = _Footprint(frozenset(), frozenset(), frozenset({place}))
        return any(
            self._footprints_conflict(arrival, active.footprint)
            for active in self._active.values()
        )

    def _admit(self) -> None:
        while not self._stopping:
            enabled = self.engine.enabled_transitions(
                self.net, self.marking, attempt=self._attempt
            )
            candidates: list[Transition] = []
            for transition in self.net.transitions:
                if transition.name not in enabled:
                    continue
                if transition.name in self._blocked_transitions:
                    continue
                handler_ref = transition.handler
                assert handler_ref is not None
                spec = self.handlers[handler_ref]
                footprint = self._footprint(transition)
                if self._lane_in_use[spec.lane] >= self.lanes[spec.lane].capacity:
                    continue
                if any(
                    self._footprints_conflict(footprint, active.footprint)
                    for active in self._active.values()
                ):
                    continue
                if self.max_consecutive_failures is not None and (
                    self._failures.get(transition.name, 0)
                    >= self.max_consecutive_failures
                ):
                    continue
                candidates.append(transition)
            if not candidates:
                return

            choice = self.registry.resolve_firing_policy(self.policy)(
                FiringPolicyInput(
                    marking=self.marking,
                    enabledTransitions=[transition.name for transition in candidates],
                    priorities={
                        transition.name: transition.priority or 0
                        for transition in candidates
                    },
                    consecutiveFailures={
                        transition.name: self._failures.get(transition.name, 0)
                        for transition in candidates
                    },
                )
            )
            if choice is None:
                return
            transition = next(item for item in candidates if item.name == choice)
            reservation = self.engine.reserve(
                self.net,
                self.marking,
                transition.name,
                attempt=self._next_attempt(),
            )
            if reservation is None:
                continue
            self.marking = reservation.reserved_marking
            self._signal()
            handler_ref = transition.handler
            assert handler_ref is not None
            spec = self.handlers[handler_ref]
            footprint = self._footprint(transition)
            inp = TransitionHandlerInput(
                transitionId=transition.name,
                inputTokens=reservation.input_tokens,
                firingContext=reservation.context,
            )
            scope = spec.scope(inp) if spec.scope is not None else None
            self._lane_in_use[spec.lane] += 1
            task = asyncio.create_task(
                self._execute(reservation, spec, footprint, scope, inp)
            )
            self._active[task] = _ActiveFiring(
                reservation, spec, footprint, scope, task
            )

    async def _execute(
        self,
        reservation: FiringReservation,
        spec: HandlerSpec,
        footprint: _Footprint,
        scope: str | None,
        inp: TransitionHandlerInput,
    ) -> None:
        ctx = RuntimeContext(self, scope)
        try:
            out = await self._invoke(spec, inp, ctx, reservation)
        except asyncio.CancelledError:
            out = self._failed_output(
                "Cancelled",
                "runtime cancellation before handler completion",
                {"scope": scope},
            )
        except Exception as exc:  # noqa: BLE001 - handler exception is a failed outcome
            out = self._failed_output(type(exc).__name__, str(exc), {})

        self.marking, record = self.engine.settle(self.marking, reservation, out)
        if record["status"] == "failed":
            self._failures[reservation.transition] = (
                self._failures.get(reservation.transition, 0) + 1
            )
            self._blocked_transitions.add(reservation.transition)
        else:
            self._failures.clear()
            self._unblock_for(set(record["outputTokens"]))
        task = asyncio.current_task()
        assert task is not None
        active = self._active.pop(task)
        self._lane_in_use[active.spec.lane] -= 1
        self._admit()
        self._signal()

    async def _invoke(
        self,
        spec: HandlerSpec,
        inp: TransitionHandlerInput,
        ctx: RuntimeContext,
        reservation: FiringReservation,
    ) -> TransitionHandlerOutput:
        if not spec.blocking:
            result = spec.handler(inp, ctx)  # type: ignore[call-arg]
            if not inspect.isawaitable(result):
                raise TypeError(
                    "non-blocking Runtime handlers must return an awaitable"
                )
            return await result

        worker = asyncio.create_task(asyncio.to_thread(spec.handler, inp, ctx))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            worker.add_done_callback(
                lambda finished: self._record_late_worker(reservation, finished)
            )
            raise
        if inspect.isawaitable(result):
            raise TypeError(
                "blocking Runtime handlers must return a completed output, not an awaitable"
            )
        return result

    def _record_late_worker(
        self, reservation: FiringReservation, worker: asyncio.Task[Any]
    ) -> None:
        try:
            worker.result()
            detail = "completed after cancellation; result suppressed"
        except asyncio.CancelledError:
            detail = "cancelled after Runtime suppression"
        except Exception as exc:  # noqa: BLE001 - lifecycle observation only
            detail = f"failed after cancellation: {type(exc).__name__}: {exc}"
        self._record_runtime(
            "suppressed",
            firing_id=reservation.context["firingId"],
            detail=detail,
        )

    @staticmethod
    def _failed_output(
        error_type: str, message: str, metadata: dict[str, Any]
    ) -> TransitionHandlerOutput:
        return TransitionHandlerOutput(
            status="failed",
            outputTokens={},
            error=HandlerError(type=error_type, message=message),
            metadata={"runtime": metadata},
        )

    def cancel_scope(self, scope: str) -> Cancellation:
        current = asyncio.current_task()
        targets = [
            task
            for task, active in self._active.items()
            if active.scope == scope and task is not current and not task.done()
        ]
        for task in targets:
            task.cancel()
        self._signal()
        return Cancellation(scope=scope, cancelled=len(targets))

    def _record_runtime(
        self,
        event: Literal["source_failed", "suppressed"],
        *,
        firing_id: str | None = None,
        source: str | None = None,
        detail: str,
    ) -> None:
        if self.journal is None:
            return
        record_runtime = getattr(self.journal, "record_runtime", None)
        if record_runtime is None:
            raise TypeError(
                "Runtime requires a journal with record_runtime() lifecycle support"
            )
        record_runtime(
            RuntimeRecord(
                event=event,
                netId=self.net.name,
                firingId=firing_id,
                source=source,
                detail=detail,
            )
        )
