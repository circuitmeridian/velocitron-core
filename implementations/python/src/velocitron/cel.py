"""CEL backend adapter protocol.

Abstracts over three interchangeable CEL evaluation backends behind one
raise-on-error API so calling code (parser + engine) is backend-agnostic:

- **``cel-python``** (pure Python, imported as ``celpy``) — the default core
  dependency. Always available; no native compilation. Lowest preference.

- **``cel-expr-python``** (a binding to the official C++ ``google/cel-cpp``
  library) — an optional extra (``pip install velocitron[cel-cpp]``).
  Middle preference.

- **``common-expression-language``** (a binding to the Rust
  ``cel-interpreter`` crate, imported as ``cel``) — an optional extra
  (``pip install velocitron[cel-rust]``). Highest preference; auto-selected
  first when installed.

``get_default_adapter()`` auto-detects the best available backend in the
three-tier preference order: Rust → C++ → pure Python.

The adapter normalizes the three backends' divergent error mechanisms to one
contract: ``compile(expr) -> Any`` raises on syntax error, and
``eval(compiled, data) -> Any`` returns a Python primitive or raises
:class:`CelEvalError` on eval error (missing field, type mismatch,
divide-by-zero, indeterminate result). celpy raises ``CELEvalError`` natively;
cel-expr-python returns a ``CelValue(type=ERROR)`` (no raise) which the adapter
converts to a ``CelEvalError`` raise; common-expression-language raises native
``RuntimeError`` / ``TypeError`` / ``ArithmeticError`` which the adapter wraps
in ``CelEvalError`` (it has no return-value ``ERROR``/``UNKNOWN`` state). The
engine's ``_eval_predicate`` ``try/except`` stays the sole barrier — same
pattern, same bite mechanism as the prior celpy-only code.

References: spec/firing-semantics.md (D6).
"""

from __future__ import annotations

import importlib.util
from typing import Any, Protocol, cast, runtime_checkable
import celpy  # pyright: ignore[reportMissingImports]
import celpy.celtypes as _ct_module  # pyright: ignore[reportMissingImports]

# celpy ships no type stubs — alias the celtypes namespace to Any so the
# wrapper-type dispatch in _celpy_to_native is not flagged as unknown-member
# access. cel-python is a core (always-installed) dependency, so it is imported
# at module scope; the optional cel-expr-python / common-expression-language
# extras stay lazily imported inside their adapter methods.
_ct: Any = _ct_module


class CelEvalError(Exception):
    """Raised by an adapter on a CEL runtime evaluation error.

    Normalizes the three backends' eval-error mechanisms to one
    raise-on-error contract: celpy's ``CELEvalError`` (raised);
    cel-expr-python's ``CelValue(type=ERROR)`` / ``CelValue(type=UNKNOWN)``
    (returned, not raised); and common-expression-language's native
    ``RuntimeError`` / ``TypeError`` / ``ArithmeticError`` (raised). Both
    eval-error and indeterminate result degrade to predicate-false in the
    engine: an indeterminate result does not enable a transition.

    References: spec/firing-semantics.md (D6).
    """


@runtime_checkable
class CelAdapter(Protocol):
    """Compile + eval surface for a CEL backend.

    ``compile`` validates syntax (free variables are deferred to eval).
    ``eval`` returns a Python primitive (``bool`` / ``int`` / ``float`` /
    ``str`` / ``None`` / ...), raising :class:`CelEvalError` on eval error so
    the engine's ``try/except`` is the sole error-handling barrier.
    """

    def compile(self, expr: str) -> Any:
        """Compile a CEL expression; raise on syntax error."""
        ...

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        """Evaluate ``compiled`` against ``data``.

        Returns a Python primitive, or raises :class:`CelEvalError` on eval
        error.
        """
        ...


