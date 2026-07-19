# A durable sqlite event store realizes tape-replay for restart, not determinism verification

**Status:** Accepted.

## Context

velocitron deliberately has no persistence. The engine emits `FiringRecord` /
`InjectionRecord` through the `Journal` hook, and `JsonlJournal` writes a
timestamped file, but nothing reconstructs a live marking from a stored log.
`spec/firing-semantics.md` D5 defines **replay** as re-run-and-compare (run the
engine again with identical handlers and assert journal equality
record-for-record) and defers **tape-replay** — advancing the net from recorded
outputs without re-invoking handlers — as a deferred test convenience rather
than part of the firing contract.

Consumers that drive long-lived nets may need durability with a different
shape: the state is the event log, replayed once into a live marking at startup
and not rebuilt while the process runs. That is tape-replay used as an
opt-in durability mechanism rather than only as a test aid.

## Decision

`velocitron.durable_sqlite` adds three things over the existing `Journal`
contract, all as an ordinary consumer of it — no engine, schema, or spec change:

- **A sqlite event store.** One append-only `events(instance, seq, kind,
  payload, recorded_at)` table, WAL mode, single writer per instance, many
  concurrent readers. `open_database` / `read_events` / `known_instances` are
  the store surface; `StoredEvent` is one decoded row. The instance name is
  caller-supplied and namespaces one net's log within a shared database — no
  domain vocabulary is baked in.
- **`DurableJournal`.** Implements the `Journal` protocol and commits each
  firing / injection / deposit-violation as it is appended, in the same critical
  section that advances the marking. A `net_revision` event kind stores the net
  definition so a log is self-describing.
- **`replay_events(net, events)`.** The handler-free tape-replay: fold the
  recorded token effects into a marking — remove the recorded consume-mode
  inputs, deposit the recorded outputs, apply injections. Firing records that
  did not complete and `deposit_violation` / `net_revision` events carry no
  marking effect.

Read-arc tokens are the one subtlety: a `FiringRecord`'s `inputTokens` includes
read-bound tokens (they bind but are not removed, ADR 0012), so `replay_events`
consults the net's arcs and removes only consume-mode inputs.

This is **not** D5's replay-determinism contract. Tape-replay reconstructs a
marking from recorded outputs; it never re-runs handlers and asserts nothing
about determinism or external-effect safety. The crash window — an external
effect performed, its event row not yet committed — remains the caller's
concern. Resume may invoke a handler again, so execution is at least once;
idempotency, deduplication, or adoption of a pre-existing effect must be
provided by the caller when required. The event store does not guarantee
exactly-once effects.

## Consequences

- The engine, journal protocol, and net schema are untouched; `durable_sqlite`
  is a pure consumer. Persistence stays opt-in and out of the core.
- `spec/firing-semantics.md` D5's deferred tape-replay now has one realization,
  scoped to durable restart. D5's own re-run-and-compare replay is unaffected.
- `properties._ReplayWalker` already reconstructs intermediate markings from a
  record stream for the property pass (ADR 0019), splitting each place's
  `inputTokens` back into per-arc slices via the D1 concatenation order.
  `replay_events` is a second, coarser reconstruction (whole-place consume-mode
  removal) with a narrower job: the final marking for restart, not every
  intermediate marking for checking. They now share the read/consume split as a
  load-bearing invariant; a future unification onto one reconstruction core is
  possible but deferred — the two differ in return shape and in per-arc-slice
  precision, and collapsing them is its own change, not a ride-along on this
  relocation.
