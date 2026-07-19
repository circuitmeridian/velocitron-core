# Built-in priority firing policy; policy input carries priorities

The `Transition.priority` field — reserved since ADR 0005, present in the schema and
ignored by the engine — is implemented as a **built-in, opt-in firing policy**. A second
reserved registry name, `PRIORITY_FIRING_POLICY` (`"priority"`), is registered on every
fresh `HandlerRegistry` alongside `first-found`: the highest-priority enabled transition
fires; ties (and the all-default case) fall back to the first maximal entry in
`enabledTransitions` (declaration) order, so the policy is deterministic/replayable and
degrades to `first-found` when no transition declares a priority. The default policy is
unchanged (`first-found`); priorities take effect only via
`Engine(registry, policy="priority")`.

To feed it, `FiringPolicyInput` gains a third key: `priorities: dict[str, int]`, keyed by
exactly the `enabledTransitions` entries, an absent declaration mapping to `0`. The engine
threads it to **every** policy each step, so custom priority-aware policies need no access
to the net (previously a consumer had to close over a name→priority map built from the
loaded net — the guinan workaround that motivated this).

ADR 0005 rejected priority for v1 because "no scenario in the first prototype demands it —
reserved in spec, implemented when needed." The demanding scenario arrived: guinan's
Guinan-speaks Chip races a 2-min deadline (`timeout_fire`) against its LLM pipeline, and
under `first-found` the only way to make the deadline win was declaration order — implicit,
invisible in every rendered artifact, silently reordered by composition merge (alias order
prefixes the merged transition list), and livelock-prone: a persistently failing transition
declared earlier starves the timeout forever (guinan F9/F10). An explicit
`"priority": 10` on the one preempting transition, decided by a policy that reads it, is
declaration-order-independent and visible in the net document and in velocitron-viz
(which already renders the field).

**Considered options:**

- Keep declaration order as the only conflict-priority mechanism. Rejected: implicit,
  invisible outside the JSON's line ordering, and rewritten by composition merge; guinan
  had to declare its preempting transition *first* and document why — the ordering was
  load-bearing and looked decorative.
- A guinan-side custom policy closing over the net's priorities (the working prototype).
  Rejected as the end state: every consumer re-implements the same policy, and
  `FiringPolicyInput` not carrying priorities forces each one to smuggle net access into
  a handler that is spec'd to need none.
- Make `priority` change enablement or bind into `fire()` semantics. Rejected: priority is
  a *selection* concern; enablement and firing stay priority-blind, so the model-checker's
  "all firings consistent with any policy" verification story (ADR 0005) is untouched.
- Built-in opt-in policy + input threading (chosen): one reserved name, one added input
  key, default unchanged. ADR 0005's rejection rationale (a scalar can't express all
  ordering needs) still stands — conditional orderings like "completion beats deadline
  unless the completion path is stuck" remain a matter of net structure (e.g. predicated
  inhibit arcs) or custom policies; the scalar covers the static-preemption case that
  actually arrived.
