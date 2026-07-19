# Documentation fields are the permitted non-behavioral exception to net purity

`description: string` and `annotations: object` are admitted as optional,
documentation-only fields on every net element (place, transition, arc, and
the top-level net). They carry no firing semantics — the engine ignores them
entirely.

This is a deliberate, scoped exception to ADR 0001's permanent exclusion of
executable inscriptions. ADR 0001 excludes *behavioral* inscriptions
(computation, transformation, side effects, handler dispatch). Documentation
fields are non-behavioral metadata — they describe, annotate, and group
elements for human readers and tooling, but never influence enablement,
binding, firing, or token flow.

**Considered options:**
- Keep `additionalProperties: false` on every element and require each
  consumer to strip documentation fields before validation. Rejected: this
  forces every consumer to maintain a strip set; the fields are universal
  enough to warrant first-class schema admission.
- Admit `description` only, keep `annotations` out. Rejected: a single
  extension point (`annotations`) avoids per-field schema additions for
  consumer-specific metadata without adding firing semantics.
- Admit both as first-class optional fields (chosen). `description` is the
  universal documentation field (JSON Schema, OpenAPI); `annotations` is the
  general extension point for arbitrary non-behavioral metadata.

**Consequences:**
- The schema's `additionalProperties: false` stays — only the two named fields
  are admitted; unknown properties are still rejected.
- The engine never references these fields (verified by grep). Net purity
  holds by inertia, not by new guard code.
- Consumers can drop documentation-field strip logic and rely on the parser
  to preserve the fields.
- `annotations` is intentionally unconstrained (`{"type": "object"}` with no
  `additionalProperties` limit) — it is the escape hatch for arbitrary
  consumer metadata without per-field schema edits.
