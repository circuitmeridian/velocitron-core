# `.petrinet` language contract

## Status and boundary

This specification defines version 1 of Velocitron's `.petrinet` authoring
language. It is a flat, progressive syntax which lowers ordered Contributions
into canonical JSON-shaped `net` or `composition` documents. The existing
`parse_net` and `parse_composition` functions remain the only semantic
validators. This contract does not define a second net model, JSON Schema
validator, CEL evaluator, composition merger, or firing engine.

The sole grammar is `grammar/VelocitronPetriNet.g4`. It is action-free and
uses ANTLR 4.13.2. Python generation uses the matching
`antlr4-python3-runtime==4.13.2`; the same ANTLR release has an official
TypeScript target, but this feature produces no TypeScript code or package.

## Source text and diagnostics

Source is strict UTF-8 without a BOM, bare CR, or isolated surrogate scalar.
LF and CRLF are accepted without normalization. Source spans identify a
portable relative source ID, half-open UTF-8 byte offsets, and 1-based
Unicode-scalar line and column positions. A diagnostic is structured as a
stable code, primary span, message, and ordered related spans. Parsing never
lowers a recovered partial tree. EOF spans are zero-width at source length.

DSL JSON values are RFC 8259 values only: double-quoted strings, lowercase
`true`, `false`, and `null`, valid JSON number syntax, arrays, and objects.
Duplicate keys, non-finite numbers, invalid escapes, raw string newlines, and
isolated escaped surrogates are rejected. Numbers must be finite IEEE-754
binary64 values; integer literals are additionally limited to
`[-9007199254740991, 9007199254740991]`.

Canonical JSON uses the project pretty profile: UTF-8; LF line endings; a
final LF; two-space indentation; object keys sorted lexicographically by
UTF-16 code units as specified by RFC 8785; semantic array order; unescaped
non-ASCII scalars; and RFC 8785 primitive string and number rendering.
Negative zero renders as `0`; exponents use
lowercase `e` without `+`. This is pretty JSON, not RFC 8785's compact
whole-document serialization.

## Names, headers, and progressive facts

`IDENT` is `[A-Za-z_][A-Za-z0-9_]*`. Every semantic name is either an `IDENT`
or a JSON string. Reserved words require quoting. Parenthesized places,
bracketed transitions, `$` templates, and `@` handles use that name form;
canonical output has no whitespace after `$` or `@`. A quoted name is literal,
including dots and hyphens. Opaque handler, guard, predicate-handler, and
firing-policy references are always JSON strings and never structurally
qualified.

A composition document begins with its required header:

```text
composition Name
```

A net document may begin with `net Name [JsonStringDescription]`. If that
header is omitted, the source is a net whose effective name is `unnamed`;
headerless source and source beginning `net unnamed` have the same document
identity for aggregation. Canonical DSL always emits the explicit net header.
The net description lowers to the documentation-only top-level field.

A composition header names only the DSL contribution namespace: it remains
required for source identity and cross-source merging, but never lowers to
composition JSON. Consequently JSON-to-DSL emits the deterministic namespace
`composition` for a composition JSON document regardless of its filesystem
path, and legacy composition JSON reaches a JSON fixed point.

Facts may precede their final declaration. Equal repeated facts are
idempotent. Unequal repeated facts conflict with both definition spans. Final
resolution rejects unresolved templates and all other required semantic
fields. A structure-only fragment may therefore parse into Contributions
without compiling.

### Same-namespace source aggregation

`compile_petrinet_sources(sources: Sequence[SourceInput])` is the sole
multi-source API. Each source has an explicit logical relative `sourceId` and
an effective document kind and decoded name. A composition source has exactly
one header; a net source has zero or one, with an omitted header supplying the
effective name `unnamed`. The API accepts sources only when their effective
document kind and name are identical, so headerless net sources aggregate with
sources explicitly headed `net unnamed`. Contributions append in supplied
source order and then source statement/part order. Same-file and separate-file
contributions therefore use the same idempotence, conflict, final-resolution,
and first-appearance rules. Duplicate source IDs, mismatched effective names,
mixed kinds, and reference cycles are deterministic `PN2xx` errors. The
single-path CLI uses a one-source sequence; composition `use` loads a
constituent document, not an implicit same-namespace contribution.

## Topology, order, and arc facts

