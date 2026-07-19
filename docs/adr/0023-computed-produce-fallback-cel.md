# Computed produce fallback: `cel` on produce templates

A produce template may declare an optional **`cel: "<expr>"`** field, mutually exclusive
with the literal `data` field. Where literal `data` emits one *fixed* fallback token for a
destination/type pair the handler left uncovered, `cel` emits one *computed* fallback
token: the expression is evaluated over the firing's consumed binding and its result
becomes the emitted token's `data`.

## Motivation

An analysis of worked examples in *Understanding Petri Nets* highlighted a
recurring representational gap: produce templates were literal-only. Counter,
refill, clock, and stack examples need output token data computed from consumed
token data; for example, `x → x−1` otherwise requires a host-registered handler.
These arithmetic transformations fit the existing CEL expression surface.
Host-language handler annotations were rejected as the primary mechanism
because they couple a net to one runtime and are not available to portable
static analysis.

## What stays true (blast radius)

- **The template remains a routing contract, not a token factory (Q3).** `cel`, like
  literal `data`, is a *fallback*: it fires only for a template whose destination/type
  pair received no handler-supplied token. Handler tokens for a pair still win over every
  fallback for that pair; parallel templates still never collapse.
- **The locked `fire` contract is unamended.** A handlerless enabled transition still
  fails atomically with `HandlerNotFound`; `cel` does not give a handlerless transition
  structural firing behavior in the Engine, exactly as literal `data` does not. A
  host that chooses to synthesize handlers remains responsible for running
  handlerless nets.
- **Opt-in per template.** Existing net documents are untouched; `data`-carrying and
  bare templates behave byte-identically.
- **Purity boundary.** The CEL contract exposes only side-effect-free evaluation over
  the binding. This constrained surface is part of the rationale; it is not a
  security or sandbox guarantee for Velocitron or for a particular evaluator.

## The inscription

```json
{
  "from": {"transition": "sell"},
  "to": {"place": "counter"},
  "produce": {
    "type": "count",
    "destination": "counter",
    "cel": "{\"n\": binding.counter[0].n - 1}"
  }
}
```

DSL form (a `data` elaboration, parallel to `predicate cel`):

```
@sell_count data cel "{\"n\": binding.counter[0].n - 1}"
```

`data` and `cel` are mutually exclusive on one template (schema `not`-clause + parser).
Both remain optional; a bare template is still pure routing.

## The CEL environment: `binding`

The expression is evaluated over a single name, **`binding`** — the same place-keyed
bound-token-data map ADR 0017 gives `correlate`: source-place name → list of bound
tokens' `data`, covering consume- **and read-mode** arcs (`weight` tokens per arc,
arc-declaration order). There is no `token` name (no candidate token exists at deposit)
and no marking access (ADR 0003's visibility rule: behavior sees its declared inputs,
never the marking).

The result must be a **JSON object** (a CEL map with string keys); it becomes the emitted
token's `data` verbatim. This is the produce-side dual of the boolean condition constant:
conditions demand exactly `true`, computed fallbacks demand exactly an object.

## Failure posture: deposit-contract violation

`cel` compiles at parse time, like every other CEL surface (D6). At deposit time, an
evaluation error (missing field, backend raise) or a non-object result is a
**deposit-contract violation**: the firing fails atomically (tentative consume rolled
back, marking unchanged), the record carries `error.type == "DepositViolation"` with a
message naming the failing template, and the engine-instantiation mode (D3:
`raise` / `record_then_raise` / `record_then_drop`) governs what happens next.

Why not the predicate's degrade-to-false (D6) or the guard's not-enabled posture (D9):
both of those run *before* consumption, where "skip this binding" is a coherent recovery.
A produce fallback runs *after* the handler completed — the firing is already committed
to producing; a token that cannot be computed is exactly a token that violates the
produce contract, so it reuses D3's machinery, journal routing, and atomic rollback
wholesale rather than growing a fourth failure lane.

## Alternatives considered

- **Host-language handler annotations.** Rejected as the primary move — see
  Motivation. A host may still provide its own escape hatch through handler
  registration without changing the schema.
- **Making handlerless cel-complete transitions fireable in the Engine.** Rejected: it
  amends the locked `fire` contract and the "handlerless transition implies no behavior"
  glossary invariant for zero additional expressiveness — a host that wants structural
  firing already synthesizes handlers.
- **A `dataCel` sibling field or `data: {cel}` discriminated object.** Rejected for
  shape: `data` is typed `object` today and a `{"cel": ...}` literal is a legal payload,
  so overloading `data` would be ambiguous; a flat mutually-exclusive `cel` string field
  matches the template's existing flat shape and canonicalizes cleanly in the DSL.

## Consequences

- Counter, clock, and passthrough examples can express their computed
  fallbacks declaratively; a host-supplied handler can use them without
  per-net output-computation registration.
- The parser gains one validation rule (produce `cel` compiles at parse; `data` XOR
  `cel`) and the deposit phase gains one evaluation site in each engine (Python,
  TypeScript).
- `explain`/viz surfaces should render the `cel` inscription where they render literal
  `data` today (tracked as follow-up work, not part of this decision).
