"""Syntax checking for Petri-net DSL content in files and Markdown fences.

``velocitron check`` walks files and directories, compiles every
``*.petrinet`` file and every ``petrinet`` fenced code block found in
Markdown files through the DSL compiler, and reports each diagnostic at
its location in the file the author edits — for fences that means the
Markdown file's own line numbers, not the extracted snippet's.

This is DSL *syntax* validation, distinct from :mod:`velocitron.lint`
(the advisory semantic lint over an already-parsed
:class:`~velocitron.schema.Net`, ADR 0016): this module answers "is this
DSL text valid at all?", not "is this valid net a likely bug?".

Fences whose info string carries ``no-lint`` after ``petrinet`` (i.e.
opened with ```` ```petrinet no-lint ````) hold intentionally-invalid
teaching examples and are skipped.

Composition documents in standalone ``*.petrinet`` files resolve their
``use`` references relative to the file, exactly like ``velocitron
validate``; composition fences are compiled without resolving references,
so the referenced files need not exist next to the Markdown.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
from typing import Any

from velocitron.parser import NetValidationError

from .api import compile_petrinet_text, read_petrinet_text
from .diagnostics import PetrinetDslError

_CHECKED_SUFFIXES = frozenset({".petrinet", ".md"})
_SKIPPED_DIRECTORY_NAMES = frozenset({"node_modules", "__pycache__", "venv"})
_NO_LINT_MARKER = "no-lint"
_FENCE_LINE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")


@dataclass(frozen=True)
class MarkdownFence:
    """One ``petrinet`` fenced block extracted from Markdown source."""

    text: str
    line_offset: int
    """Add to a 1-based snippet line to get the Markdown file line."""

    lint: bool
    """False when the info string opts out via ``no-lint``."""


@dataclass(frozen=True)
class CheckReport:
    """Aggregated result of one ``velocitron check`` invocation."""

    petrinet_files: int
    markdown_files: int
    fences: int
    skipped_fences: int
    errors: tuple[str, ...]

    def summary(self) -> str:
        text = (
            f"checked {self.petrinet_files} petrinet file(s) and "
            f"{self.fences} petrinet fence(s) in {self.markdown_files} "
            f"markdown file(s): {len(self.errors)} error(s)"
        )
        if self.skipped_fences:
            text += f" ({self.skipped_fences} no-lint fence(s) skipped)"
        return text


def extract_petrinet_fences(markdown: str) -> list[MarkdownFence]:
    """Extract ``petrinet`` fenced code blocks with their line offsets."""
    fences: list[MarkdownFence] = []
    marker: str | None = None
    petrinet = False
    lint = True
    opened_at = 0
    content: list[str] = []

    def close_fence() -> None:
        if petrinet:
            fences.append(MarkdownFence("\n".join(content) + "\n", opened_at, lint))

    for number, line in enumerate(markdown.splitlines(), start=1):
        match = _FENCE_LINE.match(line)
        if marker is None:
            if match is None:
                continue
            info = match["info"]
            if match["marker"].startswith("`") and "`" in info:
                # Per CommonMark, a backtick fence's info string cannot
                # contain backticks; this line is prose, not a fence.
                continue
            marker = match["marker"]
            tokens = info.split()
            petrinet = bool(tokens) and tokens[0] == "petrinet"
            lint = _NO_LINT_MARKER not in tokens[1:]
            opened_at = number
            content = []
            continue
        if (
            match is not None
            and match["marker"][0] == marker[0]
            and len(match["marker"]) >= len(marker)
            and not match["info"].strip()
        ):
            close_fence()
            marker = None
            continue
        content.append(line)
    if marker is not None:
        # Per CommonMark, an unterminated fence runs to end of document.
        close_fence()
    return fences


def _composition_loader(
    origin: Path,
) -> Callable[[str], Mapping[str, Any] | Path | str]:
    """Load composition ``use`` references relative to the checked file.

    Mirrors ``velocitron.dsl.cli._composition_loader``; ``cli`` imports this
    module, so sharing the helper from ``cli`` would create an import cycle.
    """

    def load(ref: str) -> Mapping[str, Any] | Path | str:
        path = Path(ref)
        if not path.is_absolute():
            path = origin / path
        suffix = path.suffix.lower()
        if suffix == ".json":
            return path
        if suffix == ".petrinet":
            return compile_petrinet_text(read_petrinet_text(path), str(path))
        raise NetValidationError(f"unsupported composition net reference: {ref!r}")

    return load


def _relocated(error: PetrinetDslError, source: str, line_offset: int = 0) -> str:
    """Render a diagnostic against the checked file's own path and lines."""
    diagnostic = error.diagnostic
    span = diagnostic.span
    return replace(
        diagnostic,
        span=replace(
            span,
            source=source,
            start=replace(span.start, line=span.start.line + line_offset),
            end=replace(span.end, line=span.end.line + line_offset),
        ),
    ).render()


