#!/usr/bin/env python3
"""Acquire, generate, and validate the checked-in Python and TypeScript ANTLR artifacts."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ANTLR_VERSION = "4.13.2"
ANTLR_SHA256 = "eae2dfa119a64327444672aff63e9ec35a20180dc5b8090b7a6ab85125df4d76"
ANTLR_URL = f"https://www.antlr.org/download/antlr-{ANTLR_VERSION}-complete.jar"
ROOT = Path(__file__).resolve().parents[1]
GRAMMAR = ROOT / "grammar" / "VelocitronPetriNet.g4"
DSL_PACKAGE = ROOT / "implementations" / "python" / "src" / "velocitron" / "dsl"
TARGET = DSL_PACKAGE / "generated"
DSL_PACKAGE_MARKER = '"""Velocitron Petri Net DSL support."""\n'
PACKAGE_MARKER = "# Generated ANTLR artifacts for the Velocitron Petri Net DSL.\n"
TYPESCRIPT_TARGET = (
    ROOT / "implementations" / "typescript" / "src" / "dsl" / "generated"
)
TYPESCRIPT_GENERATED_FILES = frozenset(
    {
        "VelocitronPetriNet.interp",
        "VelocitronPetriNet.tokens",
        "VelocitronPetriNetLexer.interp",
        "VelocitronPetriNetLexer.tokens",
        "VelocitronPetriNetLexer.ts",
        "VelocitronPetriNetParser.ts",
        "VelocitronPetriNetVisitor.ts",
    }
)


def cache_dir() -> Path:
    configured = os.environ.get("VELOCITRON_ANTLR_CACHE")
    if configured:
        return Path(configured).expanduser()
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "velocitron" / "antlr"
    return Path.home() / ".cache" / "velocitron" / "antlr"


def jar_path() -> Path:
    return cache_dir() / f"antlr-{ANTLR_VERSION}-complete.jar"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_jar() -> Path:
    jar = jar_path()
    if jar.is_file() and sha256(jar) == ANTLR_SHA256:
        return jar

    jar.parent.mkdir(parents=True, exist_ok=True)
    temporary = jar.with_suffix(".download")
    try:
        with urlopen(
            Request(ANTLR_URL, headers={"User-Agent": "velocitron-antlr-tool"}),
            timeout=60,
        ) as response:
            with temporary.open("wb") as destination:
                shutil.copyfileobj(response, destination)
        actual = sha256(temporary)
        if actual != ANTLR_SHA256:
            raise RuntimeError(
                f"ANTLR download checksum mismatch: expected {ANTLR_SHA256}, got {actual}"
            )
        temporary.replace(jar)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return jar


def java_major(version: str) -> int:
    match = re.search(r'(?:openjdk |java )?version "([^"]+)"', version)
    if match is None:
        raise RuntimeError(f"Could not determine Java version from: {version.strip()}")
    release = match.group(1)
    major_match = re.match(r"1\.(\d+)|(\d+)", release)
    if major_match is None:
        raise RuntimeError(f"Could not determine Java major version from: {release}")
    return int(major_match.group(1) or major_match.group(2))


def java_command() -> str:
    java = shutil.which("java")
    if java is None:
        raise RuntimeError("Java 11 or newer is required; `java` was not found on PATH")
    version = subprocess.run(
        [java, "-version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout
    major = java_major(version)
    if major < 11:
        raise RuntimeError(f"Java 11 or newer is required; found Java {major}")
    return java


def generate_python_into(staging: Path) -> None:
    java = java_command()
    jar = verified_jar()
    staging.mkdir()
    subprocess.run(
        [
            java,
            "-jar",
            str(jar),
            "-Dlanguage=Python3",
            "-visitor",
            "-no-listener",
            "-Werror",
            "-Xexact-output-dir",
            "-o",
            str(staging),
            GRAMMAR.name,
        ],
        cwd=GRAMMAR.parent,
        check=True,
    )
    prepare_generated_python_for_ruff(staging)
    format_generated_python(staging)
    lint_generated_python(staging)
    format_generated_python(staging)
    normalize_generated_text(staging)

    (staging / "__init__.py").write_text(PACKAGE_MARKER, encoding="utf-8")


def generate_typescript_into(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    subprocess.run(
        [
            java_command(),
            "-jar",
            str(verified_jar()),
            "-Dlanguage=TypeScript",
            "-visitor",
            "-no-listener",
            "-Werror",
            "-Xexact-output-dir",
            "-o",
            str(staging),
            GRAMMAR.name,
        ],
        cwd=GRAMMAR.parent,
        check=True,
    )
    generated_files = frozenset(
        path.name for path in staging.iterdir() if path.is_file()
    )
    if generated_files != TYPESCRIPT_GENERATED_FILES:
        missing = sorted(TYPESCRIPT_GENERATED_FILES - generated_files)
        extra = sorted(generated_files - TYPESCRIPT_GENERATED_FILES)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise RuntimeError(
            "Unexpected TypeScript ANTLR output (" + "; ".join(details) + ")"
        )
    normalize_generated_typescript(staging)


def prepare_generated_python_for_ruff(directory: Path) -> None:
    directive = "# ruff: noqa: F403, F405\n"
    for path in sorted(directory.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if directive in text:
            continue
        lines = text.splitlines(keepends=True)
        insert_at = 2 if len(lines) > 1 and lines[1].startswith("# encoding:") else 1
        lines.insert(insert_at, directive)
        path.write_text("".join(lines), encoding="utf-8")


def format_generated_python(directory: Path) -> None:
    pre_commit = shutil.which("pre-commit")
    if pre_commit is None:
        raise RuntimeError("The project Ruff formatter requires `pre-commit` on PATH")
    generated_python = [
        str(path) for path in sorted(directory.glob("*.py")) if path.is_file()
    ]
    if generated_python:
        subprocess.run(
            [pre_commit, "run", "ruff-format", "--files", *generated_python],
            cwd=ROOT,
            check=True,
        )


def lint_generated_python(directory: Path) -> None:
    pre_commit = shutil.which("pre-commit")
    if pre_commit is None:
        raise RuntimeError("The project Ruff linter requires `pre-commit` on PATH")
    generated_python = [
        str(path) for path in sorted(directory.glob("*.py")) if path.is_file()
    ]
    if generated_python:
        subprocess.run(
            [pre_commit, "run", "ruff", "--files", *generated_python],
            cwd=ROOT,
            check=True,
        )


def normalize_generated_text(directory: Path) -> None:
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix not in {".interp", ".py", ".tokens"}:
            continue
        text = path.read_text(encoding="utf-8")
        normalized = text.rstrip(" \t\r\n")
        if normalized:
            normalized += "\n"
        if normalized != text:
            path.write_text(normalized, encoding="utf-8")


def normalize_generated_typescript(directory: Path) -> None:
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix not in {".interp", ".tokens", ".ts"}:
            continue
        text = path.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if normalized:
            normalized += "\n"
        if normalized != text:
            path.write_text(normalized, encoding="utf-8", newline="\n")


def files_under(directory: Path) -> dict[Path, bytes]:
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def difference(expected: Path, actual: Path) -> str | None:
    expected_files = files_under(expected)
    actual_files = files_under(actual)
    missing = sorted(expected_files.keys() - actual_files.keys())
    extra = sorted(actual_files.keys() - expected_files.keys())
    changed = sorted(
        name
        for name in expected_files.keys() & actual_files.keys()
        if expected_files[name] != actual_files[name]
    )
    details = []
    if missing:
        details.append("missing: " + ", ".join(str(name) for name in missing))
    if extra:
        details.append("unexpected: " + ", ".join(str(name) for name in extra))
    if changed:
        details.append("changed: " + ", ".join(str(name) for name in changed))
    return "; ".join(details) if details else None


def staging_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="velocitron-antlr-")


def command_generate() -> None:
    with staging_directory() as temporary:
        staging = Path(temporary) / "generated"
        generate_python_into(staging)
        if TARGET.exists():
            shutil.rmtree(TARGET)
        DSL_PACKAGE.mkdir(parents=True, exist_ok=True)
        DSL_PACKAGE.joinpath("__init__.py").write_text(
            DSL_PACKAGE_MARKER, encoding="utf-8"
        )
        shutil.copytree(staging, TARGET)
    print(f"Generated {TARGET.relative_to(ROOT)}")


def command_check() -> None:
    with staging_directory() as temporary:
        staging = Path(temporary) / "generated"
        generate_python_into(staging)
        drift = difference(staging, TARGET)
    if (
        DSL_PACKAGE.joinpath("__init__.py").read_text(encoding="utf-8")
        != DSL_PACKAGE_MARKER
    ):
        drift = (
            "DSL package marker is out of date"
            if drift is None
            else f"{drift}; DSL package marker is out of date"
        )
    if drift:
        raise RuntimeError(f"Generated ANTLR artifacts are out of date ({drift})")
    print("Generated ANTLR artifacts are current")


def command_generate_typescript() -> None:
    with staging_directory() as temporary:
        staging = Path(temporary) / "generated"
        generate_typescript_into(staging)
        if TYPESCRIPT_TARGET.exists():
            shutil.rmtree(TYPESCRIPT_TARGET)
        TYPESCRIPT_TARGET.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, TYPESCRIPT_TARGET)
    print(f"Generated {TYPESCRIPT_TARGET.relative_to(ROOT)}")


def command_check_typescript() -> None:
    with staging_directory() as temporary:
        staging = Path(temporary) / "generated"
        generate_typescript_into(staging)
        drift = difference(staging, TYPESCRIPT_TARGET)
    if drift:
        raise RuntimeError(
            f"Generated TypeScript ANTLR artifacts are out of date ({drift})"
        )
    print("Generated TypeScript ANTLR artifacts are current")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "acquire",
            "generate",
            "check",
            "generate-typescript",
            "check-typescript",
        ),
    )
    command = parser.parse_args().command
    try:
        if command == "acquire":
            print(verified_jar())
        elif command == "generate":
            command_generate()
        elif command == "check":
            command_check()
        elif command == "generate-typescript":
            command_generate_typescript()
        else:
            command_check_typescript()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
