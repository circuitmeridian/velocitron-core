# Architecture decision records

These records preserve Velocitron's design rationale and history. Their status
and conclusions may evolve during the public alpha. An ADR is not an API,
security, support, maintenance, migration, or compatibility promise; the
current normative contracts live in `spec/`.

## Index

- [0001 — Net is pure coordination — no executable inscriptions, ever](0001-net-is-pure-coordination.md)
- [0002 — CEL for arc predicates, named handlers for guards](0002-cel-predicates-named-guards.md)
- [0003 — Handler contract: input tokens + context, side effects as places](0003-handler-contract-input-and-side-effects.md)
- [0004 — Structural ports and unidirectional wires for composition](0004-structural-ports-unidirectional-wires.md)
- [0005 — Firing policy handler with nondeterministic default](0005-firing-policy-nondeterministic-default.md)
- [0009 — Opt-in pre-run handler validation seam (`Engine.validate(net)`)](0009-opt-in-handler-validation-seam.md)
- [0010 — CEL adapter protocol for swappable evaluation backends](0010-cel-adapter-protocol.md)
- [0011 — Documentation fields are the permitted non-behavioral exception to net purity](0011-documentation-fields.md)
- [0012 — Read arcs (`mode: "read"`) are the test-without-consume primitive](0012-read-arcs-test-without-consume.md)
- [0013 — A token-injection seam, not a timer system, carries clock/deadline time](0013-clock-timer-injection-seam.md)
- [0014 — Built-in priority firing policy; policy input carries priorities](0014-priority-firing-policy.md)
- [0015 — Opt-in failure budget in `run()`; policy input carries consecutive failures](0015-failure-budget-and-failure-aware-selection.md)
- [0016 — Lint is an opt-in advisory surface; suppression rides the annotations carve-out](0016-lint-surface-annotations-suppression.md)
- [0017 — Binding-correlated inhibit arcs (`correlate`) are the anti-join primitive](0017-correlated-inhibit-arcs-anti-join.md)
- [0018 — Native timed transitions: a declarative timer over token-carried deadlines](0018-native-timed-transitions.md)
- [0019 — The declarative property pass: marking- and replay-level verification, never firing semantics](0019-declarative-property-pass.md)
- [0020 — The `.petrinet` frontend lowers portable contributions into canonical JSON](0020-petrinet-dsl-contribution-pipeline.md)
- [0022 — A durable sqlite event store realizes tape-replay for restart, not determinism verification](0022-durable-sqlite-event-store-and-tape-replay.md)
- [0023 — Computed produce fallback: `cel` on produce templates](0023-computed-produce-fallback-cel.md)

Historical numbering gaps are retained rather than filled by reconstructed records.
