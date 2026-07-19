"""Console commands for the Petri-net DSL."""

from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.resources
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Sequence, cast

import json5

from velocitron.explain import explain_net
from velocitron.parser import NetValidationError, parse_composition, parse_net
from velocitron.schema import Net
from .api import (
    compile_petrinet_text,
    emit_petrinet,
    read_petrinet_text,
    render_canonical_json,
)
from .check import check_paths
from .diagnostics import PetrinetDslError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="velocitron")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "to-json", "to-petrinet"):
        item = commands.add_parser(command)
        item.add_argument("input", metavar="INPUT")
        if command == "to-json":
            item.add_argument("--semantic-only", action="store_true")
        elif command == "to-petrinet":
            item.add_argument("--compact", action="store_true")
    explain = commands.add_parser("explain")
    explain.add_argument("input", metavar="INPUT")
    explain.add_argument("--format", choices=("markdown", "text"), default="markdown")
    explain.add_argument(
        "--level", choices=("practitioner", "newcomer"), default="practitioner"
    )
    skill = commands.add_parser("skill")
    skill.add_argument("pathname", metavar="PATHNAME")
    check = commands.add_parser(
        "check",
        help="validate DSL syntax in *.petrinet files and ```petrinet fences",
        description=(
            "Validate Petri-net DSL syntax in *.petrinet files and in "
            "```petrinet fenced code blocks inside Markdown files. "
            "Directories are walked recursively (hidden directories, "
            "node_modules, and virtualenvs are skipped). Fences opened "
            "with ```petrinet no-lint hold intentionally-invalid examples "
            "and are skipped. This checks DSL syntax only; it is distinct "
            "from the advisory semantic lint in velocitron.lint (ADR 0016)."
        ),
    )
    check.add_argument(
        "paths",
        metavar="PATH",
        nargs="*",
        help="files or directories to check (default: current directory)",
    )
    return parser


def _read(path: str) -> str:
    return read_petrinet_text(path)


def _json_document(path: str) -> dict[str, Any]:
    parsed = json.loads(_read(path))
    if not isinstance(parsed, dict):
        raise ValueError("JSON input must be an object")
    return cast(dict[str, Any], parsed)


def _strict_json_document(path: str) -> dict[str, Any]:
    def reject_non_json_constant(value: str) -> None:
        raise ValueError(f"JSON input contains non-finite value {value!r}")

    parsed = json.loads(_read(path), parse_constant=reject_non_json_constant)
    if not isinstance(parsed, dict):
        raise ValueError("JSON input must be an object")
    return cast(dict[str, Any], parsed)


def _json5_document(path: str) -> dict[str, Any]:
    parsed = json5.loads(_read(path))
    if not isinstance(parsed, dict):
        raise ValueError("JSON5 input must be an object")
    _reject_non_finite_json5_values(parsed)
    return cast(dict[str, Any], parsed)


def _reject_non_finite_json5_values(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON5 input must not contain non-finite numbers")
    elif isinstance(value, dict):
        for nested in cast(dict[str, Any], value).values():
            _reject_non_finite_json5_values(nested)
    elif isinstance(value, list):
        for nested in cast(list[Any], value):
            _reject_non_finite_json5_values(nested)


def _explain_net(path: str) -> Net:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        document = _strict_json_document(path)
    elif suffix == ".json5":
        document = _json5_document(path)
    elif suffix == ".petrinet":
        document = compile_petrinet_text(_read(path), path)
    else:
        raise NetValidationError(f"unsupported explain input type: {suffix or path!r}")

    if "nets" in document:
        raise NetValidationError("explain accepts a core Net, not a composition")
    return parse_net(document)


def _semantic_only(document: dict[str, Any]) -> dict[str, Any]:
    """Drop documentation recursively without reinterpreting semantic fields."""
    result = deepcopy(document)
    result.pop("description", None)
    result.pop("annotations", None)
    for collection in ("places", "transitions", "arcs"):
        raw_items = result.get(collection)
        if not isinstance(raw_items, list):
            continue
        for raw_item in cast(list[Any], raw_items):
            if isinstance(raw_item, dict):
                item = cast(dict[str, Any], raw_item)
                item.pop("description", None)
                item.pop("annotations", None)
    return result


def _composition_loader(origin: Path):
    def load(ref: str) -> Mapping[str, Any] | Path | str:
        path = Path(ref)
        if not path.is_absolute():
            path = origin / path
        suffix = path.suffix.lower()
        if suffix == ".json":
            return path
        if suffix == ".petrinet":
            return compile_petrinet_text(_read(str(path)), str(path))
        raise NetValidationError(f"unsupported composition net reference: {ref!r}")

    return load


def _compile_document(path: str) -> dict[str, Any]:
    origin = Path(path).parent
    return compile_petrinet_text(
        _read(path),
        path,
        net_loader=_composition_loader(origin),
        origin=origin,
    )


def _validate_document(document: dict[str, Any], path: str) -> str:
    if "nets" in document:
        origin = Path(path).parent
        parse_composition(
            document,
            origin=origin,
            net_loader=_composition_loader(origin),
        )
        return "composition"
    parse_net(document)
    return "net"


_SKILL_MANIFEST = ".velocitron-skill-manifest.json"


def _skill_source_dir() -> Path:
    """Resolve the bundled skill directory shipped as package data.

    The authoring source of truth is the repo-root ``skills/velocitron/``;
    ``src/velocitron/skill`` symlinks to it, so it ships in the wheel under
    ``velocitron/skill/`` and resolves in place under an editable install. Read
    exclusively via :mod:`importlib.resources` — one code path for both.
    """
    resource = importlib.resources.files("velocitron").joinpath("skill")
    source = Path(str(resource))
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError("bundled velocitron skill data not found")
    return source


def _relative_files(root: Path) -> list[str]:
    """Sorted POSIX-relative paths of every file under ``root``."""
    return sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )


def _read_manifest(path: Path) -> list[str]:
    """Managed-file list from a prior install's manifest (empty if absent/bad)."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    files = cast(dict[str, Any], data).get("files")
    if not isinstance(files, list):
        return []
    return [str(entry) for entry in cast(list[Any], files)]


def _install_skill(pathname: str) -> int:
    """Install or true-sync the bundled skill under ``<pathname>/velocitron/``.

    The target ``pathname`` must already exist. Every shipped file is written
    (overwriting), then — the true-sync step — any file listed in the previous
    install's manifest that the skill no longer ships is removed, while files a
    user added (in no manifest) are left untouched. A fresh manifest naming the
    managed files is written last. An existing ``SKILL.md`` marks an update.
    """
    target = Path(pathname)
    if not target.is_dir():
        raise NotADirectoryError(
            f"target path does not exist or is not a directory: {pathname}"
        )
    source = _skill_source_dir()
    dest = target / "velocitron"
    action = "updated" if (dest / "SKILL.md").is_file() else "installed"

    shipped = _relative_files(source)
    previous = _read_manifest(dest / _SKILL_MANIFEST)

    for rel in shipped:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, out)

    for rel in previous:
        if rel not in shipped:
            stale = dest / rel
            if stale.is_file():
                stale.unlink()
    for directory in sorted(
        (p for p in dest.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()

    (dest / _SKILL_MANIFEST).write_text(
        json.dumps({"files": shipped}, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(f"{action} velocitron skill at {dest}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        report = check_paths([Path(item) for item in args.paths] or [Path(".")])
        for issue in report.errors:
            sys.stderr.write(issue + "\n")
        sys.stdout.write(report.summary() + "\n")
        return 1 if report.errors else 0
    if args.command == "skill":
        try:
            return _install_skill(args.pathname)
        except OSError as error:
            sys.stderr.write(f"{args.pathname}:1:1: error[PN200]: {error}\n")
            return 1
    path = args.input
    try:
        suffix = Path(path).suffix.lower()
        if args.command == "validate":
            if suffix == ".json":
                document = _json_document(path)
                kind = _validate_document(document, path)
            else:
                document = _compile_document(path)
                kind = "composition" if "nets" in document else "net"
            sys.stdout.write(kind + "\n")
        elif args.command == "to-json":
            document = _compile_document(path)
            _validate_document(document, path)

            if args.semantic_only:
                document = _semantic_only(document)
            sys.stdout.write(render_canonical_json(document, indent=2) + "\n")
        elif args.command == "to-petrinet":
            if suffix == ".json":
                document = _json_document(path)
                _validate_document(document, path)
            else:
                document = _compile_document(path)
            sys.stdout.write(emit_petrinet(document, compact=args.compact))
        elif args.command == "explain":
            net = _explain_net(path)
            sys.stdout.write(explain_net(net, format=args.format, level=args.level))
    except PetrinetDslError as error:
        sys.stderr.write(error.diagnostic.render() + "\n")
        return 1
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        NetValidationError,
    ) as error:
        sys.stderr.write(f"{path}:1:1: error[PN200]: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
