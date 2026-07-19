# Opt-in failure budget in run(); policy input carries consecutive failures

A transition whose handler keeps returning `failed` stays enabled forever: atomic
rollback leaves its input tokens in place, so `run` burns one step per failed fire with
the marking unchanged (**spin**), and under the default `first-found` policy it starves
every transition declared after it (**starvation**). guinan F9 is the motivating
scenario: the speaks-Chip's LLM `judge_deliver` fails persistently (sim S8), eating every
`run` step; under `first-found` (sim S10) the 2-min-deadline `timeout_fire`, declared
after it, **never fires** — the hard ceiling livelocks, and guinan had to reach for the
priority policy (ADR 0014) just to escape. There was no net-visible way to say "retry
this deliver N times, then move on."

Two composable, opt-in mechanisms land, both selection-level (enablement and `fire`
stay untouched, preserving ADR 0005's verification story exactly as ADR 0014 did):

- **The failure budget** — `Engine(registry, max_consecutive_failures=N)`. Within one
  `run`, the engine counts each transition's consecutive `failed` firings (every `failed`
  record `fire` returns to the loop counts: handler-`failed`, resolve-miss, not-enabled,
  and a deposit violation under `record_then_drop`; a raising guard degrades to
  not-enabled at *enablement* and is never selected, so it never counts). At `N` the
  transition is **exhausted**: excluded from the enabled list handed to the firing
  policy, so selection moves past it under *any* policy — `first-found` included. Any
  `completed` firing resets **every** count (the marking changed; an exhausted
  transition's inputs may now differ, so it earns a fresh budget). When all enabled
  transitions are exhausted the run stops — quiescence-by-exhaustion — instead of burning
  the remaining steps. Counts are scoped to a single `run` call; `N < 1` raises
  `ValueError` at construction (like a bad `deposit_violation`). Default `None` = no
  budget, byte-for-byte the prior behavior — conservative, per the house style (ADR
  0009's opt-in validation, ADR 0014's opt-in policy).
- **Failure-aware policy input** — `FiringPolicyInput` gains `consecutiveFailures:
  dict[str, int]`, keyed by exactly the `enabledTransitions` entries (no failure history
  = 0), threaded by the engine to **every** policy whether or not a budget is configured
  — mirroring how ADR 0014 threaded `priorities`. Custom policies can skip, deprioritize,
  or deterministically back off (attempt-based) without engine access; both built-ins
  ignore it.

Replayability is untouched: the counts are a pure function of the firing sequence (never
wall-clock, honoring the handler-contract timestamps rule), failed fires still advance
the step counter (so `attempt` semantics and firingId determinism are unchanged), and
exhaustion is **not** separately journaled — it is derivable from the recorded sequence
(N consecutive `failed` records for a transition, then its absence until a `completed`
record), and a fourth journal channel would grow the three-method `Journal` protocol for
information the stream already carries (the contract-growth cost ADR 0013 weighed when it
added `record_injection`).

With this, guinan's S8 becomes: `Engine(registry, max_consecutive_failures=3)` — the
failing deliver retries 3 times, exhausts, and `timeout_fire` fires even under
`first-found` with no priority declaration; the declaration-order workaround stops being
load-bearing. The budget and the priority policy compose: priority decides *who wins
among the healthy*, the budget decides *when a failing transition stops competing*.

**Considered options:**

- Keep spinning; require consumers to bound damage with `max_steps` and priorities (the
  guinan workaround). Rejected: `max_steps` is a global fuse, not a per-transition
  budget — a failing transition still eats the whole allowance, and the priority policy
  only reorders the starvation (a failing high-priority transition starves everything).
- Make `run` not burn steps on failed firings (a separate failure counter vs step
  counter). Rejected: `attempt` is the step counter and firingIds derive from it —
  re-using an attempt after a failure would collide firingIds and break replay
  determinism; and without a budget the loop would then spin *forever* rather than to
  `max_steps`, strictly worse.
- Wall-clock backoff (retry after T seconds). Rejected outright: firing decisions must
  never branch on wall-clock (the handler-contract timestamps rule); backoff expressible
  here must be step/attempt-based, which `consecutiveFailures` enables in a custom
  policy.
- A per-transition `retryBudget` field in the net schema. Rejected for now: a retry
  budget is runtime tuning, not coordination structure (ADR 0001 — the net routes and
  gates); it would touch the parser's embedded schema and every renderer for a knob the
  engine-level budget plus a `consecutiveFailures`-aware custom policy already covers.
  If a real net needs per-transition budgets structurally, a schema field can be added
  then (the ADR 0005 "implemented when needed" posture).
- A built-in failure-aware policy (e.g. `first-found-with-backoff`) instead of an
  engine-level budget. Rejected as the primary mechanism: starvation is a property of
  the *loop*, and a policy-based fix would have to be reimplemented per policy
  (`first-found`, `priority`, every custom one); the engine-level exclusion works under
  any policy. The threading half of this ADR still gives custom policies the raw
  material.
- Exhaustion as a journaled event (a fourth hook channel). Rejected: derivable from the
  recorded firing sequence; the `Journal` protocol growth (every implementation must add
  the method) buys no replay fidelity — injections needed a channel (ADR 0013) because
  they are *not* derivable from firings; exhaustion is.
- Engine-level budget + input threading (chosen): one constructor knob, one added input
  key, default unchanged, primitives untouched.
