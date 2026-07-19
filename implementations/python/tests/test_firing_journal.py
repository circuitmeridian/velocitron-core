"""Firing-journal lock-the-coverage tests (``[lock] firing journal``).

The coverage-*lock* for ``spec/firing-semantics.md`` §(d) journal implementation
+ D4 (decoupling) + D5 (replay): the journal surface behavioral code already
exists on ``main`` (co-evolved during ``firing-semantics``); per AGENTS.md this
is a lock-the-coverage pass, not red-then-green -- each test passes against the
existing (correct) impl and was verified to bite under a targeted reversion that
breaks the invariant it pins.

Scope boundary: sibling ``(firing-engine)`` owns ``fire()`` + record *emission*
(record content F8-F10, per-cause hook routing F5/F6/F11-F13);
``(firing-policy)`` owns §(e)'s selection loop (P5 pins selection-level replay).
This lock owns what the *journal* does with a record it receives, and the
*decoupling* invariants (no-sequence, optional, protocol satisfiability), and
*journal-level* replay (record-for-record equality across two full runs, the
comparison rule, failed-record replay).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.journal import (
    FiringRecord,
    FiringStatus,
    InjectionRecord,
    Journal,
    JsonlJournal,
)
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token


# ── Shared helpers ───────────────────────────────────────────────────────
# Carried over only where a J-test already calls the shape (the firing-policy
# retro's "no speculative helper extraction" lesson). ``_bare_engine`` is NOT
# carried: no J-test uses an empty-registry engine.


def _tok(t: str = "task", **data: Any) -> Token:
    """A minimal token of type ``t`` with payload ``data``."""
    return Token(type=t, data=dict(data))


def _marking(**places: list[Token]) -> Marking:
    """A marking from ``place=tokens`` keyword pairs."""
    return Marking({place: list(toks) for place, toks in places.items()})


def _net(d: dict[str, Any]) -> Net:
    """Parse a net dict (thin alias for the parser)."""
    return parse_net(d)


def _engine(reg: HandlerRegistry, *, journal: Journal | None = None) -> Engine:
    """An Engine over ``reg`` with the given journal (default ``raise`` mode)."""
    return Engine(reg, journal=journal, deposit_violation="raise")


def _reg(*pairs: tuple[str, Any]) -> HandlerRegistry:
    """A registry from ``(name, handler)`` pairs of transition handlers."""
    reg = HandlerRegistry()
    for name, fn in pairs:
        reg.register_transition(name, fn)
    return reg


class _CapturingJournal:
    """In-memory Journal: captures records, assigns no ``sequence`` (D4)."""

    def __init__(self) -> None:
        self.firings: list[FiringRecord] = []
        self.violations: list[FiringRecord] = []
        self.injections: list[InjectionRecord] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.firings.append(record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self.violations.append(record)

    def record_injection(self, record: InjectionRecord) -> None:
        self.injections.append(record)


def _make_record(
    transition: str,
    status: FiringStatus,
    *,
    input_tokens: dict[str, list[Token]] | None = None,
) -> FiringRecord:
    """Construct a minimal ``FiringRecord`` for journal-unit tests (J1-J5).

    No ``sequence`` -- the engine never emits one (D4); the journal assigns it.
    """
    return FiringRecord(
        firingId=f"test-net/{transition}/0",
        netId="test-net",
        transition=transition,
        attempt=0,
        status=status,
        inputTokens={} if input_tokens is None else input_tokens,
        outputTokens={},
        error=None,
        metadata={},
        timestamps={"fired_at": "2026-01-01T00:00:00Z"},
    )


# ── Decoupling/replay nets (J6/J7/J9 share topology where it collapses) ──
# J6/J7: a one-transition passthrough (consume src -> produce dst). J9 needs
# >=2 firings (a single passthrough fires once), so it carries its own
# two-transition sequential net -- a distinct topology, not a collapsible pair.
# J10: consume-only (a failed fire deposits nothing, so a produce arc would be
# decorative). Every arc/place here is asserted-on or structurally required.


_PASSTHROUGH_NET = _net(
    {
        "name": "passthrough-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


_TWO_STEP_NET = _net(
    {
        "name": "two-step-net",
        "places": [
            {"name": "src", "accepts": ["task"]},
            {"name": "mid", "accepts": ["task"]},
            {"name": "dst", "accepts": ["task"]},
        ],
        "transitions": [
            {"name": "t1", "handler": "t1"},
            {"name": "t2", "handler": "t2"},
        ],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t1"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t1"},
                "to": {"place": "mid"},
                "produce": {"type": "task", "destination": "mid"},
            },
            {
                "from": {"place": "mid"},
                "to": {"transition": "t2"},
                "consume": {"type": "task"},
            },
            {
                "from": {"transition": "t2"},
                "to": {"place": "dst"},
                "produce": {"type": "task", "destination": "dst"},
            },
        ],
    }
)


_FAIL_NET = _net(
    {
        "name": "fail-net",
        "places": [{"name": "src", "accepts": ["task"]}],
        "transitions": [{"name": "t", "handler": "t"}],
        "arcs": [
            {
                "from": {"place": "src"},
                "to": {"transition": "t"},
                "consume": {"type": "task"},
            },
        ],
    }
)


def _passthrough(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Deposit the bound source tokens into dst.
    return {
        "status": "completed",
        "outputTokens": {"dst": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


def _step1(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # src -> mid.
    return {
        "status": "completed",
        "outputTokens": {"mid": inp["inputTokens"].get("src", [])},
        "error": None,
        "metadata": {},
    }


def _step2(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # mid -> dst.
    return {
        "status": "completed",
        "outputTokens": {"dst": inp["inputTokens"].get("mid", [])},
        "error": None,
        "metadata": {},
    }


def _fail_handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Always report failure -- a deterministic failed fire (D5 replay input).
    return {
        "status": "failed",
        "outputTokens": {},
        "error": {"type": "X", "message": "nope"},
        "metadata": {},
    }


# ── Cluster 1: JsonlJournal sequence & dual-stream numbering (D4) ────────


class TestJsonlJournalSequence:
    """J1-J2: the monotonic 0-based ``sequence`` and the single shared
    numbering stream across both record methods."""

    def test_sequence_is_monotonic_0_based_strict_plus_one(self):
        """J1: ``JsonlJournal`` assigns ``sequence`` 0, 1, 2, ... to successive
        records -- the 0-based START plus the strict +1 STEP topology, not just
        the outcome that the first two are 0 and 1.

        Reversion-verified bite: reverting ``_sequence = 0`` -> ``= 1`` (start
        at 1) fails the ``== 0`` first-record assertion; reverting
        ``_sequence += 1`` -> ``+= 2`` fails the strict-+1 step assertion
        (three records yield [0, 2, 4], not [0, 1, 2]). Three records (N >= 3)
        so a ``+= 2`` reversion is caught, not just an off-by-one. The
        co-evolution ``test_jsonl_journal_assigns_sequence`` asserts only
        ``== 0`` / ``== 1`` (outcome), not the start + step topology."""
        # given: a fresh JsonlJournal
        j = JsonlJournal()
        # when: recording three firings
        j.record_firing(_make_record("t1", "completed"))
        j.record_firing(_make_record("t2", "completed"))
        j.record_firing(_make_record("t3", "completed"))
        # then: sequences are exactly 0, 1, 2 (0-based start, strict +1 step)
        seqs = [r["sequence"] for r in j._records]  # pyright: ignore[reportPrivateUsage]
        assert seqs == [0, 1, 2]

    def test_firing_and_violation_share_one_numbering_stream(self):
        """J2: ``record_firing`` and ``record_deposit_violation`` share ONE
        numbering stream -- a deposit-violation record interleaved with firing
        records takes the next contiguous sequence, never a second stream.

        Reversion-verified bite: giving ``record_deposit_violation`` a SEPARATE
        counter (``self._vio_sequence``) and not routing it through
        ``_append`` yields interleaved sequences [0, 0, 1] (firing->0,
        violation->0, firing->1) instead of contiguous [0, 1, 2]; the
        contiguity assertion fails. Not locked anywhere -- the co-evolution
        journal tests call ``record_firing`` only."""
        # given: a fresh JsonlJournal
        j = JsonlJournal()
        # when: recording firing -> violation -> firing across both methods
        j.record_firing(_make_record("t1", "completed"))
        j.record_deposit_violation(_make_record("vio", "failed"))
        j.record_firing(_make_record("t2", "completed"))
        # then: the three records share one contiguous numbering stream
        seqs = [r["sequence"] for r in j._records]  # pyright: ignore[reportPrivateUsage]
        assert seqs == [0, 1, 2]
        # and: the violation record sits at position 1 (the middle slot)
        assert j._records[1]["transition"] == "vio"  # pyright: ignore[reportPrivateUsage]


# ── Cluster 2: JsonlJournal storage (D4) ────────────────────────────────


class TestJsonlJournalStorage:
    """J3-J5: ``flush`` disk behavior and ``Token`` serialization."""

    def test_prefix_none_flush_is_no_op_records_stay_buffered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """J3: with ``prefix=None`` (the default), ``flush`` is a no-op --
        nothing reaches disk and records stay buffered in memory.

        Construction-bite: the ``if self._prefix is None: return`` guard is the
        SOLE barrier to ``flush`` building a path from ``None`` and writing a
        literal ``None-<ISO8601>.jsonl`` file. ``f"{None}-{stamp}.jsonl"`` does
        not raise (it yields the string ``"None-..."``), so removing the guard
        silently writes a stray file into the cwd; the test chdir's into
        ``tmp_path`` so that stray file lands where the glob catches it, and
        the ``tmp_path`` .jsonl-empty assertion fails. No behavioural reversion
        was exercised -- removing the clause is the misbehavior."""
        # given: a prefix-less journal, cwd chdir'd into tmp_path so a stray
        # flush target would be observable here
        monkeypatch.chdir(tmp_path)  # pyright: ignore[reportUnknownMemberType]
        j = JsonlJournal()  # prefix=None
        j.record_firing(_make_record("t1", "completed"))
        # when: flushing with no prefix
        j.flush()
        # then: nothing is written to disk
        assert list(tmp_path.glob("*.jsonl")) == []
        # and: the record stays buffered in memory
        assert len(j._records) == 1  # pyright: ignore[reportPrivateUsage]

    def test_prefix_set_flush_writes_all_records_iso8601_creates_dirs_one_per_line(
        self, tmp_path: Path
    ):
        """J4: with ``prefix`` set, ``flush`` writes ALL buffered records to
        ``<prefix>-<ISO8601>.jsonl``, creating parent directories, one JSON
        object per line.

        Reversion-verified bite, representative across three sub-assertions --
        the filename-format / dir-creation / line-format mechanics are shared
        across the flushed-record surface, so one representative collapse per
        mechanic is run; the remaining sub-assertions inherit the bite:
        (a) reverting the suffix to a non-ISO8601 format (``%Y-%m-%d``) fails
        the ``\\d{8}T\\d{6}`` ISO8601-pattern assertion; (b) reverting
        ``mkdir(parents=True, exist_ok=True)`` to no mkdir fails when the
        prefix's parent dir does not yet exist (``path.open`` raises
        ``FileNotFoundError``); (c) reverting the per-line
        ``json.dumps(rec) + "\\n"`` to a single ``json.dumps(self._records)``
        yields one JSON-array line, so ``len(parsed_lines) == 2`` fails. The
        co-evolution ``test_jsonl_journal_writes_file`` checks a file exists +
        monotonic sequence only -- it does not pin the ISO8601 suffix, parent-dir
        creation, or the one-object-per-line line format."""
        # given: a journal whose prefix's parent dir does not yet exist
        prefix = str(tmp_path / "subdir" / "nested" / "firings")
        j = JsonlJournal(prefix=prefix)
        j.record_firing(_make_record("t1", "completed"))
        j.record_firing(_make_record("t2", "failed"))
        # when: flushing
        j.flush()
        # then: exactly one file matching <prefix>-<ISO8601>.jsonl exists
        files = list((tmp_path / "subdir" / "nested").glob("firings-*.jsonl"))
        assert len(files) == 1
        # and: the suffix is ISO8601 (YYYYMMDDTHHMMSS)
        assert re.search(r"firings-\d{8}T\d{6}\.jsonl$", str(files[0]))
        # and: the parent directory was created
        assert (tmp_path / "subdir" / "nested").is_dir()
        # and: each line is one independently-parseable JSON object
        lines = files[0].read_text().strip().splitlines()
        records = [json.loads(ln) for ln in lines]
        assert len(records) == 2
        assert all(isinstance(r, dict) for r in records)
        # and: the records are in buffered order with monotonic sequence
        assert records[0]["transition"] == "t1"
        assert records[1]["transition"] == "t2"
        assert [r["sequence"] for r in records] == [0, 1]

    def test_token_serializes_to_type_data_and_round_trips(self, tmp_path: Path):
        """J5: a ``Token`` (frozen dataclass) in a record serializes to
        ``{"type", "data"}`` via ``_serialize`` and round-trips through
        ``json.dumps``/``json.loads``.

        Reversion-verified bite: reverting ``_serialize``'s ``Token`` branch to
        ``return repr(obj)`` (skipping the ``{"type", "data"}`` mapping) yields
        the string ``Token(type='task', ...)`` instead of the dict, so the
        round-tripped ``inputTokens["src"][0] == {"type": ..., "data": ...}``
        shape assertion fails. (Falling through to ``dataclasses.asdict`` would
        yield the same keys -- the reversion that bites is the ``repr`` fall-
        through, named here.) The co-evolution ``test_jsonl_journal_round_trip``
        asserts ``transition``/``status``/``sequence`` only -- not the
        ``Token`` shape."""
        # given: a journal carrying a record with a Token in inputTokens
        prefix = str(tmp_path / "rt")
        j = JsonlJournal(prefix=prefix)
        j.record_firing(
            _make_record("t1", "completed", input_tokens={"src": [_tok("task", k=1)]})
        )
        # when: flushing and reading the file back
        j.flush()
        files = list(tmp_path.glob("rt-*.jsonl"))
        content = files[0].read_text()
        records = [json.loads(ln) for ln in content.strip().splitlines()]
        # then: the round-tripped token is {"type", "data"}, not a repr string
        tok = records[0]["inputTokens"]["src"][0]
        assert tok == {"type": "task", "data": {"k": 1}}


# ── Cluster 3: decoupling contract (D4, engine <-> journal) ──────────────


class TestDecouplingContract:
    """J6-J8: the engine omits ``sequence``, recording is optional, and the
    ``Journal`` protocol is ``@runtime_checkable``."""

    def test_engine_record_has_no_sequence_journal_adds_it(self):
        """J6: the engine-emitted ``FiringRecord`` carries NO ``sequence`` key;
        the journal adds it (never the engine) -- the engine<->journal boundary.

        Reversion-verified bite: reverting ``Engine._record`` to add
        ``sequence=None`` (or any ``sequence``) to the ``FiringRecord`` makes
        ``"sequence" in engine_record`` true, so the
        ``"sequence" not in engine_record`` assertion fails. The co-evolution
        ``test_firing_record_has_no_sequence`` pins the engine half only; J6
        pins the boundary -- the journal ADDS what the engine OMITS."""
        # given: an engine with a JsonlJournal over the passthrough net
        j = JsonlJournal()
        engine = _engine(_reg(("t", _passthrough)), journal=j)
        marking = _marking(src=[_tok("task", i=0)], dst=[])
        # when: firing once
        _new_marking, engine_record = engine.fire(
            _PASSTHROUGH_NET, marking, "t", attempt=0
        )
        # then: the engine-emitted record has NO sequence
        assert "sequence" not in engine_record
        # and: the journal's buffered copy HAS the sequence the journal added
        assert "sequence" in j._records[0]  # pyright: ignore[reportPrivateUsage]
        assert j._records[0]["sequence"] == 0  # pyright: ignore[reportPrivateUsage]

    def test_engine_fires_with_no_journal_no_crash(self):
        """J7: recording is optional -- the engine fires correctly with no
        journal attached (no crash, the marking advances, no record stored).

        Construction-bite: the ``if self.journal is not None`` guard in
        ``_emit_firing`` is the SOLE barrier to ``None.record_firing(...)``
        raising ``AttributeError`` on a completed fire; removing the guard makes
        ``engine.fire(...)`` raise, so the marking-advances assertion is never
        reached. No behavioural reversion was exercised -- removing the clause
        is the misbehavior. The co-evolution ``test_no_journal_engine_still_fires``
        is a ``run``-level outcome test; J7 pins the ``fire``-level no-crash
        invariant under the documented reversion."""
        # given: an engine with NO journal over the passthrough net
        engine = _engine(_reg(("t", _passthrough)))  # journal=None
        marking = _marking(src=[_tok("task", i=0)], dst=[])
        # when: firing once with no journal attached
        new_marking, _record = engine.fire(_PASSTHROUGH_NET, marking, "t", attempt=0)
        # then: the marking advanced (src consumed, dst populated), no exception
        assert list(new_marking.get("src", [])) == []
        assert list(new_marking.get("dst", [])) == [_tok("task", i=0)]

    def test_journal_protocol_is_runtime_checkable(self):
        """J8: the ``Journal`` protocol is ``@runtime_checkable`` --
        ``isinstance(obj, Journal)`` holds for a class implementing all three
        methods and reports ``False`` for one missing a method.

        Construction-bite: the ``@runtime_checkable`` decorator is the SOLE
        barrier to ``isinstance`` raising ``TypeError`` (a bare ``Protocol``
        rejects ``isinstance``); removing it makes the first ``isinstance`` call
        raise, so the ``True``-case assertion is never reached. The one-method
        stub asserting ``False`` (not ``True``) pins that a missing method is
        reported unsatisfied under the decorator. No behavioural reversion was
        exercised -- removing the clause is the misbehavior. The co-evolution
        ``test_journal_protocol_is_satisfiable`` only checks the methods are
        callable, not ``isinstance`` / the negative case."""

        class _FiringOnly:
            def record_firing(self, record: FiringRecord) -> None: ...

        # given: a class implementing all three Journal methods
        full = _CapturingJournal()
        # and: a class implementing only record_firing (missing two methods)
        partial = _FiringOnly()
        # when: isinstance-checking each against the Journal protocol
        # then: the full implementation satisfies the protocol
        assert isinstance(full, Journal)
        # and: the one-method stub does NOT satisfy the protocol
        assert not isinstance(partial, Journal)


# ── Cluster 4: replay determinism (D5) ───────────────────────────────────


def _records_without_timestamps(journal: JsonlJournal) -> list[dict[str, Any]]:
    """Firing records with the non-deterministic ``timestamps`` field stripped,
    every other field (incl. ``sequence``) kept -- the D5 comparison rule."""
    return [{k: v for k, v in r.items() if k != "timestamps"} for r in journal._records]  # pyright: ignore[reportPrivateUsage]


class TestReplayDeterminism:
    """J9-J10: journal-level replay -- record-for-record equality across two
    full runs, the comparison rule (exclude timestamps, include sequence), and
    failed-record replay."""

    def test_two_runs_produce_equal_journals_excluding_timestamps_including_sequence(
        self,
    ):
        """J9: two runs with identical net + initial marking + handlers +
        journal produce journals equal record-for-record, EXCLUDING
        ``timestamps`` (non-deterministic) and INCLUDING ``sequence``
        (deterministic, equal across re-runs).

        Reversion-verified bite, representative across the two comparison-rule
        faces -- exclude-sequence and include-timestamps probe the same
        comparison-rule surface, so both are run as the two faces of one rule:
        (a) reverting ``_sequence`` from an INSTANCE attribute
        (``self._sequence = 0`` in ``__init__``) to a CLASS-level counter
        shared across ``JsonlJournal`` instances makes the second run's journal
        start where the first left off, so the two runs' ``sequence`` lists
        differ ([0, 1] vs [2, 3]) and J9's include-sequence equality fails -- a
        single-run test (J1) would not catch this drift; (b) reverting the
        comparison to INCLUDE ``timestamps`` fails on wall-clock divergence (the
        two runs' ``fired_at`` genuinely differ -- asserted in-step -- so the
        exclusion is load-bearing). The co-evolution
        ``test_replay_produces_equal_journal_modulo_timestamps`` compares
        ``transition`` + ``status`` only and does not assert ``sequence``
        equality, so it would not catch a cross-run numbering drift."""
        # given: a two-transition sequential net (>= 2 firings, deterministic order)
        net = _TWO_STEP_NET
        initial = _marking(src=[_tok("task", i=0)], mid=[], dst=[])

        # and: a first run with a fresh JsonlJournal
        j1 = JsonlJournal()
        _engine(_reg(("t1", _step1), ("t2", _step2)), journal=j1).run(
            net, initial, max_steps=100
        )

        # and: a second run with an identical net + marking + handlers + journal
        j2 = JsonlJournal()
        _engine(_reg(("t1", _step1), ("t2", _step2)), journal=j2).run(
            net, initial, max_steps=100
        )

        # then: both runs recorded the same number of firings
        assert len(j1._records) == len(j2._records) == 2  # pyright: ignore[reportPrivateUsage]
        # and: the timestamps genuinely differ across runs (exclusion is necessary)
        assert j1._records[0]["timestamps"] != j2._records[0]["timestamps"]  # pyright: ignore[reportPrivateUsage]
        # and: the journals are equal record-for-record, excluding timestamps
        #     (sequence is included in the comparison -- a cross-run numbering
        #     drift fails here, which a single-run test like J1 would not catch)
        assert _records_without_timestamps(j1) == _records_without_timestamps(j2)

    def test_failed_record_is_part_of_deterministic_replay_sequence(self):
        """J10: replay includes ``failed`` records in the deterministic
        sequence -- a failed firing is journaled and reproduced across re-runs,
        not dropped.

        Reversion-verified bite: reverting ``Engine._fail`` to NOT call
        ``_emit_firing`` (skip the emit) leaves the failed record absent from
        both journals, so the ``len(...) == 1`` count assertion fails (the
        record-for-record equality is moot without the record). J10's
        distinctive concern is the REPLAY -- the failed record appears in BOTH
        runs' journals identically (modulo timestamps) at the same position; the
        ``(firing-engine)`` lock (F12) pins that a failed record IS emitted
        through ``record_firing`` with the right content, a distinct concern.
        ``max_steps=1`` yields exactly one (failed) fire per run."""
        # given: a one-transition consume-only net whose handler always fails
        net = _FAIL_NET
        initial = _marking(src=[_tok("task", i=0)])

        # and: a first run with a fresh JsonlJournal (one failed fire)
        j1 = JsonlJournal()
        _engine(_reg(("t", _fail_handler)), journal=j1).run(net, initial, max_steps=1)

        # and: a second run with identical net + marking + handlers + journal
        j2 = JsonlJournal()
        _engine(_reg(("t", _fail_handler)), journal=j2).run(net, initial, max_steps=1)

        # then: each run recorded exactly one failed record
        assert len(j1._records) == len(j2._records) == 1  # pyright: ignore[reportPrivateUsage]
        assert j1._records[0]["status"] == "failed"  # pyright: ignore[reportPrivateUsage]
        assert j2._records[0]["status"] == "failed"  # pyright: ignore[reportPrivateUsage]
        # and: the two journals carry the identical failed record (modulo timestamps)
        assert _records_without_timestamps(j1) == _records_without_timestamps(j2)
