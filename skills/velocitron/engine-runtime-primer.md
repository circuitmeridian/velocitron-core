# Engine & Runtime primer (Python)

Practical, current-API guide to executing a velocitron net with the Python reference implementation. The package is `velocitron` (`implementations/python/src/velocitron/`). Every symbol and signature below is from the live source; see `engine-vs-runtime.md` for choosing between the two drivers.

Two drivers exist:

- **`Engine`** (`velocitron.engine`) — a synchronous firing engine. You own the loop and the marking; firing is a pure step. Best for embedding, tests, deterministic replay, and custom control.
- **`Runtime`** (`velocitron.runtime`) — an `asyncio` supervisor around an `Engine`. It owns concurrency (bounded lanes), token sources, native timer scheduling, and lifecycle. Best for long-running coordination with real timers and concurrent handlers.

## Loading a net

```python
from velocitron.dsl.api import load_petrinet, parse_petrinet_text
from velocitron.parser import parse_net

net = load_petrinet("my_net.petrinet")        # from a .petrinet file
net = parse_petrinet_text(dsl_text, "<memory>")  # from DSL text in memory
net = parse_net(json_document)                  # from an already-parsed JSON dict
```

`net` is a validated, frozen `Net`. Its initial marking (if the DSL declared one) is `net.initial_marking`, a `Marking`.

A transition's `.handler` is `str | None`. Core JSON and DSL omit the handler key/fact
when it is `None`; absence means no behavior is bound, never an empty string, same-name
fallback, or no-op. Handlerless structure is still valid to parse, compose, inspect, and
visualize.

## Markings and tokens

A `Marking` (`velocitron.schema`) is an immutable, persistent `Mapping[str, Sequence[Token]]` — place name to the tokens it holds. It is built on `pyrsistent` structures, so every "mutation" returns a new marking sharing structure with the old; the input is never changed in place (this is what makes rollback and replay free).

```python
from velocitron.schema import Marking, Token

marking = Marking({"incoming": [Token("order", {"id": "A", "total_cents": 500})]})
marking = net.initial_marking or Marking()   # or start from the net's declared marking
tokens = marking.get("incoming", [])          # read a place like a dict
```

A `Token` is a frozen dataclass with `.type` (the color) and `.data` (a JSON-object dict). `Token.data` is a dict and therefore unhashable, so the engine compares tokens by equality, never by hashing.

The exact type string `token` is the **Generic token** convention for
classical/uncolored nets. It is still an ordinary color used for exact matching in
`accepts`, ports, markings, and arcs — not a wildcard or untyped value. Visualization
omits its generic type label without changing the core token or its `data`.

## Registering handlers

The `HandlerRegistry` (`velocitron.registry`) resolves explicitly declared handler refs to Python callables. There are four registries: transition handlers (do work, return tokens), guard handlers (return bool, may be impure), predicate handlers (return bool, must be pure), and firing policies (pick which enabled transition fires). A handlerless transition contributes no transition ref to resolve.

```python
from velocitron.registry import HandlerRegistry
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput

registry = HandlerRegistry()

def accept_order(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # inp["inputTokens"] is keyed by source place; one list per consume arc.
    order = inp["inputTokens"]["incoming"][0]
    return {
        "status": "completed",
        "outputTokens": {"accepted": [Token("accepted_order", order.data)]},
        "error": None,
        "metadata": {},
    }

registry.register_transition("accept_order@fulfil", accept_order)
# registry.register_guard("warehouse_open@fulfil", lambda inp: True)
# registry.register_predicate("order_is_fraudulent@fulfil", lambda inp: False)
```

