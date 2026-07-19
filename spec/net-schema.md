# Net Schema

This document is the human-readable semantic definition of the **core net
document** for a colored Petri net—the declarative coordination structure this
project uses to model agentic systems. The standalone JSON Schema linked below
is its canonical machine-readable shape.

Composition (joining nets through ports and wires) is a **separate document**,
`spec/composition.md`. The `Port` facet lives *here* — a port is a place with an extra
boundary declaration — but the cross-net `Wire` and the `{nets, wires}` composition
document live there.

## Purpose and the net-purity principle (ADR 0001)

A net is **pure coordination**. It routes and gates; it never computes. Concretely:

- Arc inscriptions are **predicates** — boolean filters over a single token. They never
  transform token data, never emit tokens, never call out to the world.
- All computation — data transformation, classification, LLM calls, human decisions,
  side effects — lives in explicitly bound **handlers**, resolved by name through a
  runtime registry. A handlerless transition declares coordination structure only.
- The net only declares *what* may happen *where* and, when a behavior binding is present,
  which handler supplies *how* and *what data results*.

This is a permanent exclusion (ADR 0001), not a v1 deferral. It is what makes a net
maximally verifiable (decidable predicates), portable across the Python and TypeScript
implementations, and replayable (same net + same inputs + same handler results = same
firing sequence; see `spec/firing-semantics.md`).

### Documentation fields (ADR 0011)

`description` and `annotations` are **documentation-only** fields admitted on
every element (place, transition, arc, and the top-level net). They carry no
firing semantics — the engine ignores them entirely. This is a deliberate,
scoped exception to ADR 0001's permanent exclusion: ADR 0001 excludes
*behavioral* inscriptions (computation, transformation, side effects);
documentation fields are non-behavioral metadata and are the sole permitted
exception. See `docs/adr/0011-documentation-fields.md`.

The `.petrinet` translator reserves top-level
`annotations["petrinet.dsl/v1"]` for versioned full-document presentation
metadata. Its shape is defined by
[`spec/petrinet-language.md`](./petrinet-language.md), not this intentionally
unconstrained consumer-annotation schema; it remains non-behavioral and
net-only.

## Canonical JSON Schema (draft 2020-12)

The authoritative machine-readable schema is
[`spec/net.schema.json`](./net.schema.json). Its `$id` identifier is
`https://awesome-petri.dev/spec/net.schema.json`.

The Python validator loads a byte-identical packaged copy from
`velocitron/schemas/net.schema.json` through `importlib.resources`; it never
reaches outside the installed wheel at runtime. Focused schema-resource tests
validate the canonical document itself and fail on any canonical/package
byte drift. Keeping the complete schema in the standalone artifact—rather than
duplicating it in this prose—makes that drift check enforce one-source
ownership.

### Structural validations beyond the schema

The JSON Schema catches shape and most field-level errors, but several invariants are
expressed more cleanly as parser-level structural checks (per Q5: minimal parser/validator
scaffold). The reference parser enforces:

1. **Unique place names** — no two places share a name.
2. **Unique transition names** — no two transitions share a name; arcs resolve transitions by name.
3. **Arc endpoints resolve** — every `from`/`to` refers to a declared place or transition.
4. **Direction matches arc kind** — a `consume` arc is place -> transition; a `produce`
   arc is transition -> place. (Mirrored by the schema's `allOf`/`if` clauses, but the
   parser is authoritative.)
5. **Produce destination coincides with the arc's `to` place.**
6. **Consume `type` is in the source place's `accepts`; produce `type` is in the
   destination place's `accepts`.**
7. **Predicate mutual exclusion** — `{cel}` XOR `{handler}`, never both.
8. **Port type accepted** — a place's `port.type` is one of that place's `accepts`.
9. **`correlate` is inhibit-only** — `correlate` is rejected on `consume`/`read` mode
   arcs, and its CEL is compiled at parse time like every other inline expression
   (ADR 0017; D6).
10. **Timer clock place resolves** — a transition's `timer.clock` names a declared place.
11. **Timer bind places are binding-arc sources** — every `timer.bind` value names a
    source place of one of that transition's consume- or read-mode arcs (so the
    variable always resolves to a bound token), and no `bind` key is the reserved
    variable `clock`.
12. **Timer CEL compiles** — a transition's `timer.cel` compiles at parse, like arc
    predicates (D6).
