"""Handler registry — binds names to callables, per kind.

The registry is the resolved-handler source for the firing engine
(``velocitron.engine``): ``Engine`` resolves transition/guard/predicate/
firing-policy refs through the per-kind ``register_*``/``resolve_*`` API,
with a default ``first-found`` firing policy registered on every fresh
registry.

Per-kind namespaces: resolution is dispatched by kind, so a transition ref
and a guard ref may share a name with no collision — inherent in the
per-kind ``register_*``/``resolve_*`` API (there is no cross-kind
``register(kind, …)`` form).

References: spec/handler-contract.md; ADR 0005.
"""

from __future__ import annotations

from typing import TypeVar

from .contract import (
    FiringPolicyHandler,
    FiringPolicyInput,
    GuardHandler,
    PredicateHandler,
    TransitionHandler,
)

# TypeVar binding a resolve table's value type to _resolve's return type.
# References: spec/handler-contract.md "Handler registry API".
_T = TypeVar("_T")

# Reserved name under which the default firing policy is registered, so it is
# resolvable out of the box on every fresh registry.
# References: ADR 0005.
DEFAULT_FIRING_POLICY = "first-found"

# Reserved name under which the built-in priority policy is registered —
# likewise resolvable out of the box on every fresh registry, but opt-in
# (never the default; configure via ``Engine(registry, policy="priority")``).
# References: ADR 0014.
PRIORITY_FIRING_POLICY = "priority"


class HandlerNotFound(Exception):
    """Raised when a handler ref cannot be resolved in the registry.

    Three surfaces, one typed error:

    1. A **net-referenced** handler (transition/guard/predicate) resolved
       within ``fire``/``enablement``: the engine surfaces this as a
       **transition failure, not a crash** — the transition fails (a
       ``failed`` record / not-enabled / predicate-false) rather than
       aborting the runtime. This graceful-degradation rule is **retained
       on both the direct primitive path and the ``run`` path** — ``run``
       does not auto-validate.

    2. The **firing-policy** ref (engine config, not net-referenced):
       validated at ``Engine.__init__`` as a **configuration error** —
       there is no transition context to fail within, so the
       misconfiguration surfaces at construction, not out of ``run``.

    3. The **sandwich rule**: ``Engine.validate(net)`` is a **public,
       opt-in** instance method that walks every net-declared handler ref
       and raises ``HandlerNotFound`` on the first unresolvable ref,
       **before any ``run``**; it is the only seam where
       ``HandlerNotFound`` propagates uncaught.

    Carries the unresolved ``name`` as :attr:`name`.

    References: spec/handler-contract.md; spec/firing-semantics.md (a), (b), (e).
    """

    name: str

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _first_found(inp: FiringPolicyInput) -> str | None:
    """The default firing policy: the first enabled transition, or ``None``.

    Deterministic iteration order (list order) for replay.

    References: ADR 0005.
    """
    enabled = inp["enabledTransitions"]
    return enabled[0] if enabled else None


def _priority(inp: FiringPolicyInput) -> str | None:
    """The built-in priority policy: the highest-priority enabled transition.

    ``priorities`` carries each enabled transition's declared
    ``Transition.priority`` (absent declaration = 0). Higher wins; ties fall
    back to the first maximal entry in ``enabledTransitions`` (declaration
    order) via ``max``'s first-maximal stability — deterministic and
    replayable, degrading to first-found when no transition declares a
    priority.

    References: ADR 0014.
    """
    enabled = inp["enabledTransitions"]
    if not enabled:
        return None
    priorities = inp["priorities"]
    return max(enabled, key=lambda name: priorities.get(name, 0))


class HandlerRegistry:
    """Per-kind handler namespaces with register/resolve.

    Constructing a registry registers the two built-in firing policies:
    the default ``first-found`` under :data:`DEFAULT_FIRING_POLICY` and the
    opt-in ``priority`` under :data:`PRIORITY_FIRING_POLICY`, so both are
    resolvable without any explicit registration.
    """

    def __init__(self) -> None:
        self._transitions: dict[str, TransitionHandler] = {}
        self._guards: dict[str, GuardHandler] = {}
        self._predicates: dict[str, PredicateHandler] = {}
        self._policies: dict[str, FiringPolicyHandler] = {}
        self.register_firing_policy(DEFAULT_FIRING_POLICY, _first_found)
        self.register_firing_policy(PRIORITY_FIRING_POLICY, _priority)

    @staticmethod
    def _resolve(table: dict[str, _T], name: str) -> _T:
        """Look up ``name`` in ``table``, raising ``HandlerNotFound`` on miss."""
        try:
            return table[name]
        except KeyError as exc:
            raise HandlerNotFound(name) from exc

    # ── transition ───────────────────────────────────────────────────
    def register_transition(self, name: str, handler: TransitionHandler) -> None:
        self._transitions[name] = handler

    def resolve_transition(self, name: str) -> TransitionHandler:
        return self._resolve(self._transitions, name)

    # ── guard ────────────────────────────────────────────────────────
    def register_guard(self, name: str, handler: GuardHandler) -> None:
        self._guards[name] = handler

    def resolve_guard(self, name: str) -> GuardHandler:
        return self._resolve(self._guards, name)

    # ── predicate ─────────────────────────────────────────────────────
    def register_predicate(self, name: str, handler: PredicateHandler) -> None:
        self._predicates[name] = handler

    def resolve_predicate(self, name: str) -> PredicateHandler:
        return self._resolve(self._predicates, name)

    # ── firing policy ────────────────────────────────────────────────
    def register_firing_policy(self, name: str, handler: FiringPolicyHandler) -> None:
        self._policies[name] = handler

    def resolve_firing_policy(self, name: str) -> FiringPolicyHandler:
        return self._resolve(self._policies, name)
