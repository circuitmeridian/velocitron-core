"""The declarative property pass (``velocitron.properties``; ADR 0019).

The red-phase contract for the Phase-3 first slice: a six-kind property
vocabulary checked against a single marking (``check_marking``) and along
the reconstructed intermediate markings of a journal replay
(``check_replay``), plus the ``capacityPerColorKey`` place field in the net
schema. The acceptance cases reconstruct, as minimal nets, the walks the
Pattern Catalog / alarm-chip corpus hand-rolled:

- capacity per color key: pass, marking-level violation, violation caught
  mid-replay (received-file-monitor "<=1 mod_flag per account"; the
  dagster overlay's composite ``(account_id, crawl_tag)`` key);
- stuck-token detection at quiescence (dagster orphan; RF no-silent-loss);
- correlated eventually-reaches with an orphan case (dagster origin ->
  loaded-or-failed);
- marking invariant over a replay (bounded-channel P-invariant), including
  quiescence scope and eval-error-is-violation;
- key correlation (dagster same-crawl_tag parent witness);
- firing-binding checks (dagster per-key non-interference; publish-gate
  completeness).

Replay reconstruction is pinned where it is subtle: the D1 per-arc split
(a shared place carrying a read arc AND a consume arc — only the
consume-mode slice is removed), ``failed`` records leaving the marking
unchanged, and injection records (inject appends; update replaces).

Properties never gate firing: the engine runs a net whose place declares a
``capacityPerColorKey`` bound straight past the bound.

Fails until ``velocitron.properties`` and the schema/parser support land.
"""

from __future__ import annotations

from typing import Any

import pytest

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.journal import FiringRecord, InjectionRecord
from velocitron.parser import NetValidationError, parse_net
from velocitron.properties import (
    AtMostN,
    EventuallyReaches,
    FiringBinding,
    KeyCorrelation,
    MarkingInvariant,
    PlaceEmpty,
    capacity_properties,
    check_marking,
    check_replay,
)
from velocitron.registry import HandlerRegistry
from velocitron.schema import CapacityPerColorKey, Marking, Net, Token

# ── Shared helpers ──────────────────────────────────────────────────────


def _net(
    name: str,
    places: list[dict[str, Any]],
    transitions: list[dict[str, Any]] | None = None,
    arcs: list[dict[str, Any]] | None = None,
) -> Net:
    return parse_net(
        {
            "name": name,
            "places": places,
            "transitions": transitions or [],
            "arcs": arcs or [],
        }
    )


def _tok(t: str, **data: Any) -> Token:
    return Token(type=t, data=dict(data))


