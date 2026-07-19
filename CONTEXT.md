# Velocitron

Velocitron is a public-alpha toolkit for authoring, validating, visualizing, simulating, and running typed colored Petri nets across Python and TypeScript. Its portable surfaces are the `.petrinet` authoring language and a shared JSON representation. Matthew R. Scott is the sole author. Henrique Bastos is acknowledged for collaboration toward an interoperable Petri-net specification; that acknowledgment does not indicate shared authorship. The current contracts live in `spec/`, with design rationale and history in `docs/adr/`.

## Language

### Net structure

**Net**:
A declarative colored Petri net: places, transitions, arcs, and a marking. The coordination/control structure, separate from behavior.
_Avoid_: workflow, state machine, graph, DAG

**Place**:
A condition or buffer that holds tokens and declares which token types it accepts.
_Avoid_: node, state, queue

**Transition**:
An event with input arcs (consume) and output arcs (produce), an optional behavior binding (`handler` ref), and an optional guard. A transition without a behavior binding is valid coordination structure; no same-name or no-op handler is implied.
_Avoid_: step, action, task, node

**Source transition**:
A declared transition with no input arcs (typically produce-only). Structurally enabled with the empty binding — it consumes nothing, so it is a firing candidate against any marking. Execution still requires an explicit behavior binding at the Engine/Runtime boundary. Distinct from an undeclared transition name, which is an error, never a firing (firing-semantics D10).
_Avoid_: generator, spawner, entry node, root

**Timed transition**:
A transition carrying a `timer` declaration (`{clock, cel, bind?}`): enabled only when the temporal CEL condition holds for the candidate binding, evaluated against the clock place's token plus explicitly bound input tokens. The deadline lives in token data; the engine holds no timer state; time advances only by token injection.
_Avoid_: delayed transition, scheduled transition, timeout node, timer transition

**Clock place**:
The place a `timer` names as its time reference; its first token (conventionally a singleton `tick`, advanced by replacement through the token-injection seam) is exposed to the timer's CEL as the reserved variable `clock`. An empty clock place means the timed transition is not enabled.
_Avoid_: timer place, time node, scheduler place

**Arc**:
A directed edge between a place and a transition. Input arcs carry consume patterns; output arcs carry produce templates.
_Avoid_: edge, link, connection

**Token**:
A typed JSON value flowing through the net. Has a `type` (string) and `data` (object). The "color" is the type.
_Avoid_: message, event, payload, item

**Marking**:
The distribution of tokens across places at a point in time. The net's state.
_Avoid_: state, snapshot, configuration

**Initial marking**:
The optional authored marking a Net declares as its default token distribution before any firing. It is static net data, distinct from a Runtime Marking.

**Named marking**:
An authored, named alternative token distribution on a Project Document. A consumer selects it explicitly; selection does not mutate or redefine the Net's initial marking.

**Inline marking**:
A marking supplied directly by a consumer rather than authored in the source document. It is an inspection or invocation input, not a mutation of the Net.

**Color**:
The type of a token. Distinguishes token kinds so places, arcs, and guards can filter by type.
_Avoid_: tag, label, category

**Generic token**:
A token whose color is the ordinary string `token`, used by convention for classical/uncolored nets. It is semantically an ordinary color — never a wildcard, inferred type, or untyped core value — but presentation layers omit the generic color label where no distinction would be conveyed.
_Avoid_: wildcard token, untyped token, any token

**Fusion place**:
A place tagged `fusion=True` that is a single logical place depicted by the renderer as a local dashed instance at each transition that connects to it (one place, many local copies). Used for shared/environmental places like `git_tree_diff` so the rendered graph reads as per-transition islands rather than a single global hub.
_Avoid_: hub place, shared node, global place

**Conflict**:
The Petri-net structural condition where two or more transitions are simultaneously enabled over a shared place and compete for the same token — at most one can fire and consume it. Velocitron's default first-found firing policy (ADR 0005) resolves conflict by net declaration order, making selection deterministic and replayable rather than random.
_Avoid_: race, contention, collision, tie

### Schema representation

