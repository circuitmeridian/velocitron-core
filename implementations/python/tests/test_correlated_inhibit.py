"""Binding-correlated inhibit arc (``correlate``, anti-join) tests.

The red-phase test contract for ADR 0017. An inhibit arc may declare an
optional ``correlate: {cel: "<expr>"}`` inscription making it a per-binding
zero-test: for a candidate binding B, the arc is satisfied iff NO token in its
source place — of the declared ``type``, passing the arc's single-token
``predicate`` (if any) — also satisfies ``correlate`` evaluated over
``{token: <candidate data>, binding: <B as place-keyed data>}``.

These tests pin:

- **Parsing/validation** — ``correlate`` parses on inhibit arcs only (rejected
  on consume/read modes); its CEL is compiled at *parse* time, so invalid CEL
  fails as ``NetValidationError`` at ``parse_net``, not at enablement; the
  ``{cel}`` object shape is enforced by the schema.
- **Anti-join enablement** — per-key dedup: a flag token blocks exactly the
  bindings whose bound key it correlates with, leaving other-key bindings
  enabled (the whole-place zero-test would block them all).
- **Determinism** — a correlated inhibitor *filters* candidate bindings inside
  the existing lexicographic enumeration; it never reorders them: the first
  surviving binding is selected.
- **Composition** — the arc's single-token ``predicate`` narrows which tokens
  are correlation candidates; read-arc tokens are visible in ``binding``.
- **Failure posture** — a ``correlate`` eval error (missing field) fails
  CLOSED: the candidate token is treated as blocking and the binding is
  rejected (degrades toward not-enabled, like a guard raise — D9), never a
  crash. This is deliberately asymmetric with D6's predicate-false rule,
  which on an inhibit test would fail open.
- **Orphan routing** — the motivating anti-join: ``route_to_orphaned`` fires
  exactly for stage tokens with no same-key parent token upstream.
- **Regression** — an inhibit arc WITHOUT ``correlate`` keeps the whole-place
  zero-test semantics (evaluated before binding construction).

The tests fail until ``correlate`` lands in the schema, the parser compiles
and mode-checks it, and the engine evaluates it per candidate binding.

References: ADR 0017; spec/firing-semantics.md (a); spec/net-schema.md.
"""

from __future__ import annotations

from typing import Any

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter
from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.parser import NetValidationError, parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

# ── Shared helpers ──────────────────────────────────────────────────────


def _net(
    name: str,
    places: list[str],
    transitions: list[dict[str, Any]],
    arcs: list[dict[str, Any]],
    accepts: dict[str, list[str]] | None = None,
    *,
    cel_adapter: CelAdapter | None = None,
) -> Net:
    """Build and parse a minimal net; each place accepts its own name by
    default (override via ``accepts``). Mirrors the ``test_enablement`` helper.
    """
    place_dicts = [{"name": p, "accepts": (accepts or {}).get(p, [p])} for p in places]
    return parse_net(
        {
            "name": name,
            "places": place_dicts,
            "transitions": transitions,
            "arcs": arcs,
        },
        cel_adapter=cel_adapter,
    )


def _tok(t: str, **data: Any) -> Token:
    return Token(type=t, data=dict(data))


def _bare_engine(*, cel_adapter: CelAdapter | None = None) -> Engine:
    """An engine with no registered handlers — enablement never invokes a
    transition handler, so pure enablement probes share this empty registry."""
    return Engine(HandlerRegistry(), cel_adapter=cel_adapter)


# The same-account correlation the dedup net defaults to (ADR 0017
# motivation 2). A module constant so the expression is visible at one
# spot and the default argument is not an inline mutable literal.
_SAME_ACCOUNT = {"cel": "token.account == binding.orders[0].account"}


# The per-key dedup net (ADR 0017 motivation 2): apply_mod consumes an order
# and is inhibited by a mod_flag FOR THE SAME ACCOUNT — not by any flag.
def _dedup_net(
    *,
    correlate: dict[str, Any] | None = _SAME_ACCOUNT,
    predicate: dict[str, Any] | None = None,
    cel_adapter: CelAdapter | None = None,
) -> Net:
    inhibit: dict[str, Any] = {"type": "mod_flag", "mode": "inhibit"}
    if correlate is not None:
        inhibit["correlate"] = correlate
    if predicate is not None:
        inhibit["predicate"] = predicate
    return _net(
        "per-key-dedup",
        places=["orders", "mod_flags"],
        transitions=[{"name": "apply_mod", "handler": "apply_mod"}],
        arcs=[
            {
                "from": {"place": "orders"},
                "to": {"transition": "apply_mod"},
                "consume": {"type": "order"},
            },
            {
                "from": {"place": "mod_flags"},
                "to": {"transition": "apply_mod"},
                "consume": inhibit,
            },
        ],
        accepts={
            "orders": ["order"],
            "mod_flags": ["mod_flag"],
        },
        cel_adapter=cel_adapter,
    )


