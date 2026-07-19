"""Handler registry contract types.

TypedDict/Protocol definitions for the four handler kinds and the
``FiringContext`` they all receive. Pure types only — this module holds no
firing engine, enablement, binding, deposit, or journal code; those live in
``velocitron.engine`` and ``velocitron.journal`` and consume these shapes.

The shapes here are the machine-checkable surface of the prose handler
contract. The I/O messages are JSON-serializable and are recorded by the
firing journal (``velocitron.journal``); this module defines no JSON Schema
for the messages, only the Python type definitions that prove the contract is
internally coherent.

References: spec/handler-contract.md; spec/firing-semantics.md.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

from .schema import Marking, Token


# ── FiringContext (closed, four-field shape) ────────────────────────────


class FiringTimestamps(TypedDict):
    """Wall-clock timing for a firing attempt. Metadata/logging only."""

    fired_at: str


class FiringContext(TypedDict):
    """Closed four-field context handed to every handler kind.

    Exactly ``firingId``, ``attempt``, ``netId``, ``timestamps`` — no
    additional runtime-specific fields. Handlers MUST NOT branch on
    ``timestamps`` for firing decisions; a handler that branches on wall-clock
    time is non-replayable by construction and violates the contract. Timing
    that drives behavior belongs in net-modeled state, not in
    ``firingContext``.

    - ``firingId`` — unique id for this firing attempt, deterministic for
      replay (derived from netId + transition + attempt, not a random UUID).

    - ``attempt`` — retry counter, 0 for the first attempt. Lets a handler
      distinguish a fresh fire from a net-modeled retry (``attempt + 1``).

    - ``netId`` — the net's ``name``.

    - ``timestamps`` — ``{fired_at: ISO8601}``, wall-clock when the fire was
      initiated. Metadata/logging only.
    """

    firingId: str
    attempt: int
    netId: str
    timestamps: FiringTimestamps


# ── Handler error ──────────────────────────────────────────────────────


class HandlerError(TypedDict):
    """A structured failure reported by a transition handler."""

    type: str
    message: str


# ── Transition handler ─────────────────────────────────────────────────


class TransitionHandlerInput(TypedDict):
    """``{transitionId, inputTokens, firingContext}`` — the resolved input
    binding a transition handler sees.

    ``inputTokens`` is keyed by source place name (one entry per consume arc),
    each the list of tokens that arc matched. The handler sees exactly the
    tokens the engine will consume — not the full marking: maximally testable
    and reproducible. If marking-aware behavior is needed, model it as a guard
    or restructure the net so the relevant state is in input tokens.

    References: ADR 0003.
    """

    transitionId: str
    inputTokens: dict[str, list[Token]]
    firingContext: FiringContext


class TransitionHandlerOutput(TypedDict):
    """``{status, outputTokens, error, metadata}`` — what a transition
    handler returns.

    - ``completed`` — the engine deposits ``outputTokens`` against the
      transition's produce templates (validated: each token's type must match
      a produce template's ``type`` and land in its ``destination`` place).
      ``outputTokens`` may be empty (a consume-only transition, e.g. a
      commit); when the produce template carries literal ``data`` and the
      handler returns no tokens, the engine emits the template's fixed token
      (passthrough).

    - ``failed`` — the engine does **not** consume input tokens (no marking
      change); it records the failure. Retry is net-modeled, not
      handler-internal, keeping retry observable and replayable.

    - ``metadata`` — opaque to the engine; recorded in the journal for
      observability. Must not drive firing decisions.
    """

    status: Literal["completed", "failed"]
    outputTokens: dict[str, list[Token]]
    error: HandlerError | None
    metadata: dict[str, Any]


class TransitionHandler(Protocol):
    """The work-doer: a pure function of its declared inputs.

    References: ADR 0003.
    """

    def __call__(self, inp: TransitionHandlerInput) -> TransitionHandlerOutput: ...


# ── Guard handler ──────────────────────────────────────────────────────


# A guard receives the same input shape as a transition handler: the full
# input binding across all input arcs (contrast: predicates see one token).
# Kept as an alias to express "same shape" exactly, per the contract.
GuardHandlerInput = TransitionHandlerInput


class GuardHandler(Protocol):
    """A transition-level gate.

    Returns ``True`` to enable (subject to arc enablement), ``False`` to
    disable. May be impure — may consult external state (filesystem, clock,
    API). This is the seam where impure, transition-wide decisions live, kept
    separate from pure per-token predicates.

    References: ADR 0002.
    """

    def __call__(self, inp: GuardHandlerInput) -> bool: ...


# ── Predicate handler ──────────────────────────────────────────────────


class PredicateHandlerInput(TypedDict):
    """``{token, firingContext}`` — a single candidate token for one consume
    arc plus the firing context.

    The predicate sees one token — not the full binding.

    References: ADR 0002.
    """

    token: Token
    firingContext: FiringContext


class PredicateHandler(Protocol):
    """An arc-level pure filter over one token.

    Returns ``True`` if the token matches the arc, ``False`` to filter it out.
    MUST be pure: no side effects, no external state, deterministic given the
    same token. Enforced by contract/documentation, not statically checkable in
    Python; a predicate that consults external state violates the contract and
    breaks replayability. Inline CEL predicates are pure by construction; named
    predicate handlers are the escape hatch for logic too complex for CEL and
    must uphold the same purity.

    References: ADR 0002.
    """

    def __call__(self, inp: PredicateHandlerInput) -> bool: ...


# ── Firing policy handler ──────────────────────────────────────────────


class FiringPolicyInput(TypedDict):
    """``{marking, enabledTransitions, priorities, consecutiveFailures}`` —
    the full current marking, the ids of transitions currently enabled (arcs
    satisfiable AND guard true), each enabled transition's declared priority,
    and each enabled transition's consecutive-failure count.

    ``priorities`` is keyed by exactly the entries of ``enabledTransitions``;
    a transition with no declared ``priority`` maps to ``0``. The engine
    threads it to every policy so a priority-aware policy needs no access to
    the net (the ``Transition.priority`` field lives there).

    ``consecutiveFailures`` is likewise keyed by exactly the entries of
    ``enabledTransitions``: each maps to the number of consecutive ``failed``
    firings that transition has accumulated within the current ``run`` since
    the last ``completed`` firing (no failure history = ``0``; any completed
    firing resets every count). Deterministic — derived from the firing
    sequence itself, never wall-clock — so a failure-aware policy (skip,
    deprioritize, attempt-based backoff) stays replayable. Both built-in
    policies ignore it.

    The policy is opaque: it picks which to fire; the engine handles binding
    and firing.

    References: ADR 0005; ADR 0014; ADR 0015.
    """

    marking: Marking
    enabledTransitions: list[str]
    priorities: dict[str, int]
    consecutiveFailures: dict[str, int]


class FiringPolicyHandler(Protocol):
    """Selects which enabled transition to fire.

    Returns one transition id, or ``None`` to stop (no fire this step).
    Selection is sequential; concurrent firing is not part of this contract.
    Two built-in policies are registered under reserved names on every fresh
    registry: ``first-found`` (the default — the first entry of
    ``enabledTransitions``; deterministic iteration order, replayable) and
    ``priority`` (the highest ``priorities`` value among the enabled; ties fall
    back to first-found's list order, so it degrades to first-found when no
    transition declares a priority).

    References: ADR 0005; ADR 0014.
    """

    def __call__(self, inp: FiringPolicyInput) -> str | None: ...
