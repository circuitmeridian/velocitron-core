"""Structural contracts for the canonical museum-loan capability fixture."""

from __future__ import annotations

from pathlib import Path

from velocitron.dsl.api import compile_petrinet_text
from velocitron.parser import parse_net
from velocitron.schema import Arc, Net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_FIXTURE_PATH = (
    _REPOSITORY_ROOT
    / "examples"
    / "capability-ladder"
    / "13-museum-loan"
    / "museum-loan.petrinet"
)


def _museum_loan_net() -> Net:
    """Compile the authored DSL and parse its validated core representation."""
    document = compile_petrinet_text(
        _FIXTURE_PATH.read_text(encoding="utf-8"), str(_FIXTURE_PATH)
    )
    return parse_net(document)


def _place(net: Net, name: str):
    return next(place for place in net.places if place.name == name)


def _transition(net: Net, name: str):
    return next(transition for transition in net.transitions if transition.name == name)


def _input_arc(net: Net, place: str, transition: str) -> Arc:
    return next(
        arc
        for arc in net.arcs
        if arc.from_place == place and arc.to_transition == transition
    )


def _output_arc(net: Net, transition: str, place: str) -> Arc:
    return next(
        arc
        for arc in net.arcs
        if arc.from_transition == transition and arc.to_place == place
    )


def test_museum_loan_source_and_ports_are_explicit_validated_net_contracts() -> None:
    """Given the canonical fixture, its boundary and source semantics survive validation."""
    # given: the canonical DSL fixture
    net = _museum_loan_net()

    # when: the public compiler lowers it and the core parser validates it
    source = _transition(net, "open_loan_desk")
    desk_open = _output_arc(net, "open_loan_desk", "desk_status")
    clock_tick = _output_arc(net, "open_loan_desk", "museum_clock")

    # then: a source transition has no inputs and only documented literal outputs
    assert isinstance(net, Net)
    assert source.handler == "open_loan_desk@museum_loan"
    assert not [arc for arc in net.arcs if arc.to_transition == "open_loan_desk"]
    assert desk_open.produce is not None
    assert desk_open.produce.type == "desk_status"
    assert desk_open.produce.destination == "desk_status"
    assert desk_open.produce.data == {"state": "desk_open"}
    assert clock_tick.produce is not None
    assert clock_tick.produce.type == "clock_tick"
    assert clock_tick.produce.destination == "museum_clock"
    assert clock_tick.produce.data == {"now": 0}
    loan_requests_port = _place(net, "loan_requests").port
    dispatch_handoff_port = _place(net, "dispatch_handoff").port
    assert loan_requests_port is not None
    assert loan_requests_port.direction == "input"
    assert loan_requests_port.type == "loan_request"
    assert dispatch_handoff_port is not None
    assert dispatch_handoff_port.direction == "output"
    assert dispatch_handoff_port.type == "dispatch_handoff"


def test_museum_loan_screening_uses_the_complete_intake_ledger() -> None:
    """Given a candidate request, screening retains consume, read, and inhibit semantics."""
    # given: the validated museum-loan net
    net = _museum_loan_net()

    # when: the screening input inscriptions are selected by their endpoints
    request = _input_arc(net, "loan_requests", "screen_request")
    catalog = _input_arc(net, "catalog_entries", "screen_request")
    desk = _input_arc(net, "desk_status", "screen_request")
    paused = _input_arc(net, "intake_pause", "screen_request")
    reserved = _input_arc(net, "active_reservations", "screen_request")

    # then: screening consumes a handler-filtered request and reads its context
    assert request.consume is not None
    assert request.consume.type == "loan_request"
    assert request.consume.mode == "consume"
    assert request.consume.predicate is not None
    assert request.consume.predicate.handler == "request_is_complete@museum_loan"
    assert request.consume.predicate.cel is None
    assert catalog.consume is not None
    assert catalog.consume.mode == "read"
    assert catalog.consume.type == "catalog_entry"
    assert catalog.consume.predicate is not None
    assert catalog.consume.predicate.cel == "loanable == true"
    assert catalog.consume.predicate.handler is None
    assert desk.consume is not None
    assert desk.consume.mode == "read"
    assert desk.consume.type == "desk_status"

    # and: intake has both a global zero-test and per-artifact correlated suppression
    assert paused.consume is not None
    assert paused.consume.mode == "inhibit"
    assert paused.consume.type == "intake_pause"
    assert paused.consume.correlate is None
    assert reserved.consume is not None
    assert reserved.consume.mode == "inhibit"
    assert reserved.consume.type == "active_reservation"
    assert (
        reserved.consume.correlate
        == "token.artifactId == binding.loan_requests[0].artifactId"
    )
    assert _transition(net, "screen_request").priority == 20


