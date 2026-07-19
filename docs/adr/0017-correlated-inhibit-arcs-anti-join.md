# Binding-correlated inhibit arcs (`correlate`) are the anti-join primitive

An inhibit arc may declare an optional `correlate: {cel: "<expr>"}` inscription. A correlated inhibit arc is a **per-binding zero-test**: for a candidate binding B, the arc is satisfied iff **no** token in its source place — of the arc's declared `type`, passing the arc's single-token `predicate` (if any) — also satisfies `correlate` evaluated over both that candidate token and B. This is the classic **anti-join**: "transition enabled under binding B only if NO token in place P is correlated with B."

## Motivation

Two spikes and 4 of 6 Phase-B modeling exercises named the same gap: an inhibit arc is a whole-place zero-test, optionally narrowed by a static type/predicate match, but the predicate **cannot reference the candidate binding**. Two recurring consequences in real models:

1. **Orphan detection is passive.** "Move this stage token to `orphaned` if NO parent token with the SAME `crawl_tag` exists upstream" is not expressible: the inhibit test cannot correlate on the bound token's key. Orphans are found as stuck-tokens-at-quiescence (a liveness walk outside the net) instead of being routed by the net itself.
2. **Per-key dedup forces single-key scoping.** "No `mod_flag` token FOR THIS ACCOUNT" cannot be written, so nets get scoped to a single account to make the whole-place zero-test correct.

Neither has a net-pure encoding today. A **guard** cannot express it: guards see only the input binding (`inputTokens`), never the marking, so the inhibit place's tokens are invisible to them (ADR 0003 — handlers see their declared inputs, not the full marking). The only workaround is an impure guard that reaches into external state, which wrecks replayability — exactly what ADR 0002's pure/impure split exists to prevent.

## The inscription

```json
{
  "from": {"place": "mod_flags"},
  "to": {"transition": "apply_mod"},
  "consume": {
    "type": "mod_flag",
    "mode": "inhibit",
    "correlate": {"cel": "token.account == binding.orders[0].account"}
  }
}
```

`correlate` is valid **only on `mode: "inhibit"` arcs** (the parser rejects it on consume/read arcs — those bind tokens, and cross-token conditions over bound tokens are guard territory). It composes with the arc's existing surface: `type` and the single-token `predicate` first narrow which tokens in the place are candidates; `correlate` then tests each surviving candidate against the binding.

### The CEL environment: `token` + `binding`

A correlated expression sees two names:

- **`token`** — the candidate token in the inhibit place: its `data` object.
- **`binding`** — the candidate binding, shaped exactly like the guard's `inputTokens` (`spec/handler-contract.md`) projected to data: a map of source-place name → list of bound tokens' `data` objects, covering consume- **and read-mode** arcs in arc-declaration order (D1/ADR 0012's binding shape). E.g. `binding.orders[0].account` is the `data.account` of the first token bound from place `orders`.

This deliberately **diverges from the single-token predicate environment**, where a token's `data` fields are bare top-level names (`kind == 'ripe'`). A correlated expression has two token universes in scope — the inhibit-place candidate and the bound tokens — so bare fields would be ambiguous. Namespacing under `token`/`binding` keeps both addressable, and `binding` reuses the one binding vocabulary the contract already has (the guard's place-keyed shape) rather than inventing a second.

## Semantics decisions

- **Evaluation order (the load-bearing decision).** Uncorrelated inhibit arcs (no `correlate`) keep their current **early, pre-binding** evaluation — cheap, whole-place, unchanged; no regression. Correlated inhibit arcs are evaluated **per candidate binding**, inside the deterministic binding enumeration, **after** the sub-multiset validity check and **before** the guard. Rationale for before-the-guard: a correlated inhibit is a *structural*, pure, arc-level test — it belongs with the other structural filters (predicate match, sub-multiset), and the possibly-impure guard (ADR 0002) should only see bindings that already passed every structural gate. It also keeps evaluation-cost ordering sane: pure CEL before a guard that may consult external state.

