"""Reproducible benchmark for the Engine injection and run hot path.

The fixture mirrors a telemetry consumer without importing an application: an
external sample is routed by ``source == "co2mon"``, classified by ppm, then
persisted to one of two terminal places.  Five transitions and ten arcs make
all three firings per sample repeatedly scan the whole transition set.  Every
operation uses the public Engine injection/run APIs and persistent Marking.

Baseline capture (run from ``implementations/python`` on an otherwise idle
machine, preferably once per candidate commit)::

    uv run python benchmarks/engine_hot_path.py \
      --warmup 20 --iterations 100 --batch-warmup 1 \
      --batch-size 256 --batch-iterations 3 --repeats 5 \
      > engine-benchmark-$(git rev-parse --short HEAD).json

Compare the JSON ``summary.median_ns`` and ``summary.p95_ns`` values while
holding the CLI parameters, Python, CEL backend, and host fixed.  The raw
per-repeat summaries are retained under ``repeat_summaries`` to expose drift.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import statistics
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Sequence

from velocitron.cel import (
    CelAdapter,
    CelExprAdapter,
    CelRustAdapter,
    CelpyAdapter,
    get_default_adapter,
)
from velocitron.contract import (
    TransitionHandler,
    TransitionHandlerInput,
    TransitionHandlerOutput,
)
from velocitron.engine import Engine
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry
from velocitron.schema import Marking, Net, Token

NET_DOCUMENT: dict[str, Any] = {
    "name": "guinan-shaped-co2-routing",
    "places": [
        {"name": "ingress", "accepts": ["sample"]},
        {"name": "routed", "accepts": ["sample"]},
        {"name": "normal_queue", "accepts": ["sample"]},
        {"name": "alert_queue", "accepts": ["sample"]},
        {"name": "archived", "accepts": ["sample"]},
        {"name": "alerted", "accepts": ["sample"]},
    ],
    "transitions": [
        {"name": "route_co2mon", "handler": "route_co2mon"},
        {"name": "classify_alert", "handler": "classify_alert"},
        {"name": "classify_normal", "handler": "classify_normal"},
        {"name": "persist_alert", "handler": "persist_alert"},
        {"name": "persist_normal", "handler": "persist_normal"},
    ],
    "arcs": [
        {
            "from": {"place": "ingress"},
            "to": {"transition": "route_co2mon"},
            "consume": {
                "type": "sample",
                "predicate": {"cel": 'source == "co2mon"'},
            },
        },
        {
            "from": {"transition": "route_co2mon"},
            "to": {"place": "routed"},
            "produce": {"type": "sample", "destination": "routed"},
        },
        {
            "from": {"place": "routed"},
            "to": {"transition": "classify_alert"},
            "consume": {
                "type": "sample",
                "predicate": {"cel": "ppm >= 1000"},
            },
        },
        {
            "from": {"transition": "classify_alert"},
            "to": {"place": "alert_queue"},
            "produce": {"type": "sample", "destination": "alert_queue"},
        },
        {
            "from": {"place": "routed"},
            "to": {"transition": "classify_normal"},
            "consume": {
                "type": "sample",
                "predicate": {"cel": "ppm < 1000"},
            },
        },
        {
            "from": {"transition": "classify_normal"},
            "to": {"place": "normal_queue"},
            "produce": {"type": "sample", "destination": "normal_queue"},
        },
        {
            "from": {"place": "alert_queue"},
            "to": {"transition": "persist_alert"},
            "consume": {"type": "sample"},
        },
        {
            "from": {"transition": "persist_alert"},
            "to": {"place": "alerted"},
            "produce": {"type": "sample", "destination": "alerted"},
        },
        {
            "from": {"place": "normal_queue"},
            "to": {"transition": "persist_normal"},
            "consume": {"type": "sample"},
        },
        {
            "from": {"transition": "persist_normal"},
            "to": {"place": "archived"},
            "produce": {"type": "sample", "destination": "archived"},
        },
    ],
}

_BACKEND_PACKAGES = {
    "CelpyAdapter": "cel-python",
    "CelExprAdapter": "cel-expr-python",
    "CelRustAdapter": "common-expression-language",
}


def _adapter(name: str) -> CelAdapter:
    factories: dict[str, Callable[[], CelAdapter]] = {
        "default": get_default_adapter,
        "python": CelpyAdapter,
        "cpp": CelExprAdapter,
        "rust": CelRustAdapter,
    }
    adapter = factories[name]()
    # Force an optional backend import now so a missing requested backend fails
    # before any benchmark output is emitted.
    adapter.compile("true")
    return adapter


def _pass_through(source: str, destination: str) -> TransitionHandler:
    def handler(inp: TransitionHandlerInput) -> TransitionHandlerOutput:
        return {
            "status": "completed",
            "outputTokens": {destination: inp["inputTokens"].get(source, [])},
            "error": None,
            "metadata": {},
        }

    return handler


def build_fixture(adapter: CelAdapter) -> tuple[Net, Engine]:
    """Build the real parsed net and deterministic handler registry."""
    registry = HandlerRegistry()
    for transition, source, destination in (
        ("route_co2mon", "ingress", "routed"),
        ("classify_alert", "routed", "alert_queue"),
        ("classify_normal", "routed", "normal_queue"),
        ("persist_alert", "alert_queue", "alerted"),
        ("persist_normal", "normal_queue", "archived"),
    ):
        registry.register_transition(transition, _pass_through(source, destination))
    return (
        parse_net(NET_DOCUMENT, cel_adapter=adapter),
        Engine(registry, cel_adapter=adapter),
    )


def sample_tokens(count: int) -> tuple[Token, ...]:
    """Return deterministic, balanced normal/alert CO2 samples."""
    return tuple(
        Token(
            type="sample",
            data={
                "source": "co2mon",
                "ppm": 900 if index % 2 == 0 else 1100,
                "observed_at": 1_700_000_000 + index,
                "sequence": index,
            },
        )
        for index in range(count)
    )


def _assert_quiescent_result(
    engine: Engine, net: Net, marking: Marking, expected: int
) -> None:
    transient = ("ingress", "routed", "normal_queue", "alert_queue")
    if any(marking.get(place, ()) for place in transient):
        raise RuntimeError("benchmark fixture did not drain its transient places")
    terminal_count = len(marking.get("archived", ())) + len(marking.get("alerted", ()))
    if terminal_count != expected:
        raise RuntimeError(
            f"benchmark fixture lost tokens: expected {expected}, got {terminal_count}"
        )
    if engine.enabled_transitions(net, marking):
        raise RuntimeError("benchmark fixture result is not quiescent")


def _percentile(sorted_values: Sequence[int], percentile: float) -> int:
    index = max(0, math.ceil(percentile * len(sorted_values)) - 1)
    return sorted_values[index]


def _summary(samples: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "min_ns": ordered[0],
        "median_ns": int(statistics.median(ordered)),
        "mean_ns": round(statistics.fmean(ordered), 1),
        "p95_ns": _percentile(ordered, 0.95),
        "p99_ns": _percentile(ordered, 0.99),
        "max_ns": ordered[-1],
        "stdev_ns": round(statistics.pstdev(ordered), 1),
    }


def _measure(
    operation: Callable[[int], None], *, iterations: int, repeats: int
) -> dict[str, Any]:
    all_samples: list[int] = []
    repeat_summaries: list[dict[str, int | float]] = []
    gc_was_enabled = gc.isenabled()
    try:
        for _repeat in range(repeats):
            gc.collect()
            gc.disable()
            samples: list[int] = []
            for index in range(iterations):
                started = time.perf_counter_ns()
                operation(index)
                samples.append(time.perf_counter_ns() - started)
            if gc_was_enabled:
                gc.enable()
            all_samples.extend(samples)
            repeat_summaries.append(_summary(samples))
    finally:
        if gc_was_enabled:
            gc.enable()
    return {
        "summary": _summary(all_samples),
        "repeat_summaries": repeat_summaries,
    }


def _warm(operation: Callable[[int], None], iterations: int) -> None:
    for index in range(iterations):
        operation(index)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    adapter = _adapter(args.backend)
    net, engine = build_fixture(adapter)
    tokens = sample_tokens(max(args.iterations, args.warmup, args.batch_size))
    empty = Marking()

    def inject_one(index: int) -> None:
        engine.inject_token(
            net, empty, "ingress", tokens[index % len(tokens)], attempt=index
        )

    prepared_one, _ = engine.inject_token(net, empty, "ingress", tokens[0], attempt=0)

    def run_one(_index: int) -> None:
        engine.run(net, prepared_one, max_steps=4)

    def inject_and_run_one(index: int) -> None:
        injected, _ = engine.inject_token(
            net, empty, "ingress", tokens[index % len(tokens)], attempt=index
        )
        engine.run(net, injected, max_steps=4)

    placements = tuple(("ingress", token) for token in tokens[: args.batch_size])

    def inject_batch(index: int) -> None:
        engine.inject_tokens(net, empty, placements, attempt=index)

    prepared_batch, _ = engine.inject_tokens(net, empty, placements, attempt=0)
    batch_max_steps = args.batch_size * 3 + 1

    def run_batch(_index: int) -> None:
        engine.run(net, prepared_batch, max_steps=batch_max_steps)

    def inject_and_run_batch(index: int) -> None:
        injected, _ = engine.inject_tokens(net, empty, placements, attempt=index)
        engine.run(net, injected, max_steps=batch_max_steps)

    # Compile CEL and stabilize interpreter/backend caches before every timed
    # workload.  Fixture correctness is checked outside the timing windows.
    for operation in (inject_one, run_one, inject_and_run_one):
        _warm(operation, args.warmup)
    for operation in (inject_batch, run_batch, inject_and_run_batch):
        _warm(operation, args.batch_warmup)

    one_result = engine.run(net, prepared_one, max_steps=4)
    _assert_quiescent_result(engine, net, one_result, 1)
    batch_result = engine.run(net, prepared_batch, max_steps=batch_max_steps)
    _assert_quiescent_result(engine, net, batch_result, args.batch_size)

    adapter_name = type(adapter).__name__
    package = _BACKEND_PACKAGES[adapter_name]
    try:
        package_version = version(package)
    except PackageNotFoundError:
        package_version = "unknown"

    return {
        "schema_version": 1,
        "benchmark": "engine-inject-run-guinan-shaped",
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version,
            "python_executable": sys.executable,
            "timer": "time.perf_counter_ns",
            "timer_resolution_ns": round(
                time.get_clock_info("perf_counter").resolution * 1e9
            ),
            "cel_backend": {
                "requested": args.backend,
                "adapter": adapter_name,
                "package": package,
                "version": package_version,
            },
        },
        "fixture": {
            "net": net.name,
            "transitions": len(net.transitions),
            "arcs": len(net.arcs),
            "cel_predicates": [
                'source == "co2mon"',
                "ppm >= 1000",
                "ppm < 1000",
            ],
            "firings_per_token": 3,
            "persistent_marking": f"{Marking.__module__}.{Marking.__qualname__}",
        },
        "parameters": {
            "warmup": args.warmup,
            "iterations": args.iterations,
            "batch_warmup": args.batch_warmup,
            "batch_size": args.batch_size,
            "batch_iterations": args.batch_iterations,
            "repeats": args.repeats,
        },
        "steady_state": {
            "inject_only": _measure(
                inject_one, iterations=args.iterations, repeats=args.repeats
            ),
            "run_only": _measure(
                run_one, iterations=args.iterations, repeats=args.repeats
            ),
            "inject_and_run": _measure(
                inject_and_run_one,
                iterations=args.iterations,
                repeats=args.repeats,
            ),
        },
        "reconnect_batch": {
            "inject_only": _measure(
                inject_batch,
                iterations=args.batch_iterations,
                repeats=args.repeats,
            ),
            "run_only": _measure(
                run_batch,
                iterations=args.batch_iterations,
                repeats=args.repeats,
            ),
            "inject_and_run": _measure(
                inject_and_run_batch,
                iterations=args.batch_iterations,
                repeats=args.repeats,
            ),
        },
    }


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark warmed Engine inject/run and reconnect batch paths.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Baseline capture (from implementations/python):\n"
            "  uv run python benchmarks/engine_hot_path.py --warmup 20 "
            "--iterations 100 --batch-warmup 1 --batch-size 256 "
            "--batch-iterations 3 --repeats 5 > baseline.json\n\n"
            "Compare summary median_ns/p95_ns with identical parameters, "
            "Python, CEL backend, and host."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("default", "python", "cpp", "rust"),
        default="default",
        help="CEL adapter (default: auto-select fastest installed backend)",
    )
    parser.add_argument("--warmup", type=_positive, default=20)
    parser.add_argument("--iterations", type=_positive, default=100)
    parser.add_argument("--batch-warmup", type=_positive, default=1)
    parser.add_argument("--batch-size", type=_positive, default=256)
    parser.add_argument("--batch-iterations", type=_positive, default=3)
    parser.add_argument("--repeats", type=_positive, default=5)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    result = run_benchmark(parse_args(argv))
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
