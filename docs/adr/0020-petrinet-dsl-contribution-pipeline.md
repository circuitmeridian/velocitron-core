# The `.petrinet` frontend lowers portable contributions into canonical JSON

**Status:** Accepted.

## Decision

`.petrinet` has one frontend architecture in every target:

1. The sole action-free ANTLR 4.13.2 grammar owns syntax only.
2. A target adapter lowers an intact parse tree to the ordered, portable,
   JSON-only Contribution IR. It does not lower a recovered partial tree.
3. Before resolution, the consumer rejects any unsupported IR `format` or
   `version`, then validates the complete closed v1 wire shape against
   `spec/petrinet-contribution-ir.schema.json`. Unknown members and kinds are
   errors. Source-derived IDs, contiguous ordinals, source order, and portable
   spans remain resolver invariants where JSON Schema cannot express them.
4. A handwritten resolver consumes those contributions deterministically and
   produces the canonical JSON-shaped net or composition document. It does not
   produce a target-native DSL model.
5. Only then does the target call its ordinary `parseNet`/`parseComposition`
   adapter (Python: `parse_net`/`parse_composition`) over the canonical shared
   JSON schemas and existing semantic rules. The DSL owns no parallel semantic
   validator, CEL evaluator, composition merger, net model, or firing engine.

Contribution order, first appearance, conflict handling, source spans, canonical
JSON, and diagnostic codes are portable contracts, not Python implementation
details. Canonicalization is defined by the language contract and its shared
conformance corpus; a target must not substitute a platform JSON serializer or
establish a second canonical profile.

The cross-target release blocker is resolved: canonical object keys use RFC
8785's UTF-16 code-unit ordering. The frozen Python behavior test
`implementations/python/tests/test_dsl_canonical_json.py::test_canonical_nested_values_sort_keys_without_reordering_arrays`
is the explicit cross-language oracle, including its ordering of U+1F600 before
U+E000. Every target must implement UTF-16 code-unit ordering explicitly rather
than rely on a host language or platform serializer default, even when that
default happens to produce the oracle's order.

## TypeScript amendment

The TypeScript frontend is part of the current architecture:
`implementations/typescript/` may generate a TypeScript target from the same
grammar and implement the same lowering and resolver pipeline as the Python
reference. This does not permit a TypeScript grammar fork, target actions, a
TypeScript-only IR member, a native parse-tree shortcut, or a second validator.
Unsupported IR format/version and malformed closed wire shape must fail before
the handwritten resolver in TypeScript just as they do in Python.

## Alternatives rejected

- **Parse directly to Python or TypeScript model objects.** This is
  target-specific, loses progressive provenance, and creates a second path
  around the interchange contract.
- **Encode lowering or resolution in target actions.** This couples the grammar
  to a runtime and makes generated targets disagree.
- **Use canonical JSON as the immediate parse-tree output.** It cannot represent
  conflicting or forward progressive facts and their source locations.
- **Keep TypeScript permanently excluded.** Rejected because a portable
  TypeScript frontend is a current target of the Contribution IR seam.
- **Give TypeScript its own schema, semantic validator, or diagnostic
  vocabulary.** This would make target behavior differ from the reference
  implementation and is forbidden.

## Consequences

Generated target code remains isolated and reproducible. Contribution IR and
the conformance corpus remain JSON-only cross-target contracts. DSL-specific
presentation data remains only in `annotations["petrinet.dsl/v1"]`. Existing
JSON consumers, the Engine, property checker, composition merge, and
visualization retain semantic authority. Adding a target costs a generated
parser adapter and handwritten resolver, but does not add an architectural
language or validation path.
