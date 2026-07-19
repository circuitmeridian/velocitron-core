# CEL for arc predicates, named handlers for guards

Arc predicates use inline CEL (Common Expression Language, `google/cel-spec`) or named pure predicate handlers. Guards use named handler refs (may be impure, may consult external state). This split maps to the semantic difference: predicates filter tokens (pure, single-token, arc-level), guards decide enablement (possibly impure, multi-token, transition-level).

**Considered options:**
- Custom mini-language. Rejected: it would require a new parser, compiler, evaluator hardening, and language ports before net work could proceed. CEL was designed for constrained embedded evaluation and has substantial operational use in systems such as Kubernetes and Envoy; that history informed the choice but is not a security guarantee for Velocitron or for any particular CEL adapter.
- Executable inscriptions (Python/JS expressions in arcs). Rejected: they couple net semantics to host-language code and undermine portability and net purity.
- All predicates as named handlers (no inline CEL). Rejected: trivial predicates (`token.data.confidence > 0.8`) would require a handler registration each, creating indirection noise.
- Inline CEL for predicates + named handlers for guards (chosen). Inline CEL for one-off simplicity; named pure predicate handlers for reuse and complex logic; named handlers for guards that need external state.