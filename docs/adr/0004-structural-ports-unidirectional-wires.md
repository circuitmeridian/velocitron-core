# Structural ports and unidirectional wires for composition

Ports are named boundary places marked `input` or `output` with declared token types. Wires are unidirectional cross-net arcs (output port → input port). Composition = merging net schemas and adding wires; the composed system is a single larger Petri net, verifiable as one.

**Considered options:**
- Ports as typed channels with backpressure (CSP/π-calculus style). Rejected: much more complex, blurs net structure and runtime behavior, harder to verify, and outside the current contract.
- Ports as subscription endpoints (pub/sub). Rejected: decoupled but less explicit wiring, harder to visualize topology, verification of dynamic subscription graph is much harder.
- Structural ports only, behavioral/subscription excluded (chosen). Minimal composition that's statically checkable (type compatibility, no dangling ports) and verifiable as a single net. Behavioral wire semantics (backpressure, buffering, overflow) and dynamic discovery are connection-level properties outside the current contract.
- Bidirectional wires. Rejected: bidirectional coupling should be modeled as two separate unidirectional wires, keeping the topology explicit.