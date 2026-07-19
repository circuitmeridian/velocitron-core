# Read arcs (`mode: "read"`) are the test-without-consume primitive

A consume pattern may declare a third `mode`, `"read"`, alongside `"consume"`
and `"inhibit"`. A read arc gates enablement on the **presence** of at least
`weight` matching tokens in its source place — the same type + predicate
matching a consume arc performs — and the matched tokens **contribute to the
binding** (the guard, the handler's `inputTokens`, and the firing record all
see them), but they are **not removed** when the transition fires. It is the
presence-side dual of `inhibit`'s absence-test.

## Motivation

The Phase-B modeling study found that 6 of 6 modeled nets needed to *test a
token without consuming it*: a shared clock/tick, a config or account token, a
"still running" flag, a policy record — read by many transitions and re-checked
on every fire. Without a first-class read arc, the only net-pure encoding is
**consume-and-reproduce**: consume the token on one arc and immediately deposit
an identical copy on an output arc. That fallback is structurally poor — it
doubles the arcs, writes a spurious consume+produce pair into the firing journal
on every check, serializes transitions that are logically concurrent (they all
contend for the one token they only meant to read), and is non-atomic under any
future concurrency (a reader can observe the brief window where the token is
gone). A read arc removes all four problems: the token is never taken, so any
number of transitions may read it concurrently and the journal records only the
real state change.

## Semantics decisions

- **`weight` applies to read.** A read arc of weight `N` requires `N` matching
  tokens present; all `N` contribute to the binding and none is removed. This
  mirrors the consume-arc reading of `weight` (the count of matching tokens the
  arc engages) rather than inventing a read-specific meaning, and keeps the
  `weight: 1` default the classical single-token case. `weight` stays rejected
  on inhibit arcs only.

- **The binding the guard/handler/record see includes read tokens.** Read
  tokens are part of the binding — the point of a read arc is that the
  transition's behavior can *depend* on the read token (a guard comparing a
  consumed item against a read config; a handler that needs the clock value).
  The firing record's `inputTokens` therefore include read tokens keyed by
  source place, so replay stays deterministic: the recorded binding is exactly
  what the handler saw.

- **Read/consume disjointness on a shared place falls out of the existing
  sub-multiset rule.** When a read arc and a consume arc on the *same* place
  both match, they must bind **disjoint** tokens — one token may not be both
  read and consumed in a single firing. No new check was needed: the engine
  already validates that the combined bound multiset (now including read
  tokens) is a sub-multiset of the place, so a single token cannot be claimed by
  two arcs. With one token in the place, a transition that both reads and
  consumes it is *not enabled*; with two, exactly one is consumed and the other
  is read (and survives).

- **Read + inhibit on one place is unsatisfiable; on distinct places they
  compose.** A read arc requires presence and an inhibit arc requires absence of
  the same type in the same place, so the two can never hold at once (present ⇒
  inhibit fails; absent ⇒ read fails). On distinct places the modes compose
  normally — read one place (require present, keep it), inhibit another (require
  empty).

## Considered options

- **A `read` mode on the consume pattern (chosen).** Reuses the entire
  consume-arc matching surface (type, predicate, weight, direction) and adds
  only a firing-time branch: read-bound tokens are excluded from the removal
  set. Minimal schema surface (one enum value), no new arc kind.

- **`weight: 0` as an implicit read.** Rejected: overloads `weight` with a mode
  meaning, conflicts with the `weight ≥ 1` invariant, and reads poorly (a
  "zero-weight" arc that nonetheless requires a token present and binds it is a
  contradiction in terms). The modeling-study nets that sketched `weight: 0`
  meant exactly "read"; naming the mode is clearer than a magic weight.

- **A separate top-level arc kind (a `read` arc distinct from consume/produce).**
  Rejected: read arcs share almost everything with consume arcs (place →
  transition direction, type/predicate/weight matching); a new kind would
  duplicate that surface and split the arc-centric representation the schema
  deliberately keeps uniform.

- **Leave it to consume-and-reproduce.** Rejected — this is the status quo the
  motivation rejects.

## Consequences

- The engine distinguishes a binding's **full token set** (consume + read,
  visible to guard/handler/record) from its **consumed subset** (consume-only,
  removed on fire): `_select_binding` returns a `_Binding(tokens, consumed)`
  pair. With no read arcs the two are identical, so every pre-existing net's
  firing behavior is byte-for-byte unchanged.

- Enablement and binding enumerate "binding arcs" = consume + read, in net
  declaration order. Declaration order across the two modes is load-bearing for
  deterministic selection when a read and a consume arc share a place.

- A read arc removes nothing, so it never triggers atomic rollback and never
  appears in the consumed side of the journal — only in `inputTokens`. Replay
  determinism is preserved because the read tokens that shaped the firing are
  recorded.

- Net purity (ADR 0001) is unaffected: a read arc is still a pure structural
  gate + binding contribution, not an inscription that computes or transforms.