def _order(account: int) -> Token:
    return _tok("order", account=account)


def _flag(account: int, **extra: Any) -> Token:
    return _tok("mod_flag", account=account, **extra)


# ── Parsing & validation ─────────────────────────────────────────────────


class TestCorrelateParsing:
    """``correlate`` parses on inhibit arcs; the parser rejects it elsewhere
    and compiles its CEL at parse time."""

    def test_correlate_parses_on_inhibit_arc(self):
        # given: an inhibit arc declaring a correlate CEL inscription
        net = _dedup_net()
        # then: the inhibit arc carries the correlate expression
        inhibit_arc = next(a for a in net.arcs if a.from_place == "mod_flags")
        assert inhibit_arc.consume is not None
        assert inhibit_arc.consume.correlate == _SAME_ACCOUNT["cel"]

    def test_correlate_absent_by_default(self):
        # given: an inhibit arc with no correlate
        net = _dedup_net(correlate=None)
        # then: correlate is None on the parsed pattern
        inhibit_arc = next(a for a in net.arcs if a.from_place == "mod_flags")
        assert inhibit_arc.consume is not None
        assert inhibit_arc.consume.correlate is None

    def test_correlate_rejected_on_consume_mode_arc(self):
        # given: a consume-mode (default) arc declaring correlate
        # then: parsing raises — correlate is inhibit-only
        with pytest.raises(NetValidationError):
            _net(
                "correlate-on-consume",
                places=["p"],
                transitions=[{"name": "t", "handler": "t"}],
                arcs=[
                    {
                        "from": {"place": "p"},
                        "to": {"transition": "t"},
                        "consume": {
                            "type": "p",
                            "correlate": {"cel": "token.x == 1"},
                        },
                    },
                ],
            )

    def test_correlate_rejected_on_read_mode_arc(self):
        # given: a read-mode arc declaring correlate
        # then: parsing raises — correlate is inhibit-only
        with pytest.raises(NetValidationError):
            _net(
                "correlate-on-read",
                places=["p"],
                transitions=[{"name": "t", "handler": "t"}],
                arcs=[
                    {
                        "from": {"place": "p"},
                        "to": {"transition": "t"},
                        "consume": {
                            "type": "p",
                            "mode": "read",
                            "correlate": {"cel": "token.x == 1"},
                        },
                    },
                ],
            )

    def test_invalid_correlate_cel_fails_at_parse(self):
        # given: an inhibit arc whose correlate CEL is syntactically invalid
        # then: parse_net raises NetValidationError — CEL compiles at PARSE
        # time (D6), not lazily at enablement
        with pytest.raises(NetValidationError):
            _dedup_net(correlate={"cel": "token.account ==== binding"})

    def test_correlate_requires_cel_key(self):
        # given: a correlate object carrying a handler ref instead of cel
        # then: the schema rejects it — correlate is CEL-only (ADR 0017)
        with pytest.raises(NetValidationError):
            _dedup_net(correlate={"handler": "my_correlator"})

    def test_weight_still_rejected_on_correlated_inhibit_arc(self):
        # given: a correlated inhibit arc ALSO declaring weight 2
        # then: the pre-existing D7 rule (weight is rejected on inhibit arcs)
        # survives the new inscription — parsing raises
        with pytest.raises(NetValidationError):
            _net(
                "correlate-with-weight",
                places=["flags"],
                transitions=[{"name": "t", "handler": "t"}],
                arcs=[
                    {
                        "from": {"place": "flags"},
                        "to": {"transition": "t"},
                        "consume": {
                            "type": "flag",
                            "mode": "inhibit",
                            "weight": 2,
                            "correlate": {"cel": "token.v > 5"},
                        },
                    },
                ],
                accepts={"flags": ["flag"]},
            )


# ── Anti-join enablement (per-key dedup) ─────────────────────────────────


