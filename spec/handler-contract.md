# Handler Contract

The canonical, prose-and-types definition of the **runtime handler registry contract** for
a colored Petri net — the binding layer that turns the *named refs* a net may carry
(`handler`, `guard`, predicate `handler`) into callable behavior. A transition's behavior
binding is optional; when present, the net declares *which* handler runs *where* and the
registry supplies *what it does* (ADR 0003 and its amendment). This document is the one
`spec/net-schema.md` repeatedly forwards to: binding a declared name to a callable is the
registry's job, never the net's.

Composition (joining nets through ports and wires) is a **separate document**,
`spec/composition.md`. The firing engine — enablement detection, consume/deposit, the
deterministic/replayable firing journal, and the firing-policy handler's *integration*
into that engine — is defined separately in `spec/firing-semantics.md`. This contract is
the layer both build on: it pins down what each handler kind *receives* and *returns*,
and lands the illustrative type definitions that make the contract machine-checkable.

Unlike `net-schema.md` (a serialized net document with an embedded JSON Schema), the
contract here is **normative prose plus illustrative type definitions** — handlers are
code bindings, not serialized net structure. The I/O messages are JSON-serializable (the
firing journal records them), but their schema belongs to `firing-semantics.md`; this
document defines no embedded JSON Schema for the messages. The Python `TypedDict`/
`Protocol` shapes reproduced below are the machine-checkable surface that keeps prose and
code from drifting; their names match
`implementations/python/src/velocitron/contract.py` and `registry.py` exactly.

## Purpose and the registry principle (ADR 0003)

The registry binds **declared names to callables**. A net is pure coordination: it routes
and gates, never computes (ADR 0001, stated in `net-schema.md`). All executed computation
— data transformation, classification, LLM calls, human decisions, side effects — lives
in handlers resolved by name through the registry. A handlerless transition declares
structure only; it has no implicit same-name or no-op behavior.

Keeping an explicit binding in the registry, not the net, is what makes a net portable
between the Python reference and TypeScript core implementations and lets the same
explicitly handled net run under different bindings — a real handler in an application,
a stub in tests (`net-schema.md`, "Transitions, handlers, guards, priority").
Resolution is the registry's job, never the net's.

## The four handler kinds

There are exactly four handler kinds. Each receives a typed input, returns a typed
output, and carries a purity rule. The illustrative Python shapes are reproduced per kind
so prose and code cannot drift.

**Boolean return constant.** Guard and predicate handlers must return an actual boolean.
Only the exact value `True` satisfies their condition; exact `False` rejects it. A
non-boolean return is a condition-evaluation error, never an input to Python,
JavaScript, or another host's truthiness rules. It degrades through the existing
surface-specific error posture: predicate false and guard not-enabled.

### Optional transition behavior binding

Core JSON omits `handler` when a transition has no behavior binding. The parsed
`Transition.handler` is `str | None`, defaulting to `None`; a present ref must be a
nonempty string, so `null` and `""` remain invalid. Absence does not synthesize a
same-name ref or a no-op. Handlerless nets remain valid structure for parse,
composition, and visualization. The Engine and Runtime execution boundaries are
specified under the registry API below and in `firing-semantics.md`.

### Transition handler

The **work-doer** when a transition has a behavior binding (ADR 0003). A transition
handler is a pure function of its declared inputs — it sees exactly the tokens the
engine will consume, not the full marking.

- **Receives** `{transitionId, inputTokens, firingContext}`. `inputTokens` is the resolved
  binding: one entry per consume arc (keyed by source place name), each the list of tokens
  that arc matched. The handler sees exactly the tokens the engine will consume — *not*
  the full marking. If marking-aware behavior is needed, model it as a guard or
  restructure the net so the relevant state is in input tokens (ADR 0003: maximally
  testable, reproducible).
