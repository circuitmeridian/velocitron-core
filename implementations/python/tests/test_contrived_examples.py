"""Parametrized test runner for contrived examples.

Discovers ``examples/contrived/*.test.json`` at collection time and runs each
against the Python reference engine. One case per ``.test.json`` file; the
corresponding net JSON is derived by dropping the ``.test`` suffix.

This is the shared runner the ``(validation-contrived)`` aggregate will
exercise across all 5 contrived examples.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from _cel_adapters import ADAPTER_IDS, adapters

from velocitron.cel import CelAdapter
from velocitron.engine import Engine
from velocitron.journal import JsonlJournal
from velocitron.parser import parse_net
from velocitron.registry import HandlerRegistry

# ── Test case discovery ───────────────────────────────────────────────────

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples" / "contrived"
TEST_CASES = sorted(EXAMPLES_DIR.glob("*.test.json"))
IDS = [p.stem.replace(".test", "") for p in TEST_CASES]


# ── Helpers ───────────────────────────────────────────────────────────────


def _token_to_dict(t: Any) -> dict[str, Any]:
    """Normalize one token to a plain dict, whether it is a ``Token`` dataclass
    (engine result) or a raw dict (expected marking built straight from JSON)."""
    if isinstance(t, dict):
        return {"type": t["type"], "data": t.get("data", {})}  # pyright: ignore[reportUnknownMemberType]
    return {"type": t.type, "data": t.data}


def _normalize_marking(
    m: Mapping[str, Iterable[Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Drop empty places and convert tokens to plain dicts for comparison."""
    result: dict[str, list[dict[str, Any]]] = {}
    for place, tokens in m.items():
        token_list = [_token_to_dict(t) for t in tokens]
        if token_list:
            result[place] = token_list
    return result


# ── Parametrized test ─────────────────────────────────────────────────────


@pytest.mark.parametrize("adapter", adapters(), ids=ADAPTER_IDS)
@pytest.mark.parametrize("test_path", TEST_CASES, ids=IDS)
def test_contrived_example(test_path: Path, adapter: CelAdapter) -> None:
    """Run a contrived example end-to-end against the Python engine.

    Each ``.test.json`` defines the expected firing sequence and final
    marking; the net JSON (same stem, no ``.test`` suffix) provides the
    net structure and initial marking.
    """
    # given: a test case and its corresponding net
    case = json.loads(test_path.read_text())
    net_path = test_path.with_name(test_path.stem.replace(".test", "") + ".json")
    net = parse_net(net_path, cel_adapter=adapter)
    assert net.initial_marking is not None, (
        f"Net {net_path} must declare an initialMarking"
    )
    marking = net.initial_marking

    # and: a handler registry with all contrived handlers registered
    handlers_path = EXAMPLES_DIR / "handlers.py"
    spec = importlib.util.spec_from_file_location("contrived_handlers", handlers_path)
    assert spec is not None and spec.loader is not None, (
        f"Could not load handler module from {handlers_path}"
    )
    handlers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handlers)
    registry = HandlerRegistry()
    handlers.register_all(registry)

    # and: an in-memory journal to capture the firing sequence
    journal = JsonlJournal()

    # and: an engine configured to raise on deposit violations
    engine = Engine(
        registry, journal=journal, deposit_violation="raise", cel_adapter=adapter
    )

    # when: the engine runs the net to quiescence (or maxSteps)
    final = engine.run(net, marking, max_steps=case["maxSteps"])

    # then: the firing sequence matches the expected ordered outcomes
    actual_sequence = [(r["transition"], r["status"]) for r in journal._records]  # pyright: ignore[reportPrivateUsage]
    expected_sequence = [
        (e["transition"], e["status"]) for e in case["expectedFiringSequence"]
    ]
    assert actual_sequence == expected_sequence, (
        f"Firing sequence mismatch:\n  actual:   {actual_sequence}\n  expected: {expected_sequence}"
    )

    # and: the final marking matches the expected marking (empty places dropped)
    actual_normalized = _normalize_marking(final)
    expected_normalized = _normalize_marking(case["expectedFinalMarking"])
    assert actual_normalized == expected_normalized, (
        f"Final marking mismatch:\n  actual:   {actual_normalized}\n  expected: {expected_normalized}"
    )
