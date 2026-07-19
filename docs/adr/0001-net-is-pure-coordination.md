# Net is pure coordination — no executable inscriptions, ever

The net never computes; it routes and gates. All computation — data transformation, decision logic, classification, LLM calls, human decisions — lives in handlers. Arc inscriptions are predicates (boolean filters), not transformations. This is a permanent exclusion, not a v1 deferral.

**Considered options:**
- Full CPN executable inscriptions (ML/SML expressions that transform token data). Rejected: language-coupled, hard to verify, blurs the net/handler separation.
- Predicates + transformations (expressions can produce new token data). Rejected: compromises net purity; trivial transformations should be handler concerns.
- Predicates only (chosen). The net only filters and routes; all data transformation lives in handlers. Maximally verifiable (decidable predicates), maximally portable (no embedded code), maximally agent-friendly (JSON patterns, not embedded expressions).