13. **Timer maturity CEL compiles** — when present, `timer.maturity` compiles at parse
    against the same closed environment as `timer.cel`.
14. **Produce template `data` XOR `cel`** — a produce template carries at most one of
    literal `data` and computed `cel`, and `cel` compiles at parse like every other
    inline expression (ADR 0023; D6).

These mirror the validation-error cases in `implementations/python/tests/test_parser.py`
(1–8), `test_correlated_inhibit.py` (9), `test_timed_transitions.py` (10–13), and
`test_produce_cel.py` (14).

## Places, accepted types, and ports

A **Place** is a condition or buffer that holds tokens and declares which token types
(**colors**) it accepts via `accepts`. A place never transforms a token; it only holds
tokens whose `type` is in `accepts`. Color lets places, predicates, and guards filter by
type without inspecting data.

A place may optionally carry a **Port** facet — `port: {direction, type}` — making it a
**boundary place**: an `input` port receives tokens from outside the net (from a wire);
an `output` port exposes tokens to other nets. A port is *still a place* — it sits in the
single place set, may have consume/produce arcs like any place, and participates in
marking identically. Declaring `port` just marks the composition interface (ADR 0004).
A port's `type` must be one of its place's `accepts` (enforced by the parser; see the structural-validation list above).
Non-port places are internal. Wire endpoints (defined in `spec/composition.md`) join an
output port of one net to an input port of another; the port's `type` must match on both
sides.

### Capacity per color key (ADR 0019)

A place may declare an optional **`capacityPerColorKey`** bound —
`{key, max}` — stating that the place holds at most `max` tokens per
distinct value of the named token-data key (`key` may be a single field
name or an array of field names forming a composite key, e.g.
`["account_id", "crawl_tag"]`). Example: "at most 1 `mod_flag` token per
`account_id`" is `{"key": "account_id", "max": 1}`.

The field is **non-behavioral**, in exactly the ADR 0011 sense: the engine
never reads it — it does not gate enablement, binding, firing, or deposit.
It is consumed only by the **declarative property pass**
(`spec/properties.md`), which checks the bound against a marking or along a
journal replay and *reports* violations. It is a first-class, schema-validated
field (not an `annotations` convention) so that a typo'd declaration fails
parsing instead of silently checking nothing. Tokens missing a key field
group under a shared absent-marker for the bound — they count against a
capacity rather than evading it (see `spec/properties.md`).

## Transitions, handlers, guards, priority

A **Transition** carries an optional behavior binding, serialized as a `handler` ref: a
named string the registry resolves to the actual transition handler (ADR 0003 amendment;
the handler contract is the subject of `spec/handler-contract.md`). When present,
`handler` is a nonempty string. When absent, no behavior is bound: the JSON key is
omitted and the parsed `Transition.handler` is `str | None`, defaulting to `None`.
`null`, `""`, an implicit same-name ref, and an implicit no-op are not alternate
representations. Handlerless transitions remain valid net structure for parsing,
composition, and visualization; see the execution boundary in
`spec/firing-semantics.md`. Composition preserves the optional ref unchanged and never
synthesizes or rewrites it.

An optional **`guard`** ref names a guard handler (ADR 0002). A guard gates enablement at
the **transition level**: it sees the full input binding across *all* input arcs, and it
**may be impure** (it may consult external state). This contrasts with arc predicates,
which are pure and single-token (below). The guard-vs-predicate split is the seam where
"impure, transition-wide" decisions meet "pure, per-token" filtering.
Every condition result uses the strict boolean rule defined under "Predicate split":
only the exact boolean `true` satisfies a condition; host-language truthiness is never
consulted.

An optional **`priority`** integer is consumed by
the **built-in opt-in `priority` firing policy** (ADR 0014): among simultaneously enabled
transitions the highest declared priority fires first (absent = 0; ties fall back to
declaration order — the `first-found` fallback). The default `first-found` policy still
ignores the field, so declaring priorities changes nothing unless the runtime opts in via
`Engine(registry, policy="priority")`.

## Timed transitions (`timer`, ADR 0018)

An optional **`timer`** object — `{clock, cel, bind?, maturity?}` — declares a transition
**time-gated**: it is enabled only when the temporal condition holds for the candidate
binding, in addition to ordinary enablement (arcs satisfiable, inhibits satisfied, guard
true). The condition is a CEL expression evaluated against a closed, net-declared
environment:

