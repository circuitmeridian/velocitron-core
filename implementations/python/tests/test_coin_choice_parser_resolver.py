"""Red parser/resolver coverage for Coin Choice chain and order facts."""

from __future__ import annotations
from typing import Any

import pytest

from velocitron.dsl.api import compile_petrinet_text
from velocitron.dsl.diagnostics import PetrinetDslError


_ORDERED_SOURCE = """\
net coin_choice "Coin choice"

@return: (coin_slot) -coin-> [return_coin]
@accept: (coin_slot) -coin-> [accept_coin] -coin-> (cash_box)

[return_coin] handler "return_coin"
[accept_coin] handler "accept_coin"

[accept_coin] order 1
[return_coin] order 2
@return order 2
@accept order 1

marking initial (coin_slot) <- $inserted_coin
$inserted_coin: coin {}
"""

_ORDER_FREE_SOURCE = """\
net coin_choice "Coin choice"

(coin_slot) -coin-> [return_coin]
(coin_slot) -coin-> [accept_coin] -coin-> (cash_box)

[return_coin] handler "return_coin"
[accept_coin] handler "accept_coin"

marking initial (coin_slot) <- $inserted_coin
$inserted_coin: coin {}
"""


def test_generated_parser_and_resolver_apply_coin_choice_order_facts() -> None:
    """Order facts reorder return-first chains; absent facts preserve first appearance."""
    ordered = compile_petrinet_text(_ORDERED_SOURCE, "coin-choice.ordered.petrinet")
    order_free = compile_petrinet_text(
        _ORDER_FREE_SOURCE, "coin-choice.order-free.petrinet"
    )

    expected_places = [
        {"name": "coin_slot", "accepts": ["coin"]},
        {"name": "cash_box", "accepts": ["coin"]},
    ]
    expected_accept_consume = {
        "from": {"place": "coin_slot"},
        "to": {"transition": "accept_coin"},
        "consume": {"type": "coin"},
    }
    expected_accept_produce = {
        "from": {"transition": "accept_coin"},
        "to": {"place": "cash_box"},
        "produce": {"destination": "cash_box", "type": "coin"},
    }
    expected_return_consume = {
        "from": {"place": "coin_slot"},
        "to": {"transition": "return_coin"},
        "consume": {"type": "coin"},
    }
    expected_initial_marking: dict[str, list[dict[str, Any]]] = {
        "coin_slot": [{"type": "coin", "data": {}}]
    }

    assert ordered["places"] == expected_places
    assert ordered["transitions"] == [
        {"name": "accept_coin", "handler": "accept_coin"},
        {"name": "return_coin", "handler": "return_coin"},
    ]
    assert ordered["arcs"] == [
        expected_accept_consume,
        expected_accept_produce,
        expected_return_consume,
    ]
    assert ordered["initialMarking"] == expected_initial_marking

    assert order_free["places"] == expected_places
    assert order_free["transitions"] == [
        {"name": "return_coin", "handler": "return_coin"},
        {"name": "accept_coin", "handler": "accept_coin"},
    ]
    assert order_free["arcs"] == [
        expected_return_consume,
        expected_accept_consume,
        expected_accept_produce,
    ]
    assert order_free["initialMarking"] == expected_initial_marking


def test_duplicate_transition_order_has_primary_related_span_and_help() -> None:
    """The later duplicate points back to its first rank assignment."""
    source = _ORDERED_SOURCE.replace("[return_coin] order 2", "[return_coin] order 1")
    with pytest.raises(PetrinetDslError) as raised:
        compile_petrinet_text(source, "coin-choice.petrinet")
    diagnostic = raised.value.diagnostic
    assert diagnostic.code == "PN202"
    assert (
        diagnostic.message == "transition order position 1 is assigned more than once"
    )
    assert (
        diagnostic.help
        == "assign each explicitly ordered transition a unique positive position"
    )
    assert diagnostic.span.start.line == 10
    assert diagnostic.related[0].message == "[accept_coin] first assigned position 1"
    assert diagnostic.related[0].span.start.line == 9