Topology is chain-first and expands left to right. `-Color->` is a default
consume/produce segment, `-Color->?` is a read input, and `-Color->0` is an
inhibit input; the latter two are allowed only place-to-transition and are
longest-match lexer tokens. A bare `->` is the **Generic token** default
consume/produce syntax: its core color is exactly the ordinary string `token`
and is never inferred from adjacent places or other context. `token` is the
conventional color for classical/uncolored nets, not a wildcard or an untyped
value. Core JSON and DSL resolution retain it normally; presentation layers may
omit only its visible type label.

Referenced places/transitions and standalone `(Place)` / `[Transition]`
declarations participate in first-appearance ordering. Each topology color,
including `token` from a bare arrow, is added to each adjacent place's accepted
ordered color list. Chain expansion normally determines arc order; marking
statements determine token order.

A chain handle names one consecutive expansion run. The only order facts are
`[Transition] order PositiveInt` and `@Handle order PositiveInt`. If used, an
order class is complete and uniquely ranks `1..N`; arc-run ordering preserves
left-to-right order within the run. Canonical emission normally emits one arc
per line in core arc-array order and emits order facts only when its own layout
would otherwise change core order.

The exact statement productions are:

```text
PlaceDecl      := "(" Name ")"
TransitionDecl := "[" Name "]"
Chain          := [ "@" Name ":" ] Endpoint ( ArcSegment Endpoint )+
Endpoint       := "(" Name ")" | "[" Name "]"
ArcSegment     := "-" ( Name | JsonString ) "->"
                | "-" ( Name | JsonString ) "->?"
                | "-" ( Name | JsonString ) "->0"
                | "->"
ArcFact        := "@" Name ( "predicate" ( "cel" | "handler" ) JsonString
                           | "weight" PositiveInt
                           | "data" "cel" JsonString
                           | "data" JsonValue
                           | "correlate" "cel" JsonString
                           | "order" PositiveInt )
PlacePort      := "(" Name ")" "port" ( "input" | "output" ) Name
PlaceAccepts   := "(" Name ")" "accepts" "[" Color ( "," Color )* "]"
PlaceCapacity  := "(" Name ")" "capacityPerColorKey" JsonObject
TransitionFact := "[" Name "]" ( "handler" JsonString
                               | "guard" JsonString
                               | "order" PositiveInt
                               | "priority" NonnegativeInt )
```

`@arc_<N>` is reserved for formatter-generated identity and may appear in
source only when `N` equals the zero-based index of its uniquely targeted
core arc. An authored handle that would collide with any generated handle is
rejected. `PositiveInt` is decimal `1..`; `NonnegativeInt` is decimal `0..`.
Ports, descriptions, and annotations cannot declare an unknown semantic
object; duplicate differing facts conflict.

Arc elaborations are progressive and target a handle: `predicate cel`,
`predicate handler`, `weight`, `data`, and `correlate cel`. A predicate target
must identify exactly one place-to-transition arc. `correlate cel` must target
exactly one inhibit input. Weight is valid only for eligible input modes and
is at least one; default weight one is omitted canonically. `data` targets a
produce arc and distinguishes absent data from literal `{}`. `data cel` is the
computed variant (ADR 0023): its string is an inline CEL expression over the
name `binding`, mutually exclusive with literal `data` on one arc, and lowers
to the template's `cel` field. Place capacity is
exactly `(Place) capacityPerColorKey JsonObject` with schema `key` and `max`;
it is checker-only, never an enablement or deposit gate.
An accepted-color fact is exactly `(Place) accepts [Color, ...]`. It declares
the place and fixes its complete, non-empty, ordered `accepts` list. Equal
repetitions are idempotent; unequal repetitions conflict. Topology-derived
colors combine idempotently with the declaration, but a topology color absent
from the declared list conflicts. Canonical emission omits these facts when
topology alone reconstructs every place and its exact accepted-color order; if
any place needs a fact, it emits facts for all places in core order so isolated
places and place-array order round-trip exactly.

An `arc.declare` Contribution always encodes its selected core color as
`{"kind":"explicit","value":Color}`. For bare `->`, `Color` is `token`; neither
the Contribution IR nor final resolution performs contextual color inference.
The value remains an ordinary exact-match color through parsing and core JSON,
even when visualization omits the generic label.

Standalone declarations lower to `place.declare` or `transition.declare` with
the corresponding typed target and the closed empty value `{}`. A standalone
place for which the complete aggregate supplies no explicit or topology color
resolves with `accepts: ["token"]`. Any such color evidence, including evidence
later in the same source or a later aggregate source, prevents that fallback;
the fallback never adds `token` beside evidenced colors.

