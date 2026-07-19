"""Composition validation lock-the-coverage tests (``[lock] composition engine
(merge nets + add cross-net wires between ports)``).

The coverage-*lock* for the composition *validation* surface of
``spec/composition.md`` rules 1–5 + the parser-authoritative derived-alias
identifier enforcement (D4). The composition behavioral code already exists
on ``main`` (co-evolved during ``net-schema`` / the parser/validator feature),
in one module:

- ``parser.py`` — ``parse_composition``: loads the composition document,
  validates it against the embedded ``_COMPOSITION_SCHEMA``, then walks
  ``nets[]`` and ``wires[]`` applying the five composition-level validation
  rules. The supporting seam: ``parse_net`` (per ``nets[].ref`` → constituent
  net validity, rule 5), the alias-derivation loop (omitted ``alias`` defaults
  to the referenced net's ``name``; a derived alias from a non-identifier net
  name is rejected — D4, the enforcement the schema's ``pattern`` cannot reach
  for derived defaults), ``_validate_wires`` (type-compat),
  ``_resolve_wire_port`` (no-dangling-ports + direction-compat),
  and ``_find_port``.

Per the lock-feature gate (AGENTS.md), no behavioral change to ``parser.py``
or ``engine.py`` is sanctioned — the surface already conforms to
``spec/composition.md`` rules 1–5 + D4. This is a pure coverage lock: the
deliverable is biting tests that pin the alias-derivation default (CO1), the
derived-alias identifier enforcement (CO2), alias uniqueness's two faces
(CO3/CO4), the two dangling-port faces (CO5/CO6), direction compatibility's
two faces (CO7), type compatibility (CO8), and constituent net validity
(CO9). Each test passes against the existing green impl and was verified to
bite under a targeted reversion that breaks the invariant it pins.

Scope boundary (Q1 → A1=(a)): the *runtime merge engine* — actually producing
the combined ``Net`` (alias qualification, port-place fusion, arc-endpoint
rewriting, re-exposing unwired ports) — is deferred by
``spec/composition.md`` "Out of scope" / ADR 0004. No merge code exists today;
``parse_composition`` returns a ``Composition`` (a bag of ``NetRef``s +
``Wire``s), never a fused ``Net``. A ``[lock]`` feature cannot introduce
behavior, so there is no merge surface to lock. This lock pins only the
*validator* that gates whether a composition is well-formed enough to merge;
the merge engine is enqueued as a separate
``(composition-merge-engine)`` ``[impl]`` backlog item.

Boundary with co-evolution tests: ``test_parser.py``'s ``TestComposition``
(happy-path smoke) and ``TestWireValidation`` (7 outcome-level rejection
tests) are KEPT as outcome-level smoke; this lock file is the authoritative
bite-verified coverage and adds the two faces the co-evolution suite misses
(CO5 unknown-alias dangling; CO9 net-validity). No co-evolution test is
deleted → no one-way-door subsumption-reliance axis. ``TestSchemaStrictness``
/ ``TestSchemaSync`` own the embedded-schema shape (a distinct concern); the
``(net-schema-parser)`` lock / ``TestPorts`` owns the single-net port facet;
this lock owns the cross-net wire surface that *consumes* those facets — no
overlap.

Minimal nets (cross-surface principle): the validator resolves port facets,
aliases, and constituent net-schema validity — never arcs, never handlers.
CO1–CO8 fixture nets carry only the port places the validator resolves, with
``transitions: []`` and ``arcs: []`` (a decorative consume/produce arc the
validator never resolves is noise). No ``Engine``/``HandlerRegistry`` is
constructed and no handlers are registered — the path invokes none. CO9's
invalid-net fixture carries the specific schema violation under test (a place
missing ``accepts``) and nothing else.

Timing pin (where/when): every CO test calls ONLY ``parse_composition``,
never any merge/engine surface (no merge surface exists). A rejection
deferred to a (future) merge would not raise at ``parse_composition`` and the
test would fail — this is stated in each docstring rather than a standalone
test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from velocitron.parser import NetValidationError, parse_composition
from velocitron.schema import Composition


# ── Shared helpers ──────────────────────────────────────────────────────


def _write_net(tmp_path: Path, filename: str, net: dict[str, Any]) -> Path:
    """Write a net dict to ``tmp_path / filename`` as JSON; return the path."""
    path = tmp_path / filename
    path.write_text(json.dumps(net))
    return path


def _prod_out_port() -> dict[str, Any]:
    """A single output port place named ``out``, type ``task``."""
    return {
        "name": "out",
        "accepts": ["task"],
        "port": {"direction": "output", "type": "task"},
    }


def _cons_in_port() -> dict[str, Any]:
    """A single input port place named ``in``, type ``task``."""
    return {
        "name": "in",
        "accepts": ["task"],
        "port": {"direction": "input", "type": "task"},
    }


def _minimal_net(name: str, places: list[dict[str, Any]]) -> dict[str, Any]:
    """A minimal net dict: name + places + empty transitions/arcs. The
    composition validator never resolves arcs or transitions, so these stay
    empty (no decorative structure)."""
    return {"name": name, "places": places, "transitions": [], "arcs": []}


# ── Cluster 1: Alias derivation & uniqueness (the parse_composition loop) ──


class TestCompositionAliasDerivation:
    """CO1–CO4 — the alias-derivation loop in ``parse_composition``. CO1 pins
    the omitted-alias default (derives to the net's ``name``) and that a wire
    resolves under that derived alias. CO2 pins the parser-authoritative
    derived-alias identifier enforcement (D4) the schema's ``pattern`` cannot
    reach for derived defaults. CO3/CO4 pin alias uniqueness's two faces
    (explicit collision; derived collision). The co-evolution
    ``test_parser.py::TestComposition`` / ``TestWireValidation`` tests
    (``test_alias_defaults_to_net_name``,
    ``test_duplicate_alias_explicit``,
    ``test_duplicate_alias_derived_default``,
    ``test_derived_alias_from_non_identifier_net_name_rejected``) are
    outcome-only; CO1–CO4 bite-verify."""

    def test_co1_omitted_alias_defaults_to_net_name_and_wire_resolves(
        self, tmp_path: Path
    ):
        """CO1 — an omitted ``alias`` defaults to the referenced net's ``name``,
        and a wire resolves under that derived alias. Two nets with NO explicit
        aliases and one wire referencing the derived aliases
        (``producer`` / ``consumer``). The test calls only
        ``parse_composition``; a derived alias deferred to a (future) merge
        would leave the wire's ``net`` field unresolved at parse → the merge,
        not ``parse_composition``, would surface the mismatch → the success
        assertion would fail — pinning the parse-time derivation timing.

        Reversion-verified bite: reverting the
        ``if alias is None: alias = parsed.name`` default — replacing
        ``alias = parsed.name`` with ``alias = "_" + parsed.name`` so the
        derived alias no longer equals the net's name (``_producer`` instead
        of ``producer``) — makes the wire's ``net: "producer"`` fail to match
        the ``_producer``-keyed ``alias_to_net`` → ``_resolve_wire_port``
        raises "unknown net alias" → ``parse_composition`` raises instead of
        succeeding → the ``comp.wires[0].from_net == "producer"`` assertion
        fails. Confirmed-bites."""
        # given: a producer with an output port and a consumer with an input
        # port, both with NO explicit alias (so the alias defaults to the
        # net's name), and a wire referencing the derived aliases
        path_prod = _write_net(
            tmp_path, "producer.json", _minimal_net("producer", [_prod_out_port()])
        )
        path_cons = _write_net(
            tmp_path, "consumer.json", _minimal_net("consumer", [_cons_in_port()])
        )
        comp_dict = {
            "nets": [{"ref": str(path_prod)}, {"ref": str(path_cons)}],
            "wires": [
                {
                    "from": {"net": "producer", "port": "out"},
                    "to": {"net": "consumer", "port": "in"},
                }
            ],
        }
        # when: parsing the composition
        comp = parse_composition(comp_dict)
        # then: it succeeds — the omitted aliases derived to the net names
        assert isinstance(comp, Composition)
        assert comp.nets[0].alias == "producer"
        assert comp.nets[1].alias == "consumer"
        # and: the wire resolved under the derived aliases
        assert comp.wires[0].from_net == "producer"
        assert comp.wires[0].to_net == "consumer"

    def test_co2_derived_alias_from_non_identifier_net_name_rejected_at_parse(
        self, tmp_path: Path
    ):
        """CO2 — a derived alias from a non-identifier net ``name`` (e.g.
        ``prod.line``) is rejected at PARSE, not silently accepted. The
        derived alias bypasses the schema's ``pattern`` (which governs only
        *explicit* aliases), so the parser is the authoritative enforcer (D4):
        ``prod.line`` would make ``<alias>.<placeName>`` ambiguous
        (``prod.line.out`` = alias ``prod.line`` + port ``out`` vs alias
        ``prod`` + port ``line.out``). The test calls only
        ``parse_composition``; a deferral to a merge would not raise here →
        the test would fail — pinning parse-time enforcement.

        Reversion-verified bite: reverting the
        ``if not _ALIAS_PATTERN.fullmatch(alias)`` guard (dropping the check)
        lets the ambiguous derived alias ``prod.line`` through →
        ``parse_composition`` no longer raises → the ``pytest.raises``
        assertion fails. Confirmed-bites."""
        # given: a producer whose name contains a dot (a valid net name, but
        # not a valid composition alias) with no explicit alias (so it
        # defaults to the net name "prod.line"); the raise at parser.py:707
        # (_ALIAS_PATTERN.fullmatch) precedes port resolution at line 731, so
        # port places are decorative — the bite is name-driven; places: []
        path_prod = _write_net(
            tmp_path,
            "producer.json",
            _minimal_net("prod.line", []),
        )
        comp_dict = {
            "nets": [{"ref": str(path_prod)}],
            "wires": [],
        }
        # when/then: parsing raises NetValidationError — the derived alias
        # "prod.line" is not a simple identifier and must be rejected at parse
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)

    def test_co3_duplicate_explicit_alias_rejected(self, tmp_path: Path):
        """CO3 — alias uniqueness, explicit face: two ``nets[]`` given the same
        explicit ``alias`` are rejected at parse. The test calls only
        ``parse_composition``; a deferral to a merge would not raise here →
        the test would fail.

        Reversion-verified bite: reverting the
        ``if alias in alias_to_net`` duplicate check lets both nets register
        under ``"dup"`` (the second silently overwrites the first) →
        ``parse_composition`` no longer raises → the ``pytest.raises``
        assertion fails. Confirmed-bites."""
        # given: two nets both aliased "dup", no wires
        path_a = _write_net(tmp_path, "a.json", _minimal_net("net_a", []))
        path_b = _write_net(tmp_path, "b.json", _minimal_net("net_b", []))
        comp_dict = {
            "nets": [
                {"ref": str(path_a), "alias": "dup"},
                {"ref": str(path_b), "alias": "dup"},
            ],
            "wires": [],
        }
        # when/then: parsing raises NetValidationError — duplicate alias
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)

    def test_co4_duplicate_derived_alias_rejected(self, tmp_path: Path):
        """CO4 — alias uniqueness, derived face: two nets sharing the same
        ``name`` and no explicit alias collide on the derived default and are
        rejected at parse. Distinct face from CO3 (derived collision vs
        explicit collision — the combinatorial topology). The test calls only
        ``parse_composition``; a deferral to a merge would not raise here →
        the test would fail.

        Reversion-verified bite: the mechanic is shared with CO3 — one
        ``alias_to_net`` membership check guards both the explicit and the
        derived collision — so one representative reversion is run (revert the
        ``if alias in alias_to_net`` duplicate check); the remaining face
        inherits the bite (representative-reversion framing per AGENTS.md).
        Confirmed-bites."""
        # given: two distinct nets both named "same", no explicit alias (so
        # both derive the alias "same" and collide), no wires
        path_a = _write_net(tmp_path, "a.json", _minimal_net("same", []))
        path_b = _write_net(tmp_path, "b.json", _minimal_net("same", []))
        comp_dict = {
            "nets": [{"ref": str(path_a)}, {"ref": str(path_b)}],
            "wires": [],
        }
        # when/then: parsing raises NetValidationError — derived alias collision
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)


# ── Cluster 2: Wire validation (_validate_wires / _resolve_wire_port) ──────


class TestCompositionWireValidation:
    """CO5–CO8 — wire validation. CO5/CO6 pin the two dangling-port faces
    (unknown alias; missing port name on a known alias). CO7 pins direction
    compatibility's two faces (source-is-input; target-is-output). CO8 pins
    type compatibility. The co-evolution ``test_parser.py::TestWireValidation``
    tests cover CO6/CO7/CO8 outcome-only; CO5 (unknown-alias dangling) is a
    face the co-evolution suite MISSES — ``test_wire_dangling_port`` covers
    only CO6 (missing port on a KNOWN alias)."""

    def test_co5_dangling_port_unknown_alias_rejected(self, tmp_path: Path):
        """CO5 — no dangling ports, face A: a wire referencing an UNKNOWN alias
        (a ``net`` not in ``nets[]``) is rejected at parse. This constructs the
        combinatorial face the co-evolution suite misses
        (``test_wire_dangling_port`` covers only face B — a missing port on a
        KNOWN alias). The test calls only ``parse_composition``; a deferral to
        a merge would not raise here → the test would fail.

        Reversion-verified bite: reverting the
        ``if alias not in alias_to_net`` raise in ``_resolve_wire_port``
        (e.g. returning a sentinel port instead of raising) lets the unknown
        alias ``"ghost"`` pass → ``parse_composition`` no longer raises → the
        ``pytest.raises`` assertion fails. Confirmed-bites."""
        # given: a consumer net (the wire's `to` references "cons") and a wire
        # whose `from` references "ghost" — an alias not in nets[]. The alias
        # check at _resolve_wire_port raises before _find_port is called, so
        # port places are never resolved — places: [] (schema-valid, bite
        # unaffected). The producer net is decorative (no wire endpoint
        # references "prod") — dropped per the minimal-net convention.
        path_cons = _write_net(tmp_path, "consumer.json", _minimal_net("consumer", []))
        comp_dict = {
            "nets": [{"ref": str(path_cons), "alias": "cons"}],
            "wires": [
                {
                    "from": {"net": "ghost", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                }
            ],
        }
        # when/then: parsing raises NetValidationError — unknown alias
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)

    def test_co6_dangling_port_missing_port_name_rejected(self, tmp_path: Path):
        """CO6 — no dangling ports, face B: a wire referencing a port NAME that
        is not a declared port facet on a KNOWN net is rejected at parse. The
        test calls only ``parse_composition``; a deferral to a merge would not
        raise here → the test would fail.

        Reversion-verified bite: reverting the ``if port is None`` raise in
        ``_resolve_wire_port`` (the ``_find_port(...) is None`` check — e.g.
        returning a dummy port instead of raising) lets the nonexistent port
        ``"nonexistent"`` pass → ``parse_composition`` no longer raises → the
        ``pytest.raises`` assertion fails. Confirmed-bites."""
        # given: a producer and consumer net, and a wire whose `from` references
        # a port name ("nonexistent") that does not exist on the producer.
        # The producer carries `places: []` — `_find_port` iterates the empty
        # place list, returns None, and the `if port is None` raise fires
        # before the consumer's port is reached — so the consumer's port
        # places are never resolved: `places: []` on both (schema-valid,
        # bite unaffected).
        path_prod = _write_net(tmp_path, "producer.json", _minimal_net("producer", []))
        path_cons = _write_net(tmp_path, "consumer.json", _minimal_net("consumer", []))
        comp_dict = {
            "nets": [
                {"ref": str(path_prod), "alias": "prod"},
                {"ref": str(path_cons), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "nonexistent"},
                    "to": {"net": "cons", "port": "in"},
                }
            ],
        }
        # when/then: parsing raises NetValidationError — missing port name
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)

    @pytest.mark.parametrize(
        "face, prod_port, cons_port, wire_from, wire_to",
        [
            pytest.param(
                "source-is-input",
                {
                    "name": "ctrl",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {
                    "name": "in",
                    "accepts": ["task"],
                    "port": {"direction": "input", "type": "task"},
                },
                {"net": "prod", "port": "ctrl"},
                {"net": "cons", "port": "in"},
                id="source-is-input",
            ),
            pytest.param(
                "target-is-output",
                {
                    "name": "out",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
                {
                    "name": "ack",
                    "accepts": ["task"],
                    "port": {"direction": "output", "type": "task"},
                },
                {"net": "prod", "port": "out"},
                {"net": "cons", "port": "ack"},
                id="target-is-output",
            ),
        ],
    )
    def test_co7_direction_compatibility_rejected(
        self,
        tmp_path: Path,
        face: str,
        prod_port: dict[str, Any],
        cons_port: dict[str, Any],
        wire_from: dict[str, Any],
        wire_to: dict[str, Any],
    ):
        """CO7 — direction compatibility: a wire's ``from`` must be an OUTPUT
        port and its ``to`` must be an INPUT port. Two faces parametrized:
        ``source-is-input`` (the ``from`` port declares ``input`` — must be
        ``output``) and ``target-is-output`` (the ``to`` port declares
        ``output`` — must be ``input``). The test calls only
        ``parse_composition``; a deferral to a merge would not raise here →
        the test would fail.

        Reversion-verified bite: the mechanic is shared across the
        per-direction table — one ``if port.direction != direction`` raise in
        ``_resolve_wire_port`` guards both faces (the source call enforces
        ``"output"``; the target call enforces ``"input"``) — so one
        representative reversion is run (revert the direction check); the
        remaining face inherits the bite (representative-reversion framing per
        AGENTS.md). Confirmed-bites."""
        # given: a producer and consumer whose wired port declares the WRONG
        # direction for its wire role, and a wire joining them
        path_prod = _write_net(
            tmp_path, "producer.json", _minimal_net("producer", [prod_port])
        )
        path_cons = _write_net(
            tmp_path, "consumer.json", _minimal_net("consumer", [cons_port])
        )
        comp_dict: dict[str, Any] = {
            "nets": [
                {"ref": str(path_prod), "alias": "prod"},
                {"ref": str(path_cons), "alias": "cons"},
            ],
            "wires": [{"from": wire_from, "to": wire_to}],
        }
        # when/then: parsing raises NetValidationError — direction mismatch
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)

    def test_co8_type_compatibility_rejected(self, tmp_path: Path):
        """CO8 — type compatibility: a wire joining ports of DIFFERENT types is
        rejected at parse (ADR 0004 — tokens flowing across a wire keep their
        color). The test calls only ``parse_composition``; a deferral to a
        merge would not raise here → the test would fail.

        Reversion-verified bite: reverting the
        ``if from_port.type != to_port.type`` check in ``_validate_wires``
        (e.g. dropping the comparison) lets the type mismatch (``task`` →
        ``email``) pass → ``parse_composition`` no longer raises → the
        ``pytest.raises`` assertion fails. Confirmed-bites."""
        # given: a producer with a task output port and a consumer with an
        # email input port, and a wire joining the mismatched types
        path_prod = _write_net(
            tmp_path,
            "producer.json",
            _minimal_net("producer", [_prod_out_port()]),
        )
        path_cons = _write_net(
            tmp_path,
            "consumer.json",
            _minimal_net(
                "consumer",
                [
                    {
                        "name": "in",
                        "accepts": ["email"],
                        "port": {"direction": "input", "type": "email"},
                    },
                ],
            ),
        )
        comp_dict = {
            "nets": [
                {"ref": str(path_prod), "alias": "prod"},
                {"ref": str(path_cons), "alias": "cons"},
            ],
            "wires": [
                {
                    "from": {"net": "prod", "port": "out"},
                    "to": {"net": "cons", "port": "in"},
                }
            ],
        }
        # when/then: parsing raises NetValidationError — type mismatch
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)


# ── Cluster 3: Net validity (rule 5) ──────────────────────────────────────


class TestCompositionNetValidity:
    """CO9 — constituent net validity chains to composition validity (rule 5).
    This is a GAP in the co-evolution suite — no ``TestWireValidation`` test
    exercises an invalid constituent net."""

    def test_co9_invalid_constituent_net_rejected_at_parse(self, tmp_path: Path):
        """CO9 — a composition referencing a net that fails net-schema
        validation is rejected at ``parse_composition`` (constituent net
        validity chains to composition validity, rule 5). Each ``nets[].ref``
        is validated by ``parse_net`` in the ``nets[]`` loop, so an invalid
        constituent surfaces at parse, not deferred to a merge. The test
        calls only ``parse_composition``; a deferral to a merge would not
        raise here → the test would fail — pinning the parse-time
        net-validity timing. No wire is carried: the net-validity surface
        under test resolves the constituent net ref, never a wire (a wire the
        surface never resolves is noise — cross-surface principle);
        ``wires: []``.

        Reversion-verified bite: reverting the ``parsed = parse_net(ref)``
        call in the ``parse_composition`` loop — wrapping it in
        ``try/except NetValidationError`` that substitutes a dummy ``Net`` so
        an invalid constituent is silently accepted — lets the
        structurally-invalid net (a place missing ``accepts``) pass →
        ``parse_composition`` no longer raises → the ``pytest.raises``
        assertion fails. Confirmed-bites."""
        # given: a net that is structurally invalid against the net schema
        # (a place missing `accepts`). The raise on the first (and only)
        # nets[] entry means a second net is never reached — a decorative
        # consumer net is noise under the minimality convention; one invalid
        # net suffices
        invalid_net = {
            "name": "badnet",
            "places": [
                {"name": "out", "port": {"direction": "output", "type": "task"}}
            ],
            "transitions": [],
            "arcs": [],
        }
        path_net = _write_net(tmp_path, "badnet.json", invalid_net)
        comp_dict = {
            "nets": [{"ref": str(path_net)}],
            "wires": [],
        }
        # when/then: parsing raises NetValidationError — the invalid
        # constituent net surfaces from the inner parse_net
        with pytest.raises(NetValidationError):
            parse_composition(comp_dict)
