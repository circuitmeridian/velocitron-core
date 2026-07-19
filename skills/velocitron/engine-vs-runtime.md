# Engine vs. Runtime: which to reach for

Both drivers execute the same nets with the same semantics — the `Runtime` literally wraps an `Engine` and shares its `HandlerRegistry`, `Journal`, firing policy, and failure budget. The choice is about *who owns time, concurrency, and the loop*, not about net behavior. Grounded in `velocitron.engine` and `velocitron.runtime`.

## Use the Engine alone when…

The `Engine` is synchronous and stateless between calls: it holds no marking, no clock, no threads. You pass a `Marking` in and get a new `Marking` (and a `FiringRecord`) back. Reach for it when:

- **You own the loop.** Embedding the engine in a larger system that decides when to step — a projection adapter that rebuilds the marking from external state and then fires, a CLI that fires one transition per command, a batch job.
- **Determinism and replay matter most.** `fire`/`run` are pure functions of net + marking + handler results; the journal numbers firings and injections in one stream. Same inputs replay to the same sequence, with no scheduler nondeterminism to reason about.
- **Testing.** Firing is a single call with an explicit `attempt`; assert on the returned marking and record. No event loop, no timing.
- **Time is discrete and you drive it.** `tick(net, marking, place, token)` advances the clock token and fires everything that matured, synchronously, to quiescence. Perfect when "time" is a simulation step or an externally supplied timestamp rather than a real deadline you must sleep until.
- **No concurrency is needed.** Handlers run one at a time, in-thread, in deterministic order.

The cost you accept: no built-in timer scheduling (you call `tick` yourself), no concurrency, no source supervision, no lifecycle — those are your responsibility.

## Reach for the Runtime when…

The `Runtime` is an `asyncio` supervisor that owns the marking and runs until stopped. Reach for it when:

- **Real timers.** You need a timed transition to fire *when its deadline actually arrives* in wall/monotonic time. The Runtime requires `timer.maturity` on every timed transition, computes each binding's next maturity, and sleeps until exactly then via the clock's `sleep_until` — event-driven, not polled. Marking changes (injections, firings, cancellations) reschedule immediately. (The Engine's `tick` only fires deadlines you manually advance past.)
- **Concurrent handlers.** Handlers are `async` and run on bounded **lanes** (`Lane(n)`), so independent work proceeds in parallel up to each lane's capacity while the Runtime enforces the bound. Use `blocking=True` to run a synchronous/CPU-bound handler in a bounded worker thread.
- **Long-running coordination.** A service that stays up, admitting enabled work as tokens arrive, rather than a one-shot `run` to quiescence. `start()` / `run()` / `stop()` / `wait_idle()` are the lifecycle.
- **External event streams.** `TokenSource`s are reconnectable, bounded producers (mailbox policies `lossless` / `batch` / `coalesce`) that the Runtime supervises with retry/backoff and admits through the injection seam; watch them via `source_health`.
- **Cooperative cancellation.** Handlers get a `RuntimeContext` and can `ctx.cancel(scope=...)` to cancel sibling firings in a scope (e.g. abandon a race once one branch wins).

The cost you accept: an event loop, `async` handlers, and scheduler-level concurrency to reason about (still deterministic per-net-semantics, but interleaving of concurrent handlers is real).

## Decision at a glance

| Need | Engine | Runtime |
| --- | --- | --- |
| Own the stepping loop yourself | ✅ | — |
| Deterministic replay / unit tests | ✅ (simplest) | ✅ (shares the engine) |
| Discrete/simulated time you advance | ✅ (`tick`) | ✅ |
| Real deadlines that fire on their own | manual `tick` | ✅ (native, event-driven) |
| Concurrent handlers, bounded parallelism | — | ✅ (lanes) |
| Long-lived service, arrives-as-it-goes | — | ✅ (lifecycle) |
| Reconnectable external token sources | — | ✅ (`TokenSource`) |
| Cancel sibling firings by scope | — | ✅ (`RuntimeContext`) |

## They share a core

Because the Runtime wraps an Engine, everything net-level is identical: enablement, binding selection, the produce contract, the firing policy (`first-found` / `priority` / custom), the opt-in failure budget (`max_consecutive_failures`), and the journal's single sequence stream over firings and injections. Design and validate the net once (`velocitron validate`), test its logic against the `Engine`, then run it under the `Runtime` when you need timers, concurrency, or a long-lived lifecycle — the net does not change. See `engine-runtime-primer.md` for the concrete API of each.