class TestCorrelatedInhibitEnablement:
    """A correlated inhibit arc blocks exactly the bindings its matching
    tokens correlate with — the anti-join, not a whole-place zero-test."""

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_flag_blocks_only_the_correlated_account(self, adapter: CelAdapter):
        # given: orders for accounts 1 and 2, and a mod_flag for account 1 only
        net = _dedup_net(cel_adapter=adapter)
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking({"orders": [_order(1), _order(2)], "mod_flags": [_flag(1)]})
        # when: querying enablement and the selected binding
        enabled = engine.enabled_transitions(net, marking)
        binding = engine.select_binding(net, "apply_mod", marking)
        # then: apply_mod IS enabled — account 2's binding is not correlated
        assert "apply_mod" in enabled
        # and: the selected binding is account 2's order (account 1 is blocked)
        assert binding is not None
        assert [t.data["account"] for t in binding["orders"]] == [2]

    def test_flags_for_every_account_disable_the_transition(self):
        # given: orders for accounts 1 and 2, and mod_flags for BOTH accounts
        net = _dedup_net()
        engine = _bare_engine()
        marking = Marking(
            {"orders": [_order(1), _order(2)], "mod_flags": [_flag(1), _flag(2)]}
        )
        # when: querying enablement
        # then: no binding survives the anti-join — apply_mod is not enabled
        assert "apply_mod" not in engine.enabled_transitions(net, marking)
        assert engine.select_binding(net, "apply_mod", marking) is None

    def test_no_flags_selects_first_binding(self):
        # given: orders for accounts 1 and 2, and an empty mod_flags place
        net = _dedup_net()
        engine = _bare_engine()
        marking = Marking({"orders": [_order(1), _order(2)]})
        # when: selecting a binding
        binding = engine.select_binding(net, "apply_mod", marking)
        # then: the first lexicographic binding (account 1) is selected —
        # an empty inhibit place blocks nothing
        assert binding is not None
        assert [t.data["account"] for t in binding["orders"]] == [1]

    def test_uncorrelated_inhibit_stays_whole_place(self):
        # given: the same topology WITHOUT correlate — a plain inhibit arc —
        # and a flag for account 1 only
        net = _dedup_net(correlate=None)
        engine = _bare_engine()
        marking = Marking({"orders": [_order(1), _order(2)], "mod_flags": [_flag(1)]})
        # then: ANY matching token blocks the whole transition (regression:
        # the whole-place zero-test is unchanged for uncorrelated arcs)
        assert "apply_mod" not in engine.enabled_transitions(net, marking)

    def test_predicate_narrows_correlation_candidates(self):
        # given: the correlated inhibit arc ALSO carries a single-token
        # predicate (only active flags count), and account 1's flag is inactive
        net = _dedup_net(predicate={"cel": "active == true"})
        engine = _bare_engine()
        marking = Marking(
            {"orders": [_order(1)], "mod_flags": [_flag(1, active=False)]}
        )
        # when: querying the binding
        binding = engine.select_binding(net, "apply_mod", marking)
        # then: the inactive flag is not a correlation candidate — account 1
        # is NOT blocked (predicate narrows before correlate tests)
        assert binding is not None
        assert [t.data["account"] for t in binding["orders"]] == [1]

        # given: the flag flips to active
        marking_active = Marking(
            {"orders": [_order(1)], "mod_flags": [_flag(1, active=True)]}
        )
        # then: the active flag correlates and blocks account 1
        assert engine.select_binding(net, "apply_mod", marking_active) is None

    def test_read_arc_tokens_visible_in_binding(self):
        # given: a transition reading a config token and inhibited by a flag
        # correlated with the READ token's tenant (read arcs alone form the
        # binding — no consume arc is needed for correlate to see them)
        net = _net(
            "correlate-vs-read",
            places=["config", "flags"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "config"},
                    "to": {"transition": "t"},
                    "consume": {"type": "config", "mode": "read"},
                },
                {
                    "from": {"place": "flags"},
                    "to": {"transition": "t"},
                    "consume": {
                        "type": "flag",
                        "mode": "inhibit",
                        "correlate": {
                            "cel": "token.tenant == binding.config[0].tenant"
                        },
                    },
                },
            ],
            accepts={
                "flags": ["flag"],
            },
        )
        engine = _bare_engine()
        base = {"config": [_tok("config", tenant="acme")]}
        # then: a flag for a DIFFERENT tenant does not block — the read-bound
        # config token is visible to correlate via binding.config
        other = Marking({**base, "flags": [_tok("flag", tenant="globex")]})
        assert "t" in engine.enabled_transitions(net, other)
        # and: a flag for the read token's tenant blocks
        same = Marking({**base, "flags": [_tok("flag", tenant="acme")]})
        assert "t" not in engine.enabled_transitions(net, same)