A transition handler is a pure function of its declared inputs — it sees exactly the tokens the engine will consume (`inputTokens`), not the whole marking. Return `status="completed"` with `outputTokens` keyed by destination place (the engine validates each against the transition's produce templates), or `status="failed"` (the engine consumes nothing and records the failure — retry is net-modeled, not handler-internal). `outputTokens` may be empty for a consume-only (sink) transition. Inline CEL arc predicates need no registration; only *named* predicate/guard handlers do.

## Driving the Engine

```python
from velocitron.engine import Engine

engine = Engine(registry)                       # policy="first-found" by default

# Optional preflight: resolves only refs the net actually declares.
# Handlerless transitions are valid structure and are skipped.
engine.validate(net)

# Which transitions have a satisfiable binding right now?
enabled = engine.enabled_transitions(net, marking)   # list[str], declaration order

# Fire one transition. NOTE: fire/enabled use the transition NAME ("accept_order"),
# not the handler ref ("accept_order@fulfil") the registry is keyed by.
# attempt is the caller's step counter, used for deterministic firing ids.
new_marking, record = engine.fire(net, marking, "accept_order", attempt=0)
# record is a FiringRecord: status "completed"/"failed", input/output tokens, error, timestamps.
# On any failure the returned marking is the INPUT marking, unchanged (atomic rollback).

# Or run the selection loop to quiescence:
final = engine.run(net, marking, max_steps=1000)
```

Handler presence is not part of structural enablement, so a handlerless transition may
appear in `enabled`. `fire` preserves precedence: a disabled handlerless transition
returns an atomic `NotEnabled` failure; explicitly firing one that is enabled returns the
input marking unchanged and an atomic `failed` record with
`error.type == "HandlerNotFound"` plus a message that the transition has no handler.
Neither a same-name lookup nor a no-op is attempted. A produce template's literal `data`
— and its computed sibling `cel`, evaluated over the consumed `binding` for a pair the
handler left uncovered (ADR 0023) — does not give the transition structural firing
semantics.

`run` repeats: compute enabled transitions, ask the firing policy which to fire, fire it — until nothing is enabled, the policy returns `None`, or `max_steps` is hit. It returns the final `Marking`. Determinism: binding selection is lexicographic by token insertion order and the default `first-found` policy picks in declaration order, so the same net + inputs + handler results replay to the same firing sequence.

Engine options (`Engine(registry, *, ...)`):

- `policy="first-found"` — or `"priority"` (highest `Transition.priority` wins, ties by declaration order), or a custom policy you registered.
- `journal=None` — attach a `Journal` to record firings/injections (below).
- `deposit_violation="raise"` — or `"record_then_raise"` / `"record_then_drop"` (a handler returning a token with no matching produce template is a programmer error; these modes need a journal).
- `max_consecutive_failures=None` — opt-in failure budget: cap a transition's consecutive `failed` firings within a `run` (ADR 0015). Any completed firing resets the counts.
- `cel_adapter=None` — override the CEL backend (auto-detects Rust → C++ → pure Python).

## The firing journal

The journal is decoupled from the engine via hooks; the engine emits records and the journal owns `sequence` numbering. `JsonlJournal` is the default.

```python
from velocitron.journal import JsonlJournal

journal = JsonlJournal(prefix=None)             # in-memory only; prefix=<path> to write .jsonl
engine = Engine(registry, journal=journal)
engine.run(net, marking)
journal.flush()                                 # writes prefix-<UTC>.jsonl when prefix is set
records = journal._records                      # buffered records (firings + injections + injections share one sequence stream)
```

Firings and token injections flow through the same monotonic sequence stream, so a timeline that mixes handler firings with environment arrivals replays deterministically.

## Token injection (the environment-arrival seam)

Between firings, the one sanctioned way an external token enters a running net — a file arrival, an observation, a clock tick — is `inject_token`. It validates the token's type against the place's `accepts`, journals the arrival, and returns a new marking.

```python
# Append an arrival (e.g. a new observation enabling a gated transition):
marking, rec = engine.inject_token(net, marking, "review_queue", Token("doc", {"docId": "d1"}), attempt=0)

# Replace a place's contents — the singleton clock-advance pattern:
marking, rec = engine.inject_token(net, marking, "clock", Token("tick", {"now": 1000}), attempt=1, replace=True)

# A batch of append-only arrivals in one journal-consistent step (all-or-nothing validation):
marking, recs = engine.inject_tokens(net, marking, [("q", Token("doc", {"docId": "d2"}))], attempt=2)
```

## Timed transitions with the synchronous Engine

Time enters the net only as token data; the engine never reads a wall clock. A timed transition (a `timer` clause over a clock place) is enabled only when its temporal CEL holds for the bound tokens against the clock place's token. `tick` is the engine-owned advance-and-fire loop: it replaces the clock token (an `inject_token(replace=True)`) then `run`s to quiescence, so one advance can mature and fire several deadlines.

```python
# Advance the clock to now=1000 and fire everything that matured:
marking = engine.tick(net, marking, "clock", Token("tick", {"now": 1000}), attempt=0)

# Inspect upcoming maturities without advancing (the next future timestamp per timed binding):
for m in engine.timer_maturities(net, marking):
    print(m.transition, m.timestamp)
```

The synchronous engine ignores `timer maturity` (it just re-checks the CEL on each `tick`); maturity exists for the Runtime's scheduler.

## The async Runtime

`Runtime` wraps an `Engine` and supervises a net's long-lived execution: it admits enabled work onto bounded **lanes**, reconnects **token sources**, schedules **native timers**, and journals everything. Handlers are `async` and receive a `RuntimeContext` (for cooperative cancellation by scope). Because asynchronous execution requires a `HandlerSpec` for every transition, Runtime construction rejects a handlerless net rather than inventing behavior.

```python
import asyncio
from velocitron.runtime import Runtime, HandlerSpec, Lane, TokenArrival, RuntimeContext
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput

async def review(inp: TransitionHandlerInput, ctx: RuntimeContext) -> TransitionHandlerOutput:
    return {"status": "completed", "outputTokens": {"reviewed": [Token("review", {})]},
            "error": None, "metadata": {}}

runtime = Runtime(
    net=net,
    handlers={"review@svc": HandlerSpec(handler=review, lane="default")},  # keyed by HANDLER REF
    lanes={"default": Lane(4)},                                            # bounded capacity
)

async def main() -> None:
    await runtime.start()                       # admits currently-enabled work, starts sources/timers
    await runtime.inject(TokenArrival("queue", Token("doc", {"id": "d1"})))
    await runtime.wait_idle()                   # no active handler, all source mailboxes empty
    await runtime.stop()                        # cancel in-flight, settle
    # or: await runtime.run()  — start, then block until stop() or cancellation

asyncio.run(main())
```

Key points from the source:

- **`handlers` is keyed by each transition's explicit handler *ref*, not the transition name**, and must supply a `HandlerSpec` for every transition. A handlerless transition makes Runtime construction fail; no same-name or no-op fallback exists. Every `HandlerSpec.lane` must exist in `lanes`.
- **Lanes bound concurrency.** `Lane(n)` (or a bare `int`) caps how many firings of handlers on that lane run at once; the Runtime never exceeds a lane's capacity.
- **Native timers are event-driven.** The Runtime requires `timer.maturity` on every timed transition (it raises at construction otherwise), computes each binding's next maturity, and sleeps until exactly then via the clock's `sleep_until` — no polling. Injections and firings that change the marking signal re-admission, so a new deadline or a cancelled one reschedules immediately. Pass `clock="monotonic"` (default) or a custom `Clock` implementing `now()` and `async sleep_until(when)`.
- **Token sources** (`TokenSource`) are reconnectable, bounded producers of arrivals with a mailbox `policy` (`lossless`, `batch`, or `coalesce`); the Runtime supervises reconnect/backoff and admits arrivals through the same injection seam. Observe them via `runtime.source_health`.
- **`blocking=True` handlers** run in a bounded worker thread; cancelling their firing suppresses the eventual result (Python cannot stop a running thread), which is journaled as a runtime lifecycle record rather than depositing tokens.
- Attach a `journal` and the Runtime uses `deposit_violation="record_then_drop"` automatically (a supervised system should not crash on one bad deposit).