def _celpy_to_native(value: Any) -> Any:
    """Unwrap a celpy celtypes scalar to its plain Python primitive.

    celpy returns results as celtypes wrappers (``BoolType``, ``IntType``,
    ...) which subclass the Python natives but fail identity checks —
    ``BoolType(True) is True`` is False, and ``BoolType`` is not a ``bool``
    subclass. Unwrap so ``result is True`` / ``isinstance(result, bool)``
    hold for the engine's ``bool(result)`` and for adapter tests. Containers
    are recursively unwrapped.
    """
    if isinstance(value, _ct.BoolType):
        return bool(value)
    if isinstance(value, (_ct.IntType, _ct.UintType)):
        return int(value)
    if isinstance(value, _ct.DoubleType):
        return float(value)
    if isinstance(value, _ct.StringType):
        return str(value)
    if isinstance(value, _ct.BytesType):
        return bytes(value)
    if isinstance(value, _ct.ListType):
        return [_celpy_to_native(v) for v in value]
    if isinstance(value, _ct.MapType):
        return {_celpy_to_native(k): _celpy_to_native(v) for k, v in value.items()}
    # celpy can also return PLAIN containers (a map literal evaluates to a
    # dict with celtypes keys/values), and it embeds per-entry eval errors
    # as CELEvalError VALUES instead of raising — surfaced by computed
    # produce fallbacks (ADR 0023), where a container is the expected result
    # rather than a boolean. Recurse and convert an embedded error to the
    # raise the adapter contract promises.
    if isinstance(value, celpy.CELEvalError):  # pyright: ignore[reportUnknownMemberType, reportPrivateImportUsage]
        raise CelEvalError(str(value))
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        return {_celpy_to_native(k): _celpy_to_native(v) for k, v in mapping.items()}
    if isinstance(value, (list, tuple)):
        items = cast("list[Any] | tuple[Any, ...]", value)
        return [_celpy_to_native(v) for v in items]
    return value


class CelpyAdapter(CelAdapter):
    """Adapter over the pure-Python ``celpy`` backend (default)."""

    def __init__(self) -> None:
        self._env: Any = celpy.Environment()  # pyright: ignore[reportUnknownMemberType]

    def compile(self, expr: str) -> Any:
        # celpy.Environment().compile raises CELParseError on syntax error;
        # let it propagate (the parser's broad `except Exception` catches it).
        ast = self._env.compile(expr)  # pyright: ignore[reportUnknownMemberType]
        return self._env.program(ast)  # pyright: ignore[reportUnknownMemberType]

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        # celpy needs celtypes values in the activation: a plain Python dict
        # supports no CEL field selection ("does not support field selection"),
        # so nested access like `token.account` — required by correlated
        # inhibit activations (`{token, binding}`, ADR 0017), the timer's
        # `{clock, bind}` environment (ADR 0018), and predicates over nested
        # token data — fails without json_to_cel conversion. The C++ and Rust
        # backends handle plain nested dicts natively. Top-level scalars are
        # unaffected (json_to_cel is identity-equivalent there). Conversion is
        # per-key so the activation itself stays a plain str-keyed dict.
        activation = {
            k: celpy.json_to_cel(v)  # pyright: ignore[reportUnknownMemberType, reportPrivateImportUsage]
            for k, v in data.items()
        }
        try:
            result = compiled.evaluate(activation)  # pyright: ignore[reportUnknownMemberType]
        except celpy.CELEvalError as exc:  # pyright: ignore[reportUnknownMemberType, reportPrivateImportUsage]
            raise CelEvalError(str(exc)) from exc
        return _celpy_to_native(result)


