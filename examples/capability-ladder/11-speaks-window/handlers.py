"""Instance-scoped handlers for the Speaks Window capability fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def _token_of_type(inp: TransitionHandlerInput, token_type: str) -> Token:
    """Select one bound token by its composition-stable color."""
    for tokens in inp["inputTokens"].values():
        for token in tokens:
            if token.type == token_type:
                return token
    raise ValueError(f"missing bound {token_type!r} token")


def make_start_handler(
    *,
    in_flight_destination: str,
    judge_requested_destination: str,
):
    """Create a start handler bound to the resolved internal destinations."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        request = _token_of_type(inp, "speak_req")
        return {
            "status": "completed",
            "outputTokens": {
                in_flight_destination: [Token("speak_token", dict(request.data))],
                judge_requested_destination: [Token("judge_req", dict(request.data))],
            },
            "error": None,
            "metadata": {},
        }

    return handle


def make_finish_handler(*, utterance_destination: str):
    """Create a finish handler bound to the resolved output destination."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        request = _token_of_type(inp, "judge_req")
        _token_of_type(inp, "speak_token")
        return {
            "status": "completed",
            "outputTokens": {
                utterance_destination: [Token("utterance", dict(request.data))]
            },
            "error": None,
            "metadata": {},
        }

    return handle


def register_instance(
    registry: HandlerRegistry,
    *,
    scope: str,
    in_flight_destination: str,
    judge_requested_destination: str,
    utterance_destination: str,
) -> None:
    """Register one scoped Speaks instance against resolved destinations."""
    registry.register_transition(
        f"chip_start@{scope}",
        make_start_handler(
            in_flight_destination=in_flight_destination,
            judge_requested_destination=judge_requested_destination,
        ),
    )
    registry.register_transition(
        f"finish@{scope}",
        make_finish_handler(utterance_destination=utterance_destination),
    )


def register_all(registry: HandlerRegistry) -> None:
    """Register the canonical unmerged fixture instance."""
    register_instance(
        registry,
        scope="speaks",
        in_flight_destination="in_flight",
        judge_requested_destination="judge_requested",
        utterance_destination="utterance_out",
    )
