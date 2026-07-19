# Lint is an opt-in advisory surface; suppression rides the annotations carve-out

Static lint checks over a parsed net live in a separate, opt-in surface (`velocitron.lint.lint_net(net) -> list[LintFinding]`), never in parsing/validation. Intentional occurrences of a flagged shape are acknowledged per-transition via `annotations: {"lint": {"suppress": ["<rule-id>"]}}` — a scoped, machine-read key inside the ADR 0011 documentation-fields carve-out.

Two decisions are bundled here because the second only makes sense given the first.

## Decision 1: lint is advisory and opt-in, never validation

The first rule, `consume-without-produce`, flags a transition with ≥1 consume-mode arc and zero produce arcs. The motivating bug: a net generator over-dropped produce arcs, leaving transitions that parsed clean but could never advance the net — caught only by executing it. But the flagged shape is a *legitimate* Petri-net construct (a `clear_flag` sink consuming a flag token; terminal discard transitions), so rejecting it at parse would break valid nets. Lint therefore returns findings (stable rule id + transition name + message) the consumer may act on or ignore; parsing behavior is unchanged by construction — `lint_net` takes an already-parsed `Net`.

**Considered options:**

- Make it a `NetValidationError` at parse. Rejected: sinks are legitimate; validation must accept every well-formed net.
- A `strict` flag on `parse_net` that promotes findings to errors. Rejected for now: it couples the lint lifecycle to the parser contract and forces every rule to be error-worthy; a consumer that wants hard failure can raise on a non-empty `lint_net` result in one line.
- A separate opt-in lint surface (chosen). Zero blast radius on the locked parse/fire contracts; rules can accrete without touching the schema.

## Decision 2: suppression is an `annotations.lint.suppress` list on the transition

An intentional sink acknowledges the rule by naming its id: `{"lint": {"suppress": ["consume-without-produce"]}}` on the transition's `annotations`.

**Considered options:**

- A first-class schema field (e.g. `transition.lintSuppress`). Rejected: it grows the net schema for a tooling concern and would need schema churn per future lint need; ADR 0011 admitted `annotations` exactly as the extension point for consumer metadata like this.
- Suppress by transition name in a lint-call argument (out-of-band). Rejected: the acknowledgement belongs *in the net document* next to the sink it acknowledges, so reviewers and generators see it, and it travels with the net.
- A boolean (e.g. `{"lint": {"sink": true}}`). Rejected: not rule-specific; a suppress *list of rule ids* scales to future rules and keeps each acknowledgement explicit about what it silences.
- `annotations.lint.suppress` list (chosen).

**Tension with ADR 0011, resolved:** ADR 0011 admits `annotations` as documentation-only metadata the *engine* ignores. A machine-read `lint` key bends "documentation-only" — but does not break it: the key still carries no firing semantics (enablement, binding, firing, and token flow are untouched; the engine still never reads `annotations`), and it is read only by the opt-in lint surface the consumer explicitly invoked. This is the intended use of the carve-out — "the escape hatch for arbitrary consumer metadata without per-field schema edits" — with the lint surface as the consumer.

## Consequences

- `lint_net` findings follow transition declaration order (deterministic output, diff-friendly in CI).
- Suppression **fails open**: any shape other than a `lint` object carrying a `suppress` array of strings suppresses nothing, so a malformed acknowledgement shows up as the finding still firing — never as a silently silenced rule.
- Rule ids are a stable, public contract (they appear in net documents' suppress lists); renaming one is a breaking change for annotated nets.
- Future rules join the same surface and the same suppression mechanism; the schema does not change per rule.
- The engine's "never reads `annotations`" invariant (ADR 0011) still holds and remains verifiable by grep over `engine.py`.
