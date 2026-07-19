"""Nox sessions for testing CEL backend configurations.

Each session creates an isolated uv-managed venv with a specific set of
optional CEL backends, then runs the full test suite. This exercises the
adapter-preference chain (Rust -> C++ -> pure Python) and the
environment-dependent skips in ``test_cel_adapter.py``.
"""

from __future__ import annotations

import nox


PYTHON_DIR = "implementations/python"


@nox.session(venv_backend="uv", tags=["cel"])
def test_celpy(session: nox.Session) -> None:
    """Test with only the core cel-python backend (no optional backends)."""
    session.chdir(PYTHON_DIR)
    session.install(".")
    session.install("pytest")
    session.run("pytest", *session.posargs)


@nox.session(venv_backend="uv", tags=["cel"])
def test_cel_expr(session: nox.Session) -> None:
    """Test with cel-expr-python (C++ binding) installed."""
    session.chdir(PYTHON_DIR)
    session.install(".[cel-cpp]")
    session.install("pytest")
    session.run("pytest", *session.posargs)


@nox.session(venv_backend="uv", tags=["cel"])
def test_cel_rust(session: nox.Session) -> None:
    """Test with common-expression-language (Rust binding) installed."""
    session.chdir(PYTHON_DIR)
    session.install(".[cel-rust]")
    session.install("pytest")
    session.run("pytest", *session.posargs)


@nox.session(venv_backend="uv", tags=["cel"])
def test_all(session: nox.Session) -> None:
    """Test with both optional CEL backends installed."""
    session.chdir(PYTHON_DIR)
    session.install(".[cel-cpp,cel-rust]")
    session.install("pytest")
    session.run("pytest", *session.posargs)