A formatter reconstructing an elaborated core arc without a validated
presentation handle calls it `@arc_<zero-based-core-arc-index>`. Such a handle
is generated identity, not preserved authored metadata.

## Transition, template, marking, and timer facts

Transition facts are direct and separate: `[Transition] handler JsonString`
and `[Transition] guard JsonString`. They do not declare their target; topology
or a standalone `[Transition]` declaration must declare it first. A handler
fact supplies the transition's optional behavior binding and must contain a
nonempty JSON string. If no explicit handler fact exists at final resolution,
the core `handler` key is
omitted and parsed `Transition.handler` is `None`. No opaque same-name ref,
executable no-op, or structural firing behavior is synthesized. Handlerless
structure remains valid for parse, composition, and visualization; Engine and
Runtime execution enforce the boundaries in `spec/firing-semantics.md`.
Timer facts are exactly:

```text
[Transition] timer clock (Place) cel JsonString
[Transition] timer maturity cel JsonString
[Transition] timer bind IDENT (Place)
```

`timer maturity` is optional scheduler metadata: it evaluates in the same
closed environment as the timer CEL and returns the next future monotonic
timestamp for that binding. Runtime requires it for native timers; the
synchronous Engine ignores it. `clock` is reserved; binds retain source order
in parsed DSL and use lexical name order when reconstructed from JSON.
`priority 0` is omitted canonically. Clock/bind places must be valid timer
inputs; timer facts never synthesize topology or a scheduler.

Templates and markings use exactly:

```text
$Template: Color JsonValue
marking initial (Place) <- PositiveInt
marking Name (Place) <- PositiveInt
marking initial (Place) <- [ PositiveInt "*" ] $Template
marking Name (Place) <- [ PositiveInt "*" ] $Template
```

The count is mandatory in the two count-only forms and must be strictly
positive. `marking initial (Place) <- N` and `marking Name (Place) <- N`
each append exactly `N` fresh core tokens `{"type":"token","data":{}}`.
Here `token` is the exact ordinary Generic color, not a wildcard and not
untyped core data; the target place must accept `token`.

In either contribution, a count-only right-hand side lowers to
`$defs.tokenLiteral` in `spec/petrinet-contribution-ir.schema.json`. An initial
fact's `marking.append` value is exactly
`{"count":N,"token":{"color":"token","data":{"type":"object","entries":[]}}}`;
a named fact puts the same `count` and `token` members in its
`metadata.named-marking` entry. Neither creates or references an implicit
template. The resolver accepts both
the existing template-reference shape and schema-valid `tokenLiteral` values
for initial and named marking entries. A literal's color must be accepted by
its target place, and its tagged `data` must resolve to an object valid as core
token data. Every marking fact appends its expanded tokens to that place;
repeated facts append in source order.

The `$Template` forms and their optional `N *` multiplicity are unchanged.
`initial` is reserved and may not name a non-initial marking. A template is an
immutable JSON-value abbreviation; use may precede definition, multiplicity
is positive, and expansion creates fresh JSON containers. Initial markings
lower to `initialMarking`; named markings preserve statement/place/token order
only below v1 metadata. Undefined templates, a non-JSON value, a missing or
nonpositive count where required, a conflicting template, a literal rejected
by its target place, invalid literal token data, and a named-marking collision
are rejected.

## Composition

The only composition inclusion fact is `use JsonStringRef as IDENT`; aliases
are mandatory and unique. A wire is
`wire Alias.(Place) -> Alias.(Place)`. Paths are retained verbatim and resolved
relative to the containing composition source. Wires connect an output port to
an equal-typed input port; they fuse existing places and never add transitions,
token copies, polling, handlers, or ref rewriting. Each constituent transition's
optional behavior binding is preserved unchanged, including absence.

`parse_composition(source, *, net_loader=None, origin=None)` uses
`origin: Path | None` solely to resolve relative default-loader references.
For a filesystem path, the parser supplies that path's parent automatically.
For in-memory mapping/raw text, a relative ref is rejected unless the caller
supplies `origin` or a `net_loader` closure that resolves it. Absolute paths
need no origin. `net_loader` is
`Callable[[str], Mapping | Path | str]`: a DSL loader accepts a
source-relative `.petrinet`, compiles it to JSON-shaped data, returns that
data, and the parser calls `parse_net` itself. JSON refs return JSON source
material to the same validator. Missing files, unsupported extensions, cycles,
and loader failure are stable `PN5xx` diagnostics; a loader may never return a
`Net`, `Composition`, or bypassed validation result.

