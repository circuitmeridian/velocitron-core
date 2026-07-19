"""Handler registry support for the Crossing Window capability fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def detect_crossing(inp: dict[str, Any]) -> dict[str, Any]:
    """Return one alert when the bound samples cross upward through 1000 ppm."""
    latest = inp["inputTokens"]["latest_sample"][0]
    previous = inp["inputTokens"]["previous_sample"][0]
    crossed = previous.data["ppm"] < 1000 <= latest.data["ppm"]
    output_tokens = {"alert_out": [Token("alert", {})]} if crossed else {}
    return {
        "status": "completed",
        "outputTokens": output_tokens,
        "error": None,
        "metadata": {},
    }


def register_all(registry: HandlerRegistry) -> None:
    """Register the exact transition handler named by the fixture."""
    registry.register_transition("detect_crossing@ingest", detect_crossing)
