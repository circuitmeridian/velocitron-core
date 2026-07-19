# Projection Adapter

The canonical, prose definition of the **projection adapter** — the
consumer-owned component that derives a `Marking` from external resource state
and hands it to the engine. velocitron deliberately has **no engine-side
projection primitive**: deriving a marking from the filesystem, a database, an
orchestrator's run directories, a queue, an API, or a clock is the consumer's
concern, not the engine's. This document defines the common protocol shape so
consumer implementations can share the same deterministic boundary rather than
rediscovering it.

Unlike `net-schema.md`, `composition.md`, and `handler-contract.md`, this spec
defines **no machine-checkable surface** — a projection adapter is entirely
consumer code and carries no JSON schema or handler signature the engine
enforces. It is **normative prose**: the protocol shape and its three rules are
requirements on any conforming adapter, but the engine cannot check them. The
seam the engine *does* own — `Engine.inject_token` — is defined in
`firing-semantics.md` (f); this document says how a projection feeds it and the
initial marking.

## Where a projection adapter sits in the layering

A net is **pure coordination** (ADR 0001): it routes and gates over a
`Marking`, but it does not build the marking, and it never reads the outside
world. The firing engine consumes a marking the consumer supplies — through
`run(net, marking)` for a batch trajectory, or through the `inject_token` seam
(`firing-semantics.md` (f)) for incremental arrivals. **Something has to turn
external resource state into that marking.** That something is the projection
adapter, and it runs **before and outside** the engine:

```
external resource state          projection adapter            engine
(files, DB rows, run dirs,  ──►  enumerate → probe →  ──►  run(net, marking)
 queue depth, clock, ...)        deposit (Marking)          or inject_token(...)
```

The worked examples below use a filesystem-backed pipeline as a concrete case
study of this consumer-owned boundary. The file names, stage names, and code
fragments (for example, `raw_products.parquet` and `prior.stages.update`) are
illustrative, not normative fixtures of this spec; the normative content is the
protocol shape and the three rules. The engine stays pure because the impurity
— touching disk, querying a database, polling a queue — is confined to the
adapter, ahead of the first firing.

## What a projection adapter is (and is not)

A projection adapter **is** a pure function of the evidence it has probed:
given the same probed evidence, it produces a byte-identical marking. It **is
not** a source of nondeterminism inside the net — the net's firing is a pure
function of the marking the adapter already built (`firing-semantics.md`, D5;
ADR 0001). The one impure act is **probing**: reading the filesystem, the
database, the API. Probing is the adapter's single impure edge, and it happens
once, up front, so that everything downstream — enablement, firing, the journal
— replays deterministically.

A projection adapter **is not** a transition handler. A transition handler runs
*inside* `fire`, sees only its input binding, and is required to be a pure
function of its input tokens for guards/predicates or an observable side effect
for transition handlers (ADR 0003). An adapter runs *outside* `run`, sees the
whole external world, and builds the marking the handlers will later route.
Keeping the two separate is what lets the engine claim replay: the disk-touching
code never executes inside a firing.

A projection adapter **is not** an engine feature. There is no `Engine.project`,
no projection handler kind, no schema field. The engine's contract with a
projection is only the `Marking` it receives and the `inject_token` seam it
exposes; how the marking was derived is entirely the consumer's business.

## The protocol shape: enumerate → probe → deposit

Every projection adapter, whatever its resource, follows the same three-step
shape:

1. **Enumerate correlation keys.** Walk the resource and derive the set of
   *correlation keys* — the values that identify one logical unit of work and
   that become the token color (or a field of it). In the Dagster spike the key
   is a `(account_id, crawl_tag)` tuple, enumerated by walking each root's
   `<root>/a_<account_id>/<crawl_tag>/` tree in sorted order; a run directory
   whose tag fails the expected `YYYYMMDD_HHMMSS` shape is **recorded but not
   projected** (a malformed key would be meaningless), kept as evidence rather
   than silently dropped.

