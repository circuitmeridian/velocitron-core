"""Handler registry support for the Per-Key Suppression capability fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def admit_request(inp: dict[str, Any]) -> dict[str, Any]:
    """Pass the selected request through to admitted unchanged."""
    return {
        "status": "completed",
        "outputTokens": {"admitted": inp["inputTokens"].get("requests", [])},
        "error": None,
        "metadata": {},
    }


def register_all(registry: HandlerRegistry) -> None:
    """Register the exact transition handler named by the fixture."""
    registry.register_transition("admit_request", admit_request)
