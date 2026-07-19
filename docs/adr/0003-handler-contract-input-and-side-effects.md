# Handler contract: input tokens + context, side effects as places

Transition handlers receive `{transitionId, inputTokens, firingContext}` — not the full marking. They return `{status, outputTokens, error, metadata}` — no `emittedEvents`. All side effects are modeled as places (`requested → deliver → completed`), not returned in handler results.

**Considered options:**
- Handler receives full marking (read-only). Rejected: creates an implicit dependency on global state, making handlers non-reproducible and non-testable in isolation. If marking-aware behavior is needed, model it as a guard or restructure the net so the relevant state is in input tokens.
- Handler returns `emittedEvents` for side effects. Rejected: two competing side-effect mechanisms (events + places) violates the "model everything, hide nothing" principle. The side-effect triplet is the single mechanism.
- Input tokens + context, side effects as places (chosen). Maximally testable, reproducible, and inspectable. All state is in the marking; all side effects are in the net.

## Amendment: optional transition behavior binding (2026-07-15)

The original decision above governs a transition handler **when one is bound**. A
Transition's behavior binding is now optional: core JSON omits the `handler` key, and the
parsed `Transition.handler` is `str | None` with default `None`. When present, the ref
remains a nonempty string. `null` and `""` are rejected rather than treated as absence.

Handlerless transitions are honest Petri-net structure for parsing, composition, and
visualization. Composition preserves the optional binding unchanged. The DSL likewise
omits the handler fact when no binding exists; it does not invent a ref from the
transition name.

**Execution boundary:** the synchronous Engine may inspect and validate handlerless
structure. `Engine.validate(net)` resolves only refs actually declared. Structural
enablement is checked first: explicit `Engine.fire` of a disabled handlerless transition
returns an atomic `NotEnabled` failure. Once enabled, firing a handlerless transition
returns an atomic failed record with `error.type == "HandlerNotFound"` and a message that
the transition has no handler; the marking is unchanged. There is no structural firing,
implicit output production, or no-op behavior. The asynchronous Runtime requires a
`HandlerSpec` for every transition and therefore rejects a handlerless net at
construction.

**Alternatives rejected:**

- **Empty string or `null` as "no binding."** Rejected because two serialized
  representations of absence weaken validation and make composition/round trips
  ambiguous. Absence is exactly an omitted key / `None`.
- **Implicit same-name binding.** Rejected because a transition name identifies
  structure, not registry behavior; silently coupling the namespaces hides missing
  configuration.
- **Implicit no-op or structural firing.** Rejected because it invents behavior,
  conflicts with net purity, and makes execution appear successful when no behavior was
  supplied.