- **A correlated inhibitor filters candidate bindings; it never reorders them.** Binding enumeration order is untouched (consume/read arcs in declaration order, per-arc lexicographic weight-combinations, cartesian product — D2). A binding blocked by a correlated inhibit arc is *skipped*, exactly like a sub-multiset-invalid combo or a guard-rejected binding, and the first surviving binding is selected. Determinism and replay hold with zero new machinery.

- **Failure posture: eval error ⇒ the binding is blocked (fail-closed), matching the guard's posture, not the single-token predicate's.** D6's "eval error ⇒ predicate false" rule is *fail-toward-not-enabled* on consume arcs (an unmatched token cannot be bound) — but applied to an inhibit test it would be fail-*open* (an unmatched token does not block ⇒ enabled). A correlated inhibitor is typically a safety gate (dedup, orphan-routing); enabling a transition because its safety test crashed is the wrong degradation. So a `correlate` eval error (missing field, type mismatch, a raise from any backend) marks the candidate token as **blocking**: the binding is rejected, degrading toward not-enabled — symmetric with a guard that raises (D9), which is the other binding-scoped condition. Never a crash. The uncorrelated inhibit predicate's existing fail-open behavior (a locked surface) is unchanged.

- **Compile at parse (D6, unchanged).** `correlate.cel` is compiled when the net is parsed; a syntax/compile error fails parsing as a `NetValidationError`, exactly like every other inline CEL expression.

- **Empty-binding edge.** A transition whose only input arcs are correlated inhibit arcs has the empty binding `{}` (the empty product). `correlate` is then evaluated with `binding` empty: an expression referencing a bound place eval-errors ⇒ fail-closed ⇒ not enabled; an expression over `token` alone works (a self-contained narrowing, equivalent in power to a single-token predicate).

- **`weight` stays rejected on inhibit arcs** (D7) — a correlated inhibit is still a zero-test and consumes nothing.

- **Net purity (ADR 0001) is intact.** `correlate` is a boolean filter — it never transforms token data, never emits tokens. It widens what a predicate may *reference* (the binding), not what an inscription may *do*.

## Considered options

- **A `correlate` CEL inscription on the inhibit arc (chosen).** Additive, minimal schema surface, reuses the existing parse-time-compile / adapter machinery, and names the two token universes explicitly.

- **Reuse the existing `predicate` field with an auto-detected environment.** Rejected: deciding "does this expression reference `binding`?" requires AST inspection across three CEL backends (fragile; celpy, cel-cpp, and cel-rust expose different ASTs), and it silently changes the vocabulary of an existing field — a bare `account` would mean the candidate's field in one predicate and be shadowed in another.

- **A named correlate handler (`correlate: {handler: "..."}`).** Deferred, not rejected: it needs a new handler-contract input shape (`{token, binding, firingContext}` — today's predicate handlers are single-token by contract), which is a `handler-contract.md` amendment. CEL covers the motivating cases (key equality); the `{cel}` object form leaves room to add a `handler` variant later without a schema break.

- **Express it as a guard.** Rejected as the status quo: guards see only the binding, never the marking, so the absence-test over the inhibit place is invisible to them; an impure guard reading external state breaks replay (ADR 0002/0003).

- **Widen guards to see the full marking.** Rejected: reverses ADR 0003's core decision (handlers are pure functions of their declared inputs, maximally testable), with blast radius across every existing guard.

## Consequences

- The engine gains one per-binding check between the sub-multiset validation and the guard; `_inhibit_satisfied`'s early whole-place zero-test now covers only uncorrelated inhibit arcs. With no `correlate` anywhere in a net, enablement behavior is byte-for-byte unchanged.
- Orphan routing becomes an active transition (`route_to_orphaned` consumes the stage token whose key has no upstream parent) instead of an out-of-band quiescence walk; per-key dedup nets no longer need single-key scoping.
- The CEL activation for `correlate` carries nested objects (`token`, `binding`), which the pure-Python celpy backend only supports via `celpy.json_to_cel` conversion — applied in `CelpyAdapter.eval`, which also closes the latent gap for existing predicates over nested `token.data` fields (the C++ and Rust backends handle plain nested dicts natively).
- The schema change is additive; every existing net document remains valid and behaves identically.
