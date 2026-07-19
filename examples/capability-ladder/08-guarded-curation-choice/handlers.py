"""Handler registry support for the Guarded Curation Choice capability fixture."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from velocitron.schema import Token


if TYPE_CHECKING:
    from velocitron.registry import HandlerRegistry


Guard = Callable[[dict[str, Any]], bool]


def _should_speak(inp: dict[str, Any]) -> bool:
    """Read the decision from the same full binding passed to a transition."""
    curation = inp["inputTokens"]["curation_token"][0]
    return curation.data.get("should_speak") is True


def speak_eligible(inp: dict[str, Any]) -> bool:
    """Enable the speech branch when the selected curation requests speech."""
    return _should_speak(inp)


def speak_skip(inp: dict[str, Any]) -> bool:
    """Enable the silent branch exactly when the speech branch is ineligible."""
    return not _should_speak(inp)


def raising_speak_guard(_inp: dict[str, Any]) -> bool:
    """Exercise fail-closed guard handling without changing fixture topology."""
    raise RuntimeError("speak eligibility unavailable")


def always_true(_inp: dict[str, Any]) -> bool:
    """Expose the deliberately broken both-enabled registry variant."""
    return True


def always_false(_inp: dict[str, Any]) -> bool:
    """Expose the deliberately broken neither-enabled registry variant."""
    return False


def request_speak(_inp: dict[str, Any]) -> dict[str, Any]:
    """Emit one request for the speech pipeline."""
    return {
        "status": "completed",
        "outputTokens": {"speak_request": [Token("speak_req", {})]},
        "error": None,
        "metadata": {},
    }


def skip_speak(_inp: dict[str, Any]) -> dict[str, Any]:
    """Emit one explicit silent utterance without entering the speech pipeline."""
    return {
        "status": "completed",
        "outputTokens": {
            "final_utterance": [Token("utterance", {"silent": True, "text": ""})]
        },
        "error": None,
        "metadata": {},
    }


def _guard_pair(variant: str) -> tuple[Guard | None, Guard]:
    if variant == "complementary":
        return speak_eligible, speak_skip
    if variant == "missing_speak_guard":
        return None, speak_skip
    if variant == "raising_speak_guard":
        return raising_speak_guard, speak_skip
    if variant == "both_true":
        return always_true, always_true
    if variant == "both_false":
        return always_false, always_false
    raise ValueError(f"unknown guard registry variant: {variant}")


def register_all(registry: HandlerRegistry, variant: str = "complementary") -> None:
    """Register the scoped runtime refs for one behavioral scenario variant."""
    registry.register_transition("request_speak@curate", request_speak)
    registry.register_transition("skip_speak@curate", skip_speak)
    speak_guard, skip_guard = _guard_pair(variant)
    if speak_guard is not None:
        registry.register_guard("speak_eligible@curate", speak_guard)
    registry.register_guard("speak_skip@curate", skip_guard)