2. **Probe evidence per key.** For each key, ask the resource *which
   observations hold* — one boolean probe per stage/condition of interest. The
   spike probes five pipeline stages (`raw`, `extracted`, `generated`,
   `uploaded`, `loaded`) with pure filesystem checks (does
   `raw_products.parquet` exist? does `s3_upload_manifest.json` parse and carry
   a non-empty `uploads[]`?). A stage the environment cannot reach (the spike's
   `loaded`, a Snowflake row-count unreachable read-only) is left unprobed
   rather than guessed — an honestly-reported gap, not a fabricated
   observation.

3. **Deposit observation tokens.** For each key, deposit one token per holding
   observation into the place that represents that observation. The spike
   deposits one `stage_token` per present stage into the matching
   `<stage>_observed` place. The token's `data` carries the correlation key plus
   an `evidence_ref` (a pointer back to what was probed) and a `provenance` tag;
   fields that would vary run-to-run without changing meaning (a wall-clock
   `observed_at`) are **nulled** so re-projecting unchanged evidence yields a
   byte-identical marking.

The deposit target is either the **initial marking** (batch projection: build
the whole `Marking` at once and pass it to `run(net, marking)`) or the
**injection seam** (incremental projection: deposit each token through
`Engine.inject_token(net, marking, place, token, attempt=...)` as it is
observed — see the next section).

## Normative rules

Three rules are load-bearing. Each is a place a naive adapter goes wrong, and
each is a requirement on any conforming projection.

### Do not de-duplicate on the color key

**An adapter must not collapse two observations that share a color key into
one.** De-duplication is the wrong default for any projection whose net checks a
*boundedness* property (at most one token per color key per place), because the
duplicate *is* the signal the property exists to catch — not noise to collapse.

The spike's collision scenario: two distinct pipeline runs for one account are
submitted within the same wall-clock second, so the orchestrator's
`strftime('%Y%m%d_%H%M%S')` stamps both with the *identical* `crawl_tag`. That
is a genuine key collision — two real runs sharing one color. If the adapter
de-duped on `(account_id, crawl_tag)` it would deposit one `raw_observed` token
where there should be two, and the collision would become invisible to the net.
By depositing **both**, the adapter lets the net's boundedness walk flag the
duplicate and route it. Duplicates are flagged and deposited so the net can act
on them; they are never silently merged away.

### Union mirror mounts

**An adapter must union same-key sightings that are the same logical resource
viewed twice, not count them as duplicates.** This is the mirror image of the
previous rule, and telling the two apart is the adapter's central judgement:
*"the same key seen twice" is a collision only if the two sightings are
independent units of work.*

The spike probes two read-only VM mirror mounts of the same `tmp` tree. Because
the mirror sync is partial, the *same* `(account_id, crawl_tag)` appears under
both roots at differing completeness — one root may show `raw + extracted`
while the other shows `raw + extracted + generated`. That is one run mirrored,
not two runs colliding. Counting it twice would manufacture a *false*
boundedness violation. The adapter unions instead: it keys a `merged` map by the
correlation key, seeds it from the first root, and folds later roots in by
**unioning their observations to the most complete view** (`prior.stages.update`
in the spike). Genuine same-second collisions (previous rule) are preserved;
mirror sightings are unioned. An adapter that gets this backwards either hides
real collisions or invents fake ones.

### Flag duplicates

**When a genuine duplicate survives (a real collision, per the first rule), the
adapter deposits it so the net can route it — it does not swallow the anomaly.**
Flagging is the positive form of "don't de-dup": the adapter's job on a
collision is to make the duplicate *visible to the net*, depositing both tokens
into the observation place so a boundedness transition, an alerting transition,
or a human-attention queue can consume the anomaly. A malformed or otherwise
un-projectable key is likewise **recorded as evidence** rather than dropped, so
the anomaly is inspectable after the fact.

## Relationship to the environment-arrival injection seam

A projection and the injection seam (`firing-semantics.md` (f), ADR 0013) are
the two ways external observations enter a net, and they compose:

- **A full projection builds the initial marking.** The batch path enumerates
  and probes the *entire* resource once and constructs the whole `Marking`,
  handed to `run(net, marking)`. This is the "snapshot the world, then run"
  mode.