- **Returns** `{status, outputTokens, error, metadata}`.
  - `"completed"` — the engine validates every handler token against all of the
    transition's declaration-ordered produce templates: the token's destination and
    `type` must match at least one template (`net-schema.md`, "Produce template: routing
    contract"). Parallel templates may share a destination and may declare different
    types; no destination-keyed overwrite is permitted. Handler tokens are deposited
    once in their returned order. For every template whose destination/type pair has no
    handler token, literal `data`, when present, emits that template's fixed token in
    template declaration order. Handler tokens for a pair override its literal
    fallbacks. `outputTokens` may be empty (a consume-only transition, e.g. a commit).
  - `"failed"` — the engine does **not** consume input tokens (no marking change); it
    records the failure. Retry is net-modeled (places/arcs drive the next attempt), not
    handler-internal. This keeps retry observable and replayable.
  - `metadata` — opaque to the engine; recorded in the journal for observability. Must
    not drive firing decisions.
- **Purity** — a transition handler is a pure function of its declared inputs. It may be
  impure in the sense that it performs the work (calls out, mutates the world); the
  contract is that its *result* is a pure function of `{transitionId, inputTokens,
  firingContext}`. Side effects are modeled as places (the `requested → deliver →
  completed` triplet), not returned as events (ADR 0003).
- The only defined statuses are `"completed"` and `"failed"`; `"pending"` is not valid.

```python
class TransitionHandlerInput(TypedDict):
    transitionId: str
    inputTokens: dict[str, list[Token]]
    firingContext: FiringContext

class TransitionHandlerOutput(TypedDict):
    status: Literal["completed", "failed"]
    outputTokens: dict[str, list[Token]]
    error: HandlerError | None
    metadata: dict[str, Any]

class TransitionHandler(Protocol):
    def __call__(self, inp: TransitionHandlerInput) -> TransitionHandlerOutput: ...
```

### Guard handler

A **transition-level gate** (ADR 0002). A guard sees the full input binding across *all*
input arcs and decides whether the transition is enabled (subject to arc enablement).
This is the seam where impure, transition-wide decisions live, kept separate from pure
per-token predicates.

- **Receives** `{transitionId, inputTokens, firingContext}` — the **same shape** as the
  transition handler input (`GuardHandlerInput` is an alias for `TransitionHandlerInput`):
  the full input binding across all input arcs (contrast: predicates see one token).
- **Returns** `boolean`. Exact `True` = transition enabled (subject to arc enablement);
  exact `False` = transition not enabled. Non-booleans follow the boolean return constant.
- **Purity — may be impure** (ADR 0002). A guard may consult external state (filesystem,
  clock, API). This is the seam where impure, transition-wide decisions live. The
  contract makes the guard-vs-predicate split syntactically and type-signature visible so
  an impure guard cannot masquerade as a pure predicate.

```python
# A guard receives the same input shape as a transition handler: the full
# input binding across all input arcs (contrast: predicates see one token).
GuardHandlerInput = TransitionHandlerInput

class GuardHandler(Protocol):
    def __call__(self, inp: GuardHandlerInput) -> bool: ...
```

### Predicate handler

An **arc-level pure filter** over a single token (ADR 0002). The predicate sees one
token — the candidate for one consume arc — not the full binding.

- **Receives** `{token, firingContext}`. A single candidate token for one consume arc,
  plus the firing context.
- **Returns** `boolean`. Exact `True` = token matches the arc; exact `False` = filtered
  out. Non-booleans follow the boolean return constant.
- **Purity — must be pure** (ADR 0002). No side effects, no external state, deterministic
  given the same token. The Python reference and TypeScript core enforce this by
  contract rather than by inspecting arbitrary registered callables. Inline CEL
  predicates are pure by construction; named predicate handlers are the escape hatch
  for logic too complex for CEL and must uphold the same purity.

```python
class PredicateHandlerInput(TypedDict):
    token: Token
    firingContext: FiringContext

class PredicateHandler(Protocol):
    def __call__(self, inp: PredicateHandlerInput) -> bool: ...
```

### Firing policy handler

**Selects which enabled transition to fire** (ADR 0005). The policy is opaque: it picks
which to fire; the engine handles binding and firing.

- **Receives** `{marking, enabledTransitions, priorities, consecutiveFailures}`.
  `marking` is the full current marking; `enabledTransitions` is the list of transition
  ids currently enabled (arcs satisfiable AND guard true; with a failure budget
  configured, exhausted transitions are excluded — `firing-semantics.md` (c)/(e), ADR
  0015); `priorities` maps each `enabledTransitions` entry to its declared
  `Transition.priority` (an absent declaration is `0`), so a priority-aware policy needs
  no access to the net (ADR 0014); `consecutiveFailures` maps each `enabledTransitions`
  entry to its consecutive-failure count within the current run (no failure history is
  `0`; any completed firing resets every count — deterministic, derived from the firing
  sequence, never wall-clock), so a failure-aware policy (skip, deprioritize,
  attempt-based backoff) needs no engine access and stays replayable (ADR 0015).
  `marking` is an immutable `Marking` — a persistent `Mapping[str, Sequence[Token]]`
  (structurally shared, place → token-sequence) — consistent with the policy being
  opaque/non-mutating (ADR 0005); handlers typed against `Mapping[str, Sequence[Token]]`
  receive it natively.
- **Returns** `str | None` — one transition id to fire, or `None` to stop (no fire this
  step). Selection and firing are sequential; concurrent firing is not part of this
  contract.
- **Default** — `first-found`: returns the first entry of `enabledTransitions`.
  Deterministic iteration order (list order), so under the default policy replay holds
  (ADR 0005). Custom policies are opaque; verification falls back to "all firings
  consistent with any policy" (ADR 0005).
- **Built-in opt-in** — `priority`: returns the highest-`priorities` enabled transition;
  ties (and the all-default case) fall back to the first maximal entry in
  `enabledTransitions` order, so it stays deterministic/replayable and degrades to
  `first-found` when no transition declares a priority. Never the default — configure via
  `Engine(registry, policy="priority")` (ADR 0014). Both built-ins ignore
  `consecutiveFailures`; the engine-level failure budget
  (`Engine(registry, max_consecutive_failures=N)`, `firing-semantics.md` (c)) is the
  built-in failure response, working under any policy by excluding exhausted transitions
  before the policy sees them (ADR 0015).
- **Scoping — engine-level, not net-declared** (ADR 0005). `net-schema.md` has no
  `firingPolicy` field. The policy is configured on the engine, with one policy active
  for a run and `first-found` as the default.

```python
class FiringPolicyInput(TypedDict):
    marking: Marking
    enabledTransitions: list[str]
    priorities: dict[str, int]
    consecutiveFailures: dict[str, int]

class FiringPolicyHandler(Protocol):
    def __call__(self, inp: FiringPolicyInput) -> str | None: ...
```

## FiringContext

`firingContext` is passed to **every** handler kind. It is a **closed shape** — exactly
these four fields, no additional runtime-specific fields. The same closed shape is used
by the Python reference and TypeScript core.

- `firingId: str` — unique id for this firing attempt. **Deterministic for replay**
  (derived from `netId` + transition + `attempt`, not a random UUID).
- `attempt: int` — retry counter, `0` for the first attempt. Lets a handler distinguish a
  fresh fire from a net-modeled retry (a retry re-invokes the handler with
  `attempt + 1`).
- `netId: str` — the net's `name`.
- `timestamps: {fired_at: ISO8601}` — wall-clock when the fire was initiated.

`timestamps` is **metadata/logging only**. The contract states that handlers **MUST NOT
branch on `timestamps` for firing decisions**. A handler that branches on wall-clock time
is non-replayable by construction and violates the contract. Timing that drives behavior
belongs in net-modeled state, not in `firingContext`.

```python
class FiringTimestamps(TypedDict):
    fired_at: str

class FiringContext(TypedDict):
    firingId: str
    attempt: int
    netId: str
    timestamps: FiringTimestamps
```

A structured failure reported by a transition handler is:

```python
class HandlerError(TypedDict):
    type: str
    message: str
```

## Handler registry API

The registry binds declared names to callables, **per kind**. A transition without a
behavior binding contributes no transition ref for the registry to resolve. For every
present ref, the registry supplies behavior (ADR 0003 amendment).

**Per-kind namespaces.** Resolution is dispatched by kind, so a transition ref and a guard
ref may share a name with no collision — a transition named `"commit"` and a guard named
`"commit"` resolve to different callables. This is inherent in the per-kind
`register_*`/`resolve_*` API; there is **no** cross-kind `register(kind, name, …)` form.
The per-kind API is the only interpretation consistent with the landed surface.

The API:

- `register_transition(name, handler)`, `register_guard(name, handler)`,
  `register_predicate(name, handler)`, `register_firing_policy(name, handler)`.
- `resolve_transition(name) -> TransitionHandler` (and the per-kind resolvers
  `resolve_guard`, `resolve_predicate`, `resolve_firing_policy`).
- **Resolve-miss and absent-binding execution** use the typed error
  `HandlerNotFound`, but preserve their distinct causes. A declared ref that is absent
  from its per-kind namespace is a registry resolve-miss. After structural enablement
  succeeds, an explicit Engine fire of a handlerless transition has no ref to resolve;
  the Engine returns an atomic `failed` firing record with
  `error.type == "HandlerNotFound"` and a clear message that the transition has no
  handler. A disabled handlerless transition retains the earlier `NotEnabled` outcome.
  The Engine does not invent a lookup name, invoke a no-op, consume tokens, or emit
  literal produce-template data.
  Three boundaries follow. (1) A **net-referenced** handler
  (transition/guard/predicate) resolved within `fire`/enablement: a missing registration
  fails that transition rather than aborting the Engine — a `failed` record /
  not-enabled / predicate-false (`firing-semantics.md` (b)/(a)). The direct primitive and
  `run` paths retain this graceful degradation; `run` does not auto-validate.
  (2) The **firing-policy** ref (Engine config, not net-referenced) is validated at
  `Engine.__init__` as a configuration error (`firing-semantics.md` (e)).
  (3) `Engine.validate(net)` is a **public, opt-in** instance method that resolves only
  refs actually declared: each present transition `handler`, transition `guard`, and
  consume-arc named predicate `handler`. It skips an absent transition binding and raises
  `HandlerNotFound` on the first declared but unresolvable ref, before any `run`.
  **Runtime has the stricter execution boundary:** asynchronous execution requires a
  `HandlerSpec` for every transition, so Runtime construction rejects a handlerless net.
- **Default firing policy** — `first-found` is registered under the reserved name
  `DEFAULT_FIRING_POLICY` (`"first-found"`) on every fresh registry, so it is resolvable
  out of the box when no custom policy is configured (ADR 0005). The built-in `priority`
  policy is likewise registered under `PRIORITY_FIRING_POLICY` (`"priority"`) on every
  fresh registry — resolvable out of the box, opt-in, never the default (ADR 0014).

```python
DEFAULT_FIRING_POLICY = "first-found"
PRIORITY_FIRING_POLICY = "priority"

class HandlerNotFound(Exception):
    """Raised when a declared handler ref cannot be resolved in the registry.

    Registry surfaces: (1) declared net refs (transition/guard/predicate)
    resolved within fire/enablement -> transition failure, not a crash
    (firing-semantics.md (b)/(a)), retained on the run path; (2) the
    firing-policy ref (Engine config) -> configuration error at
    Engine.__init__; (3) Engine.validate(net) -> propagates uncaught on the
    first unresolvable declared ref. After enablement, Engine also uses
    ``HandlerNotFound`` as the structured error type for an explicit fire of a
    handlerless transition, with a no-handler message; a disabled transition
    remains ``NotEnabled``. Absence is not a registry lookup under an invented
    name.
    """

def _first_found(inp: FiringPolicyInput) -> str | None:
    """The default firing policy: the first enabled transition, or None.
    Deterministic iteration order (list order) for replay (ADR 0005)."""
    enabled = inp["enabledTransitions"]
    return enabled[0] if enabled else None

class HandlerRegistry:
    """Per-kind handler namespaces with register/resolve."""

    def __init__(self) -> None:
        self._transitions: dict[str, TransitionHandler] = {}
        self._guards: dict[str, GuardHandler] = {}
        self._predicates: dict[str, PredicateHandler] = {}
        self._policies: dict[str, FiringPolicyHandler] = {}
        self.register_firing_policy(DEFAULT_FIRING_POLICY, _first_found)

    def register_transition(self, name: str, handler: TransitionHandler) -> None: ...
    def resolve_transition(self, name: str) -> TransitionHandler: ...
    def register_guard(self, name: str, handler: GuardHandler) -> None: ...
    def resolve_guard(self, name: str) -> GuardHandler: ...
    def register_predicate(self, name: str, handler: PredicateHandler) -> None: ...
    def resolve_predicate(self, name: str) -> PredicateHandler: ...
    def register_firing_policy(self, name: str, handler: FiringPolicyHandler) -> None: ...
    def resolve_firing_policy(self, name: str) -> FiringPolicyHandler: ...
```

Each `resolve_*` raises `HandlerNotFound` when the name is absent; the body in every case
is the same shape — return the stored callable, or convert the `KeyError` into a
`HandlerNotFound`.

## Composition-safe handlers

Composition (`spec/composition.md`) produces one merged net by **rewriting place
names**: the merge alias-qualifies every place and transition (`<alias>.<name>`) and,
for each wire, **fuses** the wired output/input port-places into a single shared place
named by the sorted `__`-concatenation of the equivalence class's source (output) port
qualified names (`spec/composition.md`). Both halves of the
transition handler's I/O are keyed by place name — `inputTokens` by source place,
`outputTokens` by destination place — so a handler that branches on literal place names
is broken by *every* merge: post-merge it either reads nothing (its input key no longer
exists; a wired input port's tokens arrive under the upstream producer's name) or trips
the deposit contract (its hardcoded destination matches no rewritten produce template).
This coupling was discovered by the first consumer to actually *execute* a merged net;
`implementations/python/tests/test_composition_firing.py` locks the surviving idiom on
an executed merged net.

The composition-safe contract is:

- **Read inputs by token type, never by place key.** Select from `inputTokens` by
  scanning all bound tokens (`inputTokens.values()`) for the token `type` (color) the
  handler needs; never index `inputTokens` by a literal place name. Token types are
  **invariant under a merge** — fusion never rewrites a token's color — so type-keyed
  selection survives alias qualification, fusion, fan-in, and fan-out unchanged.
- **Produce outputs against resolved destinations, never hardcoded place names.**
  `outputTokens` must be keyed by the `destination` of the transition's produce
  templates *as they exist in the net being run* — the merge rewrites every template's
  `destination` to its qualified (or fused) name, and the engine validates deposits
  against the rewritten templates. When a handler must name a destination, bind the
  **resolved destination** at registration time — a handler factory closing over the
  qualified name, registered under an instance-scoped ref (e.g. `emit@<alias>`) — never
  a pre-merge literal. A produce template carrying literal `data` (passthrough) needs no
  destination in the handler at all: return no token matching its destination/type pair
  and the engine emits that template's fixed token.

**Stable across a merge** (what a handler may rely on): token types; the transition's
arc structure relative to itself (its consume/read/inhibit arcs, their weights, modes,
and predicates, and its produce templates — endpoints are renamed, structure is
preserved); and the transition-local view (`transitionId` is the transition's
alias-qualified name; `inputTokens` still carries one entry per binding source place).
**Not stable**: absolute place names — every place is alias-qualified, and a wired
port-place is replaced by the fused shared place — and `firingContext.netId`, which is
the *merged* net's name, not the constituent's.

Handler, guard, and predicate **refs are not rewritten** by the merge — registry names
are global bindings, not net-local structure (ADR 0003). An absent transition behavior
binding likewise remains absent: composition preserves the optional binding unchanged
and never invents one. A rename-proof, config-free handler registers once and serves
every explicitly handled instance of a net in one composition; per-instance
configuration (including registration-time-bound destinations) uses instance-scoped
handler names as above.

## Key decisions

The following decisions define the 0.1.0 handler contract.

1. **Contract is prose + type definitions, not a JSON Schema.** Unlike `net-schema.md`
   (a serialized net document), handlers are code bindings. Their I/O messages are
   JSON-serializable for the journal, but the journal schema belongs to
   `firing-semantics.md`. The contract here is normative prose plus illustrative
   `TypedDict`/`Protocol` shapes. The Python reference `TypedDict`s are its
   machine-checkable surface; the TypeScript core carries corresponding native types.
   There is no embedded JSON Schema for these I/O messages.
2. **Transition handler sees `inputTokens`, not the full marking** (ADR 0003). Maximally
   testable and reproducible: a handler is a pure function of its declared inputs. If
   marking-aware behavior is needed, model it as a guard or restructure the net so the
   relevant state is in input tokens. Settled; not re-litigated.
3. **Guard vs predicate: impure/transition-wide vs pure/per-token** (ADR 0002). Guards see
   the full binding and may consult external state; predicates see one token and must be
   pure. The contract makes the split syntactically and type-signature visible. Settled by
   ADR 0002; this feature operationalizes it.
4. **`failed` does not consume tokens.** On `failed`, the marking is unchanged; the engine
   records the failure and the net's retry logic (places/arcs) drives the next attempt.
   The handler does not retry internally. This keeps retry observable and replayable.
5. **`outputTokens` validated against produce templates.** The handler returns tokens; the
   engine deposits only those matching a produce template's `{type, destination}`
   (`net-schema.md`, "Produce template: routing contract"). A handler returning a token
   for a place/type with no matching produce template is a contract violation (engine
   raises / records failure). The template is the routing contract; the handler supplies
   the data.
6. **Firing policy is engine-level, not net-declared.** `net-schema.md` has no
   `firingPolicy` field. The policy is configured on the engine (one policy per run,
   default `first-found`). This matches ADR 0005 ("pluggable… without changing the
   core").
7. **Default firing policy = `first-found`, deterministic** (ADR 0005). Iteration order
   over `enabledTransitions` is deterministic (list order), so under the default policy
   replay holds. Custom policies are opaque — verification falls back to "all firings
   consistent with any policy" (ADR 0005).
8. **Purity is a contract, not a runtime check.** Predicate handlers "must be pure" —
   documented, not dynamically inspected. Inline CEL is pure by construction; named
   predicate handlers are trusted to uphold purity in both the Python reference and
   TypeScript core.
9. **Firing policy returns `str | None`.** It returns one transition id or `None` to
   stop. Concurrent firing is not part of this contract.
10. **`FiringContext` is closed, four fields, timestamps metadata-only.** Exactly
    `firingId`, `attempt`, `netId`, `timestamps: {fired_at}`. Handlers MUST NOT branch on
    `timestamps` for firing decisions. The shape is shared by the Python reference and
    TypeScript core.
11. **Per-kind registry namespaces.** Resolution dispatched by kind; a transition ref and
    a guard ref may share a name. Inherent in the per-kind `register_*`/`resolve_*` API —
    no cross-kind `register(kind, …)` form.
12. **Transition behavior binding is optional; absence is exact.** Core JSON omits the
    `handler` key and the parsed model uses `None`. A present ref is a nonempty string;
    `null`, `""`, implicit same-name refs, and implicit no-ops are rejected. Structure
    remains valid without a binding, and `Engine.validate` resolves only declared refs.
    Engine firing preserves precedence (`NotEnabled` for a disabled transition, atomic
    `HandlerNotFound` for an enabled handlerless transition), and Runtime rejects
    handlerless nets at construction because each transition needs a `HandlerSpec`
    (ADR 0003 amendment; `firing-semantics.md`).

## Contract boundaries in 0.1.0

These adjacent surfaces are defined elsewhere rather than by this handler-I/O contract.

- **Firing engine, enablement detection, consume/deposit mechanics, and firing journal**
  — `spec/firing-semantics.md` and the Python reference implementation.
- **CEL expression evaluation** — the adapter and engine surfaces define compilation and
  evaluation behavior.
- **Composition** — `spec/composition.md` and the implementation-specific composition
  surfaces.
- **`pending` handler status** — unsupported. This contract defines only `"completed"`
  and `"failed"`.
- **Concurrent firing** — unsupported. A firing policy returns `str | None`.

## Related documents

- `spec/net-schema.md` — the net document: places, transitions, arcs, and the named refs
  this contract binds (optional transition `handler`, `guard`, predicate `handler`).
- `spec/composition.md` — the composition document (`{nets, wires}`), `Wire`, and the
  merge-and-wire rules.
- `spec/firing-semantics.md` — enablement, firing, consume/deposit, the firing-policy
  handler's integration into the engine, and the deterministic/replayable firing journal
  whose schema covers the JSON-serializable I/O messages defined here.
- `docs/adr/0002` — CEL for arc predicates, named handlers for guards (the pure/impure
  split this contract operationalizes).
- `docs/adr/0003` — handler contract: input tokens + context, side effects as places.
- `docs/adr/0005` — firing policy handler with nondeterministic default.
- `docs/adr/0001`, `0004` — further architectural decisions this contract
  operationalizes (net purity and ports).