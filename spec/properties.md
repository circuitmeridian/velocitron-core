# Properties

The canonical definition of the **declarative property pass** — the verification vocabulary a consumer declares against a net and the checker contract that validates those properties against a single marking or along a journal replay. Its scope is the marking- and replay-level checks defined here (ADR 0019).

Like `spec/handler-contract.md` and `spec/firing-semantics.md`, this document is normative prose plus illustrative type definitions; the property pass is a code module, not a serialized document. The Python shapes below match `implementations/python/src/velocitron/properties.py` exactly. The one net-document surface — the `capacityPerColorKey` place field — is defined in `spec/net-schema.md`; this document defines its checking semantics.

## Posture: non-behavioral, engine-independent, never a gate

- **Properties never gate firing.** The engine does not read `capacityPerColorKey` or any property declaration; enablement, binding, firing, and deposit are unaffected. A property violation is a *finding*, reported by the checker — never an engine error.
- **The pass is engine-independent.** It consumes a `Net`, a `Marking`, and journal record streams. It needs no handler registry, so properties whose truth depends on enablement (guard results) are out of scope (see below).
- **Checkers report; they do not raise on violations.** A raise (`ValueError`) is reserved for programmer errors: a replay-only property handed to the single-marking checker, or a record stream inconsistent with the net/marking (corruption, not a property violation).

## Scope boundary (what this is NOT)

The pass checks markings the system **actually produced** — one marking handed to it, or the markings a journal replay reconstructs. It is **not** state-space model checking. The current property-pass scope excludes:

- **Reachability-graph exploration** ("from every reachable marking…", "`published` is reachable…").
- **Fairness-conditional liveness** ("eventually fires under a fair policy").
- **Enablement enumeration** (properties over `enabled_transitions`, which need guards and hence the registry).
- **Static structural analysis** ("the only in-arcs to X are from Y" — an arc-graph check, not a marking check).

## The vocabulary

