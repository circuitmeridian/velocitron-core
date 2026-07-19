---
name: velocitron
description: Use when creating, modifying, or discussing colored Petri net design — places, transitions, arcs, arc inscriptions (predicates, guards, CEL), token data/colors, weights, read/inhibit arcs, timers, composition, or marking — and when authoring `.petrinet` DSL, using the `velocitron` / `velocitron-viz` CLIs, or driving the Python `Engine` or `Runtime`. Read before designing a net, writing net structure, or writing engine/runtime code.
---

# velocitron

`velocitron` is a typed, durable colored-Petri-net library for agentic process coordination: a language-agnostic JSON net schema, a flat authoring language (`.petrinet`), and a Python reference implementation (parser/validator, a synchronous firing `Engine`, and an asynchronous `Runtime`). A net is pure coordination structure — places (conditions/buffers that hold typed tokens), transitions (events with consume/produce arcs, an optional behavior binding, and an optional guard), and a marking (the token distribution). A handlerless transition is valid structure; no same-name or no-op behavior is implied. The exact ordinary color `token` is the Generic token convention for classical/uncolored nets: it is not a wildcard or untyped core value, although visualization omits its generic label. Design nets so every decision is visible in the structure — a choice is two transitions sharing an input place with mutually exclusive arc predicates, never one transition with two outputs (that is an AND-split that deposits to both).

Before changing net structure or terminology, also consult the repo's `CONTEXT.md` (ubiquitous language), `spec/` (the authoritative contracts), and `docs/adr/` (architectural decisions). This skill's bundled docs summarize and ground those for quick authoring.

## CLI presence check

The two console scripts are `velocitron` (validate / convert / explain / install this skill) and `velocitron-viz` (render a net as Graphviz DOT). The full, current help surface for both — every subcommand — is injected here at skill load:

!`bash "$CLAUDE_SKILL_DIR/scripts/cli-help.sh" 2>/dev/null || bash skills/velocitron/scripts/cli-help.sh 2>/dev/null || bash scripts/cli-help.sh 2>/dev/null || echo "(cli-help.sh not found — run: bash skills/velocitron/scripts/cli-help.sh)"`

The inline `!`…`` block above is a Claude Code dynamic-context feature that runs the helper at load. If the help text is absent (you are not in Claude Code, or the block did not execute), run `bash skills/velocitron/scripts/cli-help.sh` yourself to get the same output.

If the output shows `velocitron CLI NOT INSTALLED` (or `velocitron-viz CLI NOT INSTALLED`), the CLIs are not on PATH. Offer the user these two install options and wait for their choice — do not pick one yourself:

1. Install from GitHub: `uv tool install "git+https://github.com/circuitmeridian/velocitron-core#subdirectory=implementations/python"`
2. Install from a local clone the user names: `uv tool install <path-to-clone>/implementations/python` (add `--editable` to track edits).

## Authoring workflow

1. Sketch the net structure first (places, transitions, arcs) — see `structure-library.md` for the idiom that matches the problem.
2. Write the `.petrinet` DSL — `petrinet-language.md` is the language reference; `kitchen-sink.petrinet` is a validating example of every construct.
3. Validate early and often: `velocitron validate <file>.petrinet`. Convert to canonical JSON with `velocitron to-json`, back with `velocitron to-petrinet`, and get a prose walkthrough with `velocitron explain`.
4. Visualize a standalone Net: `velocitron to-json <file>.petrinet | velocitron-viz /dev/stdin`
   renders its authored initial marking by default; use `--marking queued` for an
   authored named marking, or `--marking-json '{"request_in":[{"type":"request","data":{"id":"r-17"}}]}'`
   for one validated inline marking. Use `--no-marking` to omit token detail.
   Marking selectors do not apply to composition, `--merged`, `--ports-only`, or
   `--legend`; use `--doc --fence` for a Markdown-embeddable diagram.
   Use `--direction=lr` to override Graphviz layout (`tb`, `lr`, `bt`, or `rl`,
   case-insensitive). Nets and compositions default to `tb`; `--rankdir`
   remains a backward-compatible alias.
   For the ordinary Generic color `token`, visualization suppresses only redundant type labels (accepted-color rows, generic port type, generic marking type, consume/produce arc type); counts/data, weights, read/inhibit glyphs, predicates, correlations, literal data, tooltips, and non-generic colors remain visible.
5. Execute: register every behavior binding you intend to run and drive the `Engine`; a disabled handlerless transition remains `NotEnabled`, while firing an enabled handlerless transition fails atomically with `HandlerNotFound`. `Runtime` is for timers/long-running coordination and rejects handlerless nets at construction because every transition requires a `HandlerSpec`. See `engine-runtime-primer.md` and `engine-vs-runtime.md`.

## Bundled resources

Read these as the task calls for them:

- **`petrinet-language.md`** — the `.petrinet` DSL contract: optional net headers, standalone declarations, arbitrary-length topology chains (`-Color->`, read `->?`, inhibit `->0`, Generic-token bare `->`), arc facts (predicate/weight/data/data cel/correlate), optional transition behavior bindings plus guard/priority/timer/maturity facts, places (ports, accepts, capacity), template markings plus count-only Generic-empty marking runs, composition, and metadata. Handler absence remains absence in core JSON and canonical DSL. Read before writing or changing any `.petrinet` source. (Authoritative source: `spec/petrinet-language.md` in the repo; this bundled copy is byte-identical for installed skills.)
- **`kitchen-sink.petrinet`** — one validating net whose three disconnected regions (order fulfilment, CI pipeline, document-review SLA) together exercise every DSL construct, heavily commented. Read/copy it when you need a working example of a specific construct.
- **`structure-library.md`** — a catalogue of reusable net idioms (sequence, choice/conflict, fork-join, mutex/semaphore, bounded producer-consumer, retry-with-limit, timeout, watchdog, saga/compensation, state-machine, composition via ports/wires), each with when-to-use and a minimal `.petrinet` snippet. Read when choosing how to model a coordination problem.
- **`engine-runtime-primer.md`** — practical primer on the Python API: building a `HandlerRegistry`, parsing/loading a net, `enabled_transitions` / `fire` / `run`, the firing journal, token injection, timed transitions via `tick`, and the async `Runtime` (lanes, token sources, event-driven timer scheduling). Every sample is grounded in the current API. Read before writing engine/runtime code.
- **`engine-vs-runtime.md`** — when to use the synchronous `Engine` alone (embedding, custom loops, tests, deterministic replay) versus the async `Runtime` (native timers, long-running coordination, concurrent handlers, token sources). Read when deciding which to reach for.
- **`scripts/cli-help.sh`** — the helper that dumps both CLIs' full help surface (used by the presence check above). Run it manually if the injected help text is missing.

## Installing/updating this skill elsewhere

`velocitron skill <pathname>` installs this skill into `<pathname>/velocitron/` (a fresh install, or an update detected by an existing `<pathname>/velocitron/SKILL.md`). `<pathname>` must already exist. Updates are a true sync driven by a `.velocitron-skill-manifest.json` the command writes: shipped files are overwritten, files a previous version managed but the skill no longer ships are removed, and files you added yourself are left untouched. The skill is read entirely from data packaged in the installed `velocitron` wheel (the repo's `skills/velocitron/` is the authoring source of truth, symlinked into the package).
