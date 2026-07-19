"""Illustrative projection-adapter example (spec/projection-adapter.md).

velocitron has no engine-side projection primitive: deriving a ``Marking`` from
external resource state is the consumer's concern. This file is a small,
self-contained *example* — not a lock-the-coverage test — proving the documented
protocol shape works end to end against the current public API:

    enumerate correlation keys -> probe evidence -> deposit observation tokens

The "resource" here is an in-memory fixture (the degenerate flavor named in the
spec's "The filesystem is one flavor" section): a list of ``(key, present
stages)`` sightings. The adapter enumerates keys, probes which stages hold,
deposits one ``stage_token`` per present stage into the matching
``<stage>_observed`` place, and hands the resulting Marking to ``Engine.run`` —
projection runs *before and outside* the engine. A second test pins the
headline normative rule: two sightings that collide on one color key are BOTH
deposited (never de-duplicated), so the net can see the collision.
"""

from __future__ import annotations

from typing import Any

from velocitron.contract import TransitionHandlerInput, TransitionHandlerOutput
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

# ── The example resource + adapter ───────────────────────────────────────

# One pipeline stage is enough to exercise enumerate -> probe -> deposit; the
# net advances an observed token to a verified one.
STAGES = ["raw"]

# A sighting is (correlation_key, present_stages). The correlation key is the
# token color; a run may appear more than once (a genuine collision), so the
# resource is a LIST, never a dict keyed by the correlation key -- keying by the
# color would collapse collisions the net must see.
Sighting = tuple[tuple[int, str], set[str]]


def _observed_place(stage: str) -> str:
    return f"{stage}_observed"


def project(sightings: list[Sighting]) -> Marking:
    """Enumerate -> probe -> deposit. Pure function of the (already-probed)
    evidence: same sightings in, byte-identical Marking out. Deposits one
    ``stage_token`` per present stage per sighting; does NOT de-duplicate on the
    color key."""
    places: dict[str, list[Token]] = {}
    for (account_id, crawl_tag), present in sorted(sightings, key=lambda s: s[0]):
        for stage in STAGES:
            if stage not in present:  # probe: this observation does not hold
                continue
            token = Token(
                type="stage_token",
                data={"account_id": account_id, "crawl_tag": crawl_tag, "stage": stage},
            )
            places.setdefault(_observed_place(stage), []).append(token)
    return Marking(places)


# ── The minimal net that consumes the projected marking ──────────────────


def _pipeline_net() -> Net:
    return parse_net(
        {
            "name": "pipeline",
            "places": [
                {"name": "raw_observed", "accepts": ["stage_token"]},
                {"name": "raw_verified", "accepts": ["stage_token"]},
            ],
            "transitions": [{"name": "verify_raw", "handler": "verify_raw"}],
            "arcs": [
                {
                    "from": {"place": "raw_observed"},
                    "to": {"transition": "verify_raw"},
                    "consume": {"type": "stage_token"},
                },
                {
                    "from": {"transition": "verify_raw"},
                    "to": {"place": "raw_verified"},
                    "produce": {"type": "stage_token", "destination": "raw_verified"},
                },
            ],
        }
    )


def _verify_raw(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
    # Re-emit the consumed observation as a verified token, preserving its key.
    consumed = inp["inputTokens"]["raw_observed"][0]
    return {
        "status": "completed",
        "outputTokens": {"raw_verified": [consumed]},
        "error": None,
        "metadata": {},
    }


def _engine() -> Engine:
    reg = HandlerRegistry()
    reg.register_transition("verify_raw", _verify_raw)
    return Engine(reg)


def _keys(tokens: Any) -> list[tuple[int, str]]:
    return [(t.data["account_id"], t.data["crawl_tag"]) for t in tokens]


# ── Tests ────────────────────────────────────────────────────────────────


def test_in_memory_projection_drives_the_engine_end_to_end():
    # given: two sightings -- one with `raw` present, one where the probe fails
    sightings: list[Sighting] = [
        ((1, "20260101_000001"), {"raw"}),
        ((2, "20260101_000002"), set()),  # probed, nothing to deposit
    ]
    # and: the adapter projects a marking (before/outside the engine)
    marking = project(sightings)
    # and: a minimal net that advances an observed token to a verified one
    net = _pipeline_net()

    # when: the engine runs against the projected marking
    final = _engine().run(net, marking)

    # then: only the probe-satisfied key was deposited, and it advanced
    assert _keys(marking.get("raw_observed", [])) == [(1, "20260101_000001")]
    # and: the engine drained the observation into the verified place
    assert _keys(final.get("raw_verified", [])) == [(1, "20260101_000001")]
    assert list(final.get("raw_observed", [])) == []


def test_projection_does_not_dedup_colliding_color_key():
    # given: two DISTINCT runs of one account stamped with the same crawl_tag
    # (a same-second collision) -- they share one color key
    colliding_key = (9002, "20260527_170413")
    sightings: list[Sighting] = [
        (colliding_key, {"raw"}),
        (colliding_key, {"raw"}),
    ]

    # when: the adapter projects
    marking = project(sightings)

    # then: BOTH tokens land -- the collision is preserved, not collapsed, so a
    # boundedness transition in the net could flag it (spec rule: don't de-dup
    # on the color key)
    assert _keys(marking.get("raw_observed", [])) == [colliding_key, colliding_key]
