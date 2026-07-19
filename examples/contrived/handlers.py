"""Transition handlers for the contrived example nets.

Co-located with the nets in ``examples/contrived/`` and loaded by the
parametrized runner via ``importlib`` (absolute path, no ``sys.path``
pollution). One module, one ``register_all``: as later examples add nets,
their handlers land here too. The runner registers all into one registry;
extra registrations are harmless — the engine's sandwich validation only
checks that *net-referenced* handlers resolve.

Handlers are plain callables over the contract's dict shapes
(``spec/handler-contract.md``): each takes the resolved input binding and
returns ``{status, outputTokens, error, metadata}``. Runtime-dependency-free
(the ``HandlerRegistry`` import is ``TYPE_CHECKING``-guarded, annotations
stringified via ``from __future__ import annotations``).

Transition handlers complete and route consumed tokens to their produce
destinations, or return an empty envelope when the transition is
consume-only or its outputs are supplied by produce-template passthrough —
so each body is just its routing; the shared
``{status, outputTokens, error, metadata}`` envelope is built once by
``_completed``. Guards take the same input binding but return ``bool``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


def _completed(output_tokens: dict[str, list[Any]]) -> dict[str, Any]:
    """Build the standard completed-result envelope around ``output_tokens``."""
    return {
        "status": "completed",
        "outputTokens": output_tokens,
        "error": None,
        "metadata": {},
    }


def turn_on(inp: dict[str, Any]) -> dict[str, Any]:
    """Passthrough: route the consumed 'off' status token to 'on'."""
    return _completed({"on": inp["inputTokens"].get("off", [])})


def unlock(inp: dict[str, Any]) -> dict[str, Any]:
    """Release the lock: route the consumed 'locked' job token to 'unlocked'."""
    return _completed({"unlocked": inp["inputTokens"].get("locked", [])})


def run(inp: dict[str, Any]) -> dict[str, Any]:
    """Run the waiting job to 'done' (gated by the inhibit arc on 'locked')."""
    return _completed({"done": inp["inputTokens"].get("waiting", [])})


def prep_a(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage A prep: ready_a raw material → preparing_a (demand_a consumed as trigger)."""
    return _completed({"preparing_a": inp["inputTokens"].get("ready_a", [])})