- the reserved variable **`clock`** — the `data` of the first token in the `clock` place
  (the **clock place**; an empty clock place means the transition is not enabled — no
  time reference, not matured);
- each **`bind`** variable — the `data` of the first token bound from its named place in
  the candidate binding. Every `bind` value must name a source place of one of the
  transition's consume- or read-mode arcs, and keys are simple identifiers (never
  `clock`).

The deadline **lives in the token**: the comparison is over data the tokens already
carry (e.g. `clock.now >= latch.fired_at + latch.cooldown_s`), so one shared clock place
serves any number of in-flight tokens with distinct deadlines, and the engine holds no
per-instance timer state. Time itself only advances by token injection (ADR 0013) —
the engine never reads a wall clock (ADR 0001) — which is what keeps replay
deterministic across timed firings. The `timer.cel` expression is compiled at parse
(D6); only the exact boolean `true` matures the binding. A runtime eval error or any
non-boolean result degrades to condition-false.

`maturity`, when present, is a second CEL expression over precisely that environment. It
returns the next finite timestamp strictly after `clock.now` at which the candidate may
mature; it never enables a firing. `Runtime` requires `maturity` on every native timer,
uses the earliest candidate to schedule a clock update, and still lets `timer.cel` make
the sole enablement decision. Evaluation failures, non-finite values, and non-future
values are unschedulable rather than a reason to poll. It remains optional so existing
nets and direct synchronous `Engine.tick` callers remain compatible.

The `bind` indirection exists for composition: the CEL strings reference only local
aliases, and `merge_nets` rewrites the `clock`/`bind` *place values* (alias-qualified,
fusion-rewritten) exactly like arc endpoints, never either expression. Evaluation order
and the engine-owned re-evaluation loop (`Engine.tick`) are specified in
`spec/firing-semantics.md` (a, f).

## Arcs (arc-centric)

Arcs are a **top-level flat list** with explicit `from`/`to` — *not* inputs/outputs
nested under transitions. The flat `from`/`to` form matches the formal bipartite
definition, and it unifies the cross-net **Wire** (defined in `spec/composition.md`) as
just another arc rather than a special case.

There are exactly two arc kinds, distinguished by direction and by which payload they
carry:

- **Consume arc** — `from` a place, `to` a transition; carries a `consume` pattern. It
  describes which tokens the transition pulls from the place.
- **Produce arc** — `from` a transition, `to` a place; carries a `produce` template. It
  describes which tokens the transition may deposit in the place.

Exactly one of `consume`/`produce` is present per arc; the schema's `oneOf` and the
parser's direction check enforce this.

## Consume pattern: type + predicate + mode

A `consume` pattern answers three orthogonal questions:

- **Which token type?** `type` — constrained to the source place's `accepts`.
- **Which tokens of that type?** `predicate` — an optional pure filter (below); absent
  means "any token of the declared type".
- **Presence or absence?** `mode`:
  - `"consume"` (default) — the arc requires a matching token and **removes it** when the
    transition fires.
  - `"inhibit"` — a **zero-test**: the arc requires the **absence** of a matching token
    and **consumes nothing** when the transition fires.
  - `"read"` — **test-without-consume**: the arc requires a matching token (gating
    enablement on **presence**, exactly like `consume`) and the matched token(s)
    **contribute to the binding** — the guard, the handler's `inputTokens`, and the firing
    record all see them — but they are **not removed** when the transition fires (ADR 0012).

The `inhibit` mode represents the classic Petri-net inhibitor arc structurally. It gates
enablement on the absence of a matching token and removes nothing — exactly the
zero-test. External state must first be represented by tokens in the marking; inhibit
arcs never read that state through a projection side-channel.

The `read` mode is the presence-side dual of `inhibit`'s absence-test: it gates on a token
being present without consuming it, so a shared flag, clock, or config token can gate many
transitions (and be re-checked every fire) without a consume-and-reproduce arc pair that
would double the arcs, spam the journal, and serialize logically-concurrent firings. When
a read and a consume arc on **one** place both match, they bind **disjoint** tokens (a
token may serve only one arc); a `read` and an `inhibit` arc on one place can never be
satisfied at once (presence and absence are mutually exclusive). See ADR 0012 and
`spec/firing-semantics.md` (a, b).

