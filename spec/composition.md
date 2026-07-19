# Composition

**Status:** Public alpha — the composition schema, structural validation rules, and place-fusion merge engine are implemented in the Python reference implementation (`implementations/python/src/velocitron/composition.py`). A merged net uses the ordinary firing semantics; dynamic transport and subscription semantics are not part of this structural composition contract (see [Out of scope](#out-of-scope)).

## Purpose

A **composition** merges one or more nets into a single larger net by aliasing them and adding **wires** between their **ports**. Ports are the composition interface; wires are the cross-net arcs. Per [ADR 0004], composition is *merging net schemas and adding wires*, and the composed system is a single Petri net, verifiable as one.

This document defines the composition semantics and structural validation rules. The canonical machine-readable shape is [`spec/composition.schema.json`](./composition.schema.json). Core net semantics — `Place`, `Transition`, `Arc`, `Token`, `Marking`, and the `Port` facet — live in [`spec/net-schema.md`](./net-schema.md), with their machine-readable shape in [`spec/net.schema.json`](./net.schema.json). Ports are restated briefly below for orientation.

## Canonical JSON Schema

The authoritative machine-readable composition schema is
[`spec/composition.schema.json`](./composition.schema.json), a
[JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12/schema)
document. Its `$id` identifier is
`https://awesome-petri.dev/schemas/composition.json`.

The Python validator loads a byte-identical packaged copy from
`velocitron/schemas/composition.schema.json` through `importlib.resources`; it
never reaches outside the installed wheel at runtime. Focused schema-resource
tests validate the canonical document itself and fail on any
canonical/package byte drift. The complete schema lives only in the standalone
artifact rather than being duplicated in this prose.

`parse_composition(source, *, net_loader=None, origin=None)` resolves the
retained literal `ref`. A filesystem source supplies its parent as `origin`.
For a mapping or raw text source, a relative reference is rejected unless the
caller supplies an explicit `origin: Path` or a `net_loader` closure; absolute
filesystem paths need no origin. The optional
`net_loader(ref: str) -> Mapping | Path | str` returns source material only,
and the parser still validates every result by calling `parse_net`.

The default loader accepts source-relative filesystem `.json` net documents.
The DSL loader accepts `.json` or `.petrinet`: it compiles the latter to a
JSON-shaped dictionary before `parse_composition` calls `parse_net`. Load
cycles, missing files, unsupported extensions, originless relative refs, and
loader failures are deterministic reference-loading diagnostics. A loader
never returns a prevalidated `Net` or bypasses net validation.


The parser loads each `nets[].ref` and validates it against [`spec/net.schema.json`](./net.schema.json) before applying the composition-level validation rules below.

## Ports (recap)

A **port** is a place that declares a `port` facet: `port: {direction: "input" | "output", type: "<token type>"}`. Ports are **boundary places** — the composition interface of a net. Non-port places are **internal**. The semantic definition lives in [`spec/net-schema.md`](./net-schema.md), and the machine-readable shape lives in [`spec/net.schema.json`](./net.schema.json); this document relies on both and does not redefine `Place`.

- `direction: "output"` — the net may emit tokens of `type` through this port.
- `direction: "input"` — the net may receive tokens of `type` through this port.

A port's `type` must be one of the place's `accepts` token types (enforced by the net schema/parser).

## Wires

A **wire** is a unidirectional cross-net arc joining an **output** port to an **input** port. It is a special form of an `Arc` — formed by joining two nets rather than within one ([ADR 0004]; see `Wire` in [`CONTEXT.md`](../CONTEXT.md)).

- **Direction:** `from` must be an output port; `to` must be an input port. Tokens flow source → sink.
- **Why unidirectional.** Bidirectional coupling is modeled as two separate unidirectional wires, keeping the topology explicit and statically checkable. A wire is a single directed edge, never a pair ([ADR 0004]).
- **Type compatibility.** The output port's `type` and the input port's `type` must be equal ([ADR 0004]).
- **Structural scope.** A wire declares a structural link between two compatible ports. The merge engine realizes that link by place fusion, so the merged net uses ordinary token and firing semantics. Transport or subscription concerns such as backpressure, buffering, overflow, retry, and dynamic discovery are not defined by this contract ([ADR 0004]).

## Aliasing

Each referenced net gets an **alias** — the local name under which the net's places and transitions are known inside the composition.

- **Derivation.** `alias` is optional in `nets[]`. If omitted, the alias defaults to the referenced net's `name` field.
- **Qualified naming.** In the composed net, every place and transition is referred to as `<alias>.<name>` (e.g. `prod.out`, `cons.in`). This namespaces each net's symbols so identically-named places or transitions in different nets do not collide.
- **Why dotted.** The dotted form mirrors the JSON wire shape `{net, port}` where `net` is the alias and `port` the local name — `<alias>.<portName>` is the same pair, serialized as one string. It is conventional in JSON/URI contexts and avoids the heavier `::` separator. Aliases are restricted to simple identifiers (schema `pattern`) so the `.` is an unambiguous delimiter.

## Composition = merge + wires

Merging net schemas under aliases and adding wires yields **one larger net**. The composed net is itself a valid net, verifiable as a single Petri net ([ADR 0004]).

Conceptually, the merge:

1. Qualifies every place, transition, and arc endpoint in each referenced net by its alias (`<alias>.<name>`), producing a disjoint union of namespaced nets.
2. For each wire, **identifies** (fuses) the named output port and input port into a single shared place in the composed net — the port place through which tokens flow from the source net's output to the sink net's input. A wire is thus the declarative link that, when merged, becomes structure.
3. Re-exposes any **unwired** ports of the constituent nets as the composition's own boundary ports (under their `<alias>.<portName>` names). This is what makes a composition itself composable — a chip ([`CONTEXT.md`](../CONTEXT.md)) whose boundary is its set of unwired ports.

The merge engine — producing the combined net (resolving aliases, fusing port-places, rewriting arc endpoints, re-exposing unwired ports) — is now implemented in `implementations/python/src/velocitron/composition.py` (`merge_nets` / `merge_composition`); the composed net is verifiable as a single Petri net regardless of realization ([ADR 0004]).

Because the merge **rewrites place names** (alias qualification plus port fusion), handlers must not key on literal place names — they read inputs by token type and produce against resolved destinations. That contract is defined in ["Composition-safe handlers" in `spec/handler-contract.md`](./handler-contract.md#composition-safe-handlers).

The chosen `[impl]` realization is **place fusion**. For each wire, the output port-place and input port-place fuse into a single shared place — named by the sorted `__`-concatenation of the equivalence class's output (source) port qualified names — through which tokens flow from the source net's output to the sink net's input. Unwired ports re-expose as the composition's own boundary ports (under their `<alias>.<portName>` names). The merged net is structurally valid and runnable by `Engine`. A **routing-transition** realization — inserting a synthetic transition mediating each wire — was rejected: it introduces a transition not present in the constituents and adds firing overhead per token transfer, for no structural benefit (the composed net is verifiable as a single net either way).

### Fused-place annotations

Every fused place the merge creates carries documentation-only annotations ([ADR 0011](../docs/adr/0011-documentation-fields.md)) built from its equivalence class:

- **Fusion tag.** The merge tags the fused place `annotations: {"fusion": true}` — a fused place is exactly the hub-and-spoke shape the fusion-place rendering convention exists for ([`CONTEXT.md`](../CONTEXT.md) "Fusion place"), so tooling (e.g. `velocitron-viz`) styles it without any manual tagging.
- **Member carry-through.** The member port-places' own `annotations` carry through onto the fused place. Conflicting keys resolve deterministically: **output (source) ports merge before input ports** — the fused place is named after its sources, and their annotations take the same precedence — each group in **sorted qualified-name order**, and the earliest member wins each conflicting key. The order is a property of the equivalence class, not of wire ordering — the same rule that makes the fused place's *name* deterministic.
- **Tag precedence.** The `fusion` key is set last and always wins, overriding any member-supplied `fusion` value.

Annotations remain documentation-only ([ADR 0011](../docs/adr/0011-documentation-fields.md)): the tag and carried keys never influence enablement, firing, or token flow on the merged net.

## Validation rules

These are the structural checks the JSON Schema cannot express; the Python parser implements them (see `velocitron.parser.parse_composition`). Each raises a `NetValidationError`.

1. **No dangling ports.** Every wire endpoint `(net, port)` must resolve to a declared port on the referenced net. Concretely: `net` must equal some `nets[].alias` (or a derived default), the referenced net must be loadable and valid, and `port` must be the name of a place on that net that declares a `port` facet.

2. **Direction compatibility.** The wire's `from` port must declare `direction: "output"`; the `to` port must declare `direction: "input"`. A wire from an input port or to an output port is rejected.

3. **Type compatibility.** The `from` (output) port's `type` and the `to` (input) port's `type` must be **equal** ([ADR 0004]). Tokens flowing across a wire keep their color.

4. **Alias uniqueness.** No two `nets[]` entries may resolve to the same alias — whether supplied explicitly or derived from `name`. Duplicate aliases are rejected.

5. **Net validity.** Each `nets[].ref` must parse and validate against [`spec/net.schema.json`](./net.schema.json). A composition is only as valid as its constituents.

### Out of scope

The **runtime merge engine** produces the combined net by resolving aliases, fusing port-places, rewriting arc endpoints, and re-exposing unwired ports. It is implemented in the Python reference implementation (`implementations/python/src/velocitron/composition.py`) via `merge_nets` and `merge_composition`. The resulting net uses the ordinary firing semantics. This composition contract does not define a separate dynamic transport or subscription layer, including backpressure, buffering, overflow, retry, or dynamic discovery ([ADR 0004]).

## Worked example

Adapted from `TestComposition.test_parse_composition` in [`implementations/python/tests/test_parser.py`](../implementations/python/tests/test_parser.py). A **producer** net emits `task` tokens at its `out` output port; a **consumer** net absorbs `task` tokens at its `in` input port. One wire joins them.

### `producer.json`

```json
{
  "name": "producer",
  "places": [
    {"name": "work", "accepts": ["task"]},
    {"name": "out", "accepts": ["task"], "port": {"direction": "output", "type": "task"}}
  ],
  "transitions": [{"name": "produce", "handler": "produce_handler"}],
  "arcs": [
    {"from": {"place": "work"}, "to": {"transition": "produce"}, "consume": {"type": "task"}},
    {"from": {"transition": "produce"}, "to": {"place": "out"}, "produce": {"type": "task", "destination": "out"}}
  ]
}
```

### `consumer.json`

```json
{
  "name": "consumer",
  "places": [
    {"name": "in", "accepts": ["task"], "port": {"direction": "input", "type": "task"}},
    {"name": "done", "accepts": ["task"]}
  ],
  "transitions": [{"name": "consume", "handler": "consume_handler"}],
  "arcs": [
    {"from": {"place": "in"}, "to": {"transition": "consume"}, "consume": {"type": "task"}},
    {"from": {"transition": "consume"}, "to": {"place": "done"}, "produce": {"type": "task", "destination": "done"}}
  ]
}
```

### `composition.json`

```json
{
  "nets": [
    {"ref": "producer.json", "alias": "prod"},
    {"ref": "consumer.json", "alias": "cons"}
  ],
  "wires": [
    {"from": {"net": "prod", "port": "out"}, "to": {"net": "cons", "port": "in"}}
  ]
}
```

### The merge

Validation passes: `prod.out` is an output port of type `task`; `cons.in` is an input port of type `task`; aliases `prod` and `cons` are unique. The merge (conceptually) produces one net:

- Places: `prod.work`, `cons.done`, and the **fused** port place `prod.out` ≡ `cons.in` (a single shared place holding `task` tokens).
- Transitions: `prod.produce`, `cons.consume`.
- Arcs: `prod.work → prod.produce` (consume `task`), `prod.produce → <fused port>` (produce `task`), `<fused port> → cons.consume` (consume `task`), `cons.consume → cons.done` (produce `task`).

A `task` token deposited by `prod.produce` lands in the fused port place, where `cons.consume` can take it — one connected net, verifiable as a single Petri net ([ADR 0004]).

## References

- [`spec/net.schema.json`](./net.schema.json) and [`spec/net-schema.md`](./net-schema.md) — canonical machine-readable shape and semantic definition of `Net`, `Place`, `Transition`, `Arc`, `Token`, `Marking`, and the `Port` facet. This document restates `Port` only for orientation.
- [ADR 0004 — Structural ports and unidirectional wires](../docs/adr/0004-structural-ports-unidirectional-wires.md) — the composition decision: structural ports only, unidirectional wires, dynamic transport/subscription semantics outside the composition contract, and the composed system verifiable as one net.