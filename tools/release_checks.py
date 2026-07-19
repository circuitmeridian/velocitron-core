#!/usr/bin/env python3
"""Verify release archives and exercise them from isolated consumer environments."""

from __future__ import annotations

from collections.abc import Iterable
import argparse
import email.parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from typing import Any
import zipfile

_SCHEMA_VERSION = 1
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_EXPECTED_REPOSITORY = "circuitmeridian/velocitron-core"
_CORE_REPOSITORY_IDENTITY = re.compile(
    rb"(?i)(?:(?:git\+https|https)://github\.com/"
    rb"|ssh://git@github\.com/"
    rb"|git@github\.com:)?"
    rb"(?P<owner>[a-z0-9_.-]+)/velocitron-core(?:\.git)?(?![a-z0-9_.-])"
)
_FORBIDDEN_BROWSER_SOURCE = (
    ("CommonJS require", re.compile(r"\brequire\s*\(")),
    ("dynamic Function", re.compile(r"\b(?:new\s+)?Function\s*\(")),
    ("direct eval", re.compile(r"(?<![\w$.])eval\s*\(")),
    ("WebAssembly", re.compile(r"\bWebAssembly\b")),
    ("Node protocol import", re.compile(r"['\"]node:")),
)

_PYTHON_CORE_SMOKE = r"""
import importlib.metadata
import importlib.util
from importlib.resources import files

import velocitron
from velocitron.cel import CelpyAdapter, get_default_adapter

assert importlib.metadata.version("velocitron") == EXPECTED_VERSION
assert importlib.util.find_spec("cel") is None
assert importlib.util.find_spec("cel_expr_python") is None
adapter = get_default_adapter()
assert type(adapter) is CelpyAdapter
assert adapter.eval(adapter.compile("value + 1"), {"value": 1}) == 2
assert files("velocitron.schemas").joinpath("net.schema.json").is_file()
assert files("velocitron.schemas").joinpath("composition.schema.json").is_file()
assert files("velocitron.skill").joinpath("SKILL.md").is_file()
assert velocitron.__package__ == "velocitron"
"""

_PYTHON_EXTRA_SMOKE = r"""
import importlib.metadata

from velocitron.cel import get_default_adapter

assert importlib.metadata.version("velocitron") == EXPECTED_VERSION
adapter = get_default_adapter()
assert type(adapter) is EXPECTED_CLASS
assert adapter.eval(adapter.compile("value + 1"), {"value": 1}) == 2
"""

_NODE_CONSUMER_SMOKE = r"""
import {
  Engine,
  HandlerRegistry,
  compilePetrinetText,
  createDefaultCelAdapter,
  parseNet,
} from "@velocitron/core";

const source = `net packed_consumer
(input) -> [move] -> (output)
[move] handler "move"
`;
const compiled = compilePetrinetText(source, "packed-consumer.petrinet");
if (compiled.documentKind !== "net") throw new Error("DSL did not produce a net");
const net = parseNet(compiled.document);
const registry = new HandlerRegistry();
registry.registerTransition("move", ({inputTokens}) => ({
  status: "completed",
  outputTokens: {output: inputTokens.input},
}));
const marking = {input: [{type: "token", data: {source: "consumer"}}]};
const fired = new Engine(registry).fire(net, marking, "move", {attempt: 0});
if (fired.record.status !== "completed" || fired.marking.output?.length !== 1) {
  throw new Error("simulator-facing engine seam failed");
}
const cel = createDefaultCelAdapter();
if (cel.evaluate(cel.compile("value + 1"), {value: 1}) !== 2) {
  throw new Error("browser CEL seam failed");
}
"""


