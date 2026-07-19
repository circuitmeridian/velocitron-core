# Maintainer guidance

Velocitron is a public 0.1.0 alpha. Favor correctness and explicit contracts
over compatibility scaffolding. APIs, schemas, the DSL, diagnostics, and
capabilities may change, but changes must keep the Python and TypeScript
implementations aligned with the portable contracts in this tree.

## Sources of truth

Read the relevant contract before changing behavior:

- `CONTEXT.md` defines the project's domain vocabulary.
- `spec/net-schema.md` and `spec/net.schema.json` define net documents.
- `spec/composition.md` and `spec/composition.schema.json` define composition.
- `spec/petrinet-language.md`,
  `spec/petrinet-contribution-ir.schema.json`, and
  `grammar/VelocitronPetriNet.g4` define the `.petrinet` frontend.
- `spec/handler-contract.md` and `spec/firing-semantics.md` define runtime
  behavior.
- `spec/properties.md` and `spec/projection-adapter.md` define their named
  surfaces.
- `docs/adr/` records design rationale and history. ADR text is not a promise
  of API stability, compatibility, security support, or maintenance.

Normative prose and schemas take precedence over implementation convenience.
Keep terminology consistent with `CONTEXT.md`.

## Layout

- `spec/` — portable prose contracts, JSON Schemas, and conformance data
- `grammar/` — the ANTLR grammar for `.petrinet`
- `implementations/python/` — Python package `velocitron`
- `implementations/typescript/` — ESM package `@velocitron/core`
- `examples/` — source examples arranged by capability
- `skills/velocitron/` — bundled authoring guidance and examples
- `docs/adr/` — architectural decision records
- `tools/` — repository generation and consistency tools

## Tests

Run each implementation's tests from its own directory:

```bash
cd implementations/python
uv run pytest
```

```bash
cd implementations/typescript
pnpm test
```

Tests use BDD phase comments as readable delimiters. Keep setup under
`# given:` / `// given:`, the exercised action under `# when:` / `// when:`,
and assertions under `# then:` / `// then:`. Use `and:` comments for additional
steps in the same phase. Test observable behavior and use minimal fixtures;
keep temporary filesystem work isolated to test-provided temporary paths.

## Generated artifacts and schemas

Generated sources are committed because they are part of the distributable
packages. Do not hide or remove them and do not hand-edit them.

- Edit `grammar/VelocitronPetriNet.g4`, then regenerate the Python ANTLR output
  with `python3 tools/antlr.py generate` and the TypeScript output with
  `python3 tools/antlr.py generate-typescript` from the repository root.
- Treat `spec/net.schema.json`, `spec/composition.schema.json`, and
  `spec/petrinet-contribution-ir.schema.json` as the canonical schema inputs.
  Keep the packaged Python schema resources synchronized with the canonical
  JSON, and regenerate TypeScript schema artifacts with `pnpm generate:schema`
  and `pnpm generate:ir-schema` from `implementations/typescript/`.
- Generated TypeScript files under `src/schema/generated/` and
  `src/dsl/ir/generated-validator.ts`, and ANTLR output under each
  implementation's `src/.../generated/`, must match their sources.

A contract change is incomplete until affected prose, schemas, generated
artifacts, both implementations, conformance fixtures, examples, and tests are
consistent.

## Public-alpha boundaries

Broad feedback and reproducible defect reports are welcome. External code
contributions and pull requests are not accepted yet. Do not imply that a
report will receive a response or that a proposed change will be reviewed or
merged. Security and support expectations are defined in `SECURITY.md` and
`CONTRIBUTING.md`; support is best-effort and this alpha is not intended for
production or sensitive workloads.