### Arc weight (D7)

An optional `weight` (integer `≥ 1`, default `1`) makes a consume-mode arc consume
`weight` tokens of the declared `type` (each still passing the arc's predicate) instead
of one. The `weight: 1` default is the classical one-token-per-arc case; `weight > 1`
generalizes an arc to multi-token consumption (e.g. the petrinet.org
`storage >> {Abstract: 2} >> distribute` form). On a `read` arc, `weight` is the number of
matching tokens that must be **present and bound** (all contribute to the binding, none is
removed). `weight` is **rejected on inhibit arcs** (validated by the parser): an inhibit
arc is a zero-test that consumes nothing, so a weight other than `1` has no meaning there. This is the narrow additive change from
`spec/firing-semantics.md` (D7). At the time D7 landed this was the only
net-structure addition; the optional transition behavior binding was added later by the
ADR 0003 amendment and leaves consume-weight semantics unchanged.

### Correlated inhibit: the anti-join (ADR 0017)

An inhibit arc may declare an optional **`correlate: {cel: "<expr>"}`** inscription,
turning its whole-place zero-test into a **per-binding** zero-test: the transition is
enabled under a candidate binding B only if **no** token in the inhibit place — of the
arc's declared `type`, passing the arc's single-token `predicate` (if any) — also
satisfies `correlate` evaluated over that token *and* B. This is the classic anti-join
("no `mod_flag` FOR THIS ACCOUNT"; "no parent with the SAME `crawl_tag`"), which a plain
inhibit arc cannot express (its predicate sees only the candidate token) and a guard
cannot either (guards see only the binding, never the marking).

The CEL environment carries two names — deliberately namespaced, unlike the single-token
predicate's bare fields, because two token universes are in scope:

- **`token`** — the inhibit-place candidate token's `data`.
- **`binding`** — the candidate binding as data: a map of source-place name → list of
  bound tokens' `data`, covering consume- **and read-mode** arcs (the guard's
  `inputTokens` shape, `spec/handler-contract.md`, projected to data). E.g.
  `binding.orders[0].account`.

`correlate` is valid **only on `mode: "inhibit"` arcs** (parser-enforced): consume/read
arcs bind tokens, and cross-token conditions over bound tokens are guard territory. It is
CEL-only — a named-handler variant would need a new handler-contract input shape and is
deferred (ADR 0017). Its expression is compiled at parse time (D6); a runtime eval error
or any non-boolean result **blocks the candidate binding** (fail-closed, the guard's
degrade-toward-not-enabled posture — see `spec/firing-semantics.md` (a)). An inhibit arc
without `correlate` keeps the whole-place zero-test above, evaluated before binding
construction, unchanged.

## Produce template: routing contract

A `produce` template is a **routing contract**, not a token factory.
It declares one **allowed** `{type, place}` pair the transition may deposit; the
**handler supplies the actual output tokens** (ADR 0003: the handler returns
`outputTokens: {place: [tokens]}`). Produce arcs remain a declaration-ordered sequence:
parallel templates may share a destination, including templates of the same type, and no
template overwrites another. The template's `destination` **must equal** the arc's `to`
place; the redundancy is intentional and is checked by the parser, so the contract is
stated twice and cannot drift.

At deposit, each handler token is valid when its destination and type match **any**
declared template. A destination may therefore receive every type declared by its
templates; a token for an undeclared destination/type pair is a deposit-contract
violation. Valid handler tokens are deposited once in handler order.

The optional literal `data` is a per-template fallback. For every template whose
destination/type pair has no handler-supplied token, the engine emits that template's
fixed token in template declaration order. Handler tokens for a pair win over every
literal fallback for that pair; parallel literals are never collapsed. This does not let
a handlerless transition fire structurally, and it is not a default or no-op handler.

The optional **`cel`** field (ADR 0023) is the *computed* variant of the same fallback,
mutually exclusive with `data` (validation rule 14). For a pair-uncovered template, its
expression is evaluated over the single name **`binding`** — the same place-keyed
bound-token-data map `correlate` sees (ADR 0017): source-place name → list of bound
tokens' `data`, consume- and read-mode arcs. The result must be a JSON object and becomes
the emitted token's `data`. An evaluation error or non-object result is a
**deposit-contract violation** (`spec/firing-semantics.md` D3): the firing fails
atomically. `cel` fires only as a fallback — handler tokens for the pair still win — so
the template remains a routing declaration first; the computed fallback, like the literal
one, never lets a handlerless transition fire structurally in the Engine.

