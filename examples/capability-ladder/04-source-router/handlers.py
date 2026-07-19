"""Handlers for the Source Router capability-ladder fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def _completed(destination: str, samples: list[Any]) -> dict[str, Any]:
    """Return a successful firing that preserves each consumed sample object."""
    return {
        "status": "completed",
        "outputTokens": {destination: samples},
        "error": None,
        "metadata": {},
    }


def route_co2mon(inp: dict[str, Any]) -> dict[str, Any]:
    """Route consumed sample_in samples unchanged to co2mon_samples."""
    return _completed("co2mon_samples", inp["inputTokens"].get("sample_in", []))


def route_weather(inp: dict[str, Any]) -> dict[str, Any]:
    """Route consumed sample_in samples unchanged to weather_samples."""
    return _completed("weather_samples", inp["inputTokens"].get("sample_in", []))


def is_co2mon(inp: dict[str, Any]) -> bool:
    """Select only samples whose source is co2mon."""
    return inp["token"].data.get("source") == "co2mon"


def is_weather(inp: dict[str, Any]) -> bool:
    """Select only samples whose source is weather."""
    return inp["token"].data.get("source") == "weather"


def register_all(registry: HandlerRegistry) -> None:
    """Register every transition and predicate named by the router fixtures."""
    registry.register_transition("route_co2mon", route_co2mon)
    registry.register_transition("route_weather", route_weather)
    registry.register_predicate("is_co2mon", is_co2mon)
    registry.register_predicate("is_weather", is_weather)
