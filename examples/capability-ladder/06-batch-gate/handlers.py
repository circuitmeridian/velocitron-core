"""Handlers for the Batch Gate capability-ladder fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def form_batch(_inp: dict[str, Any]) -> dict[str, Any]:
    """Produce one batch for key A after the weighted input is consumed."""
    return {
        "status": "completed",
        "outputTokens": {
            "batches": [Token(type="batch", data={"batch_id": "A"})],
        },
        "error": None,
        "metadata": {},
    }


def register_all(registry: HandlerRegistry) -> None:
    """Register every transition handler named by the Batch Gate fixture."""
    registry.register_transition("form_batch", form_batch)