def test_museum_loan_decision_confirmation_expiry_and_dispatch_remain_distinct() -> (
    None
):
    """Given accepted work, all decision, temporal, and weighted-routing contracts remain visible."""
    # given: the validated museum-loan net
    net = _museum_loan_net()

    # when: decision, reservation, and dispatch structures are inspected
    approved = _input_arc(net, "curation_decisions", "approve_loan")
    declined = _input_arc(net, "curation_decisions", "decline_loan")
    confirmation = _transition(net, "confirm_reservation")
    expiry = _transition(net, "expire_reservation")
    reports = _input_arc(net, "condition_reports", "dispatch_loan")
    capacity = _place(net, "active_reservations").capacity_per_color_key

    # then: mutually exclusive CEL decisions route approval but leave decline terminal
    assert approved.consume is not None and approved.consume.predicate is not None
    assert approved.consume.predicate.cel == "approved == true"
    assert declined.consume is not None and declined.consume.predicate is not None
    assert declined.consume.predicate.cel == "approved == false"
    assert _output_arc(net, "approve_loan", "active_reservations").produce is not None
    assert not [arc for arc in net.arcs if arc.from_transition == "decline_loan"]

    # and: confirmation uses its opaque guard while expiry binds the reservation to time
    assert confirmation.guard == "receipt_matches_reservation@museum_loan"
    assert expiry.timer is not None
    assert expiry.timer.clock == "museum_clock"
    assert expiry.timer.bind == {"reservation": "active_reservations"}
    assert expiry.timer.cel == "clock.now >= reservation.expiresAt"
    assert (
        _output_arc(net, "expire_reservation", "expired_reservations").produce
        is not None
    )
    assert not [
        arc for arc in net.arcs if arc.from_transition == "close_expired_reservation"
    ]

    # and: collection emits both templates before dispatch consumes two reports to the output
    assert _output_arc(net, "collect_artifact", "dispatch_ready").produce is not None
    assert _output_arc(net, "collect_artifact", "condition_reports").produce is not None
    assert reports.consume is not None
    assert reports.consume.mode == "consume"
    assert reports.consume.type == "condition_report"
    assert reports.consume.weight == 2
    assert (
        _transition(net, "dispatch_loan").guard
        == "reports_match_reservation@museum_loan"
    )
    dispatch = _output_arc(net, "dispatch_loan", "dispatch_handoff")
    assert dispatch.produce is not None
    assert dispatch.produce.type == "dispatch_handoff"
    assert capacity is not None
    assert capacity.keys == ("artifactId",)
    assert capacity.max == 1


def test_museum_loan_initial_marking_metadata_and_handlers_are_retained() -> None:
    """Given the canonical fixture, renderer-relevant documentation and marking reach the Net."""
    # given: the validated museum-loan net
    net = _museum_loan_net()

    # when: its retained marking, documentation, and handler declarations are examined
    catalog = _place(net, "catalog_entries")
    screening = _transition(net, "screen_request")
    request_arc = _input_arc(net, "loan_requests", "screen_request")

    # then: the initial catalog token and every output template are represented in the core net
    assert net.initial_marking is not None
    assert [
        {"type": token.type, "data": token.data}
        for token in net.initial_marking["catalog_entries"]
    ] == [
        {
            "type": "catalog_entry",
            "data": {
                "artifactId": "artifact-001",
                "loanable": True,
                "title": "Bronze Statuette",
            },
        }
    ]
    assert all(arc.produce is not None for arc in net.arcs if arc.from_transition)
    assert all(transition.handler for transition in net.transitions)

    # and: net, place, transition, and arc descriptions and annotations remain available
    assert net.description is not None
    assert net.annotations is not None
    assert net.annotations["domain"] == "museum-loan"
    assert _place(net, "loan_requests").description is not None
    assert _place(net, "loan_requests").annotations == {"interface": "loan-intake"}
    assert catalog.name == "catalog_entries"
    assert screening.description is not None
    assert screening.annotations == {"phase": "intake"}
    assert request_arc.description is not None
    assert request_arc.annotations == {"role": "request-filter"}
