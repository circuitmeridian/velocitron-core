# A token-injection seam, not a timer system, carries clock/deadline time

velocitron gains one engine method — `Engine.inject_token(net, marking, place,
token, *, attempt, replace=False)` — and one journal channel —
`Journal.record_injection` carrying an `InjectionRecord`. Together they are the
sanctioned, journal-deterministic way for a runtime consumer to advance a clock
token or add a deadline token. Native timed transitions remain future work; this
is deliberately **not** a timer system.

## Motivation

velocitron has no native timed transitions. Timing enters a net as **token
data** (a `tick`/`clock` token carrying `now`, a work token carrying
`enqueued_at`/`deadline_at`, all in epoch seconds) plus a thin **consumer
wrapper** that, between firings, advances the clock and re-checks enablement.
Every timed net in the Phase-B modeling study (5 of 6) hand-rolls this wrapper,
and two problems recur:

- **Unsanctioned marking access.** The wrapper reaches into the `Marking`
  directly to inject or advance tokens. The marking is otherwise an engine-owned
  immutable structure; ad-hoc external mutation has no contract and no
  validation (a wrapper can deposit an ill-typed token into a place).
- **Non-deterministic replay across injected time.** The engine's journal
  records firings, but a wrapper's clock advances happen *between* firings and
  are invisible to it. Replaying the journal (re-run and compare, D5) cannot
  reproduce the injected-time sequence, because the injections were never
  recorded.

The seam fixes both with the smallest possible surface.

## What it is (and is not)

`inject_token` is one write primitive with two modes:

- **inject** (`replace=False`) — append the token to the place. The
  deadline-token pattern: a fresh `deadline` token in a gate place enables a
  timed transition (e.g. `escalate_timeout`).
- **update** (`replace=True`) — replace the place's entire contents with the
  single token. The singleton clock-advance pattern: bump the one `tick` token's
  `now`. Intended for a place holding a single clock/deadline token.

It returns a new persistent `Marking` (untouched places shared structurally) and
an `InjectionRecord`, emitted through `record_injection`. It validates the token
`type` against the place's `accepts` and does **not** re-drive enablement — the
consumer calls `enabled_transitions` after injecting. It holds no timers, no
scheduler, no `TimerManager`; deciding *when* to advance the clock and *when* to
wake stays the consumer's job (and the eventual native-timed-transition work,
for which petrus is the ergonomics reference).

## Decisions

- **A dedicated `record_injection` journal channel, not an overloaded
  `record_firing`.** An injection is a consumer-driven marking event, not a
  firing; `record_firing`'s contract is explicitly "per firing attempt." Routing
  injections through it would overload that meaning and muddy the firing
  sequence. A third channel — sibling to `record_deposit_violation` — keeps the
  three event kinds distinct while a single-stream journal (`JsonlJournal`)
  numbers them together, so an injection and an interleaved firing occupy
  consecutive `sequence` slots and replay is deterministic across injected time.
  The cost is a real contract growth: the `Journal` protocol goes from two
  methods to three, so every `Journal` implementation must add `record_injection`
  (the in-repo `JsonlJournal` and the test spies do). That cost is warranted —
  the alternative silently breaks replay for any journal that doesn't record
  injections.

- **`inject` vs `update` as a `replace` flag on one method, not two methods or a
  magic weight.** Both modes are motivated by real modeled nets (append a
  deadline; replace a clock tick), and they differ only in whether the place's
  prior contents survive. One method with a boolean keeps the surface minimal;
  the `InjectionRecord.kind` (`"inject"`/`"update"`) and `replaced` field make
  the distinction auditable in the journal.

- **Type validation raises immediately (not journaled-then-raised).** Injecting a
  token whose type the place does not accept is a programmer bug, like a deposit
  violation under `raise`. It raises `ValueError` before any marking change or
  journal emission — the seam cannot smuggle an ill-typed token past the net's
  structure. Unlike deposit violations it has no `record_then_*` mode; an
  injection is consumer-initiated configuration, so failing fast is the only
  sensible behavior.

- **Single-token CEL over timestamp fields is blessed; cross-token comparison
  stays a guard.** An inline CEL predicate sees one token's `data` (D6). Integer
  epoch-seconds arithmetic and comparison (`now - enqueued_at > 10`,
  `now >= deadline_at`) are portable across all three CEL backends (celpy,
  cel-expr, cel-rust), so a single-token timestamp predicate is sanctioned and
  backend-portable. A cross-token comparison (a clock token vs a work token) is
  not expressible as a single-token predicate — naming the other token's field
  is an eval error — so it stays a **guard** over the full input binding (which
  may be impure, ADR 0002), exactly as the modeled cooldown/deadline guards are.

## Consequences

- Consumers get a first-class, validated, journal-recorded way to drive time
  into a net; they no longer mutate the marking directly.
- Replay (D5) holds across injected time: the injection records are part of the
  journal stream, so a re-run that injects the same tokens in the same order
  reproduces the recorded journal (excluding `timestamps`, as for firings).
- The `Journal` protocol is now a three-method contract. External journal
  implementations must add `record_injection`; the change is additive and the
  method mirrors the existing two.
- Native timed transitions (a `delay`/`deadline` on a transition, an
  engine-owned clock) remain a future feature. This seam is the prototype the
  consumer wrappers were already approximating, and the substrate that feature
  would build on — not a replacement for it.

## Amendment — the general environment-arrival seam

This ADR framed injection as a clock/deadline seam, but real consumers
(spike 3) stream arbitrary environment observations through it: file
arrivals, external events, observation tokens — anything the environment
delivers between firings. Nothing in the mechanism is time-specific — the
validation (place `accepts`), the journal channel (`record_injection`
sharing the firing sequence stream), and the persistent-marking return all
apply to any external arrival — so the seam is hereby blessed as the general
**environment-arrival seam**: the one sanctioned, journaled,
replay-deterministic way any external token enters a running net. Clock
ticks and deadline tokens remain the motivating (and still fully supported)
special case; the `replace=True` update mode keeps its singleton
clock-advance intent.

Two surface additions accompany the generalization: `InjectionRecord` gains
a first-class `attempt: int` field (previously only string-embedded in
`injectionId`, whose format is unchanged), and `Engine.inject_tokens` adds a
batch convenience — all-or-nothing validation, one `InjectionRecord` per
token on consecutive sequence slots — for the arrival pattern where a poll
finds several environment tokens at once (see spec/firing-semantics.md (f)).