**ConsumePattern**:
The per-arc consume contract: `{type, predicate, mode, correlate?}`, where `type` is the token type to match (must be one of the source place's `accepts`), `predicate` is an optional arc-level filter, `mode` is `"consume"` (the default — requires a matching token and removes it on fire), `"inhibit"` (a zero-test requiring the absence of a matching token; consumes nothing), or `"read"` (test-without-consume — requires a matching token and binds it, like consume, but removes nothing on fire; ADR 0012), and `correlate` (inhibit arcs only) upgrades the zero-test to a **correlated inhibit** (below; ADR 0017). Separates *which* token type, *which* tokens of that type (predicate), and presence-vs-absence-vs-read (mode).
_Avoid_: input contract, consume spec, arc input

**Correlated inhibit (anti-join)**:
An inhibit arc carrying a `correlate` CEL inscription, making its zero-test per-binding: the transition is enabled under a candidate binding only if no token in the inhibit place — of the arc's type, passing its predicate — also correlates with that binding (`correlate` sees `token`, the candidate's data, and `binding`, the bound tokens' data keyed by place). The classic anti-join: "no mod_flag for THIS account", "no parent with the SAME crawl_tag". Without `correlate` an inhibit arc stays a whole-place zero-test. (ADR 0017.)
_Avoid_: keyed inhibitor, negative join, binding predicate, conditional inhibit

**ProduceTemplate**:
The per-arc produce contract: `{type, destination, data?, cel?}`. A routing contract, not a token factory: it declares the allowed `{type, place}` a bound handler may deposit against; the transition handler supplies the actual output tokens and the engine deposits them against the template. The optional literal `data` lets a bound passthrough or routing handler emit a fixed token when it returns no token for that destination; the optional `cel` (mutually exclusive with `data`, ADR 0023) is the **computed fallback** — an inline CEL expression over `binding` (the bound tokens' data keyed by place, as `correlate` sees it) whose object result becomes the emitted token's data. Neither fallback gives a handlerless transition structural firing behavior.
_Avoid_: output contract, produce spec, arc output, output expression, produce cel template (say computed fallback)

**Predicate (discriminated)**:
The `predicate` on a consume arc is a discriminated object: either `{cel}` (an inline Common Expression Language expression, `cel: "<expr>"`, evaluated against the candidate token's data) or `{handler}` (a named pure predicate handler ref, `handler: "<ref>"`, resolved by the registry) — mutually exclusive, never both. An absent predicate matches any token of the declared `type`. Predicates only; no data transformation. (A companion to **Predicate** under Behavior; that entry remains in force.)
_Avoid_: filter, condition, matcher, expression

**Arc-centric representation**:
Arcs are a flat, top-level list with explicit `from`/`to` endpoints, not inputs/outputs nested under transitions; direction determines consume vs produce. This `arc-centric` form treats every edge uniformly and unifies `Arc` with `Wire` (a cross-net arc, defined in `spec/composition.md`), so the same shape carries both intra-net and inter-net connections.
_Avoid_: nested arcs, transition-scoped arcs, port-list form

**Port facet**:
A `port` is a facet on a place — `port: {direction: "input"|"output", type}` — not a separate top-level kind. A place carrying the `port` facet is a boundary place (the composition interface); declaring it keeps the net a single place set, and composition wires these port-places together (see **Port** under Composition). A port's `type` must be one of its place's `accepts`.
_Avoid_: port node, port kind, endpoint type

### Behavior

**Handler**:
External behavior bound to a name and resolved by the runtime's handler registry. Transition handlers do work and return tokens; guard handlers return booleans (may be impure); predicate handlers return booleans (pure).
_Avoid_: function, callback, service, worker

**Handler ref**:
A named string in the net (e.g., `"agent.review_pr"`) that the runtime resolves to a registered handler. The net says *which* handler; the registry says *what* it does.
_Avoid_: function pointer, callback id, symbol

**Behavior binding**:
The optional association between a Transition and a transition-handler ref. In core JSON the `handler` key is absent when no behavior is bound; in the parsed model `Transition.handler` is `str | None`, defaulting to `None`. An empty string or `null` is never a binding, and absence never implies a same-name or no-op handler. Handlerless transitions remain valid for parsing, composition, and visualization. For execution, `Engine.fire` returns `NotEnabled` first when structural enablement fails; firing an enabled handlerless transition records `HandlerNotFound`. `Runtime` rejects a handlerless net at construction.
_Avoid_: default handler, implicit handler, structural handler

**Guard**:
A transition-level condition, referenced by a named handler. Sees the full input binding across all input arcs. May be impure (may consult external state).
_Avoid_: condition, rule, gate, check

**Predicate**:
An arc-level condition that filters which tokens are consumed from a place. Either an inline CEL expression or a named pure predicate handler. Predicates only — no data transformation.
_Avoid_: filter, condition, expression, matcher

**Firing policy**:
A pluggable handler that decides which enabled transition(s) to fire. Two built-ins: first-found (the default — nondeterministic-in-principle, deterministic list order in practice, replayable) and priority (opt-in — highest declared Transition.priority wins, ties fall back to declaration order). Custom policies can be registered without changing the core.
_Avoid_: scheduler, dispatcher, selector, router

### Engine runtime

**Enablement**:
A transition is enabled when every consume arc can be satisfied (a matching token is present for each), every inhibit arc is satisfied (no matching token is present), and its guard (if any) returns true. Enablement detection is the engine's first phase; only enabled transitions are candidates for firing.
_Avoid_: activation, readiness, eligibility

**Binding**:
The concrete set of tokens a transition will consume when it fires — one token per consume arc, each matching that arc's declared type and predicate. The engine selects a binding deterministically (lexicographic by insertion order) so firings are replayable.
_Avoid_: match, selection, token set

**Deposit**:
The engine placing a fired transition's output tokens into their destination places, routed through the transition's produce templates. A deposit violation is a programmer-error signal: the handler returned tokens to a place with no matching produce template, or with the wrong token type.
_Avoid_: emit, output, write, place

**Selection loop**:
The engine's repeated cycle in `run`: detect enabled transitions, ask the firing policy which (if any) to fire, fire it, and repeat — until no transition is enabled or a step cap is reached.
_Avoid_: run loop, main loop, scheduler loop

**Atomic rollback**:
On a firing failure the input marking is untouched by construction — the engine consumes from an immutable, persistent marking, so a failed fire leaves state exactly as it was, with no partial mutation to undo.
_Avoid_: transaction, revert, undo step

**Failure budget**:
An opt-in, per-run cap on a transition's consecutive failed firings. A transition that fails its budget is exhausted — no longer selectable by the firing policy — until any completed firing resets the counts (the marking changed, so its inputs may differ). Prevents a persistently failing transition from spinning the selection loop or starving later-declared transitions; deterministic (counted from the firing sequence, never wall-clock), so replay holds. Off by default.
_Avoid_: retry limit, backoff, circuit breaker, rate limit

**Firing record**:
The durable record the engine emits per firing attempt: the transition id, input/output tokens, status (`completed` or `failed`), error, metadata, and timestamps. The engine fills every field except `sequence`, which the journal assigns.
_Avoid_: log entry, audit record, event

**Injection (environment-arrival seam)**:
The one sanctioned way a token from outside the net — a file arrival, an environment observation, a clock tick or deadline — enters a running net between firings. A consumer-driven marking event, not a firing: validated against the receiving place, journaled in the same sequence stream as firings so replay is deterministic across injected tokens. Originated as the clock/timer seam (ADR 0013, as amended).
_Avoid_: push, insert, external write, marking mutation

### Composition

**Port**:
A named boundary place on a net, declared via a `port` facet (`{direction: "input"|"output", type}`) rather than a separate top-level kind — see **Port facet** under Schema representation. A port carries a declared token type and marks the composition interface; wires join an output port of one net to an input port of another.
_Avoid_: endpoint, interface, channel, socket

**Wire**:
A unidirectional cross-net arc connecting an output port to an input port. A special form of an arc formed by joining two nets. Composition = merging net schemas and adding wires; the composed system is a single larger net.
_Avoid_: connection, link, bridge, pipe

**Chip**:
A reusable subnet with input/output ports. A composable component, analogous to a chip on a mainboard.
_Avoid_: module, component, subnet, fragment

### Artifacts

**Pattern**:
A documented net structure solving a recurring coordination problem. The "cookie cutter" artifact: a doc layer (problem, structure, properties), a JSON schema layer (the net definition), and language-specific code layer.
_Avoid_: template, recipe, example, snippet

**Firing journal**:
A durable, deterministic record of all firings. Enables replay: same net + same inputs + same handler results = same firing sequence.
_Avoid_: log, history, audit trail, event log

**Side-effect triplet**:
A `requested → deliver → completed` place structure modeling an external side effect's lifecycle. The handler performs the actual call; the net models the lifecycle, making the side effect observable, durable, retryable, and inspectable.
_Avoid_: side effect, effect, external call, integration

**Human attention queue**:
A place holding tokens that need human review. A transition with a human-facing handler consumes from it. The handler returns `completed` with a resolution token (accept, correct, or reject).
_Avoid_: inbox, task list, review queue, pending list

**Sink transition**:
A transition with one or more consume arcs and zero produce arcs — it consumes tokens and produces nothing (e.g. a `clear_flag` transition discarding a flag token; a terminal discard). A legitimate construct, which is why it is a lint finding and never a validation error.
_Avoid_: dead end, terminal node, drain, black hole

**Lint rule**:
An opt-in, advisory static check over a parsed net that flags a legitimate-but-usually-buggy shape (first rule: `consume-without-produce`, the sink shape a dropped produce arc leaves behind). A finding carries a stable rule id, the flagged transition's name, and a human message; declining to lint changes nothing. An intentional occurrence is acknowledged per-transition via an `annotations.lint.suppress` list naming the rule id (the ADR 0011 carve-out; ADR 0016).
_Avoid_: validation error, parse error, schema check, warning-as-error

**Projection adapter**:
A consumer-owned component that derives a `Marking` (or an incremental batch of tokens) from external resource state — filesystem, database, queue, API, clock, or an in-memory fixture — and hands the result to the engine, which runs *after* and *outside* projection. Projection is not an engine primitive. The protocol shape is *enumerate correlation keys → probe evidence → deposit observation tokens*, with three normative rules — never de-duplicate on the color key (duplicates are a signal to flag, not noise to collapse), union mirror mounts, and flag duplicates so the net can route them. A projection is a pure function of the probed evidence; probing is the impure edge. See `spec/projection-adapter.md`.
_Avoid_: importer, loader, sync, scanner, marking builder

### Language tooling

**Workspace Root**:
The client-declared filesystem boundary within which project discovery may search. A workspace root can contain multiple Petri-net projects and loose DSL source files.
_Avoid_: project, repository, source root

**Petri-net Project**:
The manifest-owned collection of project documents beneath one project root. The nearest manifest determines ownership; a workspace root is not itself a Petri-net project.
_Avoid_: workspace, workspace folder, repository

**Project Document**:
One ordered semantic aggregate declared by a Petri-net project. It may be assembled progressively from multiple DSL source files and has an identity independent of its header name.
_Avoid_: file, source, compilation unit

**DSL Source File**:
One physical `.petrinet` file with a canonical source identity. Several DSL source files may contribute facts to one project document.
_Avoid_: document, project, source

**Document Namespace**:
The decoded, renameable header name shared by every DSL source file contributing to one project document. It is a semantic name, not the project document's stable identity.
_Avoid_: document id, project id, source id

**Current Contribution State**:
The facts proved from intact syntax in one exact current document version, possibly partial while the source is damaged. It never borrows facts from an older successful analysis.
_Avoid_: current snapshot, partial tree, latest good state

**Retained Resolved Snapshot**:
The most recent fully resolved semantic snapshot retained after successful project analysis. It may predate the current contribution state and must never be presented as current.
_Avoid_: current state, current snapshot, latest good facts

**Runtime Marking**:
A `Marking` used by an executing lifecycle net. It is distinct from current contribution state, a retained resolved snapshot, and a named marking authored as DSL metadata.
_Avoid_: document state, project snapshot, named marking

**Lifecycle Projection**:
A stable, redacted observation derived only from lifecycle-net markings and static net metadata. It exposes conformance-relevant lifecycle state without exposing full runtime markings or operational metrics.
_Avoid_: runtime snapshot, full marking, metrics view

### External

**CEL**:
Common Expression Language (`google/cel-spec`). A side-effect-free expression language used for arc predicates. Velocitron 0.1.0 evaluates CEL through its Python and TypeScript implementations.
_Avoid_: expression language, DSL, query language