Beyond the fallback fields, the template never shapes dynamic output data; it constrains
*where* and *of what type* output may land.

## Predicate split (ADR 0002)

A `predicate` is a **pure, single-token, arc-level** boolean filter, expressed as a
discriminated object — exactly one of:

- `{cel: "<expr>"}` — an inline Common Expression Language expression
  (`google/cel-spec`). Side-effect-free and evaluated against the candidate token's
  `data`; supported by the Python and TypeScript implementations.
- `{handler: "<ref>"}` — a named **pure** predicate handler ref resolved by the registry.
  Same semantics (boolean over one token), used for reuse or logic too complex for CEL.

The two are mutually exclusive (enforced by `minProperties`/`maxProperties` and the
parser). The split exists because **predicates filter** (pure, arc-level, per-token)
whereas **guards gate** (possibly impure, transition-level, see the full binding) — ADR
0002. Keeping the distinction syntactically visible prevents an impure guard from
masquerading as a pure predicate, which would wreck replayability and verifiability.

**Boolean condition constant.** Across inline CEL predicates, named predicate handlers,
guards, timer conditions, and correlated inhibits, only the exact JSON boolean `true`
satisfies the condition and only exact `false` is an ordinary negative result.
Non-boolean results are condition-evaluation errors; no implementation may apply Python,
JavaScript, or another host's truthiness rules. They degrade through the existing error
posture for that surface: predicate/timer false, guard not-enabled, and correlated
inhibit fail-closed. A timer's numeric `maturity` expression is scheduling data, not a
boolean condition, and is unchanged.

## Tokens and marking

A **Token** is `{type, data}`: `type` is its color (a string), `data` is an arbitrary
JSON object. Color distinguishes token kinds so places and predicates can filter by type;
`data` carries everything else.

The exact color string `token` is the **Generic token** convention for
classical/uncolored nets. It remains an ordinary, explicit core type everywhere:
`accepts`, ports, markings, and consume/produce inscriptions all use `"token"` normally.
It is not a wildcard, inferred color, or untyped value. Presentation layers may omit the
generic color label while retaining token counts/data and all arc semantics; that
omission never changes the core document.

For Graphviz/DOT visualization specifically, presentation omits `token` from a place's
accepted-color row, a generic port's type line, a generic marking token's type label
(while retaining its count and `data`), and consume/produce arc type labels. The
omission is label-only: weights, read/inhibit modes and glyphs, predicates,
correlations, literal data, tooltips, and every non-`token` color remain visible.
Renderers omit an empty DOT attribute list rather than emitting `[]`.

Because `data` is an arbitrary JSON object (a dictionary), a token is **unhashable**:
it cannot key a set or map. Multiset operations on a place — binding validity, consume,
deposit accounting — must therefore count tokens by structural **equality**, not by
hash-based `Counter`/set arithmetic over `data`: remove each bound token once by `==`,
counting multiplicities, never keying a set or `Counter` over `data`. Every future
multiset consumer (composition, replay-diff, merge) reuses this equality-counting
approach. The Python reference engine's realization of this rule is recorded in
`AGENTS.md`'s implementation notes (the spec itself stays language-agnostic).

A **Marking** is the distribution of tokens across places — the net's state. A net may
declare an optional `initialMarking`: a map of place name -> array of tokens. Marking is
**data, not structure**: a test harness or composition may supply or override the marking
without touching the net. The optional `initialMarking` is a convenience default, never
authoritative.

## Handler, guard, and predicate refs

Transition `handler` is an **optional behavior binding**; `guard` and predicate
`handler` are optional named conditions at their respective sites. Every present ref is
a nonempty string resolved by the runtime handler registry (ADRs 0002, 0003 amendment).
The schema carries only declared names; binding one to callable behavior is the
registry's job. A missing transition `handler` is represented only by an absent key /
parsed `None`, never `null`, `""`, a same-name ref, or a no-op.