def check_petrinet_file(path: Path) -> list[str]:
    """Compile one ``*.petrinet`` file; return rendered diagnostics."""
    origin = path.parent
    try:
        compile_petrinet_text(
            read_petrinet_text(path),
            str(path),
            net_loader=_composition_loader(origin),
            origin=origin,
        )
    except PetrinetDslError as error:
        return [_relocated(error, str(path))]
    except (OSError, UnicodeError, ValueError, NetValidationError) as error:
        return [f"{path}:1:1: error[PN200]: {error}"]
    return []


def check_markdown_file(path: Path) -> tuple[int, int, list[str]]:
    """Check one Markdown file's ``petrinet`` fences.

    Returns ``(checked_fences, skipped_fences, errors)``.
    """
    try:
        markdown = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return 0, 0, [f"{path}:1:1: error[PN200]: {error}"]
    checked = 0
    skipped = 0
    errors: list[str] = []
    for fence in extract_petrinet_fences(markdown):
        if not fence.lint:
            skipped += 1
            continue
        checked += 1
        try:
            compile_petrinet_text(fence.text, str(path))
        except PetrinetDslError as error:
            errors.append(_relocated(error, str(path), fence.line_offset))
        except (ValueError, NetValidationError) as error:
            errors.append(f"{path}:{fence.line_offset + 1}:1: error[PN200]: {error}")
    return checked, skipped, errors


def _is_skipped_directory(path: Path) -> bool:
    return (
        path.name.startswith(".")
        or path.name in _SKIPPED_DIRECTORY_NAMES
        or (path / "pyvenv.cfg").is_file()
    )


def _walk(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        parent = Path(dirpath)
        dirnames[:] = sorted(
            name for name in dirnames if not _is_skipped_directory(parent / name)
        )
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() in _CHECKED_SUFFIXES:
                yield parent / filename


def _candidate_files(paths: Sequence[Path], errors: list[str]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from _walk(path)
        elif path.is_file():
            if path.suffix.lower() in _CHECKED_SUFFIXES:
                yield path
            else:
                errors.append(f"{path}: error: not a .petrinet or .md file")
        else:
            errors.append(f"{path}: error: no such file or directory")


def check_paths(paths: Sequence[Path]) -> CheckReport:
    """Check every ``*.petrinet`` file and Markdown fence under ``paths``."""
    petrinet_files = 0
    markdown_files = 0
    fences = 0
    skipped = 0
    errors: list[str] = []
    for path in _candidate_files(paths, errors):
        if path.suffix.lower() == ".petrinet":
            petrinet_files += 1
            errors.extend(check_petrinet_file(path))
        else:
            markdown_files += 1
            checked, ignored, fence_errors = check_markdown_file(path)
            fences += checked
            skipped += ignored
            errors.extend(fence_errors)
    return CheckReport(petrinet_files, markdown_files, fences, skipped, tuple(errors))
