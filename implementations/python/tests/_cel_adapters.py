"""Shared CEL adapter parametrization fixtures for tests.

Provides the adapter instances and ids used to parametrize CEL-touching tests
over all three backends (cel-python pure-Python default, cel-expr-python
optional C++ binding, common-expression-language optional Rust binding).
Mirrors the parametrization in ``test_cel_adapter.py``: always include
``CelpyAdapter`` (a core dependency); include ``CelExprAdapter`` and
``CelRustAdapter`` only when the corresponding optional backend is installed,
so a missing optional extra skips the parametrization rather than failing
collection.
"""

from __future__ import annotations

import importlib.util

from velocitron.cel import CelAdapter, CelExprAdapter, CelpyAdapter, CelRustAdapter


def cel_expr_available() -> bool:
    """Whether cel-expr-python is installed in the current environment."""
    return importlib.util.find_spec("cel_expr_python") is not None


def cel_rust_available() -> bool:
    """Whether common-expression-language is installed in the current environment."""
    return importlib.util.find_spec("cel") is not None


def adapters() -> list[CelAdapter]:
    """All available adapter instances for parametrization.

    Always includes ``CelpyAdapter`` (cel-python is a core dependency).
    Includes ``CelExprAdapter`` and ``CelRustAdapter`` only when the
    corresponding optional backend is installed.
    """
    adapters_: list[CelAdapter] = [CelpyAdapter()]
    if cel_expr_available():
        adapters_.append(CelExprAdapter())
    if cel_rust_available():
        adapters_.append(CelRustAdapter())
    return adapters_


ADAPTER_IDS = [
    "celpy",
    *(["cel-expr"] if cel_expr_available() else []),
    *(["cel-rust"] if cel_rust_available() else []),
]
