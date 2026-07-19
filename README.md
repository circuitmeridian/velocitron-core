# Velocitron

Velocitron is an early, practical toolkit for authoring, validating,
visualizing, simulating, and running typed colored Petri nets in Python and
TypeScript. Its portable contracts are the `.petrinet` authoring language and
a shared JSON representation for nets and compositions.

## Status: 0.1.0 alpha

This 0.1.0 alpha is a serious working release for technical exploration, not
a compatibility or production-readiness promise. APIs, schemas, the DSL,
diagnostics, and implementation capabilities may change without notice. There
is no migration, maintenance, support-response, or security-support guarantee.

Validation is focused on the demonstrated technical paths. It is not an
assurance that a net is correct for a particular operational use. The Python
runtime is the reference implementation; cross-language parity is limited to
surfaces verified for both packages. Examples and public guides cover the
demonstrated path, not every feature or integration.

Do not use Velocitron for production, security-sensitive, or otherwise
high-consequence workloads. Support is best-effort only.

## What is here

- `spec/` defines the shared JSON schemas and the normative net, composition,
  handler, firing, property, projection, and `.petrinet` contracts.
- `grammar/VelocitronPetriNet.g4` defines the portable `.petrinet` grammar.
- `implementations/python/` contains the Python package `velocitron` 0.1.0.
  It includes parsing and validation, the DSL frontend, composition, a
  synchronous firing engine, an asynchronous runtime, journaling, optional
  lint and property checks, durable SQLite event storage, and visualization.
- `implementations/typescript/` contains the ESM package `@velocitron/core`
  0.1.0. Its public entry point exports the CEL, DSL, engine, journal,
  registry, and schema surfaces used by Node and browser consumers.
- `examples/` contains progressively more capable source examples.
- `docs/adr/` records design rationale and history.

## Python from a source checkout

Python 3.12 or newer is required. With `uv` installed:

```bash
cd implementations/python
uv sync
uv run velocitron validate ../../examples/capability-ladder/01-coin-deposit/coin-deposit.petrinet
```

The `velocitron` CLI provides:

- `validate` — validate a JSON or `.petrinet` document;
- `to-json` — lower `.petrinet` to canonical JSON;
- `to-petrinet` — render JSON as canonical `.petrinet`;
- `explain` — explain a document as Markdown or text;
- `check` — check `.petrinet` files and fenced examples for DSL syntax; and
- `skill` — install the bundled Velocitron authoring skill into an existing
  directory.

`velocitron-viz` renders a net or composition JSON/`.petrinet` document as
Graphviz DOT.

The Python library exposes its runtime and tooling through modules under
`velocitron`, including `engine`, `runtime`, `composition`, `dsl`, `lint`,
`properties`, `journal`, and `durable_sqlite`.

## TypeScript and Node from a source checkout

`@velocitron/core` is an ESM package for Node 20.19.0 or newer. Repository
development uses Node 24.15.0 or newer and pnpm 11.13.1 or newer.

```bash
corepack enable
pnpm install
cd implementations/typescript
pnpm build
pnpm test
```

After a local workspace build, consumers can import the package entry point:

```ts
import { compilePetrinetText } from "@velocitron/core";

const compiled = compilePetrinetText(
  `net coin_deposit
(coin_slot) -coin-> [accept_coin] -coin-> (cash_box)
[accept_coin] handler "accept_coin"
`,
  "example.petrinet",
);
```

This example describes current source-tree usage. It is not a claim that
`velocitron` or `@velocitron/core` is available from a public package registry,
or that any future repository URL is currently available.

## Feedback and contributions

Browse an example or model a small process of your own, then share what feels
clear, awkward, missing, or misleading. Send broad reactions through an
existing direct channel with the author. Report reproducible defects through
[GitHub Issues](https://github.com/circuitmeridian/velocitron-core/issues).

External code contributions and pull requests are not accepted yet. These
feedback routes do not create a commitment to review, merge, respond, provide
support, or maintain compatibility. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) for the
current boundaries.

## License and authorship

Velocitron is released under the [MIT License](LICENSE).

Matthew R. Scott is the sole author and copyright holder. Henrique Bastos is
acknowledged for collaboration toward an interoperable Petri-net
specification; that acknowledgment does not indicate shared authorship.
