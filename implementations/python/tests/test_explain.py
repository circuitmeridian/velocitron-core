"""BDD-style behavioral contracts for the deterministic core-Net explainer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import pytest

from velocitron.dsl.api import compile_petrinet_text
from velocitron.explain import explain_net
from velocitron.parser import parse_net
from velocitron.schema import Net


_REPOSITORY_ROOT = Path(__file__).parents[3]
_MUSEUM_LOAN_FIXTURE = (
    _REPOSITORY_ROOT
    / "examples"
    / "capability-ladder"
    / "13-museum-loan"
    / "museum-loan.petrinet"
)


def _museum_loan_net() -> Net:
    """Compile the authored museum fixture into the validated core Net."""
    document = compile_petrinet_text(
        _MUSEUM_LOAN_FIXTURE.read_text(encoding="utf-8"), str(_MUSEUM_LOAN_FIXTURE)
    )
    return parse_net(document)


def _json(value: object) -> str:
    """The explanatory document's required canonical JSON representation."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _quoted(value: str) -> str:
    return _json(value)


def _assert_in_order(text: str, fragments: list[str]) -> None:
    """Assert each observed declaration occurs after its predecessor."""
    position = -1
    for fragment in fragments:
        next_position = text.find(fragment, position + 1)
        assert next_position >= 0, f"missing or reordered: {fragment}"
        position = next_position


def _heading(name: str, format: Literal["markdown", "text"] = "markdown") -> str:
    return f"## {name}" if format == "markdown" else name.upper()


def _transition_paragraphs(
    document: str, format: Literal["markdown", "text"] = "markdown"
) -> list[tuple[str, str]]:
    """Return declared transition labels and their sole prose paragraphs."""
    flow_start = document.index(_heading("Transition flow", format))
    flow_end = document.index(_heading("Faithfulness note", format), flow_start + 1)
    standalone_heading = _heading("Standalone places", format)
    standalone_start = document.find(standalone_heading, flow_start + 1, flow_end)
    if standalone_start >= 0:
        flow_end = standalone_start
    flow = document[flow_start:flow_end]
    label_prefix = "### Transition " if format == "markdown" else "TRANSITION "
    labels = list(
        re.finditer(
            rf"^{re.escape(label_prefix)}(?P<name>\".+\")$", flow, flags=re.MULTILINE
        )
    )

    assert labels, "transition flow must introduce transition paragraphs"
    assert not re.search(r"(?m)^\s*(?:[-*+]|\d+\.)\s+", flow), (
        "transition flow must be prose, not a nested enumeration"
    )

    paragraphs: list[tuple[str, str]] = []
    for index, label in enumerate(labels):
        following_label = (
            labels[index + 1].start() if index + 1 < len(labels) else len(flow)
        )
        body = flow[label.end() : following_label].strip()
        assert body, f"{label.group('name')} has no prose paragraph"
        assert "\n" not in body, (
            f"{label.group('name')} must have exactly one paragraph"
        )
        paragraphs.append((label.group("name"), body))
    return paragraphs


@pytest.fixture
def museum_loan_net() -> Net:
    """Given the canonical capability fixture as a parsed, validated Net."""
    return _museum_loan_net()


@pytest.mark.parametrize(
    ("format", "level"),
    [
        ("markdown", "practitioner"),
        ("markdown", "newcomer"),
        ("text", "practitioner"),
        ("text", "newcomer"),
    ],
)
def test_every_format_and_level_is_deterministic(
    museum_loan_net: Net,
    format: Literal["markdown", "text"],
    level: Literal["practitioner", "newcomer"],
) -> None:
    """Given the same core Net, every supported rendering selection is stable."""
    # when: every public format/level combination is rendered twice
    first = explain_net(museum_loan_net, format=format, level=level)
    second = explain_net(museum_loan_net, format=format, level=level)

    # then: output is deterministic, complete at EOF, and presents the core overview
    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")
    expected_title = f'Net: "{museum_loan_net.name}"'
    plain_title = f'NET: "{museum_loan_net.name}"'
    assert (f"# {expected_title}" if format == "markdown" else plain_title) in first
    assert (
        f"This net declares {len(museum_loan_net.places)} places, "
        f"{len(museum_loan_net.transitions)} transitions, and {len(museum_loan_net.arcs)} arcs."
    ) in first
    assert "Faithfulness note" in first or "FAITHFULNESS NOTE" in first


