"""Tests for the handler registry contract (spec/handler-contract.md).

Verifies the handler-registry surface defined by ``spec/handler-contract.md``:

* Four handler kinds with distinct I/O shapes:
  - Transition handler: receives ``{transitionId, inputTokens, firingContext}``,
    returns ``{status, outputTokens, error, metadata}``.
  - Guard handler: receives the same input shape, returns ``bool`` (may be impure).
  - Predicate handler: receives ``{token, firingContext}``, returns ``bool``
    (pure, single token).
  - Firing policy handler: receives ``{marking, enabledTransitions,
    priorities, consecutiveFailures}``, returns ``str | None`` (one
    transition id, or ``None`` to stop).
* ``FiringContext`` is a closed four-field shape: ``firingId``, ``attempt``,
  ``netId``, ``timestamps: {fired_at}``.
* Per-kind registry namespaces: a transition ref and a guard ref may share a
  name with no collision.
* Resolve-miss raises a typed ``HandlerNotFound``.
* A default ``first-found`` firing policy is registered and picks the first
  entry of ``enabledTransitions`` (or ``None`` when empty).

The registry is tested independently from engine enablement, consume/deposit,
and journal behavior. It binds names to callables and resolves the four
protocol kinds.
"""

from __future__ import annotations


from typing import Any

import pytest

