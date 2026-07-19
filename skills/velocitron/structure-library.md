# velocitron structure library

A catalogue of reusable colored-Petri-net idioms for coordination, each with when to use it and a minimal `.petrinet` snippet. Snippets are written in the repo DSL (see `petrinet-language.md`) and kept small. Most executable examples spell out every transition behavior binding for readability. Omitting a handler fact leaves the transition honestly handlerless — it does not resolve to a same-name ref or register a no-op. Copy an idiom, rename its places/colors/handlers, and validate with `velocitron validate <file>.petrinet`.

Two framing rules run through all of these:

- **A choice is structural.** Two transitions sharing one input place, each with a mutually exclusive arc predicate, is an OR-split (choice/conflict). One transition with two output arcs is an AND-split that deposits to *both* — never model a choice that way.
- **The marking is the state.** Prefer routing decisions that are visible in the marking (a token's presence/absence, its `data` filtered by an arc predicate) over hidden guards. Guards are the escape hatch for impure, transition-wide decisions.

A chain statement may run through arbitrarily many alternating place/transition endpoints — `(a) -x-> [b] -y-> (c) -y-> [d] -z-> (e)` expands left to right into one arc per segment, and a chain may start at a transition or close a cycle back onto an earlier place. The snippets below write linear topology as one chain per line; break a chain wherever a shorter run reads better (canonical output re-emits one arc per line either way).

## Classical/uncolored structure

Use a bare `->` when color distinctions are not part of the model. It lowers to the
**Generic token**, whose exact core color is the ordinary string `token`; it is neither a
wildcard nor an untyped value. Visualization omits the generic color label, but parsing,
matching, composition, and canonical JSON retain `token`.

```petrinet
net classical "Handlerless classical Petri-net structure"
(ready) -> [move] -> (done)
marking initial (ready) <- $seed
$seed: token {}
```

This net is valid for structural validation, composition, and visualization. `[move]`
has no behavior binding: canonical DSL omits its handler fact, direct `Engine.fire`
returns an atomic `HandlerNotFound` failure without moving the token, and `Runtime`
rejects the net at construction. Add `[move] handler "move"` and register that explicit
ref when the pattern is meant to execute.

## Sequence (pipeline)

A straight chain: each transition consumes the previous stage's token and produces the next. Use for ordered stages with no branching. A linear pipeline is the natural fit for one arbitrary-length chain.

```petrinet
net sequence "Linear pipeline"
(raw) -item-> [stage_a] -partial-> (mid) -partial-> [stage_b] -done-> (finished)
[stage_a] handler "stage_a"
[stage_b] handler "stage_b"
marking initial (raw) <- $seed
$seed: item {}
```

## Choice / conflict (OR-split)

Two transitions share one input place; mutually exclusive arc predicates decide which is enabled for a given token. The marking (the token's `data`) picks the branch. Use for accept/reject, route-by-type, approve/decline. If two are simultaneously enabled over the same token they are in *conflict* — the firing policy (declaration order by default, or priority) resolves it deterministically.

```petrinet
net choice "Approve or reject by token data"
@approve: (decisions) -request-> [approve] -approved-> (approved)
@reject:  (decisions) -request-> [reject]  -rejected-> (rejected)
@approve predicate cel "amount <= 100"
@reject  predicate cel "amount > 100"
[approve] handler "approve"
[reject]  handler "reject"
marking initial (decisions) <- $req
$req: request {"amount": 50}
```

When the branch depends on work only the handler can do (an API result, an LLM decision), use **choice-by-handler** instead: one transition with several produce arcs to different places, and the handler deposits tokens to exactly the branch it chose. The produce templates declare the allowed `{type, place}`; the handler routes.

## Parallel fork-join (AND-split / AND-join)

An AND-split is one transition with multiple produce arcs — it deposits to *all* outputs, spawning parallel branches. An AND-join is one transition with multiple consume arcs — it is enabled only when *every* input place has a matching token, synchronizing the branches. Use for scatter/gather, fan-out-then-collect.

```petrinet
net fork_join "Fan out to two branches, then join"
(start) -job-> [fork] -left-> (left_todo)
[fork] -right-> (right_todo)
(left_todo)  -left->  [do_left]  -left_done->  (left_done)
(right_todo) -right-> [do_right] -right_done-> (right_done)
(left_done)  -left_done->  [join] -result-> (results)
(right_done) -right_done-> [join]
[fork] handler "fork"
[do_left] handler "do_left"
[do_right] handler "do_right"
[join] handler "join"
marking initial (start) <- $job
$job: job {}
```

## Mutex / semaphore (resource counting)

A resource place holds one token (mutex) or N tokens (semaphore). A transition *acquires* by consuming a permit and *releases* by producing it back. Enablement is automatic: no permit, no fire. Use for exclusive access, bounded concurrency, connection pools. The acquire/release cycle is one chain that closes back onto the resource place.

```petrinet
net mutex "One holder at a time"
(lock) -permit-> [enter] -in_use-> (critical) -in_use-> [leave] -permit-> (lock)
[enter] handler "enter"
[leave] handler "leave"
marking initial (lock) <- $permit
$permit: permit {}
```

For a semaphore of N, seed the resource place with `marking initial (lock) <- N * $permit`.

## Bounded producer-consumer (buffer via free-slot permits)

Model buffer capacity as a `slots` place seeded with N permits. The producer consumes a slot to place an item; the consumer frees a slot when it takes one. The producer stalls (no enabled binding) when slots run out — real backpressure, structurally. (`capacityPerColorKey` is an advisory checker only and does *not* gate firing, so use permits when you need enforcement.)

```petrinet
net bounded_buffer "Bounded queue via free-slot permits"
(slots) -slot-> [produce] -item-> (buffer) -item-> [consume] -slot-> (slots)
[produce] handler "produce"
[consume] handler "consume"
marking initial (slots) <- 3 * $slot
$slot: slot {}
```

## Retry with a limit

Carry an attempt counter in the token's `data`. The worker re-deposits an incremented attempt token on failure (choice-by-handler between a `done` result and a re-queued attempt); a `give_up` transition consumes attempts whose count reached the limit, gated by an arc predicate. This keeps retry *observable and replayable* in the marking rather than hidden in a handler loop.

```petrinet
net retry "Retry up to a limit, then give up"
(attempts) -attempt-> [work] -result-> (done)
[work] -attempt-> (attempts)
(attempts) -attempt-> [give_up] -abandoned-> (abandoned)
@give_up_arc: (attempts) -attempt-> [give_up]
@give_up_arc predicate cel "tries >= 3"
[work] handler "work"
[give_up] handler "give_up"
marking initial (attempts) <- $first
$first: attempt {"tries": 0}
```

The engine also offers an orthogonal, engine-level guard: `Engine(..., max_consecutive_failures=N)` caps a transition's consecutive `failed` firings within a `run` (ADR 0015). Use the net-structural counter when the limit is domain logic you want in the marking; use the failure budget when it is an operational safety cap.

## Timeout via a timer

A `pending` token carries a `dueAt` deadline. A timed `[timeout]` transition fires only once the injected clock reaches the deadline; a racing `[complete]` transition can consume the pending token first. Time enters the net only as data through the injection seam — the engine never reads a wall clock. Provide `timer maturity` (the next future timestamp) so a `Runtime` scheduler can sleep exactly until then; the synchronous `Engine` ignores maturity and re-checks on each `tick`.

```petrinet
net timeout "Complete before the deadline, else time out"
(pending) -task-> [complete] -result-> (completed)
(pending) -task-> [time_out] -expired-> (expired)
@tick_read: (clock) -tick->? [time_out]
[complete] handler "complete"
[time_out] handler "time_out"
[time_out] timer clock (clock) cel "clock.now >= task.dueAt"
[time_out] timer bind task (pending)
[time_out] timer maturity cel "task.dueAt"
marking initial (clock) <- $t0
marking initial (pending) <- $task
$t0: tick {"now": 0}
$task: task {"dueAt": 1000}
```

## Watchdog (heartbeat resets the deadline)

A latch token carries the last heartbeat time and a cadence. A timed `[alarm]` fires when the clock passes `fired_at + cadence` without a heartbeat; a heartbeat transition rearms the latch (updating `fired_at`), pushing the deadline out. Use for liveness monitoring and stale-work detection.

```petrinet
net watchdog "Alarm unless heartbeats keep arriving"
(latch) -beat-> [heartbeat] -beat-> (latch)
(latch) -beat-> [alarm] -alert-> (alerts)
@wd_clock: (clock) -tick->? [alarm]
[heartbeat] handler "heartbeat"
[alarm] handler "alarm"
[alarm] timer clock (clock) cel "clock.now >= beat.fired_at + beat.cadence_s"
[alarm] timer bind beat (latch)
[alarm] timer maturity cel "beat.fired_at + beat.cadence_s"
marking initial (clock) <- $t0
marking initial (latch) <- $latch
$t0: tick {"now": 0}
$latch: beat {"fired_at": 0, "cadence_s": 300}
```

## Saga / compensation

Each forward step deposits a durable "done" token recording what it did. A failure downstream triggers compensating transitions that consume those done tokens and reverse the effect, unwinding the saga. Use for multi-step external effects that need rollback (book, charge, ship — cancel, refund, recall).

```petrinet
net saga "Forward steps with compensations"
(intake) -purchase-> [reserve] -reserved-> (reserved) -reserved-> [charge] -charged-> (charged)
(charged) -charged-> [fail] -refund_req-> (to_refund) -refund_req-> [refund] -reserved-> (reserved)
(reserved) -reserved-> [cancel] -cancelled-> (cancelled)
[reserve] handler "reserve"
[charge] handler "charge"
[fail] handler "detect_failure"
[refund] handler "refund"
[cancel] handler "cancel_reservation"
marking initial (intake) <- $po
$po: purchase {}
```

## State machine as a net

Model a single entity's lifecycle as one token that moves between state places; each transition is an allowed state change. Because only one token exists, only one state place is ever marked. Use for document/order/ticket lifecycles where the states are mutually exclusive.

```petrinet
net ticket "Open -> in-progress -> closed"
(open) -ticket-> [start] -ticket-> (in_progress) -ticket-> [close] -ticket-> (closed)
(in_progress) -ticket-> [reopen] -ticket-> (open)
[start] handler "start"
[close] handler "close"
[reopen] handler "reopen"
marking initial (open) <- $ticket
$ticket: ticket {"id": "T-1"}
```

## Composition via ports and wires

Two nets each declare boundary **ports** (a facet on a place: `port input`/`port output` with a token type). A separate **composition** document `use`s the nets and `wire`s an output port to an equal-typed input port, fusing the two places into one. Composition is a distinct document kind — `use`/`wire` cannot appear inside a `net` document. Use to build a larger system from reusable subnets (chips).

`producer.petrinet`:

```petrinet
net producer "Emits a pulse"
(seed) -seed-> [emit] -pulse-> (pulse_out)
[emit] handler "emit"
(pulse_out) port output pulse
marking initial (seed) <- $ready
$ready: seed {}
```

`consumer.petrinet`:

```petrinet
net consumer "Receives a pulse"
(pulse_in) -pulse-> [receive] -receipt-> (received)
[receive] handler "receive"
(pulse_in) port input pulse
```

`system.petrinet` (the composition):

```petrinet
composition wired_system
use "producer.petrinet" as producer
use "consumer.petrinet" as consumer
wire producer.(pulse_out) -> consumer.(pulse_in)
```
