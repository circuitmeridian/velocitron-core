# Native timed transitions: a declarative timer over token-carried deadlines

A transition may declare a **`timer`** — a structured, declarative temporal
enablement condition:

```json
{
  "name": "clear_on_cooldown",
  "handler": "clear",
  "timer": {
    "clock": "clock",
    "cel": "clock.now >= latch.fired_at + latch.cooldown_s",
    "bind": { "latch": "latch" }
  }
}
```

- **`clock`** (required) — the name of the **clock place**, whose first token
  is the time reference. An empty clock place means the transition is **not
  enabled** (no time reference ⇒ not matured).
- **`cel`** (required) — a CEL expression, compiled at parse (D6), evaluated
  per candidate binding against a fixed environment: the reserved variable
  `clock` is the clock token's `data`, and each `bind` variable is the `data`
  of the first token bound from its named place.
- **`bind`** (optional) — a map of CEL identifier → place name, exposing the
  candidate binding's tokens to the expression. Each named place must feed the
  transition through a consume- or read-mode arc (validated by the parser).

A timed transition is enabled only when its ordinary enablement holds
(arcs satisfiable, inhibits satisfied) **and** the timer condition holds for
the candidate binding **and** the guard (if any) accepts it. The timer is
evaluated per candidate binding, after the sub-multiset check and before the
guard (pure before possibly-impure), so binding enumeration skips unmatured
tokens and finds a matured one — per-instance deadline isolation falls out of
ordinary binding selection. A runtime CEL eval error degrades the condition to
false (symmetric with D6), never a crash.

Alongside the declaration, the engine gains one convenience method —
**`Engine.tick(net, marking, place, token, *, attempt, max_steps)`** — the
engine-owned re-evaluation loop: advance the clock (one
`inject_token(replace=True)`, ADR 0013) and `run` to quiescence. One advance
can mature several deadlines; `run`'s ordinary selection loop fires them all,
and inherits the firing policy (ADR 0014) and the failure budget (ADR 0015)
for free.

## Motivation

ADR 0013 gave consumers a sanctioned way to drive time into a net and
deliberately deferred native timed transitions. Early consumers of that seam
had to provide a wrapper for the remaining duties. Those experiments made
three responsibilities explicit that a native timed transition internalizes:

1. **When to advance the clock** — stays the consumer's decision. The engine
   never reads a wall clock; `now` arrives as token data through the ADR 0013
   injection seam. This is unchanged, and it is what keeps replay
   deterministic (ADR 0001's purity is what makes the journal a sufficient
   record).
2. **Which transitions are time-gated** — was implicit in guard handler code
   plus wrapper config (`timed_transitions=[...]`). It becomes **net-declared**
   via `timer`, so the temporal gate is visible in the net document, statically
   inspectable, and renderable — no longer an opaque `cooldown_elapsed` guard
   whose comparison lives only in Python.
3. **Re-evaluation after the clock moves** — was a consumer-owned poll loop
   with a risk of spinning. It becomes **engine-owned** via `tick`, built on
   `Engine.run`, inheriting the ADR 0015 failure budget so a fallible timed
   handler cannot spin the loop.

And the one thing the wrapper got right is **kept, not absorbed: the deadline
lives in the token.** The temporal comparison is over data the tokens already
carry (`clock.now - latch.fired_at >= latch.cooldown_s`); the engine holds no
per-instance timer state. In one line: the declaration and re-evaluation loop
belong to the engine, while deadlines remain token-carried and replayable.

## Decisions

- **Deadline-in-token beats timer-beside-transition.** A timer keyed only by
  transition cannot distinguish concurrently active token instances without
  separate net instances or additional host state. With the deadline in the
  token, one shared clock place serves any number of in-flight tokens with
  distinct deadlines — binding enumeration evaluates the timer per candidate
  binding, so a matured token is found even when an unmatured one precedes it
  — and the temporal state needed for replay is contained in the marking
  (journaled injections) plus the net declaration. The engine holds no
  separate timer state.

- **The timer is an enablement condition, not a selection concern.** Priority
  (ADR 0014) and the failure budget (ADR 0015) deliberately stayed
  selection-level to preserve ADR 0005's verification story. The timer is
  different in kind: a transition before its deadline is genuinely *not
  enabled* — exactly like a guard returning false — so it belongs in
  enablement/binding selection. It is declarative and pure (CEL over marking
  data), so it *strengthens* verifiability relative to the status quo, where
  the same condition hides in an impure guard handler.

