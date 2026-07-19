# `@velocitron/core`

The ESM TypeScript package for Velocitron provides shared JSON types and schema validation, `.petrinet` compilation, and structural operations for typed colored Petri nets. It makes no simulator or runtime promise.

## Install

```console
npm install @velocitron/core
```

Import the public API from the package root:

```js
import { compilePetrinetText, parseNet } from "@velocitron/core";
```

The package requires Node.js 20.19.0 or newer.

## Alpha status

Version 0.1.0 is alpha software. APIs, schemas, the DSL, diagnostics, and capabilities may change without notice. Velocitron is not production-ready and provides no compatibility, migration, support-response, or maintenance guarantees. Support is best-effort. Feedback and reproducible defect reports are welcome; external code contributions are not currently accepted.

Velocitron has no formal security-support program or currently supported
private vulnerability-reporting channel. Do not use it in production,
security-sensitive, or otherwise high-consequence contexts. The
[repository security policy](https://github.com/circuitmeridian/velocitron-core/blob/main/SECURITY.md)
describes the current boundary.

For the cross-language overview and format documentation, see the [repository documentation](https://github.com/circuitmeridian/velocitron-core#readme).

## Authorship and acknowledgment

Velocitron is authored solely by Matthew R. Scott. Henrique Bastos is acknowledged for collaboration toward an interoperable Petri-net specification; this acknowledgment does not indicate shared authorship.

Licensed under the MIT License.
