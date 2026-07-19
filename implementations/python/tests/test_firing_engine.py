"""Firing-engine lock-the-coverage tests (``[impl] firing engine``).

The coverage-*lock* for ``spec/firing-semantics.md`` §(b) Firing and
§(c) Failure handling, plus the record-emission + hook-routing surface of
§(d) that the ``fire`` cycle emits through. The fire-cycle engine already
exists on ``main`` (landed during the ``firing-semantics`` [spec]
co-evolution); per AGENTS.md this is a lock-the-coverage pass, not
red-then-green -- each test passes against the existing (correct) impl and was
verified to bite under a targeted reversion that breaks the invariant it pins.

Scope boundary: sibling ``(firing-journal)`` owns §(d)'s journal
*implementation* (``JsonlJournal``, ``sequence``, replay); ``(firing-policy)``
owns §(e)'s selection loop. This lock owns ``fire()`` + record emission only.
"""

from __future__ import annotations

from typing import Any

from velocitron.contract import (
    FiringContext,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import DepositViolation, Engine
from velocitron.journal import FiringRecord, InjectionRecord, Journal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token


# ── Shared helpers ───────────────────────────────────────────────────────


def _tok(t: str = "feature", **data: Any) -> Token:
    """A minimal token of type ``t`` with payload ``data``."""
    return Token(type=t, data=dict(data))


def _marking(**places: list[Token]) -> Marking:
    """A marking from ``place=tokens`` keyword pairs."""
    return Marking({place: list(toks) for place, toks in places.items()})


def _net(d: dict[str, Any]) -> Net:
    """Parse a net dict (thin alias for the parser)."""
    return parse_net(d)


def _bare_engine(
    *, journal: Journal | None = None, deposit_violation: str = "raise"
) -> Engine:
    """An Engine with an empty registry -- register only handlers the path invokes."""
    return Engine(
        HandlerRegistry(), journal=journal, deposit_violation=deposit_violation
    )


def _engine(
    reg: HandlerRegistry,
    *,
    journal: Journal | None = None,
    deposit_violation: str = "raise",
) -> Engine:
    """An Engine over ``reg`` with the given journal / violation mode."""
    return Engine(reg, journal=journal, deposit_violation=deposit_violation)


def _reg(*pairs: tuple[str, Any]) -> HandlerRegistry:
    """A registry from ``(name, handler)`` pairs."""
    reg = HandlerRegistry()
    for name, fn in pairs:
        reg.register_transition(name, fn)
    return reg


class _CapturingJournal:
    """In-memory Journal: captures records, assigns no ``sequence`` (D4)."""

    def __init__(self) -> None:
        self.firings: list[FiringRecord] = []
        self.violations: list[FiringRecord] = []
        self.injections: list[InjectionRecord] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.firings.append(record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self.violations.append(record)

    def record_injection(self, record: InjectionRecord) -> None:
        self.injections.append(record)


# ── Cluster A: consume & atomicity (F1, F2, F7, F14) ────────────────────
# Module-level nets + handlers for the consume-mutation / structural-sharing
# / deposit-append / raise-rollback lock cluster. Uses the shared helpers
# (`_tok`, `_marking`, `_net`, `_engine`, `_reg`, `_CapturingJournal`,
# `DepositViolation`, `TransitionHandlerInput`, `TransitionHandlerOutput`)
# by name; no header/imports/fixtures here.


# ── F1: weight>1 consume mutation at the fire surface ────────────────────
_WEIGHT_NET = _net(
    {
        "name": "weight-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "move", "handler": "move"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "move"},
                "consume": {"type": "task", "weight": 2},
            },
            {
                "from": {"transition": "move"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


def _A_move(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Passthrough: deposit the bound source tokens into dst.
    return {
        "status": "completed",
        "outputTokens": {"dst": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


# ── F2: structural-sharing is-identity on a failed handler fire ──────────
_FAIL_HANDLER_NET = _net(
    {
        "name": "fail-handler-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
        ],
    }
)


def _A_fail_t(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "failed",
        "outputTokens": {},
        "error": {"type": "X", "message": "nope"},
        "metadata": {},
    }


# ── F7: deposit appends to existing tokens, order preserved ──────────────
_APPEND_NET = _net(
    {
        "name": "append-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


def _A_append_t(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Deposit a fresh token into a destination that already holds a token.
    return {
        "status": "completed",
        "outputTokens": {"dst": [_tok("task", made=1)]},
        "error": None,
        "metadata": {},
    }


# ── F14: marking content rolled back under record_then_raise ─────────────
_RAISE_VIOLATION_NET = _net(
    {
        "name": "raise-violation-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


def _A_raise_t(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Wrong-type deposit (type "other" != template "task") -> violation.
    return {
        "status": "completed",
        "outputTokens": {"dst": [_tok("other")]},
        "error": None,
        "metadata": {},
    }


class TestConsumeAtomicity:
    def test_weight_gt_1_consume_removes_exactly_weight_bound_tokens_from_source(self):
        """F1: ``fire`` removes exactly ``weight`` bound tokens from a
        weight>1 consume arc's source place -- the consume MUTATION at the
        fire surface, not just the binding selection.

        Reversion-verified bite: overrode ``Engine._consume`` to remove only
        the FIRST bound token per place (simulating weight-ignored consume);
        ``src`` then retained 2 tokens ``[tok1, tok2]`` instead of ``[tok2]``
        and the ``src == [tok2]`` assertion failed. Confirmed-bites."""
        # given: a weight-2 consume arc with three source tokens
        engine = _engine(_reg(("move", _A_move)))
        marking = _marking(
            src=[_tok("task", i=0), _tok("task", i=1), _tok("task", i=2)],
            dst=[],
        )
        # when: firing binds the two lowest-index tokens and consumes them
        new_marking, record = engine.fire(_WEIGHT_NET, marking, "move", attempt=0)
        # then: the source retains only the unbound third token
        assert list(new_marking.get("src", [])) == [_tok("task", i=2)]
        # and: the destination receives the two bound tokens in binding order
        assert list(new_marking.get("dst", [])) == [
            _tok("task", i=0),
            _tok("task", i=1),
        ]
        # and: the fire completed
        assert record["status"] == "completed"

    def test_failed_handler_fire_returns_the_input_marking_object_by_identity(self):
        """F2: on a handler-``failed`` fire, the returned marking IS the input
        marking object (``is``-identical), not a copy -- structural sharing is
        the atomicity seam that makes rollback free.

        Reversion-verified bite: overrode ``Engine.fire`` to return the
        tentative-consumed ``new_marking`` on the failed path instead of the
        original ``marking``; ``new_marking is marking`` broke. (``Marking`` is
        immutable, so the bite is WHICH object is returned, not a mutation of
        its content.) Confirmed-bites."""
        # given: a handler that reports failure
        engine = _engine(_reg(("t", _A_fail_t)))
        marking = _marking(src=[_tok("task", i=0)])
        # when: the fire fails at the handler
        new_marking, record = engine.fire(_FAIL_HANDLER_NET, marking, "t", attempt=0)
        # then: the returned marking is the SAME object as the input
        assert new_marking is marking
        # and: the record is failed
        assert record["status"] == "failed"

    def test_deposit_appends_to_existing_destination_tokens_in_insertion_order(self):
        """F7: deposited tokens are APPENDED to a destination that already
        holds tokens (``_apply_deposit`` extends, not replaces); insertion
        order is preserved (existing token first, made token second).

        Reversion-verified bite: overrode ``Engine._apply_deposit`` to REPLACE
        (``marking.set(dest, pvector(toks))``) instead of extend; the existing
        token was lost and ``dst == [existing, made]`` failed. Confirmed-bites."""
        # given: a destination already holding a token
        engine = _engine(_reg(("t", _A_append_t)))
        existing = _tok("task", existing=1)
        made = _tok("task", made=1)
        marking = _marking(src=[_tok("task", i=0)], dst=[existing])
        # when: the handler deposits a fresh token into dst
        new_marking, record = engine.fire(_APPEND_NET, marking, "t", attempt=0)
        # then: dst is the existing token followed by the made token (order preserved)
        assert list(new_marking.get("dst", [])) == [existing, made]
        # and: the source was consumed
        assert list(new_marking.get("src", [])) == []
        # and: the fire completed
        assert record["status"] == "completed"

    def test_record_then_raise_rolls_back_marking_content(self):
        """F14: under ``record_then_raise``, a deposit-contract violation
        RAISES ``DepositViolation`` and the caller's marking CONTENT is
        unchanged -- the tentative consume was rolled back (src still holds its
        token) and no deposit leaked (dst empty). ``Marking`` is immutable, so
        ``is``-identity is trivially preserved and does NOT bite; the
        load-bearing pins are (a) the raise itself and (b) the content
        rollback (src token present, dst empty).

        Reversion-verified bite: overrode ``Engine._handle_violation`` to
        apply the deposit (``self._apply_deposit``) and return
        ``(marking, record)`` instead of raising under ``record_then_raise``;
        the ``DepositViolation`` was not raised, so the ``raised`` assertion
        failed. Confirmed-bites."""
        # given: a handler that deposits a wrong-type token (violation) under record_then_raise
        journal = _CapturingJournal()
        engine = _engine(
            _reg(("t", _A_raise_t)),
            journal=journal,
            deposit_violation="record_then_raise",
        )
        marking = _marking(src=[_tok("task", i=0)])
        # when: the fire raises a DepositViolation
        raised = False
        try:
            engine.fire(_RAISE_VIOLATION_NET, marking, "t", attempt=0)
        except DepositViolation:
            raised = True
        # then: the violation was raised
        assert raised
        # and: the source token was rolled back (still present)
        assert list(marking.get("src", [])) == [_tok("task", i=0)]
        # and: no deposit leaked into the destination
        assert list(marking.get("dst", [])) == []


# ── Cluster B (tag `B`): handler input shape (F3) + deterministic
#    firingContext (F4). Module-level nets + handlers, then the
#    `TestHandlerInputShape` BDD class. No imports/fixtures — shared header
#    owns those. ────────────────────────────────────────────────────────


# F3 — two consume arcs on the SAME source place (`src`), each weight 1, so
# the resolved binding concatenates one token per arc into a single `src`
# entry. The produce arc is the minimal sink that lets the passthrough handler
# return a well-formed output (asserted-on: the handler's captured input).
_B_SHARED_INPUT_NET = _net(
    {
        "name": "shared-input-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


# F4 — minimal passthrough net whose `name` we control ("ctx-net") so the
# deterministic firingId / netId fields are asserted against known values.
_B_CTX_NET = _net(
    {
        "name": "ctx-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


# Capture vessels (module-level so the handler closures can append; cleared at
_B_captured_input: list[dict[str, list[Token]]] = []
_B_captured_ctx: list[FiringContext] = []


def _B_shared_input_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """F3 handler: capture `inputTokens`, then passthrough to `dst`."""
    _B_captured_input.append(inp["inputTokens"])
    return {
        "status": "completed",
        "outputTokens": {"dst": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


def _B_ctx_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """F4 handler: capture `firingContext`, then passthrough to `dst`."""
    _B_captured_ctx.append(inp["firingContext"])
    return {
        "status": "completed",
        "outputTokens": {"dst": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


class TestHandlerInputShape:
    """Lock for the handler's view of the resolved binding (F3) and the
    deterministic `firingContext` (F4)."""

    def test_input_tokens_keyed_by_source_place_concatenated_across_shared_arcs(self):
        """F3 — the transition handler receives `inputTokens` keyed by source
        place, `weight` tokens per arc, CONCATENATED across arcs that share a
        place in declaration order.

        Bite (reversion-verified): override `Engine._binding_from_combo` to
        OVERWRITE per place (`binding[arc.from_place] = list(combo[i])`)
        instead of `.extend(...)` — only the LAST arc's token survives per
        place, so the handler sees `{"src": [tok1]}` (one token) instead of
        `{"src": [tok0, tok1]}`, and the length/equality assertion fails.
        """
        # given: the shared-input net (two consume arcs on `src`), a registry
        #        with the capturing passthrough handler, and a two-token src
        _B_captured_input.clear()
        reg = _reg(("t", _B_shared_input_handler))
        engine = _engine(reg)
        tok0 = _tok("task", i=0)
        tok1 = _tok("task", i=1)
        marking = _marking(src=[tok0, tok1])
        # when: firing t
        engine.fire(_B_SHARED_INPUT_NET, marking, "t", attempt=0)
        # then: the handler saw exactly `{"src": [tok0, tok1]}` — both arcs'
        #       bound tokens concatenated into the single `src` entry
        assert len(_B_captured_input) == 1
        seen = _B_captured_input[0]
        assert list(seen.keys()) == ["src"]
        assert seen["src"] == [tok0, tok1]

    def test_firing_context_is_deterministic_id_attempt_netid(self):
        """F4 — the handler sees a deterministic `firingContext`:
        `firingId == "{net.name}/{transition}/{attempt}"`, `attempt == <the
        fire's attempt arg>`, `netId == net.name`.

        Bite (reversion-verified): override `Engine._make_ctx` to ignore the
        `attempt` argument (hardcode `attempt=0`) — `firingId` becomes
        `"ctx-net/t/0"` (mismatch) and `attempt == 0` (mismatch), so both the
        firingId and attempt assertions fail.
        """
        # given: the ctx-net (name "ctx-net"), a registry with the
        #        context-capturing passthrough handler, and a one-token src
        _B_captured_ctx.clear()
        reg = _reg(("t", _B_ctx_handler))
        engine = _engine(reg)
        marking = _marking(src=[_tok("task", i=0)])
        # when: firing t with attempt=3
        engine.fire(_B_CTX_NET, marking, "t", attempt=3)
        # then: the handler saw the deterministic three-field context
        assert len(_B_captured_ctx) == 1
        ctx = _B_captured_ctx[0]
        assert ctx["firingId"] == "ctx-net/t/3"
        assert ctx["attempt"] == 3
        assert ctx["netId"] == "ctx-net"


# ── Cluster C: deposit-violation cluster (F5, F6, F13) ──────────────────

# F5 / F13 net: a templated `task` destination that the handler mistypes.
_C_WRONG_TYPE_NET = _net(
    {
        "name": "C-wrong-type-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


def _C_wrong_type_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Return a token of type ``other`` to the ``task``-templated ``dst``."""
    return {
        "status": "completed",
        "outputTokens": {"dst": [_tok("other")]},
        "error": None,
        "metadata": {},
    }


# F6 net: a templated destination carrying literal ``data:{fixed:true}`` that
# the handler must override with its own supplied token.
_C_PASSTHROUGH_WIN_NET = _net(
    {
        "name": "C-passthrough-win-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {
                    "type": "task",
                    "destination": "dst",
                    "data": {"fixed": True},
                },
            },
        ],
    }
)


def _C_passthrough_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Supply a handler token to the templated ``dst`` (must win over template)."""
    return {
        "status": "completed",
        "outputTokens": {"dst": [_tok("task", from_handler=1)]},
        "error": None,
        "metadata": {},
    }


class TestDepositViolation:
    """Lock-the-coverage for the deposit-violation surface (F5, F6, F13)."""

    def test_wrong_type_token_to_templated_destination_is_a_deposit_violation(self):
        """A handler-returned token whose type differs from the destination's
        produce template is a DepositViolation: the record is failed with
        ``error.type == "DepositViolation"``, no token lands in ``dst``, and
        the tentative consume is rolled back (``src`` retains its token).

        Bite (reversion-verified): overriding ``Engine._detect_violation`` to
        skip the type-check clause (return ``True`` only for the no-template
        branch) makes the violation pass undetected — the handler's ``other``
        token is deposited, the record is ``completed``, ``dst`` is non-empty
        and ``src`` empty, so every assertion fails. Confirmed bites."""
        # given: a templated `task` destination + a handler mistyping it
        reg = _reg(("t", _C_wrong_type_handler))
        journal = _CapturingJournal()
        engine = _engine(reg, journal=journal, deposit_violation="record_then_drop")
        marking = _marking(src=[_tok("task", i=0)], dst=[])
        # when: firing the mistyped deposit
        result_marking, record = engine.fire(_C_WRONG_TYPE_NET, marking, "t", attempt=0)
        # then: the record is a failed DepositViolation
        assert record["status"] == "failed"
        assert record["error"] is not None
        assert record["error"]["type"] == "DepositViolation"
        # and: no deposit leaked (dst empty) and the consume rolled back (src intact)
        assert list(result_marking.get("dst", [])) == []
        assert list(result_marking.get("src", [])) == [_tok("task", i=0)]

    def test_handler_token_wins_over_template_literal_data(self):
        """When a templated destination carries literal ``data`` AND the handler
        supplies a token to it, the HANDLER's token is deposited — not the
        template's fixed passthrough token.

        Bite (reversion-verified): overriding ``Engine._deposit`` to always emit
        the template's fixed token when ``template.data`` is a dict (ignoring
        handler-supplied tokens) deposits ``data == {"fixed": True}`` instead of
        the handler's ``{"from_handler": 1}``, so the data-equality assertion
        fails. Confirmed bites."""
        # given: a templated destination with literal data + a handler token
        reg = _reg(("t", _C_passthrough_handler))
        engine = _engine(reg)
        marking = _marking(src=[_tok("task", i=0)], dst=[])
        # when: firing the templated produce
        result_marking, _record = engine.fire(
            _C_PASSTHROUGH_WIN_NET, marking, "t", attempt=0
        )
        # then: the deposited token is the handler's, not the template's
        dst_toks = list(result_marking.get("dst", []))
        assert len(dst_toks) == 1
        assert dst_toks[0].data == {"from_handler": 1}

    def test_wrong_type_violation_routes_exclusively_to_record_deposit_violation(self):
        """A wrong-type deposit violation emits through
        ``record_deposit_violation`` only — ZERO records through
        ``record_firing`` — so each attempt occupies exactly one journal slot.

        Bite (reversion-verified): overriding ``Engine._handle_violation`` to
        call ``self._emit_firing(failed_record)`` instead of
        ``self._emit_violation(failed_record)`` flips the routing —
        ``len(journal.firings) == 1`` and ``len(journal.violations) == 0``,
        so both assertions fail. Confirmed bites."""
        # given: the wrong-type net + a capturing journal in drop mode
        reg = _reg(("t", _C_wrong_type_handler))
        journal = _CapturingJournal()
        engine = _engine(reg, journal=journal, deposit_violation="record_then_drop")
        marking = _marking(src=[_tok("task", i=0)], dst=[])
        # when: firing the mistyped deposit
        engine.fire(_C_WRONG_TYPE_NET, marking, "t", attempt=0)
        # then: the violation routed exclusively to the deposit-violation hook
        assert len(journal.firings) == 0
        assert len(journal.violations) == 1


# ── Cluster D (tag `D`): record fidelity & routing (F8, F9, F10, F11, F12).
#    Module-level nets + handlers, then the `TestRecordFidelity`
#    (F8, F9, F10) and `TestRecordRouting` (F11, F12) BDD classes. No
#    imports/fixtures — the shared header owns those. ─────────────────


# F8 / F10 / F11 — minimal consume/produce passthrough net: src
# --(consume task)--> t --(produce task->dst)--> dst. The handler echoes
# the bound src token to dst, so a completed fire deposits {dst:[tok]}.
# `name` is controlled so F10's `netId` assertion is against a known value.
_D_COMPLETED_IO_NET = _net(
    {
        "name": "completed-io-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


# F9 / F12 — three failure nets, identical minimal structure; the cause
# differs by engine wiring (handler registered vs not) and marking. Each
# is a distinct module-level net so the cause is visible at the call site.
_D_FAILED_HANDLER_NET = _net(
    {
        "name": "failed-handler-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
        ],
    }
)
_D_RESOLVE_MISS_NET = _net(
    {
        "name": "resolve-miss-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
        ],
    }
)
_D_NOT_ENABLED_NET = _net(
    {
        "name": "not-enabled-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
        ],
    }
)


def _D_passthrough(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Echo the bound `src` tokens to `dst` — the completed path (F8/F10/F11)."""
    return {
        "status": "completed",
        "outputTokens": {"dst": list(inp["inputTokens"].get("src", []))},
        "error": None,
        "metadata": {},
    }


def _D_failed(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    """Return a structured failure — the handler-`failed` cause (F9/F12)."""
    return {
        "status": "failed",
        "outputTokens": {},
        "error": {"type": "X", "message": "nope"},
        "metadata": {},
    }


class TestRecordFidelity:
    """Lock for the content (F8, F9) and discrete fields (F10) of emitted
    `FiringRecord`s — the replay-critical payload the journal stores."""

    def test_completed_record_carries_the_binding_as_input_and_the_deposit_as_output(
        self,
    ):
        """F8 — a COMPLETED record's `inputTokens == the resolved binding`
        and `outputTokens == the deposited tokens` (replay-critical content).

        Bite (reversion-verified): override `Engine._record` to zero
        `input_tokens`/`output_tokens` (pass `{}` for both) — the completed
        record then carries empty I/O, so the
        `inputTokens == {"src":[tok0]}` and `outputTokens == {"dst":[tok0]}`
        assertions fail.
        """
        # given: a minimal passthrough net with one bound src token and a
        #        capturing journal
        tok0 = _tok("task", i=0)
        marking = _marking(src=[tok0])
        journal = _CapturingJournal()
        engine = _engine(_reg(("t", _D_passthrough)), journal=journal)
        # when: the transition fires to completion
        _returned, record = engine.fire(_D_COMPLETED_IO_NET, marking, "t", attempt=0)
        # then: the record's inputTokens == the binding, outputTokens == the deposit
        assert record["status"] == "completed"
        assert record["inputTokens"] == {"src": [tok0]}
        assert record["outputTokens"] == {"dst": [tok0]}
        # and: the journal captured that same completed record
        assert len(journal.firings) == 1
        assert journal.firings[0]["inputTokens"] == {"src": [tok0]}
        assert journal.firings[0]["outputTokens"] == {"dst": [tok0]}

    def test_failed_record_from_handler_failed_carries_empty_input_and_output(self):
        """F9 (handler-failed) — a FAILED record carries `inputTokens=={}`
        and `outputTokens=={}` when the handler returns `status:"failed"`.

        Bite (reversion-verified): override `Engine._fail` to pass a
        non-empty `input_tokens` (e.g. `{"src":[tok]}`) — the failed record's
        `inputTokens != {}`, so the empty-IO assertions fail.
        """
        # given: the failed-handler net with the failing handler registered
        marking = _marking(src=[_tok("task", i=0)])
        engine = _engine(_reg(("t", _D_failed)))
        # when: the transition fires and the handler fails
        _returned, record = engine.fire(_D_FAILED_HANDLER_NET, marking, "t", attempt=0)
        # then: the record is failed with empty I/O
        assert record["status"] == "failed"
        assert record["inputTokens"] == {}
        assert record["outputTokens"] == {}

    def test_failed_record_from_resolve_miss_carries_empty_input_and_output(self):
        """F9 (resolve-miss) — a FAILED record carries `inputTokens=={}`
        and `outputTokens=={}` when the transition's handler ref is
        unresolved (no handler registered -> HandlerNotFound).

        Bite (reversion-verified): override `Engine._fail` to pass a
        non-empty `input_tokens` (e.g. `{"src":[tok]}`) — the failed record's
        `inputTokens != {}`, so the empty-IO assertions fail.
        """
        # given: the resolve-miss net with NO handler registered (bare engine)
        marking = _marking(src=[_tok("task", i=0)])
        engine = _bare_engine()
        # when: the transition fires and the handler ref is unresolved
        _returned, record = engine.fire(_D_RESOLVE_MISS_NET, marking, "t", attempt=0)
        # then: the record is failed with empty I/O
        assert record["status"] == "failed"
        assert record["inputTokens"] == {}
        assert record["outputTokens"] == {}

    def test_failed_record_from_not_enabled_carries_empty_input_and_output(self):
        """F9 (not-enabled) — a FAILED record carries `inputTokens=={}`
        and `outputTokens=={}` when the transition has a consume arc but
        its source place is empty (no satisfiable binding).

        Bite (reversion-verified): override `Engine._fail` to pass a
        non-empty `input_tokens` (e.g. `{"src":[tok]}`) — the failed record's
        `inputTokens != {}`, so the empty-IO assertions fail.
        """
        # given: the not-enabled net with an empty source place (bare engine)
        marking = _marking(src=[])
        engine = _bare_engine()
        # when: the transition fires but is not enabled
        _returned, record = engine.fire(_D_NOT_ENABLED_NET, marking, "t", attempt=0)
        # then: the record is failed with empty I/O
        assert record["status"] == "failed"
        assert record["inputTokens"] == {}
        assert record["outputTokens"] == {}

    def test_record_carries_net_id_transition_and_attempt_discrete_fields(self):
        """F10 — `record.netId == net.name`, `record.transition == transition`,
        `record.attempt == <the fire's attempt arg>` (here attempt=2).

        Bite (reversion-verified): override `Engine._record` to OMIT
        `netId`/`transition`/`attempt` from the constructed `FiringRecord` —
        `record["netId"]` raises KeyError, so the field assertions fail.
        """
        # given: a minimal passthrough net fired at attempt=2
        tok0 = _tok("task", i=0)
        marking = _marking(src=[tok0])
        engine = _engine(_reg(("t", _D_passthrough)))
        # when: the transition fires at attempt=2
        _returned, record = engine.fire(_D_COMPLETED_IO_NET, marking, "t", attempt=2)
        # then: the discrete fields carry the net id, transition, and attempt
        assert record["netId"] == _D_COMPLETED_IO_NET.name
        assert record["transition"] == "t"
        assert record["attempt"] == 2


class TestRecordRouting:
    """Lock for the hook routing of completed (F11) and failed (F12) records:
    each routes to `record_firing`, NEVER `record_deposit_violation`."""

    def test_completed_fire_routes_one_record_to_firing_and_zero_to_violation(self):
        """F11 — a completed fire emits exactly one `record_firing` and ZERO
        `record_deposit_violation`.

        Bite (reversion-verified): override `Engine._emit_firing` to route to
        `record_deposit_violation` instead — `firings==0` and `violations==1`,
        so the `firings==1` / `violations==0` assertions fail.
        """
        # given: a minimal passthrough net with a capturing journal
        tok0 = _tok("task", i=0)
        marking = _marking(src=[tok0])
        journal = _CapturingJournal()
        engine = _engine(_reg(("t", _D_passthrough)), journal=journal)
        # when: the transition fires to completion
        engine.fire(_D_COMPLETED_IO_NET, marking, "t", attempt=0)
        # then: exactly one firing, zero violations
        assert len(journal.firings) == 1
        assert len(journal.violations) == 0

    def test_handler_failed_fire_routes_to_firing_with_zero_violations(self):
        """F12 (handler-failed) — a handler-`failed` fire routes its record to
        `record_firing`, NEVER `record_deposit_violation`.

        Bite (reversion-verified): override `Engine._emit_firing` to route to
        `record_deposit_violation` instead — `firings==0` and `violations==1`,
        so the `firings==1` / `violations==0` assertions fail.
        """
        # given: the failed-handler net with a capturing journal
        marking = _marking(src=[_tok("task", i=0)])
        journal = _CapturingJournal()
        engine = _engine(_reg(("t", _D_failed)), journal=journal)
        # when: the transition fires and the handler fails
        engine.fire(_D_FAILED_HANDLER_NET, marking, "t", attempt=0)
        # then: exactly one firing, zero violations
        assert len(journal.firings) == 1
        assert len(journal.violations) == 0

    def test_resolve_miss_fire_routes_to_firing_with_zero_violations(self):
        """F12 (resolve-miss) — an unresolved-handler fire routes its record
        to `record_firing`, NEVER `record_deposit_violation`.

        Bite (reversion-verified): override `Engine._emit_firing` to route to
        `record_deposit_violation` instead — `firings==0` and `violations==1`,
        so the `firings==1` / `violations==0` assertions fail.
        """
        # given: the resolve-miss net (bare engine) with a capturing journal
        marking = _marking(src=[_tok("task", i=0)])
        journal = _CapturingJournal()
        engine = _bare_engine(journal=journal)
        # when: the transition fires and the handler ref is unresolved
        engine.fire(_D_RESOLVE_MISS_NET, marking, "t", attempt=0)
        # then: exactly one firing, zero violations
        assert len(journal.firings) == 1
        assert len(journal.violations) == 0

    def test_not_enabled_fire_routes_to_firing_with_zero_violations(self):
        """F12 (not-enabled) — a not-enabled fire routes its record to
        `record_firing`, NEVER `record_deposit_violation`.

        Bite (reversion-verified): override `Engine._emit_firing` to route to
        `record_deposit_violation` instead — `firings==0` and `violations==1`,
        so the `firings==1` / `violations==0` assertions fail.
        """
        # given: the not-enabled net with an empty source place and a journal
        marking = _marking(src=[])
        journal = _CapturingJournal()
        engine = _bare_engine(journal=journal)
        # when: the transition fires but is not enabled
        engine.fire(_D_NOT_ENABLED_NET, marking, "t", attempt=0)
        # then: exactly one firing, zero violations
        assert len(journal.firings) == 1
        assert len(journal.violations) == 0


# ── Parallel produce-template contracts ─────────────────────────────────


def _parallel_produce_net(
    name: str, templates: list[dict[str, Any]], accepted_types: list[str]
) -> Net:
    """Build a source transition with declaration-ordered parallel outputs."""
    return _net(
        {
            "name": name,
            "places": [{"name": "dst", "accepts": accepted_types}],
            "transitions": [{"name": "emit", "handler": "emit"}],
            "arcs": [
                {
                    "from": {"transition": "emit"},
                    "to": {"place": "dst"},
                    "produce": {
                        "type": template["type"],
                        "destination": "dst",
                        "data": template["data"],
                    },
                }
                for template in templates
            ],
        }
    )


class TestParallelProduceTemplates:
    """Parallel templates preserve every declaration and validate by type."""

    def test_same_destination_different_types_accepts_earlier_handler_type(self):
        """An earlier-type handler output wins only its matching fallback.

        Reversion bite: destination-keying ``_produce_templates`` silently
        keeps only the final ``beta`` template, so the handler's valid
        ``alpha`` tokens become a deposit violation and its declaration plus
        the ``beta`` literal fallback cannot both survive.
        """
        # given: alpha then beta templates share dst, each with literal data
        net = _parallel_produce_net(
            "parallel-different-types",
            [
                {"type": "alpha", "data": {"source": "alpha-fallback"}},
                {"type": "beta", "data": {"source": "beta-fallback"}},
            ],
            ["alpha", "beta"],
        )
        supplied = [_tok("alpha", order=2), _tok("alpha", order=1)]

        def emit(_inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            return {
                "status": "completed",
                "outputTokens": {"dst": supplied},
                "error": None,
                "metadata": {},
            }

        journal = _CapturingJournal()
        engine = _engine(_reg(("emit", emit)), journal=journal)
        existing = _tok("beta", existing=True)
        marking = _marking(dst=[existing])
        # when: firing with alpha handler tokens but no beta handler token
        new_marking, record = engine.fire(net, marking, "emit", attempt=0)
        beta_fallback = _tok("beta", source="beta-fallback")
        # then: handler order is preserved, followed by the unmatched literal
        assert list(new_marking["dst"]) == [
            existing,
            supplied[0],
            supplied[1],
            beta_fallback,
        ]
        # and: the original persistent marking was not mutated
        assert list(marking["dst"]) == [existing]
        # and: the journal record describes exactly the deposited tokens
        assert record["outputTokens"] == {
            "dst": [supplied[0], supplied[1], beta_fallback]
        }
        assert journal.firings == [record]

    def test_same_destination_same_type_preserves_literals_then_handler_overrides_all(
        self,
    ):
        """Parallel same-type literals survive, while handler tokens emit once.

        Reversion bite: destination-keying ``_produce_templates`` overwrites
        the first literal, so the no-output firing records only ``second``;
        the paired handler firing also locks against a naive list migration
        that duplicates the same handler tokens once per matching template.
        """
        # given: two same-destination, same-type templates in declaration order
        net = _parallel_produce_net(
            "parallel-same-type",
            [
                {"type": "task", "data": {"literal": "first"}},
                {"type": "task", "data": {"literal": "second"}},
            ],
            ["task"],
        )
        supplied = [_tok("task", order=2), _tok("task", order=1)]

        def emit(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
            output = [] if inp["firingContext"]["attempt"] == 0 else supplied
            return {
                "status": "completed",
                "outputTokens": {"dst": output},
                "error": None,
                "metadata": {},
            }

        engine = _engine(_reg(("emit", emit)))
        marking = _marking(dst=[])
        # when: the handler supplies no tokens for the shared pair
        literal_marking, literal_record = engine.fire(net, marking, "emit", attempt=0)
        # then: every literal fallback emits in template declaration order
        literals = [
            _tok("task", literal="first"),
            _tok("task", literal="second"),
        ]
        assert list(literal_marking["dst"]) == literals
        assert literal_record["outputTokens"] == {"dst": literals}
        # when: the handler supplies ordered tokens for that destination/type pair
        supplied_marking, supplied_record = engine.fire(net, marking, "emit", attempt=1)
        # then: handler tokens win over both fallbacks and emit exactly once
        assert list(supplied_marking["dst"]) == supplied
        assert supplied_record["outputTokens"] == {"dst": supplied}
