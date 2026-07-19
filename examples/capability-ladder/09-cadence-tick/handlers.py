"""Instance-scoped handlers for the Cadence Tick capability fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


Trigger = Literal["tick", "alert"]


def _token_of_type(inp: TransitionHandlerInput, token_type: str) -> Token:
    """Select a bound token by its composition-stable color."""
    for tokens in inp["inputTokens"].values():
        for token in tokens:
            if token.type == token_type:
                return token
    raise ValueError(f"missing bound {token_type!r} token")


def make_cadence_handler(
    trigger: Trigger,
    *,
    latch_destination: str,
    refresh_destination: str,
):
    """Bind one cadence handler to its resolved produce destinations."""

    def handle(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        # Given the selected firing's consumed latch and read clock binding,
        latch = _token_of_type(inp, "tick_latch")
        clock = _token_of_type(inp, "clock")

        # when either a mature tick or an alert wins the shared-latch conflict,
        rearmed_latch = Token(
            "tick_latch",
            {
                "fired_at": clock.data["now"],
                "cadence_s": latch.data["cadence_s"],
            },
        )
        refresh_data = {"trigger": trigger}
        if trigger == "alert":
            alert = _token_of_type(inp, "alert")
            refresh_data = {**alert.data, "trigger": "alert"}

        # then the handler rearms from injected time and requests one refresh.
        return {
            "status": "completed",
            "outputTokens": {
                latch_destination: [rearmed_latch],
                refresh_destination: [Token("refresh_due", refresh_data)],
            },
            "error": None,
            "metadata": {},
        }

    return handle


def register_instance(
    registry: HandlerRegistry,
    *,
    scope: str,
    latch_destination: str,
    refresh_destination: str,
) -> None:
    """Register one scoped cadence instance against resolved destinations."""
    registry.register_transition(
        f"on_tick@{scope}",
        make_cadence_handler(
            "tick",
            latch_destination=latch_destination,
            refresh_destination=refresh_destination,
        ),
    )
    registry.register_transition(
        f"on_alert@{scope}",
        make_cadence_handler(
            "alert",
            latch_destination=latch_destination,
            refresh_destination=refresh_destination,
        ),
    )


def register_all(registry: HandlerRegistry) -> None:
    """Register the canonical fixture instance under its scoped runtime refs."""
    register_instance(
        registry,
        scope="cadence",
        latch_destination="tick_latch",
        refresh_destination="refresh_due",
    )