class _ListJournal:
    """A minimal Journal capturing records in order (the consumer pattern)."""

    def __init__(self) -> None:
        self.records: list[FiringRecord | InjectionRecord] = []

    def record_firing(self, record: FiringRecord) -> None:
        self.records.append(record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self.records.append(record)

    def record_injection(self, record: InjectionRecord) -> None:
        self.records.append(record)


def _engine(journal: _ListJournal | None = None, **transitions: Any) -> Engine:
    reg = HandlerRegistry()
    for name, fn in transitions.items():
        reg.register_transition(name, fn)
    return Engine(reg, journal=journal)


def _emit_to(dest: str, token: Token):
    def _handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        return {
            "status": "completed",
            "outputTokens": {dest: [token]},
            "error": None,
            "metadata": {},
        }

    return _handler


def _forward_to(dest: str):
    """Pass every bound input token through to ``dest``."""

    def _handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        moved = [t for toks in inp["inputTokens"].values() for t in toks]
        return {
            "status": "completed",
            "outputTokens": {dest: moved},
            "error": None,
            "metadata": {},
        }

    return _handler


def _consume_only(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {"status": "completed", "outputTokens": {}, "error": None, "metadata": {}}


def _failing(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    return {
        "status": "failed",
        "outputTokens": {},
        "error": {"type": "Boom", "message": "sink down"},
        "metadata": {},
    }


# ── Nets (minimal per surface under test) ────────────────────────────────

# Marking-level capacity checks resolve only the place + its bound.
_FLAG_PLACES = _net(
    "flag-places",
    places=[
        {
            "name": "mod_flags",
            "accepts": ["mod_flag"],
            "capacityPerColorKey": {"key": "account_id", "max": 1},
        }
    ],
)

# Composite key (dagster (account_id, crawl_tag) per-key boundedness).
_STAGE_PLACES = _net(
    "stage-places",
    places=[
        {
            "name": "raw_observed",
            "accepts": ["stage_token"],
            "capacityPerColorKey": {"key": ["account_id", "crawl_tag"], "max": 1},
        }
    ],
)

# Replay-level capacity: a source transition depositing into the bound place.
_FLAG_REPLAY = _net(
    "flag-replay",
    places=[
        {
            "name": "mod_flags",
            "accepts": ["mod_flag"],
            "capacityPerColorKey": {"key": "account_id", "max": 1},
        }
    ],
    transitions=[{"name": "set_flag", "handler": "set_flag"}],
    arcs=[
        {
            "from": {"transition": "set_flag"},
            "to": {"place": "mod_flags"},
            "produce": {"type": "mod_flag", "destination": "mod_flags"},
        }
    ],
)

# Unkeyed bound + stuck-token: a bare latch place (alarm-chip |fired| <= 1).
_LATCH_PLACES = _net("latch-places", places=[{"name": "fired", "accepts": ["alarm"]}])

# Eventually-reaches: raw -> loaded (ok == true) or failed (ok == false).
_PIPELINE = _net(
    "pipeline",
    places=[
        {"name": "raw", "accepts": ["job"]},
        {"name": "loaded", "accepts": ["job"]},
        {"name": "failed", "accepts": ["job"]},
    ],
    transitions=[
        {"name": "advance", "handler": "advance"},
        {"name": "reject", "handler": "reject"},
    ],
    arcs=[
        {
            "from": {"place": "raw"},
            "to": {"transition": "advance"},
            "consume": {"type": "job", "predicate": {"cel": "ok == true"}},
        },
        {
            "from": {"transition": "advance"},
            "to": {"place": "loaded"},
            "produce": {"type": "job", "destination": "loaded"},
        },
        {
            "from": {"place": "raw"},
            "to": {"transition": "reject"},
            "consume": {"type": "job", "predicate": {"cel": "ok == false"}},
        },
        {
            "from": {"transition": "reject"},
            "to": {"place": "failed"},
            "produce": {"type": "job", "destination": "failed"},
        },
    ],
)

# Scope semantics: a token transits `mid` and leaves (always vs quiescence).
_TWO_HOP = _net(
    "two-hop",
    places=[
        {"name": "raw", "accepts": ["job"]},
        {"name": "mid", "accepts": ["job"]},
        {"name": "loaded", "accepts": ["job"]},
    ],
    transitions=[
        {"name": "hop1", "handler": "hop1"},
        {"name": "hop2", "handler": "hop2"},
    ],
    arcs=[
        {
            "from": {"place": "raw"},
            "to": {"transition": "hop1"},
            "consume": {"type": "job"},
        },
        {
            "from": {"transition": "hop1"},
            "to": {"place": "mid"},
            "produce": {"type": "job", "destination": "mid"},
        },
        {
            "from": {"place": "mid"},
            "to": {"transition": "hop2"},
            "consume": {"type": "job"},
        },
        {
            "from": {"transition": "hop2"},
            "to": {"place": "loaded"},
            "produce": {"type": "job", "destination": "loaded"},
        },
    ],
)

# Bounded-channel P-invariant: enqueue moves a slot from channel_free to channel.
_CHANNEL = _net(
    "channel",
    places=[
        {"name": "channel_free", "accepts": ["slot"]},
        {"name": "channel", "accepts": ["slot"]},
    ],
    transitions=[{"name": "enqueue", "handler": "enqueue"}],
    arcs=[
        {
            "from": {"place": "channel_free"},
            "to": {"transition": "enqueue"},
            "consume": {"type": "slot"},
        },
        {
            "from": {"transition": "enqueue"},
            "to": {"place": "channel"},
            "produce": {"type": "slot", "destination": "channel"},
        },
    ],
)

# Key correlation: every child_verified token needs a same-key parent_verified.
_CORRELATION_PLACES = _net(
    "correlation-places",
    places=[
        {"name": "child_verified", "accepts": ["stage_token"]},
        {"name": "parent_verified", "accepts": ["stage_token"]},
    ],
)

# Firing-binding key uniformity: a join over two places (dagster DG-4 shape).
_JOIN = _net(
    "join",
    places=[
        {"name": "files", "accepts": ["file"]},
        {"name": "flags", "accepts": ["flag"]},
    ],
    transitions=[{"name": "join", "handler": "join"}],
    arcs=[
        {
            "from": {"place": "files"},
            "to": {"transition": "join"},
            "consume": {"type": "file"},
        },
        {
            "from": {"place": "flags"},
            "to": {"transition": "join"},
            "consume": {"type": "flag"},
        },
    ],
)

# Firing-binding CEL: the publish-gate completeness check (PG-P3 shape).
_GATE = _net(
    "gate",
    places=[{"name": "publish_request", "accepts": ["run"]}],
    transitions=[{"name": "admit_publish", "handler": "admit_publish"}],
    arcs=[
        {
            "from": {"place": "publish_request"},
            "to": {"transition": "admit_publish"},
            "consume": {"type": "run"},
        }
    ],
)

# Reconstruction: read arc + weight-2 consume in one firing.
_RECON = _net(
    "recon",
    places=[
        {"name": "gate", "accepts": ["flag"]},
        {"name": "pool", "accepts": ["job"]},
        {"name": "out", "accepts": ["job"]},
    ],
    transitions=[{"name": "burst", "handler": "burst"}],
    arcs=[
        {
            "from": {"place": "gate"},
            "to": {"transition": "burst"},
            "consume": {"type": "flag", "mode": "read"},
        },
        {
            "from": {"place": "pool"},
            "to": {"transition": "burst"},
            "consume": {"type": "job", "weight": 2},
        },
        {
            "from": {"transition": "burst"},
            "to": {"place": "out"},
            "produce": {"type": "job", "destination": "out"},
        },
    ],
)

# Reconstruction split: read arc AND consume arc sharing ONE place (D1 order).
_SHARED = _net(
    "shared",
    places=[{"name": "pool", "accepts": ["job"]}],
    transitions=[{"name": "take", "handler": "take"}],
    arcs=[
        {
            "from": {"place": "pool"},
            "to": {"transition": "take"},
            "consume": {"type": "job", "mode": "read"},
        },
        {
            "from": {"place": "pool"},
            "to": {"transition": "take"},
            "consume": {"type": "job"},
        },
    ],
)

# Injection update kind: a singleton clock place (no transitions needed).
_CLOCK_PLACES = _net("clock-places", places=[{"name": "clock", "accepts": ["tick"]}])


# ── capacityPerColorKey: schema + parsing ────────────────────────────────


class TestCapacitySchema:
    """The capacityPerColorKey place field parses, validates, and stays
    non-behavioral."""

    def test_string_key_parses_to_normalized_tuple(self):
        # given: a place declaring a single-field capacity bound
        net = _FLAG_PLACES

        # then: the parsed place carries the normalized declaration
        assert net.places[0].capacity_per_color_key == CapacityPerColorKey(
            keys=("account_id",), max=1
        )

    def test_array_key_parses_to_composite_tuple(self):
        # given: a place declaring a composite-key capacity bound
        net = _STAGE_PLACES

        # then: the key fields are preserved in order
        assert net.places[0].capacity_per_color_key == CapacityPerColorKey(
            keys=("account_id", "crawl_tag"), max=1
        )

    def test_place_without_declaration_has_none(self):
        # given: a place with no capacity bound
        net = _LATCH_PLACES

        # then: the field defaults to None
        assert net.places[0].capacity_per_color_key is None

    def test_max_below_one_fails_at_parse(self):
        # given: a capacity bound with max 0
        doc = {
            "name": "bad",
            "places": [
                {
                    "name": "p",
                    "accepts": ["t"],
                    "capacityPerColorKey": {"key": "k", "max": 0},
                }
            ],
            "transitions": [],
            "arcs": [],
        }

        # then: the schema rejects it at parse (minimum 1)
        with pytest.raises(NetValidationError, match="minimum"):
            parse_net(doc)

    def test_missing_max_fails_at_parse(self):
        # given: a capacity bound without max
        doc = {
            "name": "bad",
            "places": [
                {
                    "name": "p",
                    "accepts": ["t"],
                    "capacityPerColorKey": {"key": "k"},
                }
            ],
            "transitions": [],
            "arcs": [],
        }

        # then: the schema rejects it at parse (max is required)
        with pytest.raises(NetValidationError, match="'max' is a required property"):
            parse_net(doc)

    def test_unknown_field_inside_declaration_fails_at_parse(self):
        # given: a capacity bound carrying an unknown field
        doc = {
            "name": "bad",
            "places": [
                {
                    "name": "p",
                    "accepts": ["t"],
                    "capacityPerColorKey": {"key": "k", "max": 1, "bogus": 2},
                }
            ],
            "transitions": [],
            "arcs": [],
        }

        # then: additionalProperties: false rejects the unknown field itself
        with pytest.raises(NetValidationError, match="bogus"):
            parse_net(doc)

    def test_engine_fires_straight_past_the_bound(self):
        # given: a net whose place is bound to 1 mod_flag per account
        net = _FLAG_REPLAY
        # and: an engine depositing two same-account flags
        engine = _engine(
            set_flag=_emit_to("mod_flags", _tok("mod_flag", account_id="A"))
        )
        marking = Marking()

        # when: firing the producer twice
        marking, r1 = engine.fire(net, marking, "set_flag", attempt=0)
        marking, r2 = engine.fire(net, marking, "set_flag", attempt=1)

        # then: both fires complete — the bound never gates firing
        assert r1["status"] == "completed" and r2["status"] == "completed"
        # and: the marking holds both tokens (the violation exists, unreported
        # by the engine; the property pass is what reports it)
        assert len(marking["mod_flags"]) == 2

    def test_capacity_properties_extracts_declared_bounds(self):
        # given: a net with one declared bound
        net = _FLAG_PLACES

        # when: extracting the net's capacity properties
        props = capacity_properties(net)

        # then: the declaration surfaces as an AtMostN property
        assert props == [AtMostN(place="mod_flags", max=1, key=("account_id",))]


# ── Capacity: marking-level and mid-replay ───────────────────────────────


class TestCapacityChecking:
    """capacityPerColorKey bounds are checked automatically, per key value."""

    def test_distinct_key_values_within_bound_pass(self):
        # given: two flags for two different accounts
        marking = Marking(
            {
                "mod_flags": [
                    _tok("mod_flag", account_id="A"),
                    _tok("mod_flag", account_id="B"),
                ]
            }
        )

        # when: checking the marking (capacity bounds auto-included)
        report = check_marking(_FLAG_PLACES, marking)

        # then: no violation
        assert report.ok

    def test_same_key_value_over_bound_violates(self):
        # given: two flags for one account
        marking = Marking(
            {
                "mod_flags": [
                    _tok("mod_flag", account_id="A"),
                    _tok("mod_flag", account_id="A"),
                ]
            }
        )

        # when: checking the marking
        report = check_marking(_FLAG_PLACES, marking)

        # then: the bound is violated at the offending place
        assert not report.ok
        assert report.violations[0].kind == "at-most-n"
        assert report.violations[0].place == "mod_flags"
        # and: the message names the offending key value
        assert "A" in report.violations[0].message

    def test_composite_key_distinguishes_full_tuples(self):
        # given: same account under two crawl_tags, and a true duplicate
        ok_marking = Marking(
            {
                "raw_observed": [
                    _tok("stage_token", account_id="A", crawl_tag="T1"),
                    _tok("stage_token", account_id="A", crawl_tag="T2"),
                ]
            }
        )
        dup_marking = Marking(
            {
                "raw_observed": [
                    _tok("stage_token", account_id="A", crawl_tag="T1"),
                    _tok("stage_token", account_id="A", crawl_tag="T1"),
                ]
            }
        )

        # then: distinct composite keys pass; identical composite keys violate
        assert check_marking(_STAGE_PLACES, ok_marking).ok
        assert not check_marking(_STAGE_PLACES, dup_marking).ok

    def test_tokens_missing_the_key_field_share_the_absent_group(self):
        # given: two flags with no account_id at all
        marking = Marking({"mod_flags": [_tok("mod_flag"), _tok("mod_flag")]})

        # when: checking the marking
        report = check_marking(_FLAG_PLACES, marking)

        # then: unkeyed tokens count against one shared bound (no evasion)
        assert not report.ok

    def test_violation_caught_mid_replay_with_step_index(self):
        # given: a journaled run that deposits two same-account flags
        journal = _ListJournal()
        engine = _engine(
            journal,
            set_flag=_emit_to("mod_flags", _tok("mod_flag", account_id="A")),
        )
        marking = Marking()
        marking, _ = engine.fire(_FLAG_REPLAY, marking, "set_flag", attempt=0)
        marking, _ = engine.fire(_FLAG_REPLAY, marking, "set_flag", attempt=1)

        # when: replaying the journal against the bound
        report = check_replay(_FLAG_REPLAY, Marking(), journal.records)

        # then: the violation is caught at the second record's post-state
        assert not report.ok
        assert report.violations[0].kind == "at-most-n"
        assert report.violations[0].step == 1


# ── AtMostN (Python-declared, unkeyed) ───────────────────────────────────


class TestAtMostN:
    """The unkeyed bound is the alarm-chip 1-bounded-latch walk."""

    def test_within_bound_passes(self):
        # given: one latch token and the fire-once bound
        marking = Marking({"fired": [_tok("alarm")]})

        # when: checking the marking
        report = check_marking(_LATCH_PLACES, marking, [AtMostN("fired", max=1)])

        # then: no violation
        assert report.ok

    def test_over_bound_violates(self):
        # given: two latch tokens against a 1-bound
        marking = Marking({"fired": [_tok("alarm"), _tok("alarm")]})

        # when: checking the marking
        report = check_marking(_LATCH_PLACES, marking, [AtMostN("fired", max=1)])

        # then: the fire-once bound is violated
        assert not report.ok
        assert report.violations[0].place == "fired"


# ── PlaceEmpty: stuck tokens and predicate-scoped emptiness ──────────────────────


class TestPlaceEmpty:
    """place-empty is the stuck-token / forbidden-token witness."""

    def test_empty_place_passes(self):
        # given: an empty latch place
        report = check_marking(_LATCH_PLACES, Marking(), [PlaceEmpty("fired")])

        # then: no violation
        assert report.ok

    def test_stuck_token_violates(self):
        # given: a token stranded in the place
        marking = Marking({"fired": [_tok("alarm")]})

        # when: checking place-empty
        report = check_marking(_LATCH_PLACES, marking, [PlaceEmpty("fired")])

        # then: the stuck token is reported
        assert not report.ok
        assert report.violations[0].kind == "place-empty"
        assert report.violations[0].place == "fired"

    def test_cel_predicate_flags_only_matching_tokens(self):
        # given: a drop place holding an sftp entry (allowed to be dropped)
        net = _net("drops", places=[{"name": "dropped", "accepts": ["entry_group"]}])
        sftp = Marking({"dropped": [_tok("entry_group", protocol="sftp")]})
        ftp = Marking({"dropped": [_tok("entry_group", protocol="ftp")]})
        prop = PlaceEmpty("dropped", cel='protocol == "ftp"')

        # then: sftp drops pass; an ftp drop violates (BC-3: FTP never dropped)
        assert check_marking(net, sftp, [prop]).ok
        assert not check_marking(net, ftp, [prop]).ok

    def test_scope_quiescence_ignores_transit_but_always_catches_it(self):
        # given: a journaled run where the token transits `mid` and leaves
        journal = _ListJournal()
        engine = _engine(journal, hop1=_forward_to("mid"), hop2=_forward_to("loaded"))
        initial = Marking({"raw": [_tok("job", id="j1")]})
        engine.run(_TWO_HOP, initial)

        # when: checking mid-emptiness under both scopes
        quiescent = check_replay(
            _TWO_HOP, initial, journal.records, [PlaceEmpty("mid")]
        )
        always = check_replay(
            _TWO_HOP,
            initial,
            journal.records,
            [PlaceEmpty("mid", scope="always")],
        )

        # then: quiescence scope passes (mid is empty at the end)
        assert quiescent.ok
        # and: always scope catches the transit, at the depositing step
        assert not always.ok
        assert always.violations[0].step == 0


# ── EventuallyReaches ────────────────────────────────────────────────────


class TestEventuallyReaches:
    """Key-correlated conservation: every key entering source ends in a
    target (dagster origin -> loaded-or-failed; RF no-silent-loss)."""

    def test_all_entrants_reach_a_target(self):
        # given: a journaled run where j1 advances and j2 is rejected
        journal = _ListJournal()
        engine = _engine(
            journal, advance=_forward_to("loaded"), reject=_forward_to("failed")
        )
        initial = Marking(
            {"raw": [_tok("job", id="j1", ok=True), _tok("job", id="j2", ok=False)]}
        )
        engine.run(_PIPELINE, initial)

        # when: checking conservation into either target
        report = check_replay(
            _PIPELINE,
            initial,
            journal.records,
            [EventuallyReaches("raw", ("loaded", "failed"), key="id")],
        )

        # then: both keys are accounted for
        assert report.ok

    def test_orphan_key_violates_and_is_named(self):
        # given: a run after which an injected token never leaves the source
        # (only `advance` can ever fire here: no ok=false token exists)
        journal = _ListJournal()
        engine = _engine(journal, advance=_forward_to("loaded"))
        initial = Marking({"raw": [_tok("job", id="j1", ok=True)]})
        marking = engine.run(_PIPELINE, initial)
        # and: an orphan arriving by injection with no further firings
        engine.inject_token(
            _PIPELINE, marking, "raw", _tok("job", id="j3", ok=True), attempt=9
        )

        # when: checking conservation
        report = check_replay(
            _PIPELINE,
            initial,
            journal.records,
            [EventuallyReaches("raw", ("loaded", "failed"), key="id")],
        )

        # then: the orphan key is reported by value
        assert not report.ok
        assert report.violations[0].kind == "eventually-reaches"
        assert "j3" in report.violations[0].message

    def test_tokens_deposited_into_source_mid_replay_count_as_entrants(self):
        # given: a two-hop run where `mid` is fed by a firing, not the
        # initial marking
        journal = _ListJournal()
        engine = _engine(journal, hop1=_forward_to("mid"), hop2=_forward_to("loaded"))
        initial = Marking({"raw": [_tok("job", id="j1")]})
        engine.run(_TWO_HOP, initial)

        # when: treating `mid` as the conservation source
        report = check_replay(
            _TWO_HOP,
            initial,
            journal.records,
            [EventuallyReaches("mid", ("loaded",), key="id")],
        )

        # then: the deposited entrant reached the target
        assert report.ok


# ── MarkingInvariant ─────────────────────────────────────────────────────


class TestMarkingInvariant:
    """CEL over per-place counts (bounded-channel P-invariant walk)."""

    def test_p_invariant_holds_at_every_step(self):
        # given: a journaled run moving both slots across the channel
        journal = _ListJournal()
        engine = _engine(journal, enqueue=_forward_to("channel"))
        initial = Marking({"channel_free": [_tok("slot"), _tok("slot")]})
        engine.run(_CHANNEL, initial)

        # when: checking the slot-conservation invariant along the replay
        report = check_replay(
            _CHANNEL,
            initial,
            journal.records,
            [MarkingInvariant("count.channel + count.channel_free == 2")],
        )

        # then: the invariant holds at the initial and every intermediate
        # marking
        assert report.ok

    def test_injection_breaking_the_invariant_is_caught_at_its_step(self):
        # given: a run whose second record injects a third slot
        journal = _ListJournal()
        engine = _engine(journal, enqueue=_forward_to("channel"))
        initial = Marking({"channel_free": [_tok("slot"), _tok("slot")]})
        marking, _ = engine.fire(_CHANNEL, initial, "enqueue", attempt=0)
        engine.inject_token(_CHANNEL, marking, "channel_free", _tok("slot"), attempt=1)

        # when: checking the conservation invariant
        report = check_replay(
            _CHANNEL,
            initial,
            journal.records,
            [MarkingInvariant("count.channel + count.channel_free == 2")],
        )

        # then: the violation lands at the injection's record index
        assert not report.ok
        assert report.violations[0].kind == "marking-invariant"
        assert report.violations[0].step == 1

    def test_quiescence_scope_checks_only_the_final_marking(self):
        # given: a run whose intermediate markings violate `channel == 2`
        journal = _ListJournal()
        engine = _engine(journal, enqueue=_forward_to("channel"))
        initial = Marking({"channel_free": [_tok("slot"), _tok("slot")]})
        engine.run(_CHANNEL, initial)

        # when: checking the count only at quiescence
        report = check_replay(
            _CHANNEL,
            initial,
            journal.records,
            [MarkingInvariant("count.channel == 2", scope="quiescence")],
        )

        # then: no violation — only the final marking is consulted
        assert report.ok

    def test_counts_default_zero_for_places_absent_from_the_marking(self):
        # given: a marking that never mentions `channel`
        marking = Marking({"channel_free": [_tok("slot")]})

        # when: an invariant reads the absent place's count
        report = check_marking(
            _CHANNEL, marking, [MarkingInvariant("count.channel == 0")]
        )

        # then: the absent place counts as zero
        assert report.ok

    def test_eval_error_is_a_violation_not_a_pass(self):
        # given: an invariant referencing a place the net does not declare
        marking = Marking({"channel_free": [_tok("slot")]})

        # when: checking the unevaluable invariant
        report = check_marking(_CHANNEL, marking, [MarkingInvariant("count.nope > 0")])

        # then: an invariant that cannot be evaluated does not hold
        assert not report.ok
        assert report.violations[0].kind == "marking-invariant"


# ── KeyCorrelation ───────────────────────────────────────────────────────


class TestKeyCorrelation:
    """Every token in a place has a same-key witness in another place
    (dagster same-crawl_tag parent invariant)."""

    def test_child_with_same_key_parent_passes(self):
        # given: a child and its same-key parent
        marking = Marking(
            {
                "child_verified": [_tok("stage_token", account_id="A")],
                "parent_verified": [_tok("stage_token", account_id="A")],
            }
        )

        # when: checking the correlation
        report = check_marking(
            _CORRELATION_PLACES,
            marking,
            [KeyCorrelation("child_verified", "parent_verified", key="account_id")],
        )

        # then: no violation
        assert report.ok

    def test_child_without_witness_violates(self):
        # given: a child whose key has no parent witness
        marking = Marking(
            {
                "child_verified": [_tok("stage_token", account_id="B")],
                "parent_verified": [_tok("stage_token", account_id="A")],
            }
        )

        # when: checking the correlation
        report = check_marking(
            _CORRELATION_PLACES,
            marking,
            [KeyCorrelation("child_verified", "parent_verified", key="account_id")],
        )

        # then: the orphan child is reported
        assert not report.ok
        assert report.violations[0].kind == "key-correlation"
        assert report.violations[0].place == "child_verified"


# ── FiringBinding ────────────────────────────────────────────────────────


class TestFiringBinding:
    """Per-record binding checks: key uniformity and single-token CEL."""

    def test_uniform_binding_keys_pass(self):
        # given: a journaled join over same-account tokens
        journal = _ListJournal()
        engine = _engine(journal, join=_consume_only)
        initial = Marking(
            {
                "files": [_tok("file", account_id="A")],
                "flags": [_tok("flag", account_id="A")],
            }
        )
        engine.run(_JOIN, initial)

        # when: checking per-key non-interference (DG-4)
        report = check_replay(
            _JOIN,
            initial,
            journal.records,
            [FiringBinding("join", key="account_id")],
        )

        # then: all bound tokens share one key
        assert report.ok

    def test_mixed_binding_keys_violate(self):
        # given: a journaled join binding tokens of two accounts
        journal = _ListJournal()
        engine = _engine(journal, join=_consume_only)
        initial = Marking(
            {
                "files": [_tok("file", account_id="A")],
                "flags": [_tok("flag", account_id="B")],
            }
        )
        engine.run(_JOIN, initial)

        # when: checking per-key non-interference
        report = check_replay(
            _JOIN,
            initial,
            journal.records,
            [FiringBinding("join", key="account_id")],
        )

        # then: the mixed-key firing is reported at its record index
        assert not report.ok
        assert report.violations[0].kind == "firing-binding"
        assert report.violations[0].step == 0

    def test_binding_cel_pass_and_violation(self):
        # given: two journaled admissions — one complete run, one short run
        journal = _ListJournal()
        engine = _engine(journal, admit_publish=_consume_only)
        initial = Marking(
            {
                "publish_request": [
                    _tok("run", produced_count=3, configured_count=3),
                    _tok("run", produced_count=2, configured_count=3),
                ]
            }
        )
        engine.run(_GATE, initial)

        # when: checking the completeness predicate over each admission
        # (PG-P3)
        report = check_replay(
            _GATE,
            initial,
            journal.records,
            [FiringBinding("admit_publish", cel="produced_count == configured_count")],
        )

        # then: only the short run's admission violates
        assert len(report.violations) == 1
        assert report.violations[0].kind == "firing-binding"
        assert report.violations[0].step == 1


# ── Replay reconstruction mechanics ──────────────────────────────────────


class TestReplayReconstruction:
    """The walker recovers every intermediate marking from records alone."""

    def test_read_tokens_stay_and_weighted_consume_is_exact(self):
        # given: a journaled fire reading a gate flag and consuming 2 jobs
        journal = _ListJournal()
        engine = _engine(journal, burst=_emit_to("out", _tok("job", id="merged")))
        initial = Marking(
            {
                "gate": [_tok("flag")],
                "pool": [_tok("job", id="j1"), _tok("job", id="j2")],
            }
        )
        engine.run(_RECON, initial)

        # when: replaying with quiescence assertions over the reconstruction
        report = check_replay(
            _RECON,
            initial,
            journal.records,
            [
                # the read token was NOT removed (ADR 0012)
                MarkingInvariant("count.gate == 1", scope="quiescence"),
                # both weighted consumes were removed; one deposit landed
                MarkingInvariant(
                    "count.pool == 0 && count.out == 1", scope="quiescence"
                ),
            ],
        )

        # then: the reconstructed final marking matches the engine's
        assert report.ok

    def test_shared_place_split_removes_only_the_consume_slice(self):
        # given: read + consume arcs on ONE place; the record's inputTokens
        # concatenate per-arc slices in declaration order (D1): read binds
        # j1, consume binds j2
        journal = _ListJournal()
        engine = _engine(journal, take=_consume_only)
        initial = Marking({"pool": [_tok("job", id="j1"), _tok("job", id="j2")]})
        engine.run(_SHARED, initial)

        # when: replaying with per-token quiescence assertions
        report = check_replay(
            _SHARED,
            initial,
            journal.records,
            [
                # j1 (the read slice) must still be present
                PlaceEmpty("pool", cel='id == "j1"'),
                # j2 (the consume slice) must be gone
                PlaceEmpty("pool", cel='id == "j2"'),
            ],
        )

        # then: exactly the read token remains — j1 present (violation),
        # j2 absent (pass)
        assert len(report.violations) == 1
        assert "j1" in report.violations[0].message

    def test_failed_record_leaves_the_marking_unchanged(self):
        # given: a journaled failing fire (atomic rollback)
        journal = _ListJournal()
        engine = _engine(journal, advance=_failing)
        initial = Marking({"raw": [_tok("job", id="j1", ok=True)]})
        engine.fire(_PIPELINE, initial, "advance", attempt=0)

        # when: replaying with an every-step conservation invariant
        report = check_replay(
            _PIPELINE,
            initial,
            journal.records,
            [MarkingInvariant("count.raw == 1", scope="always")],
        )

        # then: the failed record consumed nothing
        assert report.ok

    def test_update_injection_replaces_the_place_contents(self):
        # given: a journaled clock-advance (replace=True)
        journal = _ListJournal()
        engine = _engine(journal)
        initial = Marking({"clock": [_tok("tick", now=1)]})
        engine.inject_token(
            _CLOCK_PLACES,
            initial,
            "clock",
            _tok("tick", now=2),
            attempt=0,
            replace=True,
        )

        # when: replaying with per-token quiescence assertions
        report = check_replay(
            _CLOCK_PLACES,
            initial,
            journal.records,
            [
                # the old tick was replaced, not accumulated
                PlaceEmpty("clock", cel="now == 1"),
                MarkingInvariant("count.clock == 1", scope="quiescence"),
            ],
        )

        # then: the update semantics were applied
        assert report.ok

    def test_record_stream_not_matching_the_marking_raises(self):
        # given: a record consuming a token the initial marking lacks
        journal = _ListJournal()
        engine = _engine(journal, enqueue=_forward_to("channel"))
        initial = Marking({"channel_free": [_tok("slot")]})
        engine.run(_CHANNEL, initial)

        # when: replaying against an EMPTY initial marking
        # then: the mismatch is a programmer error, not a violation
        with pytest.raises(ValueError):
            check_replay(_CHANNEL, Marking(), journal.records)


# ── Checker contract ─────────────────────────────────────────────────────


class TestCheckerContract:
    """API-shape guarantees: replay-only rejection, plain-dict markings."""

    def test_replay_only_property_rejected_by_check_marking(self):
        # given: a replay-only property
        prop = EventuallyReaches("raw", ("loaded",), key="id")

        # then: check_marking refuses it (programmer error)
        with pytest.raises(ValueError, match="replay"):
            check_marking(_PIPELINE, Marking(), [prop])

    def test_plain_dict_marking_is_accepted(self):
        # given: a plain dict-of-lists marking (the projection/tooling case)
        marking = {"fired": [_tok("alarm"), _tok("alarm")]}

        # when: checking it directly
        report = check_marking(_LATCH_PLACES, marking, [AtMostN("fired", max=1)])

        # then: the checker consumes the Mapping shape
        assert not report.ok