def work_a(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage A work: preparing_a → working_a."""
    return _completed({"working_a": inp["inputTokens"].get("preparing_a", [])})


def done_a(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage A done: working_a → product_a AND restock ready_a."""
    working = inp["inputTokens"].get("working_a", [])
    return _completed({"product_a": working, "ready_a": working})


def prep_b(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage B prep: ready_b → preparing_b, demand_b → demand_a (pull: B demands A)."""
    return _completed(
        {
            "preparing_b": inp["inputTokens"].get("ready_b", []),
            "demand_a": inp["inputTokens"].get("demand_b", []),
        },
    )


def work_b(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage B work: preparing_b + product_a → working_b (product_a consumed as subassembly)."""
    return _completed({"working_b": inp["inputTokens"].get("preparing_b", [])})


def done_b(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage B done: working_b → product_b AND restock ready_b."""
    working = inp["inputTokens"].get("working_b", [])
    return _completed({"product_b": working, "ready_b": working})


def prep_c(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage C prep: ready_c → preparing_c, demand_c → demand_b (pull: C demands B)."""
    return _completed(
        {
            "preparing_c": inp["inputTokens"].get("ready_c", []),
            "demand_b": inp["inputTokens"].get("demand_c", []),
        },
    )


def work_c(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage C work: preparing_c + product_b → working_c (product_b consumed as subassembly)."""
    return _completed({"working_c": inp["inputTokens"].get("preparing_c", [])})


def done_c(inp: dict[str, Any]) -> dict[str, Any]:
    """Stage C done: working_c → product_c AND restock ready_c."""
    working = inp["inputTokens"].get("working_c", [])
    return _completed({"product_c": working, "ready_c": working})


def receive_c(inp: dict[str, Any]) -> dict[str, Any]:
    """Deliver: product_c → received (terminal sink; quiescence)."""
    return _completed({"received": inp["inputTokens"].get("product_c", [])})


# One slice takes 2 mm off the cheese block; slicing needs at least that much.
_SLICE_THICKNESS_MM = 2


def enough_cheese_left(inp: dict[str, Any]) -> bool:
    """Guard: the cheese block still has enough thickness to slice (>= 2 mm)."""
    block = inp["inputTokens"]["cheese_block"][0]
    return block.data["thickness_mm"] >= _SLICE_THICKNESS_MM


def slice_cheese(inp: dict[str, Any]) -> dict[str, Any]:
    """Slice 2 mm off the cheese block; the cheese_slice token is supplied by
    the produce template's literal data passthrough."""
    block = inp["inputTokens"]["cheese_block"][0]
    thinner = replace(
        block,
        data={
            **block.data,
            "thickness_mm": block.data["thickness_mm"] - _SLICE_THICKNESS_MM,
        },
    )
    return _completed({"cheese_block": [thinner]})


def layer(_inp: dict[str, Any]) -> dict[str, Any]:
    """Layer 2 bread + 3 cheese into a sandwich; the sandwich token is supplied
    by the produce template's literal data passthrough."""
    return _completed({})


def eat_cheese_sandwich(_inp: dict[str, Any]) -> dict[str, Any]:
    """Consume-only: eat the sandwich (no output tokens)."""
    return _completed({})


def see_mold(inp: dict[str, Any]) -> dict[str, Any]:
    """Passthrough: route the consumed bread token to moldy_bread_slices."""
    return _completed(
        {"moldy_bread_slices": inp["inputTokens"].get("bread_slices", [])},
    )


def see_no_mold(inp: dict[str, Any]) -> dict[str, Any]:
    """Passthrough: route the consumed bread token to edible_bread_slices."""
    return _completed(
        {"edible_bread_slices": inp["inputTokens"].get("bread_slices", [])},
    )


def compost(_inp: dict[str, Any]) -> dict[str, Any]:
    """Consume-only: compost the moldy bread (no output tokens)."""
    return _completed({})


def accept_coin(inp: dict[str, Any]) -> dict[str, Any]:
    """Accept the coin: route the consumed 'coin_slot' coin to 'cash_box'; the
    'signal' control token is supplied by the produce template's literal data
    passthrough."""
    return _completed({"cash_box": inp["inputTokens"].get("coin_slot", [])})


def vend_packet(inp: dict[str, Any]) -> dict[str, Any]:
    """Vend a packet: route the consumed 'storage' packet to 'compartment'
    (the 'signal' control token is consumed as a catalyst, not re-produced)."""
    return _completed({"compartment": inp["inputTokens"].get("storage", [])})


def return_coin(_inp: dict[str, Any]) -> dict[str, Any]:
    """Consume-only: return the coin to the customer (no output tokens)."""
    return _completed({})


def take_packet(_inp: dict[str, Any]) -> dict[str, Any]:
    """Consume-only: take the packet from the compartment (no output tokens)."""
    return _completed({})


def register_all(registry: HandlerRegistry) -> None:
    """Register every handler any contrived net names."""
    registry.register_transition("turn_on", turn_on)
    registry.register_transition("unlock", unlock)
    registry.register_transition("run", run)
    registry.register_transition("prep_a", prep_a)
    registry.register_transition("work_a", work_a)
    registry.register_transition("done_a", done_a)
    registry.register_transition("prep_b", prep_b)
    registry.register_transition("work_b", work_b)
    registry.register_transition("done_b", done_b)
    registry.register_transition("prep_c", prep_c)
    registry.register_transition("work_c", work_c)
    registry.register_transition("done_c", done_c)
    registry.register_transition("receive_c", receive_c)
    registry.register_transition("slice_cheese", slice_cheese)
    registry.register_transition("layer", layer)
    registry.register_transition("eat_cheese_sandwich", eat_cheese_sandwich)
    registry.register_transition("see_mold", see_mold)
    registry.register_transition("see_no_mold", see_no_mold)
    registry.register_transition("compost", compost)
    registry.register_guard("enough_cheese_left", enough_cheese_left)
    registry.register_transition("accept_coin", accept_coin)
    registry.register_transition("vend_packet", vend_packet)
    registry.register_transition("return_coin", return_coin)
    registry.register_transition("take_packet", take_packet)