# ── Determinism: the filter never reorders ───────────────────────────────


class TestCorrelatedInhibitDeterminism:
    """A correlated inhibitor filters candidate bindings within the existing
    lexicographic enumeration order — the first SURVIVING binding is selected,
    never a reordered one."""

    def test_first_surviving_binding_is_selected_in_order(self):
        # given: orders for accounts 1, 2, 3 in insertion order, and a flag
        # blocking account 1 only
        net = _dedup_net()
        engine = _bare_engine()
        marking = Marking(
            {
                "orders": [_order(1), _order(2), _order(3)],
                "mod_flags": [_flag(1)],
            }
        )
        # when: selecting a binding
        binding = engine.select_binding(net, "apply_mod", marking)
        # then: the binding is account 2 — the first candidate AFTER the
        # blocked one in the original enumeration order, not account 3 and
        # not a reordering
        assert binding is not None
        assert [t.data["account"] for t in binding["orders"]] == [2]


# ── Failure posture: eval error fails closed ─────────────────────────────


class _RaisingEvalAdapter:
    """A CEL adapter stub whose ``eval`` always raises.

    The "raise from any backend" face of the fail-closed posture (ADR 0017):
    ``compile`` succeeds (so the net parses), and every evaluation raises a
    plain ``RuntimeError`` — not a ``CelEvalError`` — probing the engine's
    broad catch rather than one backend's error taxonomy.
    """

    def compile(self, expr: str) -> Any:
        return expr

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        raise RuntimeError("backend eval blew up")


class TestCorrelateFailurePosture:
    """A correlate eval error marks the candidate token as blocking — the
    binding is rejected (fail-closed, like a guard raise), never a crash and
    never a silently-enabled transition."""

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_eval_error_degrades_to_not_enabled(self, adapter: CelAdapter):
        # given: a correlate referencing a field the flag token does not carry
        net = _dedup_net(
            correlate={"cel": "token.missing_key == binding.orders[0].account"},
            cel_adapter=adapter,
        )
        engine = _bare_engine(cel_adapter=adapter)
        # and: an order and a (field-less) flag are present
        marking = Marking({"orders": [_order(1)], "mod_flags": [_tok("mod_flag")]})
        # when: querying enablement
        # then: the eval error fails CLOSED — the binding is blocked, the
        # transition is not enabled, and the engine does NOT raise
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "apply_mod", marking) is None

    def test_backend_raise_degrades_to_not_enabled(self):
        # given: a CEL backend whose eval raises a plain RuntimeError on
        # every call — the "raise from any backend" case, independent of any
        # real backend's error taxonomy
        adapter = _RaisingEvalAdapter()
        net = _dedup_net(cel_adapter=adapter)
        engine = _bare_engine(cel_adapter=adapter)
        # and: an order and a same-account flag are present
        marking = Marking({"orders": [_order(1)], "mod_flags": [_flag(1)]})
        # when: querying enablement
        # then: the raise fails CLOSED — not enabled, no crash
        assert engine.enabled_transitions(net, marking) == []
        assert engine.select_binding(net, "apply_mod", marking) is None

    def test_binding_reference_with_empty_binding_fails_closed(self):
        # given: a transition whose ONLY input arc is a correlated inhibit arc
        # referencing a bound place — the candidate binding is empty, so the
        # reference is an eval error for every candidate token
        net = _net(
            "inhibit-only-binding-ref",
            places=["flags"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flags"},
                    "to": {"transition": "t"},
                    "consume": {
                        "type": "flag",
                        "mode": "inhibit",
                        "correlate": {"cel": "token.v == binding.orders[0].v"},
                    },
                },
            ],
            accepts={"flags": ["flag"]},
        )
        engine = _bare_engine()
        # then: with a candidate flag present, the eval error blocks (fail-closed)
        assert "t" not in engine.enabled_transitions(
            net, Marking({"flags": [_tok("flag", v=1)]})
        )
        # and: with no candidate tokens there is nothing to test — enabled
        assert "t" in engine.enabled_transitions(net, Marking())

    def test_token_only_correlate_works_over_empty_binding(self):
        # given: an inhibit-only transition whose correlate references ONLY
        # the candidate token (no binding fields)
        net = _net(
            "inhibit-only-token-ref",
            places=["flags"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "flags"},
                    "to": {"transition": "t"},
                    "consume": {
                        "type": "flag",
                        "mode": "inhibit",
                        "correlate": {"cel": "token.v > 5"},
                    },
                },
            ],
            accepts={"flags": ["flag"]},
        )
        engine = _bare_engine()
        # then: a non-matching flag (v <= 5) does not block
        assert "t" in engine.enabled_transitions(
            net, Marking({"flags": [_tok("flag", v=1)]})
        )
        # and: a matching flag (v > 5) blocks
        assert "t" not in engine.enabled_transitions(
            net, Marking({"flags": [_tok("flag", v=9)]})
        )


