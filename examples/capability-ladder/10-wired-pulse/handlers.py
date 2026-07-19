"""Instance-scoped handlers for the Wired Pulse capability fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def _token_of_type(inp: TransitionHandlerInput, token_type: str) -> Token:
    """Select a bound token by its composition-stable color."""
    for tokens in inp["inputTokens"].values():
        for token in tokens:
            if token.type == token_type:
                return token
    raise ValueError(f"missing bound {token_type!r} token")


def make_emit_handler(*, pulse_destination: str):
    """Bind pulse emission to the resolved output-place destination."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        seed = _token_of_type(inp, "seed")
        # when the opaque source handler runs,
        pulse = Token("pulse", dict(seed.data))
        # then one correlated pulse is deposited at the resolved port place.
        return {
            "status": "completed",
            "outputTokens": {pulse_destination: [pulse]},
            "error": None,
            "metadata": {},
        }

    return handle


def make_receive_handler(*, receipt_destination: str):
    """Bind pulse receipt to the resolved terminal-place destination."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        # Given the pulse consumed through the fused wire place,
        pulse = _token_of_type(inp, "pulse")
        # when the opaque sink handler runs,
        receipt = Token("receipt", {**pulse.data, "received": True})
        # then one receipt records end-to-end delivery at the resolved place.
        return {
            "status": "completed",
            "outputTokens": {receipt_destination: [receipt]},
            "error": None,
            "metadata": {},
        }

    return handle


def register_instance(
    registry: HandlerRegistry,
    *,
    pulse_destination: str,
    receipt_destination: str,
) -> None:
    """Register one source/sink pair against its resolved destinations."""
    registry.register_transition(
        "emit@source",
        make_emit_handler(pulse_destination=pulse_destination),
    )
    registry.register_transition(
        "receive@sink",
        make_receive_handler(receipt_destination=receipt_destination),
    )


def register_all(registry: HandlerRegistry) -> None:
    """Register the canonical standalone fixture handlers."""
    register_instance(
        registry,
        pulse_destination="pulse_out",
        receipt_destination="received",
    )
