"""Instance-scoped handlers for the Guinan graduation composition fixture."""

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


def make_on_tick_handler(
    *,
    latch_destination: str,
    refresh_due_destination: str,
):
    """Create a cadence handler bound to its resolved produce destinations."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        clock = _token_of_type(inp, "clock")
        latch = _token_of_type(inp, "tick_latch")
        latch_data = dict(latch.data)
        latch_data["fired_at"] = clock.data["now"]
        return {
            "status": "completed",
            "outputTokens": {
                latch_destination: [Token("tick_latch", latch_data)],
                refresh_due_destination: [Token("refresh_due", {"trigger": "tick"})],
            },
            "error": None,
            "metadata": {},
        }

    return handle


def make_curate_handler(*, speak_request_destination: str):
    """Create a Curate handler bound to the resolved fused request place."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        refresh_due = _token_of_type(inp, "refresh_due")
        return {
            "status": "completed",
            "outputTokens": {
                speak_request_destination: [Token("speak_req", dict(refresh_due.data))]
            },
            "error": None,
            "metadata": {},
        }

    return handle


def make_start_handler(
    *,
    in_flight_destination: str,
    work_destination: str,
):
    """Create a Speaks start handler bound to both resolved destinations."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        request = _token_of_type(inp, "speak_req")
        return {
            "status": "completed",
            "outputTokens": {
                in_flight_destination: [Token("speak_token", dict(request.data))],
                work_destination: [Token("speak_work", dict(request.data))],
            },
            "error": None,
            "metadata": {},
        }

    return handle


def make_finish_handler(*, utterance_destination: str):
    """Create a Speaks finish handler bound to the resolved output boundary."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        work = _token_of_type(inp, "speak_work")
        _token_of_type(inp, "speak_token")
        return {
            "status": "completed",
            "outputTokens": {
                utterance_destination: [Token("utterance", dict(work.data))]
            },
            "error": None,
            "metadata": {},
        }

    return handle


def register_instance(
    registry: HandlerRegistry,
    *,
    cadence_scope: str,
    curate_scope: str,
    speaks_scope: str,
    latch_destination: str,
    refresh_due_destination: str,
    speak_request_destination: str,
    in_flight_destination: str,
    work_destination: str,
    utterance_destination: str,
) -> None:
    """Register one composition instance against resolved produce destinations."""
    registry.register_transition(
        f"on_tick@{cadence_scope}",
        make_on_tick_handler(
            latch_destination=latch_destination,
            refresh_due_destination=refresh_due_destination,
        ),
    )
    registry.register_transition(
        f"curate@{curate_scope}",
        make_curate_handler(speak_request_destination=speak_request_destination),
    )
    registry.register_transition(
        f"start@{speaks_scope}",
        make_start_handler(
            in_flight_destination=in_flight_destination,
            work_destination=work_destination,
        ),
    )
    registry.register_transition(
        f"finish@{speaks_scope}",
        make_finish_handler(utterance_destination=utterance_destination),
    )


def register_all(registry: HandlerRegistry) -> None:
    """Register standalone constituent handlers with their local destinations."""
    register_instance(
        registry,
        cadence_scope="cadence",
        curate_scope="curate",
        speaks_scope="speaks",
        latch_destination="tick_latch",
        refresh_due_destination="refresh_due_out",
        speak_request_destination="speak_request",
        in_flight_destination="in_flight",
        work_destination="work",
        utterance_destination="utterance_out",
    )