def test_markdown_practitioner_describes_each_declared_transition_as_ordered_prose(
    museum_loan_net: Net,
) -> None:
    """Given the fixture, Markdown describes immediate transition pre/post declarations."""
    # when: the validated Net is explained without evaluating any declaration
    document = explain_net(museum_loan_net)
    paragraphs = _transition_paragraphs(document)
    paragraph_by_name = dict(paragraphs)

    # then: exactly one paragraph follows each transition label in declaration order
    assert [name for name, _ in paragraphs] == [
        _quoted(transition.name) for transition in museum_loan_net.transitions
    ]
    assert len(paragraph_by_name) == len(museum_loan_net.transitions)
    assert document.startswith('# Net: "museum_loan"\n')
    assert (
        'It declares description "A concurrent loan desk that suppresses duplicate active reservations by artifact identifier."'
        in document
    )
    assert 'It declares opaque annotations {"domain":"museum-loan"}.' in document
    assert "petrinet.dsl/v1" not in document

    # and: the optional marking preserves its canonical token declaration
    assert (
        'Place "catalog_entries" initially contains token type "catalog_entry" with data '
        '{"artifactId":"artifact-001","loanable":true,"title":"Bronze Statuette"}.'
    ) in document

    # and: source and sink status are immediate topology facts, not inferred behavior
    open_desk = paragraph_by_name['"open_loan_desk"']
    assert 'opaque handler reference "open_loan_desk@museum_loan"' in open_desk
    assert "source transition because it has no immediate input arcs" in open_desk
    assert (
        'postcondition routes output through place "desk_status" (accepts colors "desk_status"): '
        'template token type "desk_status" declares destination "desk_status", '
        'with literal data {"state":"desk_open"}'
    ) in open_desk
    assert (
        'place "museum_clock" (accepts colors "clock_tick"): template token type "clock_tick" '
        'declares destination "museum_clock", with literal data {"now":0}'
    ) in open_desk
    _assert_in_order(
        open_desk,
        [
            'place "desk_status"',
            'place "museum_clock"',
        ],
    )

    assert (
        "sink transition because it has no immediate output arcs"
        in paragraph_by_name['"decline_loan"']
    )

    # and: the complete immediate input vocabulary retains conditions and weights
    screen = paragraph_by_name['"screen_request"']
    assert (
        'place "loan_requests" (accepts colors "loan_request"; has an "input" port accepting '
        '"loan_request"; declares description "Externally supplied loan requests awaiting intake '
        'screening."; declares opaque annotations {"interface":"loan-intake"}), consuming a '
        'matching token of type "loan_request" whose opaque predicate handler is '
        '"request_is_complete@museum_loan" (arc declares description "Consumes only requests '
        'accepted by the named screening predicate handler.", opaque annotations '
        '{"role":"request-filter"})'
    ) in screen
    assert (
        'place "catalog_entries" (accepts colors "catalog_entry"), reading and binding a '
        'matching token of type "catalog_entry" whose opaque CEL predicate is "loanable == true" '
        "while preserving those tokens"
    ) in screen
    assert (
        'place "intake_pause" (accepts colors "intake_pause"), an inhibit check requiring no '
        'matching token of type "intake_pause" and removing none'
    ) in screen
    assert (
        'place "active_reservations" (accepts colors "active_reservation"; has capacity 1 token '
        'per distinct value of "artifactId"), a correlated inhibit check requiring, for each '
        'candidate binding, no matching token of type "active_reservation" satisfying opaque '
        'correlate CEL "token.artifactId == binding.loan_requests[0].artifactId", and removing none'
    ) in screen
    assert (
        'place "desk_status" (accepts colors "desk_status"), reading and binding a matching '
        'token of type "desk_status" while preserving those tokens'
    ) in screen
    _assert_in_order(
        screen,
        [
            'place "loan_requests"',
            'place "catalog_entries"',
            'place "desk_status"',
            'place "intake_pause"',
            'place "active_reservations"',
        ],
    )
    assert (
        'place "condition_reports" (accepts colors "condition_report"), consuming 2 matching '
        'tokens of type "condition_report"'
    ) in paragraph_by_name['"dispatch_loan"']

    # and: non-default transition declarations and output templates remain opaque routing facts
    assert "It declares priority 20." in screen
    assert (
        'It declares opaque guard "receipt_matches_reservation@museum_loan".'
        in paragraph_by_name['"confirm_reservation"']
    )
    dispatch = paragraph_by_name['"dispatch_loan"']
    assert (
        'It declares opaque guard "reports_match_reservation@museum_loan".' in dispatch
    )
    assert (
        'It declares a timer using clock "museum_clock", opaque CEL '
        '"clock.now >= reservation.expiresAt" and bindings {"reservation":"active_reservations"}.'
        in paragraph_by_name['"expire_reservation"']
    )
    assert (
        'postcondition routes output through place "dispatch_handoff" (accepts colors '
        '"dispatch_handoff"; has an "output" port accepting "dispatch_handoff"): template token '
        'type "dispatch_handoff" declares destination "dispatch_handoff".'
    ) in dispatch

    # and: the document never claims to evaluate opaque declarations
    assert (
        "does not infer behavior from names, expressions, descriptions, or annotations"
        in document
    )
    assert (
        "does not resolve handlers, evaluate CEL, load source, or follow composition"
        in document
    )


