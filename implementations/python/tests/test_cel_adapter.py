"""CEL adapter protocol tests.

Tests the adapter protocol that abstracts over cel-python (pure Python),
cel-expr-python (C++ binding), and common-expression-language (Rust binding)
backends. The adapter normalizes all three backends to a common raise-on-error
API: ``compile(expr) -> Any`` and ``eval(compiled, data) -> Any`` (raises
``CelEvalError`` on eval error).

These tests verify the ``velocitron.cel`` adapter protocol — ``CelAdapter``,
``CelpyAdapter``, ``CelExprAdapter``, ``CelRustAdapter``, ``CelEvalError``,
and ``get_default_adapter()`` — against all three backends.
"""

from __future__ import annotations

import pytest
from _cel_adapters import ADAPTER_IDS, cel_expr_available, cel_rust_available
from _cel_adapters import adapters as _adapters

from velocitron.cel import (
    CelAdapter,
    CelEvalError,
    CelExprAdapter,
    CelpyAdapter,
    CelRustAdapter,
    get_default_adapter,
)

# ── Cluster 1: Protocol shape ───────────────────────────────────────────


class TestCelAdapterProtocol:
    """The adapter protocol shape — class existence, inheritance, and the
    ``CelEvalError`` exception."""

    def test_cel_eval_error_is_exception(self):
        """``CelEvalError`` is an ``Exception`` subclass."""
        assert issubclass(CelEvalError, Exception)

    def test_celpy_adapter_is_cel_adapter(self):
        """``CelpyAdapter`` implements the ``CelAdapter`` protocol."""
        assert isinstance(CelpyAdapter(), CelAdapter)

    def test_cel_expr_adapter_is_cel_adapter(self):
        """``CelExprAdapter`` implements the ``CelAdapter`` protocol."""
        assert isinstance(CelExprAdapter(), CelAdapter)

    def test_cel_rust_adapter_is_cel_adapter(self):
        """``CelRustAdapter`` implements the ``CelAdapter`` protocol."""
        assert isinstance(CelRustAdapter(), CelAdapter)


# ── Cluster 2: Compile ──────────────────────────────────────────────────


class TestCelAdapterCompile:
    """``adapter.compile(expr)`` — compiles a CEL expression string.

    Compile validates syntax; free variables are deferred to eval.
    All three backends must accept the same expressions."""

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_compile_simple_literal(self, adapter: CelAdapter) -> None:
        """``compile('1 + 1')`` returns a compiled expression."""
        compiled = adapter.compile("1 + 1")
        assert compiled is not None

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_compile_free_variable_expression(self, adapter: CelAdapter) -> None:
        """``compile('priority > 5')`` with a free variable compiles cleanly —
        compile validates syntax only, not variable resolution."""
        compiled = adapter.compile("priority > 5")
        assert compiled is not None

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_compile_invalid_expression_raises(self, adapter: CelAdapter) -> None:
        """``compile('> > >')`` — a syntactically invalid expression raises."""
        with pytest.raises(Exception):
            adapter.compile("> > >")


# ── Cluster 3: Eval ─────────────────────────────────────────────────────


