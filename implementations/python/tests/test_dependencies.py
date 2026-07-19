"""Dependency-floor guards for the Python reference parser and validator.

The embedded draft-2020-12 schemas require the maintained ``jsonschema``
runtime declared by ``pyproject.toml``. This suite preserves the 4.26.0
minimum dependency contract.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# implementations/python/tests -> implementations/python.
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _dependencies() -> dict[str, str]:
    # given: the project's declared dependencies
    with _PYPROJECT.open("rb") as fh:
        project = tomllib.load(fh)
    deps: dict[str, str] = {}
    for spec in project["project"]["dependencies"]:
        name, _, version = spec.partition(">=")
        deps[name.strip().lower()] = version.strip()
    return deps


class TestJsonschemaPin:
    """The parser's ``jsonschema`` dependency must remain at least 4.26.0."""

    def test_jsonschema_pinned_at_least_4_26_0(self):
        # given: the declared dependencies in pyproject.toml
        deps = _dependencies()
        # then: jsonschema retains the required >= 4.26.0 floor
        assert "jsonschema" in deps, "jsonschema must be a declared dependency"
        floor = deps["jsonschema"]
        assert floor != "", "jsonschema must use a >= floor pin, not be unbounded"
        parts = tuple(int(p) for p in floor.split("."))
        # pad to (major, minor, patch) for comparison
        major, minor, patch = (parts + (0, 0, 0))[:3]
        assert (major, minor, patch) >= (4, 26, 0), (
            f"jsonschema floor is {floor}; must be >= 4.26.0"
        )