def test_handlerless_transition_explains_that_no_behavior_is_bound() -> None:
    """Given a traditional transition, explanation states the absent behavior."""
    # given: a parsed handlerless transition with no invented reference
    net = parse_net(
        {
            "name": "traditional",
            "places": [],
            "transitions": [{"name": "step"}],
            "arcs": [],
        }
    )
    # when: explaining the validated core net
    paragraph = dict(_transition_paragraphs(explain_net(net)))['"step"']
    # then: the prose explicitly distinguishes structure from bound behavior
    assert "No behavior handler is bound to this transition." in paragraph
    assert "opaque handler reference" not in paragraph


def test_only_non_default_declarations_are_emitted_and_unmarked_isolated_places_remain_visible() -> (
    None
):
    """Given a sparse Net, absent facts are omitted while its standalone place is retained."""
    net = parse_net(
        {
            "name": "sparse",
            "places": [
                {"name": "inbox", "accepts": ["message"]},
                {"name": "outbox", "accepts": ["message"]},
                {"name": "orphan", "accepts": ["note"]},
            ],
            "transitions": [
                {"name": "start", "handler": "start@demo"},
                {"name": "discard", "handler": "discard@demo"},
            ],
            "arcs": [
                {
                    "from": {"transition": "start"},
                    "to": {"place": "outbox"},
                    "produce": {
                        "type": "message",
                        "destination": "outbox",
                        "data": {"z": 1, "a": 2},
                    },
                },
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "discard"},
                    "consume": {"type": "message"},
                },
            ],
        }
    )

    # when: it has no marking and defaults were never declared
    document = explain_net(net)
    paragraphs = dict(_transition_paragraphs(document))

    # then: absent marking is not invented, a disconnected place is prose, and JSON is canonical
    assert "Initial marking" not in document
    assert (
        'Place "orphan" (accepts colors "note") has no adjacent transition.' in document
    )
    assert (
        'template token type "message" declares destination "outbox", '
        'with literal data {"a":2,"z":1}'
    ) in paragraphs['"start"']
    assert (
        "source transition because it has no immediate input arcs"
        in paragraphs['"start"']
    )
    assert (
        "sink transition because it has no immediate output arcs"
        in paragraphs['"discard"']
    )

    # and: no declaration-free placeholder or absent condition leaks into the prose
    assert "none declared" not in document
    for absent_fact in (
        "opaque guard",
        "declares priority",
        "declares a timer",
        "opaque predicate",
        "declares description",
        "opaque annotations",
    ):
        assert absent_fact not in document