class TestCelAdapterEval:
    """``adapter.eval(compiled, data)`` — evaluates a compiled expression
    against a data dict. Returns a Python primitive. Raises ``CelEvalError``
    on eval error (missing field, type mismatch, div-by-zero)."""

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_literal_expression(self, adapter: CelAdapter) -> None:
        """``eval(compile('1 + 1'), {})`` returns ``2``."""
        compiled = adapter.compile("1 + 1")
        result = adapter.eval(compiled, {})
        assert result == 2

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_with_data(self, adapter: CelAdapter) -> None:
        """``eval(compile('x > 5'), {'x': 10})`` returns ``True``."""
        compiled = adapter.compile("x > 5")
        result = adapter.eval(compiled, {"x": 10})
        assert result is True

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_with_data_false(self, adapter: CelAdapter) -> None:
        """``eval(compile('x > 5'), {'x': 3})`` returns ``False``."""
        compiled = adapter.compile("x > 5")
        result = adapter.eval(compiled, {"x": 3})
        assert result is False

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_nested_field_selection(self, adapter: CelAdapter) -> None:
        """``eval(compile('a.b == 1'), {'a': {'b': 1}})`` — nested field
        selection over a plain-dict activation value works on every backend.
        celpy requires the adapter's ``json_to_cel`` activation conversion (a
        plain dict "does not support field selection"); the C++/Rust backends
        handle plain nested dicts natively. The correlated-inhibit activation
        (``{token, binding}``, ADR 0017) relies on this."""
        compiled = adapter.compile("a.b == 1")
        assert adapter.eval(compiled, {"a": {"b": 1}}) is True
        assert adapter.eval(compiled, {"a": {"b": 2}}) is False

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_missing_field_raises_cel_eval_error(
        self,
        adapter: CelAdapter,
    ) -> None:
        """``eval(compile('missing > 0'), {})`` — a missing field raises
        ``CelEvalError``, not a return-value error."""
        compiled = adapter.compile("missing > 0")
        with pytest.raises(CelEvalError):
            adapter.eval(compiled, {})

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_type_mismatch_raises_cel_eval_error(
        self,
        adapter: CelAdapter,
    ) -> None:
        """``eval(compile('x > 5'), {'x': 'not_a_number'})`` — a type mismatch
        raises ``CelEvalError``."""
        compiled = adapter.compile("x > 5")
        with pytest.raises(CelEvalError):
            adapter.eval(compiled, {"x": "not_a_number"})

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_division_by_zero_raises_cel_eval_error(
        self,
        adapter: CelAdapter,
    ) -> None:
        """``eval(compile('1 / 0'), {})`` — division by zero raises
        ``CelEvalError``."""
        compiled = adapter.compile("1 / 0")
        with pytest.raises(CelEvalError):
            adapter.eval(compiled, {})

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_bool_result_is_python_bool(self, adapter: CelAdapter) -> None:
        """``eval(compile('true'), {})`` returns a Python ``bool``, not a
        wrapper object — ``bool(result)`` works correctly."""
        compiled = adapter.compile("true")
        result = adapter.eval(compiled, {})
        assert result is True
        assert isinstance(result, bool)

    @pytest.mark.parametrize("adapter", _adapters(), ids=ADAPTER_IDS)
    def test_eval_int_zero_is_falsy(self, adapter: CelAdapter) -> None:
        """``eval(compile('0'), {})`` returns ``0`` — ``bool(0)`` is ``False``.
        This preserves the CE6-deferred bool-coercion behavior: a bare field
        ``"cel": "count"`` returning 0 → ``bool(0)`` → ``False``."""
        compiled = adapter.compile("0")
        result = adapter.eval(compiled, {})
        assert result == 0
        assert bool(result) is False


# ── Cluster 4: Default adapter auto-detection ───────────────────────────


class TestDefaultAdapter:
    """``get_default_adapter()`` — auto-detects the best available backend
    in the three-tier preference order: Rust → C++ → pure Python."""

    def test_get_default_adapter_returns_cel_adapter(self) -> None:
        """``get_default_adapter()`` returns a ``CelAdapter`` instance."""
        adapter = get_default_adapter()
        assert isinstance(adapter, CelAdapter)

    def test_get_default_adapter_is_cel_rust_when_available(self) -> None:
        """When common-expression-language is installed, the default is
        ``CelRustAdapter`` (highest preference)."""
        if not cel_rust_available():
            pytest.skip("common-expression-language is not installed")
        adapter = get_default_adapter()
        assert isinstance(adapter, CelRustAdapter)

    def test_get_default_adapter_is_cel_expr_when_rust_absent_and_cpp_present(
        self,
    ) -> None:
        """When cel-expr-python is installed and the Rust backend is absent,
        the default is ``CelExprAdapter`` (middle preference)."""
        if cel_rust_available():
            pytest.skip(
                "common-expression-language is installed — default is CelRustAdapter"
            )
        if not cel_expr_available():
            pytest.skip("cel-expr-python is not installed")
        adapter = get_default_adapter()
        assert isinstance(adapter, CelExprAdapter)

    def test_get_default_adapter_is_celpy_when_no_optional_installed(
        self,
    ) -> None:
        """When neither optional backend is installed, the default is
        ``CelpyAdapter`` (fallback)."""
        if cel_expr_available() or cel_rust_available():
            pytest.skip("an optional CEL backend is installed")
        adapter = get_default_adapter()
        assert isinstance(adapter, CelpyAdapter)