Six property kinds, declared as frozen dataclasses. *Stepwise* kinds carry a `scope`: `"always"` (checked at every intermediate marking of a replay) or `"quiescence"` (checked only at the replay's final marking). *Replay-only* kinds are meaningful only over a record stream.

```python
@dataclass(frozen=True)
class AtMostN:
    # Stepwise (always). <= max tokens in `place`, optionally per distinct
    # value of the token-data key(s). Tokens missing a key field group under
    # a shared absent-marker (they count against a bound; they cannot evade
    # it). The keyed form is declarable in the net document as the
    # `capacityPerColorKey` place field; capacity_properties(net) extracts
    # those declarations as AtMostN instances.
    place: str
    max: int
    key: str | tuple[str, ...] | None = None

@dataclass(frozen=True)
class PlaceEmpty:
    # Stepwise (default: quiescence — the stuck-token witness). No token in
    # `place`; with `cel`, no token whose data matches the single-token CEL
    # predicate (arc-predicate semantics: an eval error means "does not
    # match", mirroring D6).
    place: str
    cel: str | None = None
    scope: Literal["always", "quiescence"] = "quiescence"

@dataclass(frozen=True)
class EventuallyReaches:
    # Replay-only. Every key value that ever ENTERS `source` (initial
    # marking, firing deposit, or injection) is present in >= 1 of `targets`
    # at the end of the replay. The key-correlated conservation walk.
    source: str
    targets: tuple[str, ...]
    key: str | tuple[str, ...]

@dataclass(frozen=True)
class MarkingInvariant:
    # Stepwise (default: always). A CEL predicate over per-place token
    # counts, evaluated against {"count": {<place>: int}} with EVERY declared
    # place present (empty = 0). Anything other than a `true` result —
    # including an eval error — is a violation: an invariant that cannot be
    # evaluated does not hold. (Deliberately stricter than D6, which keeps
    # FIRING robust; a verification pass must not silently degrade.)
    cel: str
    scope: Literal["always", "quiescence"] = "always"

@dataclass(frozen=True)
class KeyCorrelation:
    # Stepwise (default: always). Every token in `place` has a same-key
    # token in `witness_place` in the same marking.
    place: str
    witness_place: str
    key: str | tuple[str, ...]
    scope: Literal["always", "quiescence"] = "always"

@dataclass(frozen=True)
class FiringBinding:
    # Replay-only; checks each `completed` FiringRecord of `transition`
    # directly. Exactly one of:
    #   key — all bound input tokens share one key value (per-key
    #         non-interference);
    #   cel — every bound token's data satisfies a single-token CEL
    #         predicate.
    transition: str
    key: str | tuple[str, ...] | None = None
    cel: str | None = None
```

**Keys.** A key is one token-data field name or a tuple of field names (a composite key, e.g. `("account_id", "crawl_tag")`). A token's key value is the tuple of its `data` values for those fields; a missing field contributes a shared absent-marker so partially-keyed tokens still group and count. Because token-data values may be unhashable (nested objects), key values are *grouped by their `repr`* — equality-safe for JSON data, and the same rendering the violation message carries.

**CEL.** Token predicates (`PlaceEmpty.cel`, `FiringBinding.cel`) evaluate against a single token's `data` with arc-predicate semantics (D6: eval error ⇒ does not match / does not satisfy — for `FiringBinding` an eval error therefore IS a violation, since the bound token failed to satisfy the predicate). `MarkingInvariant.cel` evaluates against the counts environment and treats eval errors as violations, as stated above. Expressions are compiled once per checker call via the configured CEL adapter (`velocitron.cel`).

## The checker contract

```python
def capacity_properties(net: Net) -> list[AtMostN]:
    """The net's declared capacityPerColorKey bounds, as AtMostN properties."""

def check_marking(net: Net, marking: Marking | Mapping[str, Sequence[Token]],
                  properties: Iterable[Property] = (), *,
                  cel_adapter: CelAdapter | None = None) -> PropertyReport:
    """Check one marking: the net's capacity declarations plus every given
    stepwise property (scope is irrelevant for a single marking). A
    replay-only property here raises ValueError."""

def check_replay(net: Net, initial_marking: Marking | Mapping[...],
                 records: Iterable[FiringRecord | InjectionRecord | Mapping],
                 properties: Iterable[Property] = (), *,
                 cel_adapter: CelAdapter | None = None) -> PropertyReport:
    """Reconstruct every intermediate marking along `records`, checking
    scope="always" stepwise properties (and capacity declarations) at the
    initial marking and after every record, scope="quiescence" ones at the
    final marking, and replay-only properties over the stream."""
```

Reports:

```python
@dataclass(frozen=True)
class PropertyViolation:
    kind: str            # "at-most-n" | "place-empty" | "eventually-reaches"
                         # | "marking-invariant" | "key-correlation"
                         # | "firing-binding"
    message: str         # human-readable, carries the offending key value
    place: str | None    # the offending place, when the kind has one
    step: int | None     # 0-based record index whose post-state (or record)
                         # violates; None for the initial marking, a
                         # single-marking check, or an end-of-replay check

@dataclass(frozen=True)
class PropertyReport:
    violations: tuple[PropertyViolation, ...]
    # .ok — True iff no violations
```

Violations are reported in a deterministic order (walk order: step; within a step, stepwise properties in declaration order — capacity declarations first — then `FiringBinding` findings for that record's firing; end-of-replay checks last), so a property report is replay-stable.

## Replay reconstruction (no engine, no handlers)

`check_replay` reconstructs intermediate markings from the record stream alone:

- **`completed` `FiringRecord`** — the consumed multiset is recovered by splitting each place's `inputTokens` back into per-arc slices: D1 guarantees each binding arc (consume- and read-mode, in net arc-declaration order) contributed exactly `weight` tokens, concatenated per place in that order. Only the consume-mode slices are removed (read tokens stay in the marking, ADR 0012); `outputTokens` are then appended per place. Token removal is equality-based with multiplicities, per the net-schema multiset rule.
- **`failed` `FiringRecord`** (including deposit-violation records) — the marking is unchanged (atomic rollback, firing-semantics (b)/(c)).
- **`InjectionRecord`** — `kind: "inject"` appends its tokens to the place; `kind: "update"` replaces the place's contents (ADR 0013).

Records may be the engine-emitted `TypedDict`s or plain JSON mappings (e.g. loaded from a `JsonlJournal` `.jsonl` file); token dicts `{"type", "data"}` are coerced. Extra fields (`sequence`) are ignored. A stream that cannot be split against the net's arcs, or whose tokens are absent from the reconstructed marking at removal time, raises `ValueError` — the stream does not belong to this net/marking.

This makes D1's concatenation order and ADR 0012's read/consume split load-bearing for a second consumer beyond the engine; a change to either must update this reconstruction.

## Cross-document pointers

- `spec/net-schema.md` — the `capacityPerColorKey` place field (the one net-document property surface) and the equality-based multiset rule the reconstruction reuses.
- `spec/firing-semantics.md` — D1 (binding shape/order), D5 (the journal as the deterministic record this pass replays), (b)/(c) (atomic rollback for `failed` records), (f)/ADR 0013 (injection records).
- `docs/adr/0019` — the property-pass decision: vocabulary, declaration surface, coverage mapping to representative example properties, and the out-of-scope boundary.
- `docs/adr/0011` — the non-behavioral-field posture `capacityPerColorKey` extends.
