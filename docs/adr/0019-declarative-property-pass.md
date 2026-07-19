# The declarative property pass: marking- and replay-level verification, never firing semantics

velocitron gains a **declarative property pass** — a small vocabulary of verification properties plus a checker that validates them against a single `Marking` and along the intermediate markings of a **journal replay**. Its current scope is deliberately limited to markings the system *actually produced* (or one marking handed to it), never markings the net *could* reach. It is explicitly **not** state-space model checking: reachability-graph exploration, fairness-conditional liveness, and temporal-logic checking are outside this decision.

The vocabulary was distilled from recurring checks in representative example nets and prototype property lists: capacity per color key, stuck-token detection at quiescence, key correlation between places, and marking invariants over journal scans.

## The property vocabulary (six kinds)

Declared in Python (`velocitron.properties`) as frozen dataclasses; checked by `check_marking(net, marking, properties)` and `check_replay(net, initial_marking, records, properties)`. Kinds marked *stepwise* carry a `scope` of `"always"` (checked at every intermediate marking of a replay) or `"quiescence"` (checked only at the final marking); `check_marking` applies stepwise kinds to its single marking regardless of scope.

1. **`AtMostN(place, max, key=None)`** — the place holds at most `max` tokens, optionally per distinct value of the named token-data key(s) (`key` is a field name or tuple of field names; tokens missing a key field group under a shared absent-marker, so unkeyed tokens still count against a bound rather than evading it). Stepwise, always. The keyed form is also declarable in the net document as the `capacityPerColorKey` place field (below).
2. **`PlaceEmpty(place, cel=None, scope="quiescence")`** — the place holds no token (optionally: no token whose `data` matches a single-token CEL predicate, with arc-predicate semantics — an eval error means the token does not match, mirroring D6). The `quiescence` default is the stuck-token liveness witness: run to quiescence, assert nothing is stranded.
3. **`EventuallyReaches(source, targets, key)`** — replay-only. Every key value that ever *enters* `source` (present in the initial marking, deposited by a firing, or injected) is present in at least one of `targets` at the end of the replay. The key-correlated token-conservation walk ("every token entering A ends in B or C").
4. **`MarkingInvariant(cel, scope="always")`** — a CEL predicate over per-place token counts, evaluated against `{"count": {<place>: int}}` with every declared place present (empty = 0). Anything other than a `true` result — including an eval error — is a violation: an invariant that cannot be evaluated does not hold. This deliberately differs from the D6 predicate-false rule, which exists to keep *firing* robust; a verification pass must not silently degrade.
5. **`KeyCorrelation(place, witness_place, key, scope="always")`** — every token in `place` has a same-key token in `witness_place` in the same marking (the "every verified child traces to a same-key verified parent" invariant).
6. **`FiringBinding(transition, key=None, cel=None)`** — replay-only; checks each `completed` firing record of `transition` directly (no marking needed). `key` asserts all bound input tokens share one key value (per-key non-interference); `cel` asserts every bound token's `data` satisfies a single-token CEL predicate (e.g. the publish-gate completeness check `produced_count == configured_count`). Exactly one of the two must be given.

Checkers return a `PropertyReport` carrying `PropertyViolation`s (kind, message, offending place, and — when caught mid-replay — the 0-based record index). **The pass never raises on a violation and never gates firing**; a raise is reserved for programmer errors (a replay-only property handed to `check_marking`, a record stream that does not match the net).

## Declaration surface: one schema field, the rest Python

