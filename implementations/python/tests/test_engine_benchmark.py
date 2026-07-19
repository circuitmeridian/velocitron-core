"""Focused semantic checks for the Engine hot-path benchmark fixture."""

import importlib.util
from pathlib import Path

from velocitron.cel import CelpyAdapter
from velocitron.schema import Marking, Token

_BENCHMARK_PATH = Path(__file__).parents[1] / "benchmarks" / "engine_hot_path.py"
_SPEC = importlib.util.spec_from_file_location("engine_hot_path", _BENCHMARK_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK)
build_fixture = _BENCHMARK.build_fixture
sample_tokens = _BENCHMARK.sample_tokens


def test_benchmark_batch_routes_every_co2_sample_to_one_terminal_place():
    # given: the representative net and a deterministic balanced sample batch
    net, engine = build_fixture(CelpyAdapter())
    tokens = sample_tokens(4)
    original = Marking()
    injected, _records = engine.inject_tokens(
        net,
        original,
        [("ingress", token) for token in tokens],
        attempt=0,
    )

    # when: one reconnect-style run drains all three stages per token
    result = engine.run(net, injected, max_steps=len(tokens) * 3 + 1)

    # then: normal and alert samples reach their distinct terminal places
    assert [token.data["ppm"] for token in result["archived"]] == [900, 900]
    assert [token.data["ppm"] for token in result["alerted"]] == [1100, 1100]
    # and: the transient places drain while the persistent inputs stay unchanged
    assert all(
        not result.get(place, ())
        for place in ("ingress", "routed", "normal_queue", "alert_queue")
    )
    assert len(injected["ingress"]) == 4
    assert not original


def test_benchmark_route_uses_the_real_co2mon_cel_predicate():
    # given: a sample with the same shape but a different source
    net, engine = build_fixture(CelpyAdapter())
    foreign = Token(
        type="sample",
        data={
            "source": "weather_api",
            "ppm": 1100,
            "observed_at": 1_700_000_000,
            "sequence": 0,
        },
    )
    injected, _record = engine.inject_token(
        net, Marking(), "ingress", foreign, attempt=0
    )

    # when: the engine scans all transitions
    result = engine.run(net, injected, max_steps=4)

    # then: source == "co2mon" prevents the foreign sample from routing
    assert list(result["ingress"]) == [foreign]
    assert not result.get("archived", ())
    assert not result.get("alerted", ())
