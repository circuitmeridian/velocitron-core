"""The firing-journal hook module.

The journal is decoupled from the engine via hooks: the engine emits
``FiringRecord`` objects through the ``Journal`` protocol and does NOT assign
``sequence``, pick storage, or require a journal's presence. ``JsonlJournal``
is the default implementation.

References: spec/firing-semantics.md (D4).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from .contract import FiringTimestamps, HandlerError
from .schema import Token


# ── FiringRecord ─────────────────────────────────────────────────────────


# The two terminal firing statuses. "pending" is v2.
# spec/firing-semantics.md
FiringStatus = Literal["completed", "failed"]


class FiringRecord(TypedDict):
    """What the engine emits per firing attempt.

    The engine fills every field here except ``sequence`` — the journal
    implementation owns unique-id numbering. This record is a plain
    ``TypedDict``, so callers may construct it directly.

    References: spec/firing-semantics.md (D4).
    """

    firingId: str
    netId: str
    transition: str
    attempt: int
    status: FiringStatus
    inputTokens: dict[str, list[Token]]
    outputTokens: dict[str, list[Token]]
    error: HandlerError | None
    metadata: dict[str, Any]
    timestamps: FiringTimestamps


# The two injection kinds. "inject" appends a token to a place; "update"
# replaces a place's contents (the singleton clock/deadline-advance pattern).
# spec/firing-semantics.md (f); ADR 0013.
InjectionKind = Literal["inject", "update"]


class InjectionRecord(TypedDict):
    """What the engine emits per token injection (any environment arrival).

    A consumer-driven marking event — NOT a firing — recorded so replay stays
    deterministic across injected tokens: the environment arrivals (file
    arrivals, observations, clock/deadline tokens; ADR 0013, as amended) a
    runtime wrapper injects between firings. Like :class:`FiringRecord` it carries no
    ``sequence`` (the journal owns numbering) and its ``timestamps`` are
    metadata-only (excluded from replay comparison).

    - ``injectionId`` — deterministic id, ``<netId>/@inject/<place>/<attempt>``,
      distinct from a firing's ``firingId`` (the ``@inject`` marker).
    - ``attempt`` — the injection attempt counter as a first-class field
      (also embedded in ``injectionId``), so replay tooling reads it directly
      instead of parsing the id.
    - ``kind`` — ``"inject"`` (token appended) or ``"update"`` (place replaced).
    - ``tokens`` — the token(s) now present by this injection.
    - ``replaced`` — the token(s) an ``"update"`` removed (empty for ``"inject"``).

    References: spec/firing-semantics.md (f); ADR 0013.
    """

    injectionId: str
    netId: str
    place: str
    attempt: int
    kind: InjectionKind
    tokens: list[Token]
    replaced: list[Token]
    timestamps: FiringTimestamps


# ── Runtime lifecycle records ───────────────────────────────────────────


RuntimeEvent = Literal["source_failed", "suppressed"]


class RuntimeRecord(TypedDict):
    """A lifecycle event outside the Petri-net firing/injection contract.

    Runtime records are intentionally a separate channel: a detached worker's
    late result was suppressed, so presenting it as another firing would make
    replay incorrectly apply a second terminal outcome.  The journal owns its
    sequence in the same way it owns firing and injection records.
    """

    event: RuntimeEvent
    netId: str
    firingId: str | None
    source: str | None
    detail: str


@runtime_checkable
class RuntimeJournal(Protocol):
    """Optional journal capability used by :class:`velocitron.runtime.Runtime`."""

    def record_runtime(self, record: RuntimeRecord) -> None: ...


# ── Journal hook protocol ────────────────────────────────────────────────


@runtime_checkable
class Journal(Protocol):
    """The hook protocol the engine emits records through.

    The engine holds a ``Journal | None`` and calls these methods only when a
    journal is attached. Any class implementing all three methods satisfies it.

    ``record_injection`` is the third hook channel (alongside ``record_firing``
    and ``record_deposit_violation``): the engine routes a token injection
    (any environment arrival — clock/deadline tokens included) exclusively
    through it, never through ``record_firing`` (an injection is not a
    firing). A journal that numbers all channels from one stream (e.g.
    :class:`JsonlJournal`) gives injections and firings a single monotonic
    sequence so replay is deterministic across injected tokens.

    References: spec/firing-semantics.md (D4, f).
    """

    def record_firing(self, record: FiringRecord) -> None: ...

    def record_deposit_violation(self, record: FiringRecord) -> None: ...

    def record_injection(self, record: InjectionRecord) -> None: ...


# ── Serialization helper ─────────────────────────────────────────────────


def _serialize(obj: Any) -> Any:
    """``json.dumps(default=...)`` helper for records containing dataclasses.

    Token (a frozen dataclass from ``.schema``) serializes to
    ``{"type": ..., "data": ...}``; any other dataclass falls back to
    ``dataclasses.asdict``. Anything else falls back to ``repr``.
    """
    if isinstance(obj, Token):
        return {"type": obj.type, "data": obj.data}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return repr(obj)


# ── JsonlJournal — the default implementation ────────────────────────────


class JsonlJournal:
    """The default journal: appends each record to a timestamped ``.jsonl`` file.

    The journal assigns each record a monotonic 0-based ``sequence`` itself.
    With ``prefix=None`` (the default) records accumulate in memory only and
    nothing is written to disk; ``flush`` is a no-op. With ``prefix`` set,
    ``flush`` writes all buffered records to
    ``f"{prefix}-{ISO8601_UTC}.jsonl"``, one JSON object per line.
    """

    def __init__(self, prefix: str | None = None) -> None:
        self._prefix = prefix
        self._records: list[dict[str, Any]] = []
        self._sequence = 0

    def record_firing(self, record: FiringRecord) -> None:
        """Append a firing record, assigning the next monotonic sequence."""
        self._append(record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        """Append a deposit-violation record, assigning the next sequence.

        Same numbering stream as ``record_firing`` — both are firings.
        """
        self._append(record)

    def record_injection(self, record: InjectionRecord) -> None:
        """Append a token-injection record, assigning the next sequence.

        Same monotonic numbering stream as ``record_firing`` — so a firing and
        an interleaved injection (any environment arrival) occupy consecutive
        sequence slots, and replay across injected tokens is deterministic.
        """
        self._append(record)

    def record_runtime(self, record: RuntimeRecord) -> None:
        """Append a Runtime lifecycle event in the shared sequence stream."""
        self._append(record)

    def _append(self, record: FiringRecord | InjectionRecord | RuntimeRecord) -> None:
        """Buffer a record with the next monotonic 0-based ``sequence``."""
        rec = {**record, "sequence": self._sequence}
        self._sequence += 1
        self._records.append(rec)

    def flush(self) -> None:
        """Write all buffered records to a timestamped ``.jsonl`` file.

        No-op when no ``prefix`` was given. Creates parent directories as
        needed. Each record is one ``json.dumps(rec, default=_serialize)``
        line.
        """
        if self._prefix is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = Path(f"{self._prefix}-{stamp}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for rec in self._records:
                fh.write(json.dumps(rec, default=_serialize))
                fh.write("\n")