**`capacityPerColorKey` is promoted to a first-class optional place field** — `{"key": <string|array>, "max": <int ≥ 1>}` — rather than a convention inside the ADR 0011 `annotations` object. Rationale: `annotations` is deliberately unconstrained freeform for *consumer* metadata, and a spec-defined shape hidden inside it would either go schema-unvalidated (a typo'd key silently checks nothing — the worst failure mode for a verification feature) or force constraining the object ADR 0011 deliberately left open. Capacity-per-key is a spec-level concept consumed by the property pass and appeared consistently in the representative source material, so it earns schema admission with full validation. It stays **non-behavioral** in exactly the ADR 0011 sense: the engine never reads it; it does not gate enablement, binding, firing, or deposit; only the property pass consumes it. ADR 0011's carve-out (documentation fields as the sole non-behavioral exception to ADR 0001) is hereby widened to a category: *non-behavioral metadata fields that never influence firing*, of which `description`/`annotations` (documentation) and `capacityPerColorKey` (verification) are the members.

**The richer property kinds are declared in Python only** for this scope. The vocabulary is new and may change if broader verification forms such as reachability introduce quantifiers; freezing a JSON surface now invites churn in the net schema, which is the project's most stability-sensitive contract. `capacityPerColorKey` is the exception because it is place-local, structurally simple, stable across the representative inputs, and meaningless to separate from the place it bounds. Net-level JSON property declarations remain outside the current property-pass contract.

## Marking-level vs replay-level checking

`check_marking` validates one marking — the projection/tooling case (a live system's projected marking, a quiescent `run` result). `check_replay` walks a journal record stream and reconstructs every intermediate marking, so stepwise properties are checked at each step and a capacity violation that appears transiently mid-run is caught even if the final marking is clean.

Reconstruction needs no engine re-run and no handlers: for a `completed` `FiringRecord`, the consumed multiset is recovered by splitting each place's `inputTokens` back into per-arc slices — D1 guarantees per-arc `weight` tokens concatenated in arc-declaration order across the transition's consume- and read-mode arcs — and removing only the consume-mode slices (read tokens stay, ADR 0012); `outputTokens` are then deposited. `failed` records leave the marking unchanged (atomic rollback); `InjectionRecord`s apply their inject/append or update/replace (ADR 0013). Token removal is equality-based with multiplicities, per the net-schema multiset rule. A record stream that does not match the net or marking (unsplittable binding, token absent at removal) raises `ValueError` — that is corruption, not a property violation.

## Coverage: mapping the source corpus to the vocabulary

Study inputs included a seven-property timed-alarm prototype and property sets from six representative nets: alarm-chip (AC), received-file-monitor (RF), bounded-channel-backpressure (BC), dagster-chain-projection-overlay (DG), feed-iteration-shell (FS), and publish-gate-triplet (PG).

| Source property | Kind(s) | Coverage |
|---|---|---|
| AC-P1 fire-once (`\|fired\| ≤ 1`) | AtMostN (unkeyed) | full |
| AC-P2 no two live alarms | AtMostN over replay | full (witness: a second fire without a clear surfaces as `\|fired\| = 2` in the post-firing marking) |
| AC-P3 no-alarm-lost | MarkingInvariant (quiescence) | partial (the breach-with-no-alarm witness at quiescence; "eventually fires" is fairness liveness — out of scope) |
| AC-P4 clear liveness | PlaceEmpty (quiescence) | partial (quiescence witness; enablement probing is out of scope) |
| AC-P5 boundedness | AtMostN | full |
| AC-P6 isolation (two instances) | AtMostN keyed / KeyCorrelation | partial (per-key bounds; the compositional non-interference claim is out of scope) |
| AC-P7 priority precedence | — | out of scope (firing-policy semantics; the engine's own tests cover it) |
| RF-1 no silent loss | EventuallyReaches + PlaceEmpty (quiescence) | full (witness form) |
| RF-2 ≤ 1 flag per account | capacityPerColorKey (the eponymous case) | full for the flag bound; the per-epoch firing-count form is out of scope |
| RF-3 restart-window loss visible | EventuallyReaches | partial (loss lands in a terminal target; the crash-cycle count equality is net-specific arithmetic) |
| RF-4 boundedness / P-invariants | MarkingInvariant + AtMostN | full |
| RF-5 refresh mutex | MarkingInvariant | partial (token-count form; interleaving isolation is trivial under the sequential engine) |
| BC-1 channel bound + P-invariant | AtMostN + MarkingInvariant | full |
| BC-2 no SFTP entry lost | EventuallyReaches | full |
| BC-3 FTP never dropped | PlaceEmpty (cel predicate, always) | full |
| BC-4 FTP liveness / dead marking | PlaceEmpty (quiescence) | partial (the dead-marking witness at quiescence; reachability form out of scope) |
| BC-5 drop timeliness | PlaceEmpty (quiescence) | partial (stuck-token witness; timed liveness out of scope) |
| DG-1 origin → loaded-or-failed | EventuallyReaches + PlaceEmpty (quiescence) | full (witness form) |
| DG-2 per-key boundedness | capacityPerColorKey (composite key) | full |
| DG-3 correlation invariant | KeyCorrelation | full |
| DG-4 per-key non-interference | FiringBinding (key) | full |
| FS-1 prerequisite safety | — | out of scope (enablement enumeration needs guards/registry; the pass is engine-independent) |
| FS-2 human-gate no-bypass | — | out of scope (static arc-graph analysis — a future structural pass) |
| FS-3 human-gate liveness | PlaceEmpty (quiescence) | partial (quiescence witness) |
| FS-4 barrier termination | — | out of scope (fairness-conditional liveness) |
| FS-5 single in-flight | AtMostN + capacityPerColorKey (`key: "kind"`) | full |
| FS-6 projection read-only | — | out of scope (static structural check) |
| PG-P1/P2 blocking/fatal gate | MarkingInvariant | partial (co-presence witness; the per-firing pre-marking form is a natural future kind — the replay walker already has every pre-marking) |
| PG-P3 completeness | FiringBinding (cel) | full |
| PG-P4 tolerance | — | out of scope (reachability: "can reach") |
| PG-P5 no-orphan-confirm | MarkingInvariant | full |
| PG-P6 fan-out conservation | MarkingInvariant | partial (the phase-scoped sum; unconditional invariants are full) |
| PG-P7 verdict immutability | MarkingInvariant | partial (count form; token identity is out of scope) |
| PG-P8 OR-split soundness | EventuallyReaches + AtMostN | partial ("reaches some outcome exactly once" ≥-1 half; transition-level mutual exclusion out of scope) |

Every "out of scope" row falls in one of four classes named above: state-space reachability, fairness-conditional liveness, enablement enumeration (needs the registry), or static structural analysis. Those are the honest boundary of a marking/replay pass.

**Considered options:**

- Check properties inside the engine (a hook per fire). Rejected: couples verification to firing, violates the non-behavioral posture, and cannot check a marking the engine did not produce (projections, hand-built markings).
- Reconstruct replay markings by re-running the engine with recorded handlers. Rejected: requires the registry and handler determinism; the journal + net already determine every intermediate marking via the D1 split.
- Put `capacityPerColorKey` under `annotations`. Rejected above (unvalidated or contradicts ADR 0011's unconstrained escape hatch).
- A full net-level JSON property vocabulary now. Rejected above as a premature schema freeze; broader reachability-oriented forms are outside the current property-pass scope.

**Consequences:**

- The engine, registry, and journal are untouched; the pass is a pure consumer of `Net` + `Marking` + record streams. Properties can never gate firing.
- The net schema gains one optional, non-behavioral place field; `additionalProperties: false` stays everywhere.
- The D1 concatenation order and ADR 0012 read/consume split are now load-bearing for a second consumer (replay reconstruction), not just the engine — a change there must update `check_replay`.
- The representative properties have a mechanical home; the four out-of-scope classes remain explicitly outside the current pass.