- **Incremental arrivals go through `inject_token`.** When observations arrive
  over time (a new run directory appears, a queue message lands, the clock
  advances), the consumer deposits each new token through
  `Engine.inject_token(net, marking, place, token, *, attempt, replace=False)`.
  Every injection is journaled through the `record_injection` hook
  (`firing-semantics.md` (d), (f)), sharing one sequence stream with firings, so
  a live projection that interleaves injections and firings replays
  deterministically across injected time. The seam validates the token `type`
  against the place's `accepts`, so a projection cannot smuggle an ill-typed
  observation past the net's structure.

The two are the same protocol at different cadences: an incremental projection
runs *enumerate → probe → deposit* per poll and routes each deposit through
`inject_token`; a batch projection runs it once and routes every deposit into
the initial marking. `inject_token` is per-token, so a key with N observations
is N threaded injection calls in the incremental mode (a per-poll adapter
threads the returned marking and a monotonic `attempt` through each call).

## Determinism and replay

A projection is the boundary that keeps the net's replay claim honest, and it
does so by pushing all impurity to a single up-front edge:

- **Probing is the only impure act.** Enumeration order is made deterministic
  (sorted directory walks, sorted merged keys), and volatile fields are nulled,
  so that **re-projecting unchanged evidence yields a byte-identical marking**.
  A byte-identical initial marking plus the deterministic first-found firing
  policy (`firing-semantics.md` (e); ADR 0005) yields a byte-identical firing
  journal — replay holds end to end.
- **The net is a pure function of the probed evidence.** Once the marking is
  built, no transition handler, guard, or predicate touches the resource again
  (ADR 0003); firing consumes only tokens. This is why the disk-touching code
  must live in the adapter, before `run`, and never inside a firing — the split
  is what lets the engine guarantee same net + same marking + same handler
  results = same sequence.
- **Projection failures fail loudly, before any firing.** An adapter that
  cannot probe (a missing mount, an unparseable manifest) fails at projection
  time, ahead of the first firing, rather than corrupting the marking the engine
  will run.

## The filesystem is one flavor

The Dagster spike projects a filesystem, but the filesystem is only the flavor
this case study happened to use. The protocol — *enumerate correlation keys →
probe evidence → deposit observation tokens; don't de-dup on the color key,
union mirror sources, flag duplicates* — is resource-agnostic. The same shape
projects:

- **a database** — enumerate keys from a `SELECT DISTINCT`, probe each row's
  columns, deposit a token per satisfied condition;
- **a queue** — enumerate messages, probe headers/attributes, deposit an
  arrival token per message (the "union mirror mounts" rule becomes "union the
  same message seen on two consumers");
- **an API / orchestrator** — enumerate runs from a list endpoint, probe each
  run's status, deposit a stage token per completed step;
- **a clock** — enumerate the single tick key, probe the current time, deposit
  (or `replace`) one clock token (`firing-semantics.md` (f)'s clock-advance
  pattern is a degenerate one-key projection);
- **an in-memory fixture** — enumerate the fixture's keys, "probe" the
  dictionary, deposit the tokens; a test's initial marking *is* a trivial
  projection.

"Mirror mounts" generalizes to "the same logical resource observed through two
channels," and "the color key" generalizes to whatever tuple identifies one
logical unit of work in the resource. An adapter for any of these is conforming
if it follows the three-step shape and the three rules above.

## Cross-document pointers

- `spec/firing-semantics.md` (f) — the clock/timer token-injection seam
  (`Engine.inject_token` + the `record_injection` journal hook, ADR 0013): the
  sanctioned write primitive an incremental projection uses to deposit each
  arrival, journaled so replay holds across injected time.
- `spec/firing-semantics.md` (d), (e) — the decoupled firing journal and the
  first-found firing policy: the deterministic machinery a byte-identical
  projected marking feeds into to make replay hold end to end.
- `docs/adr/0001` — net purity: the principle a projection adapter protects by
  confining all impurity to the pre-run probing edge.
- `docs/adr/0003` — the handler contract: the pure-function-of-input-tokens rule
  that a transition handler obeys and a projection adapter (which runs outside
  firing) is exempt from — the split that keeps the disk-touching code out of
  the firing loop.
- `CONTEXT.md` — the **Projection adapter** glossary entry (and **Marking**,
  **Color**, **Token**): the ubiquitous-language terms this document uses.
