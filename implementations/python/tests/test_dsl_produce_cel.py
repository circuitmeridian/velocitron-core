"""The ``data cel`` DSL arc fact (computed produce fallback, ADR 0023).

The red-phase test contract for the DSL leg of ADR 0023: ``@handle data cel
JsonString`` lowers to the produce template's ``cel`` field, canonical
emission prints it back as ``data cel``, and the fact composes with the
existing ``data`` fact rules (conflicts diagnosed, unknown handles rejected,
invalid CEL diagnosed at compile like ``predicate cel``).

These tests pin:

- **Lowering** — ``data cel`` targets a produce arc and lands on the
  template's ``cel`` field; literal ``data`` stays absent (rule 14 XOR).
- **Canonical emission** — a document whose template carries ``cel`` emits
  ``@handle data cel "<expr>"``, and the emitted source recompiles to the
  same document (round-trip).
- **Conflicts** — ``data`` and ``data cel`` on one handle conflict; two
  differing ``data cel`` facts conflict; an identical repetition is
  idempotent (the general duplicate-fact rule).
- **Compile timing** — invalid CEL in ``data cel`` fails at compile with a
  diagnostic, mirroring ``predicate cel`` (PN203), not at engine time.

The tests fail until the compiler lowers the grammar's ``DATA CEL STRING``
alternative, the resolver applies/validates it, and the canonical printer
emits it.

References: ADR 0023; spec/petrinet-language.md (ArcFact).
"""

from __future__ import annotations

import json

import pytest

from velocitron.dsl.api import compile_petrinet_text, emit_petrinet
from velocitron.dsl.diagnostics import PetrinetDslError

_DECREMENT = '{"n": binding.counter[0].n - 1}'

# The Reisig counter as DSL source (research note Figs 1.9-1.11): sell
# consumes the count token and re-produces it decremented.
_COUNTER_SOURCE = f"""\
net counter

@sale: (counter) -count-> [sell]
@restock: [sell] -count-> (counter)
@restock data cel {json.dumps(_DECREMENT)}
"""


def test_data_cel_fact_lowers_to_the_template_cel_field() -> None:
    # Given the counter source with a data cel fact on the produce arc.
    # When the source is compiled.
    document = compile_petrinet_text(_COUNTER_SOURCE, "counter.petrinet")

    # Then the produce template carries cel and no literal data (rule 14).
    produce_arcs = [a for a in document["arcs"] if "produce" in a]
    assert len(produce_arcs) == 1
    assert produce_arcs[0]["produce"] == {
        "type": "count",
        "destination": "counter",
        "cel": _DECREMENT,
    }


def test_canonical_emission_round_trips_data_cel() -> None:
    # Given the compiled counter document.
    document = compile_petrinet_text(_COUNTER_SOURCE, "counter.petrinet")

    # When it is canonically emitted and recompiled.
    canonical = emit_petrinet(document)
    recompiled = compile_petrinet_text(canonical, "counter-canonical.petrinet")

    # Then the emission carries the data cel fact and the round trip is exact.
    assert f"data cel {json.dumps(_DECREMENT)}".replace("'", '"') in canonical or (
        f"data cel {_DECREMENT}" in canonical
    )
    assert recompiled["arcs"] == document["arcs"]


def test_literal_data_and_data_cel_on_one_handle_conflict() -> None:
    # Given a produce arc carrying both a literal data fact and a cel fact.
    source = f"""\
net conflict

@restock: [sell] -count-> (counter)
@restock data {{"n": 0}}
@restock data cel {json.dumps(_DECREMENT)}
"""

    # When/then: compiling fails with a diagnostic (rule 14 XOR).
    with pytest.raises(PetrinetDslError):
        compile_petrinet_text(source, "conflict.petrinet")


def test_differing_data_cel_facts_conflict_and_identical_are_idempotent() -> None:
    # Given two DIFFERING data cel facts on one handle.
    conflicting = f"""\
net conflict

@restock: [sell] -count-> (counter)
@restock data cel {json.dumps(_DECREMENT)}
@restock data cel "{{}}"
"""
    # When/then: compiling fails with a diagnostic.
    with pytest.raises(PetrinetDslError):
        compile_petrinet_text(conflicting, "conflicting-cel.petrinet")

    # And given the SAME fact repeated verbatim.
    idempotent = f"""\
net idempotent

@restock: [sell] -count-> (counter)
@restock data cel {json.dumps(_DECREMENT)}
@restock data cel {json.dumps(_DECREMENT)}
"""
    # When it is compiled.
    document = compile_petrinet_text(idempotent, "idempotent-cel.petrinet")
    # Then the repetition is idempotent.
    produce_arcs = [a for a in document["arcs"] if "produce" in a]
    assert produce_arcs[0]["produce"]["cel"] == _DECREMENT


def test_data_cel_on_a_consume_arc_is_rejected() -> None:
    # Given a data cel fact targeting a consume arc (data targets produce).
    source = """\
net wrong_mode

@sale: (counter) -count-> [sell]
@sale data cel "true"
"""

    # When/then: compiling fails with a diagnostic.
    with pytest.raises(PetrinetDslError):
        compile_petrinet_text(source, "wrong-mode.petrinet")


def test_invalid_cel_in_data_cel_fails_at_compile() -> None:
    # Given a data cel fact whose expression is malformed.
    source = """\
net invalid

@restock: [sell] -count-> (counter)
@restock data cel "{\\"n\\": binding.counter[0].n -"
"""

    # When/then: compiling fails with a diagnostic, mirroring predicate cel
    # (the expression never reaches the engine).
    with pytest.raises(PetrinetDslError):
        compile_petrinet_text(source, "invalid-cel.petrinet")