def _cel_expr_to_native(value: Any) -> Any:
    """Unwrap a cel-expr-python container result to plain Python values.

    ``CelValue.value()`` returns container ENTRIES as ``_CelMapItemAccessor``
    wrappers whose ``plain_value()`` yields the fully-native value — surfaced
    by computed produce fallbacks (ADR 0023), where a map is the expected
    result rather than a boolean. Scalars pass through untouched.
    """
    plain = getattr(value, "plain_value", None)
    if callable(plain):
        return plain()  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {
            _cel_expr_to_native(k): _cel_expr_to_native(v)
            for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
        }
    if isinstance(value, (list, tuple)):
        return [_cel_expr_to_native(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class CelExprAdapter(CelAdapter):
    """Adapter over ``cel-expr-python`` (the official C++ CEL binding).

    The C++ binding is imported lazily inside ``compile`` / ``eval`` so the
    adapter constructs (and ``velocitron.cel`` imports) successfully without
    the optional extra installed. Methods raise ``ImportError`` only when
    actually invoked without the backend present.
    """

    def compile(self, expr: str) -> Any:
        import cel_expr_python.cel as cel  # pyright: ignore[reportMissingImports]

        env = cel.NewEnv()  # pyright: ignore[reportUnknownMemberType]
        # disable_check defers variable/type checking to runtime, matching
        # celpy (compile validates syntax only); without it, free-variable
        # expressions raise "undeclared reference" at compile time.
        return env.compile(expr, disable_check=True)  # pyright: ignore[reportUnknownMemberType]

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        import cel_expr_python.cel as cel  # pyright: ignore[reportMissingImports]

        result = compiled.eval(data=data)  # pyright: ignore[reportUnknownMemberType]
        # cel-expr-python returns CelValue(type=ERROR) on eval error rather
        # than raising; bool(CelValue) is always True, so .value() is required
        # for the primitive. UNKNOWN (indeterminate) degrades to error too —
        # indeterminate ⇒ do not enable a transition.
        # D6: spec/firing-semantics.md.
        if result.type() in (cel.Type.ERROR, cel.Type.UNKNOWN):  # pyright: ignore[reportUnknownMemberType]
            raise CelEvalError(f"CEL evaluation error: {result.value()!r}")
        return _cel_expr_to_native(result.value())  # pyright: ignore[reportUnknownMemberType]


class CelRustAdapter(CelAdapter):
    """Adapter over ``common-expression-language`` (the Rust ``cel-interpreter``
    binding).

    The Rust backend is imported lazily inside ``compile`` / ``eval`` so the
    adapter constructs (and ``velocitron.cel`` imports) successfully without
    the optional extra installed. Methods raise ``ImportError`` only when
    actually invoked without the backend present. It is the simplest of the
    three adapters: free-variable compile needs no flag (unlike
    ``CelExprAdapter``'s ``disable_check=True``), and results are native
    Python primitives needing no unwrapping (unlike
    ``CelpyAdapter._celpy_to_native`` and ``CelExprAdapter``'s ``.value()``).
    """

    def compile(self, expr: str) -> Any:
        # common-expression-language's cel.compile raises ValueError on
        # syntax error — let it propagate (the parser's broad
        # `except Exception` catches it). Free-variable compile needs no
        # flag: variable resolution is deferred to eval by default.
        import cel  # pyright: ignore[reportMissingImports]

        return cel.compile(expr)  # pyright: ignore[reportUnknownMemberType]

    def eval(self, compiled: Any, data: dict[str, Any]) -> Any:
        # common-expression-language raises native Python exceptions on eval
        # error (no return-value ERROR/UNKNOWN state): RuntimeError for
        # undefined variable/missing field, TypeError for no-such-overload
        # (type mismatch), ZeroDivisionError (a subclass of ArithmeticError)
        # for divide-by-zero. Wrap the precise observed eval-error set in
        # CelEvalError; results are already native primitives, so no
        # unwrapping helper is needed. ValueError is the compile-error type,
        # deliberately not caught here.

        try:
            return compiled.execute(data)  # pyright: ignore[reportUnknownMemberType]
        except (RuntimeError, TypeError, ArithmeticError) as exc:
            raise CelEvalError(str(exc)) from exc


def get_default_adapter() -> CelAdapter:
    """Auto-detect the best available backend.

    Three-tier preference order: Rust (``common-expression-language``) →
    C++ (``cel-expr-python``) → pure Python (``cel-python``). Returns the
    highest-preference installed backend, else ``CelpyAdapter`` (the
    always-available core dependency).
    """

    if importlib.util.find_spec("cel") is not None:
        return CelRustAdapter()
    if importlib.util.find_spec("cel_expr_python") is not None:
        return CelExprAdapter()
    return CelpyAdapter()