The Engine offers an opt-in `Engine.validate(net)` that checks **declared** refs against
the registry and raises `HandlerNotFound` on the first unresolvable ref before any
`run`. It skips a handlerless transition because there is no ref to resolve. Explicit
Engine firing checks structural enablement first (`NotEnabled` on failure); firing an
enabled handlerless transition returns an atomic `HandlerNotFound` failed record.
Runtime rejects handlerless nets at construction; see
`handler-contract.md` / `firing-semantics.md` (e).

The full handler registry contract — what each bound handler kind receives and returns —
is the subject of `spec/handler-contract.md`. The split there is: transition handler
(receives `{transitionId, inputTokens, firingContext}`, returns
`{status, outputTokens, error, metadata}`), guard handler (returns boolean, may be
impure), and predicate handler (returns boolean, must be pure).

## Firing and replay

Firing semantics (enablement: all input arcs satisfiable AND guard true; firing: require
an explicit transition behavior binding, tentatively consume per consume patterns,
invoke the bound handler, on `completed` deposit handler tokens against the produce
templates, record in a deterministic journal) and the firing-policy handler (default
first-found, deterministic and replayable; ADR 0005) are specified in
`spec/firing-semantics.md`. A handlerless transition remains structurally enabled when
its arc/timer/guard conditions hold, but an Engine firing attempt fails atomically with
`HandlerNotFound`; no structural fire occurs. The net-purity principle above is what
makes replay hold: a net never computes, so same net + same inputs + same handler results
produce the same firing sequence.


## Worked example: a planning slice

This planning-slice example shows a feature flowing
`backlog -> plan_needed -> plan_drafted -> qa_check -> done`, a structural
`git_tree_diff` gating pattern, and an inhibitor preventing a finished feature from
restarting:

- `start_feature` — a bootstrap commit (no git arc): consumes a `feature` from `backlog`
  and inhibits on `done` so a completed feature cannot restart.
- `write_plan` — a unit-start edit: inhibits `git_tree_diff` (require clean, consume
  nothing) and produces a `git_status` token into it (the edit dirties the tree).
- `commit_plan` — a gated commit: consumes the `git_status` token (require dirty; the
  commit eats the diff) and has no git output arc, leaving `git_tree_diff` empty (clean).

```json
{
  "name": "planning-slice",
  "places": [
    { "name": "backlog",       "accepts": ["feature"] },
    { "name": "plan_needed",   "accepts": ["feature"] },
    { "name": "plan_drafted",  "accepts": ["feature"] },
    { "name": "qa_check",      "accepts": ["feature"] },
    { "name": "done",          "accepts": ["feature"] },
    { "name": "git_tree_diff", "accepts": ["git_status"] }
  ],
  "transitions": [
    { "name": "start_feature", "handler": "start_feature" },
    { "name": "write_plan",    "handler": "write_plan" },
    { "name": "commit_plan",   "handler": "commit_plan" }
  ],
  "arcs": [
    { "from": { "place": "backlog" },       "to": { "transition": "start_feature" }, "consume": { "type": "feature" } },
    { "from": { "place": "done" },          "to": { "transition": "start_feature" }, "consume": { "type": "feature", "mode": "inhibit" } },
    { "from": { "transition": "start_feature" }, "to": { "place": "plan_needed" },  "produce": { "type": "feature", "destination": "plan_needed" } },

    { "from": { "place": "git_tree_diff" }, "to": { "transition": "write_plan" },   "consume": { "type": "git_status", "mode": "inhibit" } },
    { "from": { "place": "plan_needed" },   "to": { "transition": "write_plan" },   "consume": { "type": "feature" } },
    { "from": { "transition": "write_plan" }, "to": { "place": "git_tree_diff" },  "produce": { "type": "git_status", "destination": "git_tree_diff" } },
    { "from": { "transition": "write_plan" }, "to": { "place": "plan_drafted" },   "produce": { "type": "feature", "destination": "plan_drafted" } },

    { "from": { "place": "git_tree_diff" }, "to": { "transition": "commit_plan" },  "consume": { "type": "git_status" } },
    { "from": { "place": "plan_drafted" },  "to": { "transition": "commit_plan" },  "consume": { "type": "feature" } },
    { "from": { "transition": "commit_plan" }, "to": { "place": "qa_check" },      "produce": { "type": "feature", "destination": "qa_check" } }
  ]
}
```

### Reading the structural gating pattern