## Full-document metadata and presentation

The exact metadata facts are:

```text
MetadataTarget description JsonString
MetadataTarget annotation Name JsonValue
extensions JsonObject
view Name position ViewTarget at {"x": Number, "y": Number}
view Name route @Handle orthogonal [ {"x": Number, "y": Number}, ... ]
```

`MetadataTarget` is `net`, `(Place)`, `[Transition]`, or `@Handle`.
`ViewTarget` is only `(Place)` or `[Transition]`; views cannot position the
document or an arc. `net description` and `net annotation` lower directly to
top-level core documentation fields, so arbitrary ordinary net annotation keys
survive JSON→DSL→JSON. `extensions` is net-scope only. Annotation keys use
`Name`; the reserved `"petrinet.dsl/v1"` key is rejected in ordinary annotation
facts. An unknown target, non-object extension, malformed/non-finite position,
unsupported view style/member, route to an unknown or stale handle, or a
metadata fact trying to declare a semantic object is rejected.

Descriptions and ordinary annotations lower directly to their core
non-behavioral fields. Named markings, geometry, routes, authored arc handles,
and opaque extensions lower only below net
`annotations["petrinet.dsl/v1"]`. Version 1 has exactly these shapes:

```text
arcHandles: { Handle: { index: NonnegativeInt,
                        fingerprint: { from, to, type, mode } } }
markings:   { Name: { Place: [Token, ...] } }
views:      { Name: { positions: { Target: Point },
                      routes: { Handle: { style: "orthogonal",
                                          points: [Point, ...] } } } }
extensions: JsonObject
```

Formatting validates handle index first and exact fingerprint second. Only
`extensions` may contain unknown future fields. Metadata cannot change Engine,
property, composition, or default visualization behavior; `view row` is not
syntax.

## Portable Contribution IR

Contribution IR has `format: "velocitron.petrinet/contribution-ir"` and
`version: 1`. Its machine-readable schema is
`spec/petrinet-contribution-ir.schema.json`. It contains a document kind and
an ordered contribution array. Every contribution has a source-derived unique
identity, contiguous source ordinal, closed kind, explicit typed target,
JSON-only value, and portable source span. It represents progressive facts;
source metadata never reaches resolved canonical JSON. Unsupported format or
version is rejected before resolution.

## Canonical output and CLI

Canonical DSL is LF-terminated, explicit, deterministic, and a fixed point. It
always emits a net header and explicit arc colors (including `-token->`).
For each transition that has no incident topology arc, canonical output emits
a standalone `[Transition]` declaration before that transition's optional
facts. It emits a transition handler fact exactly when the core transition has
a behavior binding and omits the fact when the core `handler` key is absent.
Header omission, bare arrows, and place-color fallback remain authoring sugar;
handler absence remains absence. Canonical output emits topology in core order;
standalone isolated-transition declarations; present handlers/guards; timer and
priority facts; place facts; arc elaborations; markings/templates; then
documentation and full-document metadata.

For markings, every maximal consecutive run of
`{"type":"token","data":{}}` emits `marking initial (Place) <- N` or
`marking Name (Place) <- N` with the integer run length substituted for `N`,
including `1`; canonical output does not generate a template for that run. All
other tokens retain generated
`$token_<zero-based-distinct-token-ordinal>` template facts, and only
consecutive structurally equal tokens coalesce. Generic-empty runs do not
consume a generated-template ordinal.

Compact output is separately requested and may replace eligible default
`-token->` consume/produce segments with bare `->`; bare arrows never
abbreviate another color.

The `velocitron` console script provides exactly:

```text
velocitron validate INPUT
velocitron to-json INPUT [--semantic-only]
velocitron to-petrinet INPUT [--compact]
```

Successful commands write only UTF-8 output to stdout and no stderr; `validate`
writes `net\n` or `composition\n`. User errors exit 1 with deterministic
`PATH:LINE:COLUMN: error[CODE]: MESSAGE` stderr and no traceback. Argument
misuse exits 2. `to-json` preserves full net metadata by default;
`--semantic-only` is the only removal path.

## Contract amendments

The former Slice 03 requirement to canonically recover `@vend_signal` is
replaced by generated `@arc_3`: core JSON has no source-only handle field.
Slice 02 authored return-first topology remains a valid parse/resolution case,
not required canonical JSON-to-DSL text. Composition headers remain
DSL-local namespaces and do not add core JSON metadata.