- **A structured `{clock, cel, bind}` form, with CEL over declared aliases —
  not CEL over place names, and not a `delay` scalar.** Three rejected
  shapes:
  - *Environment keyed by place names* (no `bind` indirection): composition
    merge alias-qualifies place names (`latch` → `demoA.latch`, and a wired
    clock port fuses to `source.stream`-style names); dotted names are not CEL
    identifiers, so the expression string would break under merge — the exact
    place-name coupling composition must avoid for handlers. With `bind`, the
    CEL string references only stable local aliases; `merge_nets` rewrites the
    `clock`/`bind` *place values*
    (alias-qualify + fusion rewrite, like arc endpoints) and never touches the
    expression.
  - *A `delay`/`duration` scalar (`set_delay` literally)*: a bare duration has
    no start time; the engine would have to remember per-instance arming
    times, which is timer-beside-the-transition state in disguise and breaks
    replay. The CEL-over-token form keeps the arithmetic over data the tokens
    carry.
  - *A general CEL-over-binding guard*: strictly broader — it would replace
    named guards wholesale and reopen ADR 0002. The timer scopes the
    cross-token CEL environment to the temporal case (clock + explicitly
    bound places); the general form is outside this decision.

- **Cross-token CEL is sanctioned here, narrowly.** ADR 0013 blessed
  single-token timestamp predicates and kept cross-token comparison a guard,
  because an arc predicate sees one token. The timer's environment is the
  declarative, explicitly-scoped exception: the expression sees exactly the
  clock token plus the places named in `bind` — a closed, net-declared
  variable set, not an open binding. Nested-map activations
  (`clock.now`, `latch.fired_at`) are portable across all three CEL backends
  (the pure-Python backend requires the adapter to convert activations to
  celtypes, which the reference implementation does).

- **Empty clock place ⇒ not enabled.** Before any clock injection there is no
  time reference, so no timed transition can have matured. This is the
  conservative reading and gives nets a free "time is not flowing yet" gate.
  If the clock place holds several tokens the first (insertion order) is the
  reference; the singleton clock-advance pattern (`inject_token`
  `replace=True`, ADR 0013) keeps it at one in practice.

- **Compile at parse; eval error ⇒ condition false; validated structurally.**
  The timer's CEL compiles when the net is parsed (a compile error is a
  malformed net, D6). The parser also checks: the clock place is declared;
  every `bind` value names a source place of one of the transition's consume-
  or read-mode arcs (so the variable always resolves to a bound token); `bind`
  keys are simple identifiers and never the reserved `clock`. At eval time an
  error (missing field, type mismatch) degrades to condition-false — the
  transition is simply not enabled with that binding — mirroring predicate
  (D6) and guard (D9) degradation.

- **`tick` composes the existing primitives; it adds no new journal channel.**
  `tick` = `inject_token(replace=True)` + `run`. The injection is journaled
  through `record_injection` (ADR 0013) and the firings through
  `record_firing`, in one sequence stream — so a `tick`-driven timeline is
  replay-deterministic end to end, and the ADR 0015 exhaustion analysis
  applies unchanged to timed transitions with fallible handlers. Consumers
  needing append-mode injection (a deadline token rather than a clock advance)
  compose `inject_token` + `run` themselves.

## Consequences

- Timed nets need no `ClockWrapper`, `timed_transitions` config,
  `cooldown_elapsed` guard handler, or consumer poll loop. The temporal gate
  is net-declared, present in the parsed model, and available for renderers to
  inspect.
- `Transition` grows an optional `timer` field (schema + parser + engine);
  `merge_nets` rewrites timer place references the way it rewrites arc
  endpoints. All existing nets are unchanged (the field is optional; nothing
  changes unless declared).
- The pure-Python CEL adapter converts activations to celtypes so nested-map
  environments, including nested token `data`, are available to celpy
  predicates.
- Guards remain the escape hatch for temporal conditions the closed timer
  environment cannot express (and for impure gates), exactly as ADR 0002
  scopes them.
