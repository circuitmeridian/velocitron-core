# Firing policy handler with nondeterministic default

A pluggable firing policy handler decides which enabled transition(s) to fire. The default is nondeterministic first-found (deterministic iteration order, replayable). Custom policies can be registered without changing the core.

**Considered options:**
- Pure nondeterministic (any enabled transition may fire, runtime picks randomly). Rejected: random selection makes bugs non-reproducible. The model-checker explores all firings, but the runtime should be deterministic for replay.
- Priority-based (transitions declare priority levels). Rejected for v1: priority is a scalar that can't express all ordering needs, and no scenario in the first prototype demands it. Reserved in spec, implemented when needed.
- Opaque policy handler (chosen): the extension point exists from day one. Default = first-found (deterministic, replayable). Verification: under the default policy, the model-checker explores all possible firings (maximally verifiable). Under a custom policy, the policy is opaque — verification falls back to "all firings consistent with any policy" (conservative, still sound).