`git_tree_diff` holds one `git_status` token **iff the working tree is dirty**; it is
empty iff clean (projection, not a guard). Three arc forms express a transition's
relationship to the tree, all on the single place:

- **Require clean** — a `consume` arc with `mode: "inhibit"` on `git_tree_diff`. Enablement
  requires the place to be empty; firing consumes nothing. Used by `write_plan` (an edit
  must start on a clean tree).
- **Produce dirty** — a `produce` arc into `git_tree_diff`. Firing deposits a `git_status`
  token, modeling an edit that dirties the tree. Used by `write_plan` (the edit itself).
- **Require dirty / commit** — a `consume` arc (default mode) on `git_tree_diff`. Firing
  **consumes** the `git_status` token; because a commit has no git output arc, the place is
  left empty — the net models "a commit cleans the tree" structurally as consumption of
  the diff. Used by `commit_plan`.

`start_feature` has no arc to `git_tree_diff`, so working-tree state neither gates it nor
changes that place structurally.
And `start_feature` inhibits on `done` to prevent restarting an already-completed feature
— a zero-test unrelated to git, demonstrating that `mode: "inhibit"` is a general
structural gate, not a git-specific mechanism.

## Lint rules (advisory, opt-in)

A **lint rule** is a static check over a parsed net that flags a *legitimate* shape which is far more often a bug than intent. Lint is **opt-in and advisory, never validation**: every shape a lint rule flags parses clean and fires correctly, so a finding is a warning a consumer may act on or ignore — it is never a parse/validation error, and declining to lint changes nothing. (Contrast the structural validations above, which reject a net outright.) The Python reference implementation exposes this as `velocitron.lint.lint_net(net) -> list[LintFinding]`; each finding carries a **stable rule id**, the flagged transition's name, and a human-readable message.

### Rule `consume-without-produce`

A transition with **one or more `consume`-mode arcs and zero produce arcs** is flagged: it consumes but never produces — likely a missing produce arc. The motivating incident: a net generator over-dropped produce arcs, leaving `advance_*` transitions with consume+read inputs and no outputs — a net that parsed clean but could never advance, caught only by executing it. This rule catches that statically.

The rule is scoped to *consumption* sinks:

- **Read- and inhibit-mode arcs are not outputs.** A read arc removes nothing and an inhibit arc is a zero-test, so neither exempts a consuming transition from the rule — the motivating consume+read shape is flagged.
- **A transition whose only inputs are read/inhibit arcs (and no produce arcs) is clean.** It removes no tokens, so it cannot silently drain the net; it is a pure gate/observer (its handler fires for effect), a different shape from the dropped-produce-arc bug this rule targets, and deliberately out of this rule's scope.

Sink transitions are a legitimate construct — a `clear_flag` transition that consumes a flag token and produces nothing, or a terminal discard — which is exactly why this is a lint, not a validation.

### Suppressing an intentional sink (ADR 0016)

An intentional sink is acknowledged per-transition through the documentation-fields carve-out (ADR 0011), by naming the rule id in a `lint.suppress` list inside the transition's `annotations`:

```json
{
  "name": "clear_flag",
  "handler": "clear_flag",
  "annotations": { "lint": { "suppress": ["consume-without-produce"] } }
}
```

Suppression is **rule-specific** (the list names exactly the rule ids being acknowledged) and **fails open**: anything other than the documented shape — a `lint` object carrying a `suppress` array of rule-id strings — suppresses nothing, so a malformed acknowledgement surfaces as the finding still firing rather than silently silencing a rule. The engine continues to ignore `annotations` entirely; only the opt-in lint surface reads the `lint` key. See `docs/adr/0016-lint-surface-annotations-suppression.md`.

## Cross-document pointers

- `spec/composition.md` — the composition document (`{nets, wires}`), `Wire`, and the
  merge-and-wire rules. Defines how port-places from this schema are joined.
- `spec/handler-contract.md` — the runtime handler registry contract:
  transition, guard, and predicate handler signatures.
- `spec/firing-semantics.md` — enablement, firing, the firing-policy
  handler, and the deterministic/replayable firing journal.
- `spec/properties.md` — the declarative property pass: the verification
  vocabulary and the checking semantics of `capacityPerColorKey` (ADR 0019).
- `docs/adr/` — design rationale and history for the retained decisions cited above.