from velocitron.contract import (
    FiringContext,
    FiringPolicyInput,
    FiringTimestamps,
    GuardHandlerInput,
    HandlerError,
    PredicateHandlerInput,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.registry import (
    DEFAULT_FIRING_POLICY,
    HandlerNotFound,
    HandlerRegistry,
)
from velocitron.schema import Marking, Token


# ── FiringContext (closed, four-field shape) ─────────────────────────────


class TestFiringContext:
    """``FiringContext`` is exactly four fields, closed for portability."""

    def test_has_exactly_the_four_required_keys(self):
        # given: a fully-populated firing context
        ctx: FiringContext = {
            "firingId": "net-x#start_feature#0",
            "attempt": 0,
            "netId": "net-x",
            "timestamps": {"fired_at": "2026-06-25T22:47:00Z"},
        }

        # then: the key set is exactly the four contract fields
        assert set(ctx.keys()) == {
            "firingId",
            "attempt",
            "netId",
            "timestamps",
        }

    def test_required_keys_match_contract(self):
        # then: every field is required (closed shape)
        # Closed shape: every field is required (no optional keys).
        assert FiringContext.__required_keys__ == frozenset(
            {"firingId", "attempt", "netId", "timestamps"}
        )
        # and: there are no optional keys
        assert FiringContext.__optional_keys__ == frozenset()


def _ctx() -> FiringContext:
    """A minimal valid firing context for handler invocations."""
    return {
        "firingId": "net-x#start_feature#0",
        "attempt": 0,
        "netId": "net-x",
        "timestamps": {"fired_at": "2026-06-25T22:47:00Z"},
    }


def _completed_transition(
    inp: TransitionHandlerInput,
) -> TransitionHandlerOutput:
    """A canonical completed transition handler used as a sentinel across tests."""
    return {
        "status": "completed",
        "outputTokens": {},
        "error": None,
        "metadata": {},
    }


# The four per-kind resolve methods, shared by the resolve-miss and R1 lock
# parametrizations (identical lists — one constant removes the duplication).
_ALL_RESOLVE_METHODS = (
    "resolve_transition",
    "resolve_guard",
    "resolve_predicate",
    "resolve_firing_policy",
)


# ── Transition handler ────────────────────────────────────────────────────


class TestTransitionHandler:
    """Receives ``{transitionId, inputTokens, firingContext}``, returns
    ``{status, outputTokens, error, metadata}``."""

    def test_completed_returns_output_tokens(self):
        # given: a registry and a completed transition handler
        registry = HandlerRegistry()

        def start_feature(
            inp: TransitionHandlerInput,
        ) -> TransitionHandlerOutput:
            assert inp["transitionId"] == "start_feature"
            assert inp["firingContext"]["netId"] == "net-x"
            # inputTokens: one entry per consume arc, keyed by source place.
            backlog_tokens = inp["inputTokens"]["backlog"]
            assert len(backlog_tokens) == 1
            return {
                "status": "completed",
                "outputTokens": {
                    "plan_needed": [Token(type="feature", data=backlog_tokens[0].data)]
                },
                "error": None,
                "metadata": {"source": "test"},
            }

        # when: registering, resolving, and invoking the handler with a binding
        registry.register_transition("start_feature", start_feature)
        resolved = registry.resolve_transition("start_feature")

        out = resolved(
            {
                "transitionId": "start_feature",
                "inputTokens": {"backlog": [Token(type="feature", data={"id": "f1"})]},
                "firingContext": _ctx(),
            }
        )

        # then: the output reports completed with produced tokens and metadata
        assert out["status"] == "completed"
        assert out["error"] is None
        assert "plan_needed" in out["outputTokens"]
        assert out["outputTokens"]["plan_needed"][0].type == "feature"
        assert out["metadata"] == {"source": "test"}

    def test_failed_does_not_carry_output_tokens(self):
        # given: a registry and a failing transition handler
        registry = HandlerRegistry()

        def failing(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            return {
                "status": "failed",
                "outputTokens": {},
                "error": {"type": "PreCommitError", "message": "hooks failed"},
                "metadata": {},
            }

        # when: registering, resolving, and invoking the handler
        registry.register_transition("failing", failing)
        out = registry.resolve_transition("failing")(
            {
                "transitionId": "failing",
                "inputTokens": {},
                "firingContext": _ctx(),
            }
        )

        # then: the output reports failed with an error and no output tokens
        assert out["status"] == "failed"
        assert out["error"] == {
            "type": "PreCommitError",
            "message": "hooks failed",
        }
        assert out["outputTokens"] == {}

    def test_completed_may_have_empty_output_tokens(self):
        """A consume-only transition (e.g. a commit) returns no tokens."""
        # given: a registry and a consume-only commit handler
        registry = HandlerRegistry()

        def commit(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            return {
                "status": "completed",
                "outputTokens": {},
                "error": None,
                "metadata": {},
            }

        # when: registering, resolving, and invoking the handler
        registry.register_transition("commit", commit)
        out = registry.resolve_transition("commit")(
            {
                "transitionId": "commit",
                "inputTokens": {"git_tree_diff": [Token(type="git_status", data={})]},
                "firingContext": _ctx(),
            }
        )

        # then: the output reports completed with no produced tokens
        assert out["status"] == "completed"
        assert out["outputTokens"] == {}


# ── Guard handler ─────────────────────────────────────────────────────────


class TestGuardHandler:
    """Receives the transition input shape, returns ``bool`` (may be impure)."""

    def test_returns_true_when_enabled(self):
        # given: a registry and a guard that enables when no git diff is bound
        registry = HandlerRegistry()

        def tree_is_clean(inp: GuardHandlerInput) -> bool:
            # A guard sees the full input binding across all input arcs.
            return "git_tree_diff" not in inp["inputTokens"]

        # when: registering and resolving the guard
        registry.register_guard("tree_is_clean", tree_is_clean)
        guard = registry.resolve_guard("tree_is_clean")

        # then: invoking the guard with an empty binding returns True
        assert (
            guard(
                {
                    "transitionId": "commit",
                    "inputTokens": {},
                    "firingContext": _ctx(),
                }
            )
            is True
        )

    def test_returns_false_when_disabled(self):
        # given: a registry and a guard that disables when a git diff is bound
        registry = HandlerRegistry()

        def tree_is_clean(inp: GuardHandlerInput) -> bool:
            return "git_tree_diff" not in inp["inputTokens"]

        # when: registering and resolving the guard
        registry.register_guard("tree_is_clean", tree_is_clean)
        guard = registry.resolve_guard("tree_is_clean")

        # then: invoking the guard with a git-diff binding returns False
        assert (
            guard(
                {
                    "transitionId": "commit",
                    "inputTokens": {
                        "git_tree_diff": [Token(type="git_status", data={})]
                    },
                    "firingContext": _ctx(),
                }
            )
            is False
        )


# ── Predicate handler ─────────────────────────────────────────────────────


class TestPredicateHandler:
    """Receives ``{token, firingContext}`` (single token), returns ``bool``
    (pure)."""

    def test_filters_a_single_token(self):
        # given: a registry and a high-confidence predicate over a single token
        registry = HandlerRegistry()

        def high_confidence(inp: PredicateHandlerInput) -> bool:
            # A predicate sees ONE candidate token — not the full binding.
            return inp["token"].data.get("confidence", 0) > 0.8

        # when: registering and resolving the predicate
        registry.register_predicate("high_confidence", high_confidence)
        pred = registry.resolve_predicate("high_confidence")

        # then: a 0.9-confidence token passes the filter
        assert (
            pred(
                {
                    "token": Token(type="classified", data={"confidence": 0.9}),
                    "firingContext": _ctx(),
                }
            )
            is True
        )
        # and: a 0.3-confidence token is filtered out
        assert (
            pred(
                {
                    "token": Token(type="classified", data={"confidence": 0.3}),
                    "firingContext": _ctx(),
                }
            )
            is False
        )


# ── Firing policy handler ────────────────────────────────────────────────


class TestFiringPolicyHandler:
    """Receives ``{marking, enabledTransitions, priorities,
    consecutiveFailures}``, returns ``str | None``."""

    def test_custom_policy_picks_a_transition(self):
        # given: a registry, a backlog marking, and a first-by-name policy
        registry = HandlerRegistry()
        marking: Marking = Marking(
            {"backlog": [Token(type="feature", data={"id": "f1"})]}
        )

        def first_by_name(inp: FiringPolicyInput) -> str | None:
            assert inp["marking"] is marking
            enabled = inp["enabledTransitions"]
            return enabled[0] if enabled else None

        # when: registering and resolving the policy
        registry.register_firing_policy("first_by_name", first_by_name)
        policy = registry.resolve_firing_policy("first_by_name")

        # then: invoking it with two enabled transitions returns the first
        assert (
            policy(
                {
                    "marking": marking,
                    "enabledTransitions": ["start_feature", "write_plan"],
                    "priorities": {"start_feature": 0, "write_plan": 0},
                    "consecutiveFailures": {"start_feature": 0, "write_plan": 0},
                }
            )
            == "start_feature"
        )

    def test_custom_policy_may_stop(self):
        # given: a registry and a never-fire policy
        registry = HandlerRegistry()

        def never_fire(inp: FiringPolicyInput) -> str | None:
            return None

        # when: registering and resolving the policy
        registry.register_firing_policy("never_fire", never_fire)
        policy = registry.resolve_firing_policy("never_fire")

        # then: invoking it returns None (stop firing)
        assert (
            policy(
                {
                    "marking": Marking(),
                    "enabledTransitions": ["start_feature"],
                    "priorities": {"start_feature": 0},
                    "consecutiveFailures": {"start_feature": 0},
                }
            )
            is None
        )


# ── Registry API: resolve-miss ───────────────────────────────────────────


class TestRegistryApi:
    """Resolve-miss raises ``HandlerNotFound`` for every kind. Per-kind
    namespace isolation is locked exhaustively in ``TestRegistryMechanicsLock``
    (R4, all six kind pairs)."""

    @pytest.mark.parametrize("resolve_method", _ALL_RESOLVE_METHODS)
    def test_resolve_miss_raises_handler_not_found(self, resolve_method: str):
        # given: an empty handler registry and a per-kind resolver
        registry = HandlerRegistry()
        resolver = getattr(registry, resolve_method)

        # then: resolving an unregistered name raises HandlerNotFound
        with pytest.raises(HandlerNotFound):
            resolver("does-not-exist")


# ── Default first-found firing policy ────────────────────────────────────


class TestDefaultFiringPolicy:
    """The default ``first-found`` policy is registered under a reserved name
    and picks the first enabled transition (ADR 0005)."""

    def test_default_policy_name_is_registered(self):
        # given: a fresh handler registry
        registry = HandlerRegistry()
        # DEFAULT_FIRING_POLICY is the reserved name; resolvable out of the box.

        # when: resolving the reserved default policy name
        policy = registry.resolve_firing_policy(DEFAULT_FIRING_POLICY)

        # then: a policy callable is returned
        assert policy is not None

    def test_default_picks_first_enabled_transition(self):
        # given: a registry with the default policy resolved and a backlog marking
        registry = HandlerRegistry()
        policy = registry.resolve_firing_policy(DEFAULT_FIRING_POLICY)
        marking: Marking = Marking(
            {"backlog": [Token(type="feature", data={"id": "f1"})]}
        )

        # then: invoking the policy with two enabled transitions returns the first
        assert (
            policy(
                {
                    "marking": marking,
                    "enabledTransitions": ["write_plan", "start_feature"],
                    "priorities": {"write_plan": 0, "start_feature": 0},
                    "consecutiveFailures": {"write_plan": 0, "start_feature": 0},
                }
            )
            == "write_plan"
        )

    def test_default_returns_none_when_nothing_enabled(self):
        # given: a registry with the default policy resolved
        registry = HandlerRegistry()
        policy = registry.resolve_firing_policy(DEFAULT_FIRING_POLICY)

        # then: invoking it with no enabled transitions returns None
        assert (
            policy(
                {
                    "marking": Marking(),
                    "enabledTransitions": [],
                    "priorities": {},
                    "consecutiveFailures": {},
                }
            )
            is None
        )

    def test_default_is_deterministic_under_same_inputs(self):
        """First-found is deterministic for replay (ADR 0005)."""
        # given: a registry with the default policy resolved, a marking, and fixed args
        registry = HandlerRegistry()
        policy = registry.resolve_firing_policy(DEFAULT_FIRING_POLICY)
        marking: Marking = Marking(
            {"backlog": [Token(type="feature", data={"id": "f1"})]}
        )
        args: FiringPolicyInput = {
            "marking": marking,
            "enabledTransitions": ["a", "b", "c"],
            "priorities": {"a": 0, "b": 0, "c": 0},
            "consecutiveFailures": {"a": 0, "b": 0, "c": 0},
        }

        # then: two invocations with the same inputs yield the same first transition
        assert policy(args) == policy(args) == "a"


# ── Registry mechanics lock (R1–R5) ───────────────────────────────────────


class TestRegistryMechanicsLock:
    """Biting lock tests pinning the registry's behavioral invariants.

    Each test names its bite mechanism in its docstring (reversion-verified):
    the invariant, the exact reversion applied, and the confirmed failure.
    """

    # R1 — HandlerNotFound.name carries the unresolved name (all four kinds)
    @pytest.mark.parametrize("resolve_method", _ALL_RESOLVE_METHODS)
    def test_handler_not_found_carries_unresolved_name(self, resolve_method: str):
        """``HandlerNotFound`` carries the unresolved name as ``.name`` so a
        traceback identifies which handler was missing (the resolve site has
        only the name string). Bite (reversion-verified): reverting
        ``HandlerNotFound.__init__`` in ``registry.py`` to drop the
        ``self.name = name`` line (leaving only ``super().__init__(name)``)
        fails this test (confirmed: ``AttributeError: 'HandlerNotFound'
        object has no attribute 'name'``).
        """
        # given: an empty handler registry and a per-kind resolver
        registry = HandlerRegistry()
        resolver = getattr(registry, resolve_method)

        # when: resolving an unregistered name raises HandlerNotFound
        with pytest.raises(HandlerNotFound) as exc:
            resolver("does-not-exist")

        # then: the exception carries the unresolved name as .name
        assert exc.value.name == "does-not-exist"

    # R2 — DEFAULT_FIRING_POLICY is the reserved literal "first-found"
    def test_default_firing_policy_is_first_found_literal(self):
        """``DEFAULT_FIRING_POLICY`` is the reserved literal ``"first-found"``
        — the contract the engine's default ``policy=`` param hinges on
        (ADR 0005). Bite (reversion-verified): reverting
        ``DEFAULT_FIRING_POLICY = "first-found"`` to ``"first_found"`` in
        ``registry.py`` fails this test (confirmed: ``AssertionError`` —
        ``'first_found' != 'first-found'``).
        """
        # then: the reserved literal is exactly "first-found"
        assert DEFAULT_FIRING_POLICY == "first-found"

    # R3 — resolve_* returns the very callable registered (is-identical), all four kinds
    @pytest.mark.parametrize(
        "resolve_method,register_method,sentinel",
        [  # pyright: ignore[reportUnknownArgumentType]
            ("resolve_transition", "register_transition", _completed_transition),
            ("resolve_guard", "register_guard", lambda inp: True),  # pyright: ignore[reportUnknownLambdaType]
            ("resolve_predicate", "register_predicate", lambda inp: True),  # pyright: ignore[reportUnknownLambdaType]
            (
                "resolve_firing_policy",
                "register_firing_policy",
                lambda inp: "x",  # pyright: ignore[reportUnknownLambdaType]
            ),
        ],
    )
    def test_resolve_returns_registered_callable_is_identical(
        self, resolve_method: str, register_method: str, sentinel: Any
    ):
        """``resolve_*`` returns the stored callable, not a wrapper. The
        engine calls the resolved callable directly, so a wrapper could
        diverge from the registered behavior. Bite (reversion-verified):
        reverting ``HandlerRegistry._resolve`` in ``registry.py`` from
        ``return table[name]`` to
        ``return (lambda *a, **k: table[name](*a, **k))`` (wrap in a proxy)
        fails each parametrized case (confirmed: ``assert ... is ...`` is
        ``False``). Pins all four kinds.
        """
        # given: a fresh registry and a distinct sentinel callable
        registry = HandlerRegistry()
        register = getattr(registry, register_method)
        resolver = getattr(registry, resolve_method)
        handler = sentinel  # type: ignore[arg-type]

        # when: registering the sentinel under name "h"
        register("h", handler)  # type: ignore[arg-type]

        # then: resolving "h" returns the very same callable (is-identical)
        assert resolver("h") is handler  # type: ignore[arg-type]

    # R4 — Cross-kind namespace isolation is exhaustive across all 6 kind pairs
    @pytest.mark.parametrize(
        "resolve_a,resolve_b",
        [
            ("resolve_transition", "resolve_guard"),
            ("resolve_transition", "resolve_predicate"),
            ("resolve_transition", "resolve_firing_policy"),
            ("resolve_guard", "resolve_predicate"),
            ("resolve_guard", "resolve_firing_policy"),
            ("resolve_predicate", "resolve_firing_policy"),
        ],
    )
    def test_cross_kind_namespace_isolation_all_pairs(
        self, resolve_a: str, resolve_b: str
    ):
        """Per-kind namespaces: every pair of kinds sharing one name resolves
        to distinct callables. The existing lock only pinned the
        transition×guard pair, so a reversion collapsing two *other* kind
        tables into one shared dict would slip past it. Bite
        (reversion-verified): in ``HandlerRegistry.__init__`` aliasing
        ``self._guards = self._transitions`` (collapsing guard and transition
        into one shared dict) fails the parametrized transition×guard case
        (confirmed: ``assert ... is not ...`` is ``False`` — re-registration
        of the guard overwrites the shared slot, so both resolve to the same
        callable).
        """
        # given: a registry with four distinct sentinel callables, one per kind,
        # all registered under the SAME name "shared"
        registry = HandlerRegistry()
        transition_h = _completed_transition  # distinct sentinel
        guard_h = lambda inp: True  # noqa: E731  # pyright: ignore[reportUnknownLambdaType, reportUnknownVariableType]
        predicate_h = lambda inp: True  # noqa: E731  # pyright: ignore[reportUnknownLambdaType, reportUnknownVariableType]
        policy_h = lambda inp: "x"  # noqa: E731  # pyright: ignore[reportUnknownLambdaType, reportUnknownVariableType]
        registry.register_transition("shared", transition_h)  # type: ignore[arg-type]
        registry.register_guard("shared", guard_h)  # type: ignore[arg-type]
        registry.register_predicate("shared", predicate_h)  # type: ignore[arg-type]
        registry.register_firing_policy("shared", policy_h)  # type: ignore[arg-type]

        # then: the two kinds resolve to distinct callables (no cross-kind aliasing)
        resolve_a_fn = getattr(registry, resolve_a)
        resolve_b_fn = getattr(registry, resolve_b)
        assert resolve_a_fn("shared") is not resolve_b_fn("shared")

    # R5 — Re-registration overwrites (latest wins), all four kinds
    @pytest.mark.parametrize(
        "resolve_method,register_method,sentinel_factory",
        [  # pyright: ignore[reportUnknownArgumentType]
            (
                "resolve_transition",
                "register_transition",
                lambda v: (  # pyright: ignore[reportUnknownLambdaType]
                    lambda inp: {  # pyright: ignore[reportUnknownLambdaType]
                        "status": "completed",
                        "outputTokens": {},
                        "error": None,
                        "metadata": {"v": v},
                    }
                ),
            ),
            ("resolve_guard", "register_guard", lambda v: lambda inp: v),  # pyright: ignore[reportUnknownLambdaType]
            ("resolve_predicate", "register_predicate", lambda v: lambda inp: v),  # pyright: ignore[reportUnknownLambdaType]
            (
                "resolve_firing_policy",
                "register_firing_policy",
                lambda v: lambda inp: v,  # pyright: ignore[reportUnknownLambdaType]
            ),
        ],
    )
    def test_re_registration_overwrites_latest_wins(
        self, resolve_method: str, register_method: str, sentinel_factory: Any
    ):
        """Registering the same name again replaces the binding (dict-assignment
        latest-wins semantics). Bite (reversion-verified): reverting the
        parametrized ``register_*`` in ``registry.py`` from
        ``self._<table>[name] = handler`` to
        ``self._<table>.setdefault(name, handler)`` (insert-if-absent) fails
        this test (confirmed: ``resolve`` returns ``h1``, not ``h2`` —
        ``assert h2 is h1`` is ``False``). Pins all four kinds.
        """
        # given: a fresh registry and two distinct sentinel callables
        registry = HandlerRegistry()
        register = getattr(registry, register_method)
        resolver = getattr(registry, resolve_method)
        h1 = sentinel_factory("one")  # type: ignore[arg-type]
        h2 = sentinel_factory("two")  # type: ignore[arg-type]

        # when: registering h1 then h2 under the same name "n"
        register("n", h1)  # type: ignore[arg-type]
        register("n", h2)  # type: ignore[arg-type]

        # then: resolving "n" returns h2 (the latest binding wins)
        assert resolver("n") is h2  # type: ignore[arg-type]


# ── Contract shape lock (C1–C2) ──────────────────────────────────────────


class TestContractShapesLock:
    """Biting lock tests pinning the I/O TypedDict shapes.

    Each test names its bite mechanism in its docstring (reversion-verified):
    the invariant, the exact reversion applied, and the confirmed failure.
    """

    # C1 — TypedDict required-key sets for all six I/O shapes
    @pytest.mark.parametrize(
        "shape,expected_required",
        [
            (FiringTimestamps, {"fired_at"}),
            (HandlerError, {"type", "message"}),
            (
                TransitionHandlerInput,
                {"transitionId", "inputTokens", "firingContext"},
            ),
            (
                TransitionHandlerOutput,
                {"status", "outputTokens", "error", "metadata"},
            ),
            (PredicateHandlerInput, {"token", "firingContext"}),
            (
                FiringPolicyInput,
                {
                    "marking",
                    "enabledTransitions",
                    "priorities",
                    "consecutiveFailures",
                },
            ),
        ],
    )
    def test_io_shapes_required_keys_match_contract(
        self, shape: Any, expected_required: set[str]
    ):
        """Each I/O shape is a closed TypedDict with exactly the spec-named
        required keys and no optional keys — symmetric to the existing
        ``TestFiringContext.test_required_keys_match_contract`` closed-shape
        lock. Bite (reversion-verified): removing the
        ``metadata: dict[str, Any]`` line from ``TransitionHandlerOutput`` in
        ``contract.py`` (so its required keys drop ``metadata``) fails the
        ``TransitionHandlerOutput`` parametrized case (confirmed:
        ``AssertionError`` — ``frozenset({'status', 'outputTokens',
        'error'}) != frozenset({'status', 'outputTokens', 'error',
        'metadata'})``). Adding an optional field would symmetrically break
        ``__optional_keys__ == frozenset()``. Pins all six shapes.
        """
        # then: the shape's required keys are exactly the spec-named set
        assert shape.__required_keys__ == frozenset(expected_required)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        # and: the shape is closed (no optional keys)
        assert shape.__optional_keys__ == frozenset()  # pyright: ignore[reportUnknownMemberType]

    # C2 — GuardHandlerInput is an alias for TransitionHandlerInput (ADR 0002)
    def test_guard_handler_input_is_transition_handler_input_alias(self):
        """``GuardHandlerInput`` is an alias for ``TransitionHandlerInput``
        — the contract's expression that guards see the transition input
        shape (ADR 0002). Bite (reversion-verified): replacing
        ``GuardHandlerInput = TransitionHandlerInput`` in ``contract.py``
        with a separate ``class GuardHandlerInput(TypedDict): …`` (same keys
        but a distinct class) fails this test (confirmed: ``is`` is ``False``
        — the alias guarantee is severed even though the key sets match).
        """
        # then: GuardHandlerInput IS TransitionHandlerInput (identity, not just same keys)
        assert GuardHandlerInput is TransitionHandlerInput