class ReleaseCheckError(ValueError):
    """An archive, package consumer, or release identity failed verification."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stdout = getattr(exc, "stdout", "") or ""
        stderr = getattr(exc, "stderr", "") or ""
        detail = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
        suffix = f":\n{detail}" if detail else ""
        raise ReleaseCheckError(f"command failed: {' '.join(command)}{suffix}") from exc
    return completed.stdout.strip()


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "NODE_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
    ):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _validate_identity(
    *, repository: str, tag: str, tag_ref: str, commit: str, version: str, run_id: int
) -> None:
    if repository != _EXPECTED_REPOSITORY:
        raise ReleaseCheckError(
            f"repository must be {_EXPECTED_REPOSITORY!r}, got {repository!r}"
        )
    if not _VERSION.fullmatch(version):
        raise ReleaseCheckError(f"invalid release version: {version!r}")
    if tag != f"v{version}":
        raise ReleaseCheckError(f"tag {tag!r} does not match version {version!r}")
    if not _HEX_40.fullmatch(tag_ref):
        raise ReleaseCheckError(
            "tag_ref must be a lowercase 40-character Git object ID"
        )
    if not _HEX_40.fullmatch(commit):
        raise ReleaseCheckError("commit must be a lowercase 40-character Git commit ID")
    if run_id <= 0:
        raise ReleaseCheckError("run_id must be positive")


def _normalized_member(name: str, *, directory: bool = False) -> str:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise ReleaseCheckError(f"unsafe archive member path: {name!r}")
    stripped = name[:-1] if directory and name.endswith("/") else name
    parts = PurePosixPath(stripped).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ReleaseCheckError(f"unsafe archive member path: {name!r}")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != stripped:
        raise ReleaseCheckError(f"non-normalized archive member path: {name!r}")
    return normalized


def _expected_directories(files: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for name in files:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _validate_member_set(
    *,
    kind: str,
    actual_files: set[str],
    actual_directories: set[str],
    expected_files: list[str],
) -> None:
    expected = set(expected_files)
    if actual_files != expected:
        missing = sorted(expected - actual_files)
        unexpected = sorted(actual_files - expected)
        raise ReleaseCheckError(
            f"{kind} archive file set differs: missing={missing}, unexpected={unexpected}"
        )
    unexpected_directories = actual_directories - _expected_directories(expected_files)
    if unexpected_directories:
        raise ReleaseCheckError(
            f"{kind} archive has unexpected directories: {sorted(unexpected_directories)}"
        )


def _inspect_zip(
    path: Path, *, kind: str, expected_files: list[str]
) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseCheckError(f"cannot read {kind} archive: {path}") from exc
    seen: set[str] = set()
    files: set[str] = set()
    directories: set[str] = set()
    try:
        for info in infos:
            normalized = _normalized_member(info.filename, directory=info.is_dir())
            if normalized in seen:
                raise ReleaseCheckError(
                    f"{kind} archive has duplicate member: {normalized}"
                )
            seen.add(normalized)
            mode = info.external_attr >> 16
            if info.is_dir():
                if stat.S_IFMT(mode) not in (0, stat.S_IFDIR):
                    raise ReleaseCheckError(
                        f"{kind} archive has non-regular member: {normalized}"
                    )
                directories.add(normalized)
            elif stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ReleaseCheckError(
                    f"{kind} archive has non-regular member: {normalized}"
                )
            else:
                files.add(normalized)
        _validate_member_set(
            kind=kind,
            actual_files=files,
            actual_directories=directories,
            expected_files=expected_files,
        )
    except Exception:
        archive.close()
        raise
    return archive


def _inspect_tar(
    path: Path, *, kind: str, expected_files: list[str]
) -> tarfile.TarFile:
    try:
        archive = tarfile.open(path, "r:gz")
        members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseCheckError(f"cannot read {kind} archive: {path}") from exc
    seen: set[str] = set()
    files: set[str] = set()
    directories: set[str] = set()
    try:
        for member in members:
            normalized = _normalized_member(member.name, directory=member.isdir())
            if normalized in seen:
                raise ReleaseCheckError(
                    f"{kind} archive has duplicate member: {normalized}"
                )
            seen.add(normalized)
            if member.isdir():
                directories.add(normalized)
            elif member.isfile():
                files.add(normalized)
            else:
                raise ReleaseCheckError(
                    f"{kind} archive has non-regular member: {normalized}"
                )
        _validate_member_set(
            kind=kind,
            actual_files=files,
            actual_directories=directories,
            expected_files=expected_files,
        )
    except Exception:
        archive.close()
        raise
    return archive


def _read_zip_member(archive: zipfile.ZipFile, member: str) -> bytes:
    try:
        return archive.read(member)
    except KeyError as exc:
        raise ReleaseCheckError(
            f"archive metadata member is missing: {member}"
        ) from exc


def _read_tar_member(archive: tarfile.TarFile, member: str) -> bytes:
    try:
        info = archive.getmember(member)
        source = archive.extractfile(info)
    except (KeyError, tarfile.TarError) as exc:
        raise ReleaseCheckError(
            f"archive metadata member is missing: {member}"
        ) from exc
    if source is None:
        raise ReleaseCheckError(f"archive metadata member is not a file: {member}")
    return source.read()


def _python_metadata(content: bytes, *, kind: str) -> dict[str, Any]:
    metadata = email.parser.BytesParser().parsebytes(content)
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ReleaseCheckError(f"{kind} package metadata lacks Name or Version")
    project_urls: dict[str, str] = {}
    for value in metadata.get_all("Project-URL", []):
        label, separator, url = value.partition(", ")
        if not separator or not label or not url or label in project_urls:
            raise ReleaseCheckError(f"{kind} package metadata has invalid Project-URL")
        project_urls[label] = url
    return {
        "name": name,
        "version": version,
        "requiresDist": sorted(metadata.get_all("Requires-Dist", [])),
        "providesExtra": sorted(metadata.get_all("Provides-Extra", [])),
        "projectUrls": dict(sorted(project_urls.items())),
    }


def _load_allowlist(path: Path, *, version: str) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCheckError(f"missing regular allowlist file: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseCheckError(f"cannot read artifact allowlist: {path}") from exc
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "version",
        "artifacts",
    }:
        raise ReleaseCheckError("artifact allowlist has an unexpected top-level shape")
    if document["schemaVersion"] != _SCHEMA_VERSION or document["version"] != version:
        raise ReleaseCheckError(
            "artifact allowlist schema or version does not match candidate"
        )
    artifacts = document["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 3:
        raise ReleaseCheckError(
            "artifact allowlist must describe exactly three packages"
        )
    expected_kinds = ["python-wheel", "python-sdist", "npm-package"]
    seen_paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) or set(artifact) != {
            "files",
            "format",
            "kind",
            "packageName",
            "path",
        }:
            raise ReleaseCheckError("artifact allowlist entry has an unexpected shape")
        if artifact["kind"] != expected_kinds[index]:
            raise ReleaseCheckError(
                "artifact allowlist kinds are missing or out of order"
            )
        expected_format = "zip" if artifact["kind"] == "python-wheel" else "tar-gz"
        if artifact["format"] != expected_format:
            raise ReleaseCheckError(f"wrong archive format for {artifact['kind']}")
        relative_path = artifact["path"]
        if not isinstance(relative_path, str):
            raise ReleaseCheckError("allowlisted artifact path must be a string")
        _normalized_member(relative_path)
        if relative_path in seen_paths:
            raise ReleaseCheckError(
                f"duplicate allowlisted artifact path: {relative_path}"
            )
        seen_paths.add(relative_path)
        files = artifact["files"]
        if (
            not isinstance(files, list)
            or not files
            or not all(isinstance(item, str) for item in files)
            or files != sorted(files)
            or len(files) != len(set(files))
        ):
            raise ReleaseCheckError(
                f"{artifact['kind']} file allowlist must be unique and sorted"
            )
        for member in files:
            _normalized_member(member)
    return document, _sha256(path)


def _expected_project_urls(repository: str) -> dict[str, str]:
    base = f"https://github.com/{repository}"
    return {
        "Homepage": base,
        "Issues": f"{base}/issues",
        "Repository": base,
    }


def _wrong_repository_identities(content: bytes) -> list[str]:
    return sorted(
        {
            match.group(0).decode("ascii")
            for match in _CORE_REPOSITORY_IDENTITY.finditer(content)
            if match.group("owner").decode("ascii").lower() != "circuitmeridian"
        }
    )


def _reject_wrong_repository_identity(
    kind: str, members: Iterable[tuple[str, bytes]]
) -> None:
    offenders = {
        member: identities
        for member, content in members
        if (identities := _wrong_repository_identities(content))
    }
    if offenders:
        raise ReleaseCheckError(
            f"{kind} archive contains wrong repository identity: {offenders}"
        )


def inspect_artifacts(
    root: Path, allowlist_path: Path, *, version: str, repository: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate exact archive members and return package metadata records."""
    _validate_identity(
        repository=repository,
        tag=f"v{version}",
        tag_ref="0" * 40,
        commit="0" * 40,
        version=version,
        run_id=1,
    )
    root = root.resolve()
    allowlist, allowlist_sha256 = _load_allowlist(allowlist_path, version=version)
    project_urls = _expected_project_urls(repository)
    records: list[dict[str, Any]] = []
    for spec in allowlist["artifacts"]:
        relative_path = spec["path"]
        artifact_path = root / relative_path
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ReleaseCheckError(
                f"missing regular release artifact: {relative_path}"
            )
        kind = spec["kind"]
        expected_name = spec["packageName"]
        if spec["format"] == "zip":
            with _inspect_zip(
                artifact_path, kind=kind, expected_files=spec["files"]
            ) as archive:
                _reject_wrong_repository_identity(
                    kind,
                    (
                        (member, _read_zip_member(archive, member))
                        for member in spec["files"]
                    ),
                )
                metadata_members = [
                    name
                    for name in spec["files"]
                    if name.endswith(".dist-info/METADATA")
                ]
                if len(metadata_members) != 1:
                    raise ReleaseCheckError(
                        "wheel allowlist must contain exactly one METADATA"
                    )
                metadata = _python_metadata(
                    _read_zip_member(archive, metadata_members[0]), kind=kind
                )
                if metadata["projectUrls"] != project_urls:
                    raise ReleaseCheckError(
                        f"{kind} package metadata repository URLs differ"
                    )
        elif kind == "python-sdist":
            prefix = f"velocitron-{version}/"
            with _inspect_tar(
                artifact_path, kind=kind, expected_files=spec["files"]
            ) as archive:
                _reject_wrong_repository_identity(
                    kind,
                    (
                        (member, _read_tar_member(archive, member))
                        for member in spec["files"]
                    ),
                )
                metadata = _python_metadata(
                    _read_tar_member(archive, f"{prefix}PKG-INFO"), kind=kind
                )
                try:
                    pyproject = tomllib.loads(
                        _read_tar_member(archive, f"{prefix}pyproject.toml").decode(
                            "utf-8"
                        )
                    )
                except (UnicodeError, tomllib.TOMLDecodeError) as exc:
                    raise ReleaseCheckError("sdist pyproject.toml is invalid") from exc
                project = pyproject.get("project")
                if not isinstance(project, dict):
                    raise ReleaseCheckError("sdist pyproject project table is missing")
                if project.get("name") != expected_name:
                    raise ReleaseCheckError(
                        "sdist pyproject name differs from candidate"
                    )
                if project.get("version") != version:
                    raise ReleaseCheckError(
                        "sdist pyproject version differs from candidate"
                    )
                if project.get("urls") != project_urls:
                    raise ReleaseCheckError(
                        "sdist pyproject repository URLs differ from candidate"
                    )
                if metadata["projectUrls"] != project_urls:
                    raise ReleaseCheckError(
                        f"{kind} package metadata repository URLs differ"
                    )
        else:
            with _inspect_tar(
                artifact_path, kind=kind, expected_files=spec["files"]
            ) as archive:
                _reject_wrong_repository_identity(
                    kind,
                    (
                        (member, _read_tar_member(archive, member))
                        for member in spec["files"]
                    ),
                )
                try:
                    package = json.loads(
                        _read_tar_member(archive, "package/package.json").decode(
                            "utf-8"
                        )
                    )
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ReleaseCheckError("npm package.json is invalid") from exc
                if not isinstance(package, dict):
                    raise ReleaseCheckError("npm package.json must be an object")
                metadata = {
                    "name": package.get("name"),
                    "version": package.get("version"),
                    "type": package.get("type"),
                    "main": package.get("main"),
                    "types": package.get("types"),
                    "dependencies": package.get("dependencies"),
                    "homepage": package.get("homepage"),
                    "bugs": package.get("bugs"),
                    "repository": package.get("repository"),
                }
                base_url = project_urls["Homepage"]
                expected_npm_identity = {
                    "homepage": base_url,
                    "bugs": {"url": project_urls["Issues"]},
                    "repository": {
                        "type": "git",
                        "url": f"git+{base_url}.git",
                        "directory": "implementations/typescript",
                    },
                }
        if metadata["name"] != expected_name or metadata["version"] != version:
            raise ReleaseCheckError(
                f"{kind} metadata identity differs: name={metadata['name']!r}, version={metadata['version']!r}"
            )
        if kind == "npm-package" and any(
            metadata[field] != expected_npm_identity[field]
            for field in ("homepage", "bugs", "repository")
        ):
            raise ReleaseCheckError("npm package metadata repository URLs differ")
        records.append(
            {
                "kind": kind,
                "path": relative_path,
                "sha256": _sha256(artifact_path),
                "size": artifact_path.stat().st_size,
                "memberCount": len(spec["files"]),
                "metadata": metadata,
            }
        )
    return records, {
        "path": allowlist_path.name,
        "sha256": allowlist_sha256,
        "artifactMemberCounts": {
            record["kind"]: record["memberCount"] for record in records
        },
    }


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_command(environment: Path, command: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return (
        environment / ("Scripts" if os.name == "nt" else "bin") / f"{command}{suffix}"
    )


def _create_venv(uv: str, python_version: str, environment: Path) -> Path:
    _run(
        [uv, "venv", "--python", python_version, str(environment)],
        env=_clean_environment(),
    )
    return _venv_python(environment)


def _install_python(
    uv: str, python: Path, artifact: Path, *, extra: str | None = None
) -> None:
    requirement = str(artifact.resolve())
    if extra is not None:
        requirement = f"{requirement}[{extra}]"
    _run(
        [uv, "pip", "install", "--python", str(python), requirement],
        env=_clean_environment(),
    )


def _exercise_python_core(
    python: Path, environment: Path, *, version: str
) -> dict[str, Any]:
    script = f"EXPECTED_VERSION = {version!r}\n" + _PYTHON_CORE_SMOKE
    _run([str(python), "-c", script], env=_clean_environment())
    net_path = environment / "consumer.petrinet"
    net_path.write_text(
        "net consumer\n(input) -> [move] -> (output)\n", encoding="utf-8"
    )
    cli = _venv_command(environment, "velocitron")
    viz = _venv_command(environment, "velocitron-viz")
    _run([str(cli), "validate", str(net_path)], env=_clean_environment())
    canonical = _run([str(cli), "to-json", str(net_path)], env=_clean_environment())
    json_path = environment / "consumer.json"
    json_path.write_text(canonical + "\n", encoding="utf-8")
    _run(
        [str(cli), "explain", str(net_path), "--format", "text"],
        env=_clean_environment(),
    )
    _run([str(viz), str(json_path)], env=_clean_environment())
    return {
        "celBackend": "CelpyAdapter",
        "checks": ["import", "resources", "cli", "visualizer", "cel"],
        "pythonVersion": _run(
            [str(python), "-c", "import platform; print(platform.python_version())"],
            env=_clean_environment(),
        ),
    }


def _exercise_python_extra(
    python: Path, *, version: str, class_name: str
) -> dict[str, Any]:
    script = (
        f"EXPECTED_VERSION = {version!r}\n"
        f"from velocitron.cel import {class_name} as EXPECTED_CLASS\n"
        + _PYTHON_EXTRA_SMOKE
    )
    _run([str(python), "-c", script], env=_clean_environment())
    return {
        "celBackend": class_name,
        "checks": ["import", "cel-extra"],
        "pythonVersion": _run(
            [str(python), "-c", "import platform; print(platform.python_version())"],
            env=_clean_environment(),
        ),
    }


def _node_builtin_modules(node: str) -> set[str]:
    source = (
        "import {builtinModules} from 'node:module';"
        "console.log(JSON.stringify(builtinModules));"
    )
    try:
        modules = json.loads(
            _run(
                [node, "--input-type=module", "-e", source],
                env=_clean_environment(),
            )
        )
    except json.JSONDecodeError as exc:
        raise ReleaseCheckError("Node builtin-module inventory is invalid") from exc
    if not isinstance(modules, list) or not all(
        isinstance(module, str) for module in modules
    ):
        raise ReleaseCheckError("Node builtin-module inventory is invalid")
    return {module.removeprefix("node:") for module in modules}


def _audit_installed_browser_files(
    package_roots: dict[str, Path], node_builtins: set[str]
) -> dict[str, int]:
    if not node_builtins:
        raise ReleaseCheckError("Node builtin-module inventory is empty")
    builtins = "|".join(
        re.escape(module) for module in sorted(node_builtins, key=len, reverse=True)
    )
    builtin_import = re.compile(
        rf"(?:\bfrom\s+|\bimport\s*(?:\(\s*)?)['\"](?:{builtins})['\"]"
    )
    counts: dict[str, int] = {}
    for package_name, source_root in package_roots.items():
        javascript = sorted(source_root.rglob("*.js"))
        if not javascript:
            raise ReleaseCheckError(
                f"installed browser package contains no JavaScript: {package_name}"
            )
        for path in javascript:
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(source_root).as_posix()
            for label, pattern in _FORBIDDEN_BROWSER_SOURCE:
                if pattern.search(source):
                    raise ReleaseCheckError(
                        f"browser seam rejected {label} in {package_name}/{relative}"
                    )
            if builtin_import.search(source):
                raise ReleaseCheckError(
                    f"browser seam rejected Node builtin import in "
                    f"{package_name}/{relative}"
                )
        counts[package_name] = len(javascript)
    return counts


def run_consumers(
    root: Path,
    *,
    version: str,
    python_version: str,
    uv: str,
    node: str,
    npm: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Install exact artifacts outside the source tree and exercise public seams."""
    root = root.resolve()
    wheel = root / "python" / f"velocitron-{version}-py3-none-any.whl"
    sdist = root / "python" / f"velocitron-{version}.tar.gz"
    npm_package = root / "npm" / f"velocitron-core-{version}.tgz"
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="velocitron-release-consumers-"
    ) as temporary:
        consumer_root = Path(temporary)
        wheel_core = consumer_root / "python-wheel-core"
        wheel_python = _create_venv(uv, python_version, wheel_core)
        _install_python(uv, wheel_python, wheel)
        results.append(
            {
                "artifact": "python-wheel",
                "installation": "core",
                **_exercise_python_core(wheel_python, wheel_core, version=version),
            }
        )

        sdist_core = consumer_root / "python-sdist-core"
        sdist_python = _create_venv(uv, python_version, sdist_core)
        _install_python(uv, sdist_python, sdist)
        results.append(
            {
                "artifact": "python-sdist",
                "installation": "core",
                **_exercise_python_core(sdist_python, sdist_core, version=version),
            }
        )

        for extra, class_name in (
            ("cel-cpp", "CelExprAdapter"),
            ("cel-rust", "CelRustAdapter"),
        ):
            extra_environment = consumer_root / f"python-wheel-{extra}"
            extra_python = _create_venv(uv, python_version, extra_environment)
            _install_python(uv, extra_python, wheel, extra=extra)
            results.append(
                {
                    "artifact": "python-wheel",
                    "installation": extra,
                    **_exercise_python_extra(
                        extra_python, version=version, class_name=class_name
                    ),
                }
            )

        node_consumer = consumer_root / "node"
        node_consumer.mkdir()
        (node_consumer / "package.json").write_text(
            json.dumps(
                {
                    "name": "velocitron-release-consumer",
                    "private": True,
                    "type": "module",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _run(
            [
                npm,
                "install",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--save-exact",
                str(npm_package),
            ],
            cwd=node_consumer,
            env=_clean_environment(),
        )
        installed_root = node_consumer / "node_modules" / "@velocitron" / "core"
        installed_metadata = json.loads(
            (installed_root / "package.json").read_text(encoding="utf-8")
        )
        if (
            installed_metadata.get("name") != "@velocitron/core"
            or installed_metadata.get("version") != version
        ):
            raise ReleaseCheckError(
                "installed npm package identity differs from candidate"
            )
        _run(
            [node, "--input-type=module", "-e", _NODE_CONSUMER_SMOKE],
            cwd=node_consumer,
            env=_clean_environment(),
        )
        scanned_files = _audit_installed_browser_files(
            {
                "@velocitron/core": installed_root / "dist",
                "@marcbachmann/cel-js": (
                    node_consumer / "node_modules" / "@marcbachmann" / "cel-js" / "lib"
                ),
            },
            _node_builtin_modules(node),
        )
        results.append(
            {
                "artifact": "npm-package",
                "installation": "packed-core",
                "checks": [
                    "install",
                    "import",
                    "dsl",
                    "simulator-seam",
                    "cel",
                    "browser-static-audit",
                ],
                "browserJavaScriptFiles": scanned_files,
                "nodeVersion": _run(
                    [node, "--version"], env=_clean_environment()
                ).removeprefix("v"),
            }
        )

    toolchain = {
        "controllerPython": sys.version.split()[0],
        "node": _run([node, "--version"], env=_clean_environment()).removeprefix("v"),
        "npm": _run([npm, "--version"], env=_clean_environment()),
        "uv": _run([uv, "--version"], env=_clean_environment()),
    }
    return results, toolchain


def create_evidence(
    root: Path,
    allowlist_path: Path,
    *,
    repository: str,
    tag: str,
    tag_ref: str,
    commit: str,
    version: str,
    run_id: int,
    python_version: str,
    uv: str,
    node: str,
    npm: str,
) -> dict[str, Any]:
    """Inspect the candidate, run clean consumers, and return deterministic evidence."""
    _validate_identity(
        repository=repository,
        tag=tag,
        tag_ref=tag_ref,
        commit=commit,
        version=version,
        run_id=run_id,
    )
    artifacts, allowlist = inspect_artifacts(
        root, allowlist_path, version=version, repository=repository
    )
    consumers, toolchain = run_consumers(
        root,
        version=version,
        python_version=python_version,
        uv=uv,
        node=node,
        npm=npm,
    )
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "source": {
            "repository": repository,
            "tag": tag,
            "tagRef": tag_ref,
            "commit": commit,
            "runId": run_id,
            "version": version,
        },
        "allowlist": allowlist,
        "artifacts": artifacts,
        "consumers": consumers,
        "toolchain": toolchain,
    }


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--tag-ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--node", default="node")
    parser.add_argument("--npm", default="npm")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        root = args.root.resolve()
        output = args.output.resolve()
        if not output.is_relative_to(root):
            raise ReleaseCheckError("evidence output must be inside the release root")
        evidence = create_evidence(
            root,
            args.allowlist.resolve(),
            repository=args.repository,
            tag=args.tag,
            tag_ref=args.tag_ref,
            commit=args.commit,
            version=args.version,
            run_id=args.run_id,
            python_version=args.python_version,
            uv=args.uv,
            node=args.node,
            npm=args.npm,
        )
        write_evidence(output, evidence)
    except (ReleaseCheckError, OSError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
