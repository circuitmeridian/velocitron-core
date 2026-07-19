"""Durable sqlite event store, journal, and handler-free tape replay.

A :class:`DurableJournal` implements velocitron's ``Journal`` protocol and
appends each firing / injection to a sqlite event log in the same critical
section that advances the marking — the engine calls the hook synchronously
during ``fire`` and ``inject_token``. :func:`replay_events` rebuilds a marking
from a recorded log WITHOUT invoking handlers: it applies each event's recorded
token effects — consume the recorded consume-mode inputs, deposit the recorded
outputs, apply injections. The state IS the event log: replayed once into a live
marking at startup, never rebuilt while a process runs.

This realizes the **tape-replay** that ``spec/firing-semantics.md`` D5 defers as
a test convenience — advancing the net from recorded outputs without re-invoking
handlers. It is distinct from D5's replay-determinism contract (re-run the
engine with identical handlers and assert journal equality record-for-record):
tape-replay reconstructs a marking, it does not verify determinism.

The crash window — an external effect performed, its event row not yet written —
is the caller's concern, not this module's: a check-before-do handler re-fires on
resume and adopts the pre-existing effect. One monotonic ``seq`` per instance,
single-writer, WAL — many readers may replay concurrently.

References: spec/firing-semantics.md (D5); ADR 0022.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import FiringRecord, InjectionRecord
from .schema import Marking, Net, Token

# Event-log kinds. Firing and injection carry marking effects; a deposit
# violation is a failed firing (no effect); a net revision is definitional, so
# the log is self-describing and a consumer can recover the net that produced it.
FIRING = "firing"
DEPOSIT_VIOLATION = "deposit_violation"
INJECTION = "injection"
NET_REVISION = "net_revision"


# ── The event store: schema and connection helpers ───────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    instance    TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    recorded_at TEXT    NOT NULL,
    PRIMARY KEY (instance, seq)
);
"""


@dataclass(frozen=True)
class StoredEvent:
    """One decoded row of the event log, in sequence order for one instance."""

    seq: int
    kind: str
    payload: dict[str, Any]


def open_database(database_path: Path) -> sqlite3.Connection:
    """Open (creating if absent) the event-log database in WAL mode."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(_SCHEMA)
    connection.commit()
    return connection


def read_events(connection: sqlite3.Connection, instance: str) -> list[StoredEvent]:
    """Read one instance's events in sequence order, payloads decoded from JSON."""
    rows = connection.execute(
        "SELECT seq, kind, payload FROM events WHERE instance = ? ORDER BY seq",
        (instance,),
    ).fetchall()
    return [
        StoredEvent(row["seq"], row["kind"], json.loads(row["payload"])) for row in rows
    ]


def known_instances(connection: sqlite3.Connection) -> list[str]:
    """Every instance that has at least one recorded event."""
    rows = connection.execute(
        "SELECT DISTINCT instance FROM events ORDER BY instance"
    ).fetchall()
    return [row["instance"] for row in rows]


# ── The durable journal ──────────────────────────────────────────────────────


def _serialize(obj: Any) -> Any:
    if isinstance(obj, Token):
        return {"type": obj.type, "data": obj.data}
    raise TypeError(f"cannot serialize {type(obj)!r} into an event payload")


def _token(token_dict: dict[str, Any]) -> Token:
    return Token(type=token_dict["type"], data=token_dict["data"])


class DurableJournal:
    """A velocitron ``Journal`` that appends one instance's records to sqlite.

    Single-writer discipline: the owning process is the sole writer for its
    instance, and every record is committed as it is appended, so a kill between
    firings never loses a committed event. The instance name is caller-supplied
    and namespaces one net's log within a shared database.
    """

    def __init__(self, connection: sqlite3.Connection, instance: str) -> None:
        self._connection = connection
        self._instance = instance
        row = connection.execute(
            "SELECT MAX(seq) AS max_seq FROM events WHERE instance = ?", (instance,)
        ).fetchone()
        self._seq = 0 if row["max_seq"] is None else row["max_seq"] + 1

    def record_firing(self, record: FiringRecord) -> None:
        self._append(FIRING, record)

    def record_deposit_violation(self, record: FiringRecord) -> None:
        self._append(DEPOSIT_VIOLATION, record)

    def record_injection(self, record: InjectionRecord) -> None:
        self._append(INJECTION, record)

    def record_net_revision(self, net_document: dict[str, Any]) -> None:
        """Record a net-definition revision so the log is self-describing."""
        self._append(NET_REVISION, net_document)

    def _append(self, kind: str, record: Any) -> None:
        self._connection.execute(
            "INSERT INTO events(instance, seq, kind, payload, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self._instance,
                self._seq,
                kind,
                json.dumps(record, default=_serialize),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()
        self._seq += 1


# ── Handler-free tape replay ─────────────────────────────────────────────────


def _consume_sources(net: Net) -> dict[str, set[str]]:
    """Map each transition to the source places it consumes (not read/inhibit).

    A firing record's ``inputTokens`` includes read-arc tokens (they bind but are
    not removed), so replay must consult the net to remove only consume-mode
    inputs — otherwise a read clock token would be wrongly consumed.
    """
    sources: dict[str, set[str]] = {}
    for arc in net.arcs:
        if (
            arc.to_transition is not None
            and arc.from_place is not None
            and arc.consume is not None
            and arc.consume.mode == "consume"
        ):
            sources.setdefault(arc.to_transition, set()).add(arc.from_place)
    return sources


def _remove_tokens(
    marking: Marking, place: str, token_dicts: list[dict[str, Any]]
) -> Marking:
    remaining = list(marking.get(place, []))
    for token_dict in token_dicts:
        target = _token(token_dict)
        for index, existing in enumerate(remaining):
            if existing == target:
                del remaining[index]
                break
    return marking.set(place, remaining)


def _append_tokens(marking: Marking, place: str, tokens: Iterable[Token]) -> Marking:
    return marking.set(place, [*marking.get(place, []), *tokens])


def replay_events(net: Net, events: Iterable[StoredEvent]) -> Marking:
    """Rebuild a marking by applying recorded event effects, invoking no handlers."""
    marking = net.initial_marking or Marking()
    consume_sources = _consume_sources(net)
    for event in events:
        if event.kind == FIRING:
            record = event.payload
            if record.get("status") != "completed":
                continue
            sources = consume_sources.get(record["transition"], set())
            for place, token_dicts in record["inputTokens"].items():
                if place in sources:
                    marking = _remove_tokens(marking, place, token_dicts)
            for place, token_dicts in record["outputTokens"].items():
                marking = _append_tokens(
                    marking, place, [_token(t) for t in token_dicts]
                )
        elif event.kind == INJECTION:
            record = event.payload
            tokens = [_token(t) for t in record["tokens"]]
            if record["kind"] == "update":
                marking = marking.set(record["place"], tokens)
            else:
                marking = _append_tokens(marking, record["place"], tokens)
        # DEPOSIT_VIOLATION and NET_REVISION carry no marking effect.
    return marking