# ── Orphan routing (the motivating anti-join, fire/run level) ────────────


def _route_stage_to(dest: str):
    """A handler that forwards its bound stage token into ``dest``."""

    def _handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        tok = inp["inputTokens"]["stage"][0]
        return {
            "status": "completed",
            "outputTokens": {dest: [tok]},
            "error": None,
            "metadata": {},
        }

    return _handler


class TestOrphanRouting:
    """The motivating case (ADR 0017): route_to_orphaned fires exactly for
    stage tokens with NO parent token carrying the same crawl_tag."""

    @staticmethod
    def _orphan_net() -> Net:
        return _net(
            "orphan-routing",
            places=["stage", "parents", "orphaned"],
            transitions=[{"name": "route_to_orphaned", "handler": "route_to_orphaned"}],
            arcs=[
                {
                    "from": {"place": "stage"},
                    "to": {"transition": "route_to_orphaned"},
                    "consume": {"type": "stage_item"},
                },
                {
                    "from": {"place": "parents"},
                    "to": {"transition": "route_to_orphaned"},
                    "consume": {
                        "type": "parent",
                        "mode": "inhibit",
                        "correlate": {
                            "cel": "token.crawl_tag == binding.stage[0].crawl_tag"
                        },
                    },
                },
                {
                    "from": {"transition": "route_to_orphaned"},
                    "to": {"place": "orphaned"},
                    "produce": {"type": "stage_item", "destination": "orphaned"},
                },
            ],
            accepts={
                "stage": ["stage_item"],
                "parents": ["parent"],
                "orphaned": ["stage_item"],
            },
        )

    def test_run_routes_exactly_the_orphaned_stage_tokens(self):
        # given: stage tokens tagged A and B, and a parent for tag A only
        net = self._orphan_net()
        reg = HandlerRegistry()
        reg.register_transition("route_to_orphaned", _route_stage_to("orphaned"))
        engine = Engine(reg)
        marking = Marking(
            {
                "stage": [
                    _tok("stage_item", crawl_tag="A"),
                    _tok("stage_item", crawl_tag="B"),
                ],
                "parents": [_tok("parent", crawl_tag="A")],
            }
        )
        # when: running to quiescence
        final = engine.run(net, marking)
        # then: exactly the orphan (tag B, no same-key parent) was routed
        assert list(final.get("orphaned", [])) == [_tok("stage_item", crawl_tag="B")]
        # and: the parented stage token (tag A) stays in stage — its binding
        # is blocked by the correlated parent, so the net quiesced
        assert list(final.get("stage", [])) == [_tok("stage_item", crawl_tag="A")]
        # and: the parent token is untouched (inhibit consumes nothing)
        assert list(final.get("parents", [])) == [_tok("parent", crawl_tag="A")]

    def test_fire_reports_not_enabled_for_fully_parented_stage(self):
        # given: a single stage token whose crawl_tag HAS a parent
        net = self._orphan_net()
        reg = HandlerRegistry()
        reg.register_transition("route_to_orphaned", _route_stage_to("orphaned"))
        engine = Engine(reg)
        marking = Marking(
            {
                "stage": [_tok("stage_item", crawl_tag="A")],
                "parents": [_tok("parent", crawl_tag="A")],
            }
        )
        # when: forcing a fire
        new_marking, record = engine.fire(net, marking, "route_to_orphaned", attempt=0)
        # then: the fire fails as not-enabled and the marking is unchanged
        assert record["status"] == "failed"
        assert record["error"] is not None and record["error"]["type"] == "NotEnabled"
        assert new_marking is marking
