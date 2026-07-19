"""Stable source diagnostics for the Petri-net DSL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePosition:
    """A 1-based Unicode-scalar position plus a zero-based UTF-8 byte offset."""

    byte_offset: int
    line: int
    column: int

    def as_dict(self) -> dict[str, int]:
        return {
            "byteOffset": self.byte_offset,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class SourceSpan:
    """A half-open span in one logical source."""

    source: str
    start: SourcePosition
    end: SourcePosition

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "start": self.start.as_dict(),
            "end": self.end.as_dict(),
        }


@dataclass(frozen=True)
class RelatedDiagnostic:
    """A deterministic secondary source location for a diagnostic."""

    message: str
    span: SourceSpan


@dataclass(frozen=True)
class Diagnostic:
    """A portable, deterministic DSL diagnostic."""

    code: str
    message: str
    span: SourceSpan
    help: str | None = None
    related: tuple[RelatedDiagnostic, ...] = ()

    def render(self) -> str:
        position = self.span.start
        primary = f"{self.span.source}:{position.line}:{position.column}: error[{self.code}]: {self.message}"
        return primary if self.help is None else f"{primary}\nhelp: {self.help}"


class PetrinetDslError(Exception):
    """Raised when source parsing or contribution resolution fails."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.render())
        self.diagnostic = diagnostic