def test_produce_cel_template_narrates_computed_fallback() -> None:
    """Given a produce template carrying a computed CEL fallback (ADR 0023),
    the output clause names the expression and its deposit-time fallback role."""
    # given: a transition consuming a binding whose produce template computes
    # its fallback data from that binding (the consume arc is what the CEL
    # expression's `binding.orders` reference resolves over)
    expression = '{"n": binding.orders[0].n - 1}'
    net = parse_net(
        {
            "name": "counter",
            "places": [
                {"name": "orders", "accepts": ["order"]},
                {"name": "count_store", "accepts": ["count"]},
            ],
            "transitions": [{"name": "sell", "handler": "sell@demo"}],
            "arcs": [
                {
                    "from": {"place": "orders"},
                    "to": {"transition": "sell"},
                    "consume": {"type": "order"},
                },
                {
                    "from": {"transition": "sell"},
                    "to": {"place": "count_store"},
                    "produce": {
                        "type": "count",
                        "destination": "count_store",
                        "cel": expression,
                    },
                },
            ],
        }
    )

    # when: explaining the validated core net
    paragraph = dict(_transition_paragraphs(explain_net(net)))['"sell"']

    # then: the output clause names the opaque expression and states that it
    # computes token data from the consumed binding only when the handler
    # leaves the destination/type pair uncovered
    assert (
        f'template token type "count" declares destination "count_store", '
        f"with data computed by opaque CEL {_quoted(expression)} over the "
        "consumed binding when the handler leaves this destination/type pair "
        "uncovered"
    ) in paragraph
    # and: the computed fallback is never misnarrated as a literal payload
    assert "literal data" not in paragraph


def test_newcomer_adds_orientation_without_changing_transition_declarations(
    museum_loan_net: Net,
) -> None:
    """Given either presentation, newcomer prose is additive and transition facts are identical."""
    for format in ("markdown", "text"):
        practitioner = explain_net(museum_loan_net, format=format, level="practitioner")
        newcomer = explain_net(museum_loan_net, format=format, level="newcomer")
        transition_heading = _heading("Transition flow", format)

        # then: shared transition declarations and the faithfulness note remain untouched
        assert (
            practitioner.split(transition_heading, maxsplit=1)[1]
            == newcomer.split(transition_heading, maxsplit=1)[1]
        )
        assert _transition_paragraphs(practitioner, format) == _transition_paragraphs(
            newcomer, format
        )

        # and: newcomers receive vocabulary that remains descriptive rather than behavioral
        assert newcomer.count(_heading("How to read", format)) == 1
        assert "read arcs preserve them while binding them" in newcomer
        assert "Output templates declare routing after the transition" in newcomer
        assert (
            "Handler references, CEL, descriptions, and annotations remain quoted opaque declarations"
            in newcomer
        )


def test_text_has_the_same_transition_paragraphs_without_markdown_syntax(
    museum_loan_net: Net,
) -> None:
    """Given plain text is requested, labels change but transition prose does not."""
    markdown = explain_net(museum_loan_net)
    text = explain_net(museum_loan_net, format="text")

    # then: plain labels replace Markdown headings without changing the prose contract
    assert text.startswith('NET: "museum_loan"\n')
    assert "\nTRANSITION FLOW\n" in text
    assert "\nFAITHFULNESS NOTE\n" in text
    assert '\nTRANSITION "screen_request"\n' in text
    assert "#" not in text
    assert _transition_paragraphs(markdown) == _transition_paragraphs(text, "text")


def test_invalid_selections_have_explicit_deterministic_results() -> None:
    """Given unsupported public choices, selection errors are not silently guessed."""
    net = parse_net(
        {
            "name": "unmarked",
            "places": [{"name": "inbox", "accepts": ["message"]}],
            "transitions": [{"name": "discard", "handler": "discard@demo"}],
            "arcs": [
                {
                    "from": {"place": "inbox"},
                    "to": {"transition": "discard"},
                    "consume": {"type": "message"},
                }
            ],
        }
    )

    # when: unsupported public selections are requested
    # then: each selection fails with its stable diagnostic
    with pytest.raises(ValueError, match='^format must be "markdown" or "text"$'):
        explain_net(net, format="html")  # type: ignore[arg-type]
    with pytest.raises(
        ValueError, match='^level must be "practitioner" or "newcomer"$'
    ):
        explain_net(net, level="expert")  # type: ignore[arg-type]
