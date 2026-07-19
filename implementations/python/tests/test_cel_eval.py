"""CEL expression evaluation lock-the-coverage tests (``[lock] CEL expression
evaluation (inline predicates)``).

The coverage-*lock* for the CEL-evaluation surface of
``spec/firing-semantics.md`` D6 and ``spec/net-schema.md``'s predicate prose
(ADR 0002). The CEL behavioral code already exists on ``main`` (co-evolved
during ``firing-semantics`` / ``net-schema``), spanning two modules:

- ``parser.py`` — ``_validate_cel``: compiles each inline CEL predicate *at
  parse time* (D6 first half); a syntax/compile error raises
  ``NetValidationError`` (the net is malformed). Compile-only — no
  ``evaluate`` — so free-variable expressions parse cleanly (variable
  resolution is deferred to the engine).
- ``engine.py`` — ``_compile_cel`` / ``_eval_predicate``: the D6 second half.
  A CEL predicate is evaluated against ``token.data``; a runtime eval error ⇒
  predicate ``False``, not a crash (D6); ``None`` predicate ⇒ ``True``.

Per the lock-feature gate (AGENTS.md), no behavioral change to ``parser.py``
or ``engine.py`` is sanctioned — the surface already conforms to D6 / ADR
0002. This is a pure coverage lock: the deliverable is biting tests that pin
the compile-at-parse timing (CE1/CE2), the fire-surface degrade (CE3), and
the single-token-data-scope combinatorial (CE5). Each test passes against the
existing green impl and was verified to bite under a targeted reversion that
breaks the invariant it pins.

Scope boundary: the ``(engine-enablement)`` lock owns the enablement-probe
CEL degrade (C2 in ``test_enablement.py`` probes
``enabled_transitions`` / ``select_binding``, never ``fire``) and the
inhibit+CEL combinatorial. This lock does NOT re-pin those; CE3 pins the
``fire``-surface degrade and CE5 pins the two-place cross-place-ref
combinatorial the enablement lock's single-place C2 never constructs. The
``(firing-engine)`` lock owns ``fire()`` record *content* generally; CE3 pins
only the CEL-specific fire-level outcome (``NotEnabled`` + marking-unchanged).

These tests are pure computation — no ``tmp_path`` / journal / firing needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter
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
    cel_adapter: CelAdapter | None = None,
) -> Net:
    """Build a minimal net dict and parse it.

    Each place accepts a single type defaulting to its own name; override a
    place's accepted types via ``accepts``. Mirrors the ``test_enablement.py``
    helper so the lock net dicts stay short while staying explicit about
    accepted types (the parser enforces consume-type ∈ place-accepts).
    """
    place_dicts = [{"name": p, "accepts": (accepts or {}).get(p, [p])} for p in places]
    return parse_net(
        {"name": name, "places": place_dicts, "transitions": transitions, "arcs": arcs},
        cel_adapter=cel_adapter,
    )


def _tok(t: str = "task", **data: Any) -> Token:
    """A token of type ``t`` with the given data fields."""
    return Token(type=t, data=dict(data))


def _bare_engine(cel_adapter: CelAdapter | None = None) -> Engine:
    """An engine with no registered handlers — for CEL-degrade / scope probing.

    CE3/CE5 probe the not-enabled path (a CEL eval error ⇒ predicate false ⇒
    arc unsatisfiable ⇒ not enabled), which never invokes a transition
    handler, so an empty-registry engine is the minimal fixture. Mirrors the
    ``test_enablement.py`` / ``test_firing_engine.py`` shared-fixture
    convention.
    """
    return Engine(HandlerRegistry(), cel_adapter=cel_adapter)


class _RecordingAdapter:
    """Minimal semantic adapter that exposes engine compile/eval delegation."""

    def __init__(self) -> None:
        self.compile_calls: list[str] = []
        self.eval_calls: list[tuple[str, dict[str, Any]]] = []

    def compile(self, expr: str) -> str:
        self.compile_calls.append(expr)
        return expr

    def eval(self, compiled: str, data: dict[str, Any]) -> Any:
        self.eval_calls.append((compiled, data))
        if compiled == 'source == "co2mon" && active':
            return data.get("source") == "co2mon" and data.get("active") is True
        raise RuntimeError("recording adapter rejects this data shape")

    def reset(self) -> None:
        self.compile_calls.clear()
        self.eval_calls.clear()


def _predicate_net(expr: str, adapter: CelAdapter) -> Net:
    """Build one consume arc for predicate fast-path enablement probes."""
    return _net(
        "predicate-fast-path",
        places=["src"],
        transitions=[{"name": "t", "handler": "t"}],
        arcs=[
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "sample", "predicate": {"cel": expr}},
            }
        ],
        accepts={"src": ["sample"]},
        cel_adapter=adapter,
    )


# ── Cluster 1: Parser — compile-at-parse timing (D6 first half) ─────────


class TestCelEvalParserTiming:
    """CE1, CE2 — the two faces of compile-at-parse. CE1 pins that a
    syntactically INVALID CEL expression fails PARSING (the error surfaces
    from ``parse_net``, not deferred to ``fire`` / ``enabled_transitions``).
    CE2 pins that a syntactically VALID CEL expression with free variables
    parses CLEANLY: compile-at-parse validates SYNTAX only, not variable
    resolution against token ``data``. The co-evolution
    ``test_parser.py::TestPredicates`` tests
    (``test_valid_cel_compiles_at_parse``,
    ``test_invalid_cel_rejected_at_parse``) are outcome-only and not
    bite-verified; CE1/CE2 pin the where/when + bite."""

    @staticmethod
    def _cel_consume_net(cel: str, *, cel_adapter: CelAdapter | None = None) -> Net:
        """A minimal net whose single consume arc carries the given CEL
        predicate. No produce arc — the assertion is at parse, and a produce
        arc is never resolved at parse time (it would be decorative)."""
        return _net(
            "cel-parse-net",
            places=["inbox"],
            transitions=[{"name": "proc", "handler": "proc"}],
            arcs=[
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "proc"},
                    "consume": {"type": "msg", "predicate": {"cel": cel}},
                },
            ],
            accepts={"inbox": ["msg"]},
            cel_adapter=cel_adapter,
        )

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_invalid_cel_fails_parsing_as_net_validation_error(
        self, adapter: CelAdapter
    ) -> None:
        """CE1 — a syntactically invalid CEL expression (``priority > > 5``)
        fails PARSING: ``parse_net`` raises ``NetValidationError``, and the
        error is NOT deferred to ``fire`` / ``enabled_transitions``. The test
        calls only ``parse_net``, never ``fire``, so a deferred-to-fire
        compile would not raise here and the test would fail — catching a
        silent timing deferral.

        Reversion-verified bite: reverting ``_validate_cel`` to a no-op
        (``return`` without compiling) lets the invalid-CEL net parse cleanly
        → ``parse_net`` no longer raises → the ``pytest.raises`` assertion
        fails. Confirmed-bites."""
        # given: a net whose consume arc carries a syntactically invalid CEL
        # expression (double comparison operator)
        # when/then: parsing raises NetValidationError — the compile error
        # surfaces at parse, not deferred to fire/enabled_transitions
        with pytest.raises(NetValidationError):
            self._cel_consume_net("priority > > 5", cel_adapter=adapter)

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_free_variable_cel_parses_cleanly_compile_validates_syntax_only(
        self, adapter: CelAdapter
    ) -> None:
        """CE2 — a syntactically valid CEL expression with a free variable
        (``priority > 5``, no token supplies ``priority`` at parse time)
        parses CLEANLY: compile-at-parse validates SYNTAX only, not variable
        resolution against token ``data``. Variable resolution is deferred to
        the engine (where a missing binding degrades to predicate false, D6).

        Reversion-verified bite: reverting ``_validate_cel`` to evaluate the
        compiled program against an empty binding
        (``adapter.compile(expr)`` then ``adapter.eval(compiled, {})``) makes
        ``priority > 5`` raise ``CelEvalError`` at parse → the parser raises
        instead of accepting the net → the "parses cleanly" assertion fails.
        Confirmed-bites."""
        # given: a net whose consume arc carries a free-variable CEL expression
        # (valid syntax, but `priority` is not bound at parse time)
        # when: parsing the net
        net = self._cel_consume_net("priority > 5", cel_adapter=adapter)
        # then: it parses cleanly — compile-at-parse validates syntax only,
        # not variable resolution against token data
        assert net is not None
        # and: the CEL predicate is preserved on the consume arc
        arc = net.arcs[0]
        assert arc.consume is not None
        assert arc.consume.predicate is not None
        assert arc.consume.predicate.cel == "priority > 5"


# ── Cluster 2: Engine — fire-surface degrade (D6 second half) ───────────


class TestCelEvalFireDegradation:
    """CE3 — a CEL predicate that raises at eval (an undeclared reference
    against the candidate token's ``data``) ⇒ ``fire`` returns a ``NotEnabled``
    failed record with the marking UNCHANGED, not a crash. This pins the
    ``fire``-surface degrade, distinct from the enablement-probe degrade
    owned by ``test_enablement.py`` C2 (which probes
    ``enabled_transitions`` / ``select_binding``, never ``fire``)."""

    @staticmethod
    def _failing_cel_net(*, cel_adapter: CelAdapter | None = None) -> Net:
        """A minimal net with one consume arc carrying a CEL predicate that
        references a field the candidate token does not carry. No produce arc
        — the not-enabled path deposits nothing, so a produce arc would be
        decorative. No handler is registered (the not-enabled path returns
        before handler resolution)."""
        return _net(
            "cel-fire-net",
            places=["src"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "src"},
                    "to": {"transition": "t"},
                    "consume": {"type": "task", "predicate": {"cel": "missing > 0"}},
                },
            ],
            accepts={"src": ["task"]},
            cel_adapter=cel_adapter,
        )

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_eval_error_at_fire_returns_not_enabled_with_marking_unchanged(
        self, adapter: CelAdapter
    ) -> None:
        """CE3 — a CEL predicate that raises at eval (``missing > 0`` against a
        token with ``data={}``) ⇒ ``fire`` returns a ``NotEnabled`` failed
        record and the marking UNCHANGED (atomic rollback — the
        degrade-to-false path does not consume), not a crash.

        Construction-bite: the ``try/except`` around ``adapter.eval()`` in
        ``_eval_predicate`` is the sole barrier — removing it makes
        ``_select_binding`` (called by ``fire``) raise instead of returning
        ``None``, so ``fire`` raises instead of returning the
        ``(marking, NotEnabled-record)`` pair. The marking-unchanged
        assertion (``returned_marking is input_marking``) is the fire-level
        rollback face: the not-enabled branch returns the original
        ``marking`` reference, so structural-sharing identity pins the
        rollback (sibling to the marking-data lock's run-level ``is``-identity
        invariant) — it is NOT a trailing assertion subsumed by the
        ``error.type`` check (the two probe distinct invariants: the error
        type vs the rollback identity). Confirmed-bites."""
        # given: a net whose consume arc has a CEL predicate referencing a
        # field the token does not carry, with a bare engine and a present token
        net = self._failing_cel_net(cel_adapter=adapter)
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking({"src": [_tok("task")]})  # data={}, no 'missing' field
        # when: firing the transition
        returned_marking, record = engine.fire(net, marking, "t", attempt=0)
        # then: no exception (the try/except around adapter.eval() in
        # _eval_predicate degrades the CEL eval error to predicate false), the record is failed NotEnabled
        assert record["status"] == "failed"
        assert record["error"]["type"] == "NotEnabled"  # pyright: ignore[reportOptionalSubscript]
        # and: the marking is UNCHANGED — the returned marking IS the input
        # marking object (atomic rollback of the not-enabled path)
        assert returned_marking is marking


# ── Cluster 3: Engine — single-token data scope (ADR 0002) ──────────────


class TestCelEvalSingleTokenScope:
    """CE5 — a CEL predicate sees ONLY the candidate token's ``data``, not the
    full input binding across places (ADR 0002 single-token purity). A CEL
    expression referencing a field present in ANOTHER place's token (but
    absent from the candidate token's ``data``) ⇒ eval error ⇒ predicate
    false ⇒ the arc unsatisfiable ⇒ not enabled. This constructs the
    two-place cross-place-ref combinatorial the enablement lock's single-place
    C2 never constructs."""

    @staticmethod
    def _cross_place_ref_net(*, cel_adapter: CelAdapter | None = None) -> Net:
        """A minimal net with two places and two consume arcs: from ``a``
        carrying a CEL predicate referencing ``shared``, and from ``b`` (no
        predicate). No produce arc — the assertion is at enablement, not
        deposit. The cross-place-ref combinatorial requires both arcs."""
        return _net(
            "cel-scope-net",
            places=["a", "b"],
            transitions=[{"name": "t", "handler": "t"}],
            arcs=[
                {
                    "from": {"place": "a"},
                    "to": {"transition": "t"},
                    "consume": {"type": "ta", "predicate": {"cel": "shared > 0"}},
                },
                {
                    "from": {"place": "b"},
                    "to": {"transition": "t"},
                    "consume": {"type": "tb"},
                },
            ],
            accepts={"a": ["ta"], "b": ["tb"]},
            cel_adapter=cel_adapter,
        )

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    def test_cel_predicate_sees_only_candidate_token_data_not_cross_place(
        self, adapter: CelAdapter
    ) -> None:
        """CE5 — a CEL predicate on the ``a``-arc (``shared > 0``) sees ONLY
        the candidate token's ``data``. The ``a`` token carries no ``shared``
        field; the ``b`` token does (``data={"shared": 1}``). Because CEL is
        scoped to the candidate token's ``data`` only, the cross-place
        reference is undeclared ⇒ eval error ⇒ predicate false ⇒ the ``a``-arc
        is unsatisfiable ⇒ ``t`` is NOT enabled. The referenced field IS
        present in the marking (in ``b``'s token), so a scope-widening
        reversion would resolve it — this constructs the combinatorial
        topology that makes the bite faithful.

        Reversion-verified bite: reverting ``_eval_predicate`` to pass a
        merged/flattened token-data map across all bound tokens (so CEL sees
        ``b``'s ``shared`` field while evaluating the ``a``-arc's predicate)
        makes the cross-place reference resolve → predicate true → the
        ``a``-arc satisfiable → ``t`` enabled → the "not enabled" assertion
        fails. Confirmed-bites."""
        # given: a net with two consume arcs — `a`-arc with a CEL predicate
        # referencing `shared`, `b`-arc with no predicate — and a marking
        # where `a`'s token lacks `shared` but `b`'s token carries it
        net = self._cross_place_ref_net(cel_adapter=adapter)
        engine = _bare_engine(cel_adapter=adapter)
        marking = Marking(
            {
                "a": [_tok("ta")],  # data={}, no 'shared' field
                "b": [_tok("tb", shared=1)],  # data={"shared": 1}
            }
        )
        # when: querying enablement
        enabled = engine.enabled_transitions(net, marking)
        # then: t is NOT enabled — the a-arc's CEL predicate sees only a's
        # token data (no `shared`) → eval error → predicate false → arc
        # unsatisfiable, even though `shared` is present in b's token
        assert "t" not in enabled
        # and: select_binding returns None, not a binding
        assert engine.select_binding(net, "t", marking) is None


# ── Cluster 4: Engine — exact string-equality fast path ─────────────────


class TestCelExactStringEqualityFastPath:
    """The optimized shape stays exact and delegates every uncertain case."""

    def test_benchmark_predicate_matches_and_rejects_without_adapter_eval(self) -> None:
        # given: the benchmark fixture's exact production predicate
        adapter = _RecordingAdapter()
        net = _predicate_net('source == "co2mon"', adapter)
        adapter.reset()
        engine = _bare_engine(adapter)

        # when/then: the matching string enables the transition
        assert (
            engine.select_binding(
                net, "t", Marking({"src": [_tok("sample", source="co2mon")]})
            )
            is not None
        )
        # and: an unmatched string does not
        assert (
            engine.select_binding(
                net, "t", Marking({"src": [_tok("sample", source="weather")]})
            )
            is None
        )
        # and: parsed operands are cached while the CEL adapter remains unused
        assert engine._cel_string_equalities == {  # pyright: ignore[reportPrivateUsage]
            'source == "co2mon"': ("source", "co2mon")
        }
        assert adapter.compile_calls == []
        assert adapter.eval_calls == []

    @pytest.mark.parametrize(
        "reserved",
        (
            "false",
            "in",
            "null",
            "true",
            "as",
            "break",
            "const",
            "continue",
            "else",
            "for",
            "function",
            "if",
            "import",
            "let",
            "loop",
            "package",
            "namespace",
            "return",
            "var",
            "void",
            "while",
        ),
    )
    def test_reserved_word_equality_delegates_to_adapter(self, reserved: str) -> None:
        # given: token data has a same-named field that CEL cannot read as a bare identifier
        adapter = _RecordingAdapter()
        expr = f'{reserved} == "token-field"'
        net = _predicate_net(expr, adapter)
        adapter.reset()
        engine = _bare_engine(adapter)
        data = {reserved: "token-field"}

        # when: enablement evaluates the syntactically reserved expression
        selected = engine.select_binding(
            net, "t", Marking({"src": [Token(type="sample", data=data)]})
        )

        # then: CEL owns reserved-word semantics instead of the token-data fast path
        assert selected is None
        assert adapter.compile_calls == [expr]
        assert adapter.eval_calls == [(expr, data)]

    @pytest.mark.parametrize(
        ("data", "description"),
        [
            ({}, "missing identifier"),
            ({"source": 7}, "non-string value"),
        ],
    )
    def test_missing_or_non_string_data_falls_back_to_adapter(
        self, data: dict[str, Any], description: str
    ) -> None:
        # given: an exact equality whose candidate data is not safely comparable
        adapter = _RecordingAdapter()
        net = _predicate_net('source == "co2mon"', adapter)
        adapter.reset()
        engine = _bare_engine(adapter)

        # when: enablement evaluates the candidate
        selected = engine.select_binding(
            net, "t", Marking({"src": [Token(type="sample", data=data)]})
        )

        # then: the adapter owns missing/type-error semantics and degrades false
        assert selected is None, description
        assert adapter.compile_calls == ['source == "co2mon"']
        assert adapter.eval_calls == [('source == "co2mon"', data)]

    @pytest.mark.parametrize(
        ("expr", "value"),
        [
            (r"""message == 'it\'s ready'""", "it's ready"),
            (r'''path == "C:\\sensors"''', r"C:\sensors"),
        ],
    )
    def test_quote_and_backslash_escapes_preserve_string_value(
        self, expr: str, value: str
    ) -> None:
        # given: a single- or double-quoted exact equality with a safe CEL escape
        adapter = _RecordingAdapter()
        net = _predicate_net(expr, adapter)
        adapter.reset()
        engine = _bare_engine(adapter)
        identifier = "message" if expr.startswith("message") else "path"

        # when/then: the decoded value matches without adapter evaluation
        assert (
            engine.select_binding(
                net,
                "t",
                Marking({"src": [Token(type="sample", data={identifier: value})]}),
            )
            is not None
        )
        assert adapter.compile_calls == []
        assert adapter.eval_calls == []

    @pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
    @pytest.mark.parametrize(
        ("expr", "identifier", "value"),
        [
            (r"""message == 'it\'s ready'""", "message", "it's ready"),
            (r'''path == "C:\\sensors"''', "path", r"C:\sensors"),
        ],
    )
    def test_supported_escapes_match_shipped_backend_semantics(
        self, adapter: CelAdapter, expr: str, identifier: str, value: str
    ) -> None:
        # given: an escaped literal accepted by every shipped CEL adapter
        compiled = adapter.compile(expr)

        # when/then: CEL decodes it to the same string as the fast path
        assert adapter.eval(compiled, {identifier: value}) is True

    def test_complex_expression_uses_adapter(self) -> None:
        # given: a valid CEL conjunction containing an equality subexpression
        adapter = _RecordingAdapter()
        expr = 'source == "co2mon" && active'
        net = _predicate_net(expr, adapter)
        adapter.reset()
        engine = _bare_engine(adapter)
        data = {"source": "co2mon", "active": True}

        # when: enablement evaluates the complex predicate
        selected = engine.select_binding(
            net, "t", Marking({"src": [Token(type="sample", data=data)]})
        )

        # then: exact-shape recognition declines it and delegates to CEL
        assert selected is not None
        assert adapter.compile_calls == [expr]
        assert adapter.eval_calls == [(expr, data)]
