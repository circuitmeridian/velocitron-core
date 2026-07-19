#!/usr/bin/env python3
"""Create and verify the immutable Velocitron release-candidate manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

_SCHEMA_VERSION = 3
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_WORKFLOW_PATH = ".github/workflows/release-candidate.yml"
_EXPECTED_REPOSITORY = "circuitmeridian/velocitron-core"
_VERIFICATION_TOOL_PATHS = (
    "tools/release_identity.py",
    "tools/release_manifest.py",
)


class ManifestError(ValueError):
    """The candidate manifest or one of its subjects is invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_specs(version: str) -> tuple[tuple[str, str], ...]:
    return (
        ("python-sdist", f"python/velocitron-{version}.tar.gz"),
        ("python-wheel", f"python/velocitron-{version}-py3-none-any.whl"),
        ("npm-package", f"npm/velocitron-core-{version}.tgz"),
    )


def _validate_identity(
    *, repository: str, tag: str, tag_ref: str, commit: str, version: str, run_id: int
) -> None:
    if repository != _EXPECTED_REPOSITORY:
        raise ManifestError(
            f"repository must be {_EXPECTED_REPOSITORY!r}, got {repository!r}"
        )
    if not _VERSION.fullmatch(version):
        raise ManifestError(f"invalid release version: {version!r}")
    if tag != f"v{version}":
        raise ManifestError(f"tag {tag!r} does not match version {version!r}")
    if not _HEX_40.fullmatch(tag_ref):
        raise ManifestError("tag_ref must be a lowercase 40-character Git object ID")
    if not _HEX_40.fullmatch(commit):
        raise ManifestError("commit must be a lowercase 40-character Git commit ID")
    if run_id <= 0:
        raise ManifestError("run_id must be positive")


def _validate_workflow_identity(
    *,
    repository: str,
    tag: str,
    commit: str,
    workflow_path: str,
    workflow_ref: str,
    workflow_sha: str,
) -> None:
    if workflow_path != _WORKFLOW_PATH:
        raise ManifestError(f"unexpected candidate workflow path: {workflow_path!r}")
    expected_ref = f"{repository}/{workflow_path}@refs/tags/{tag}"
    if workflow_ref != expected_ref:
        raise ManifestError("candidate workflow ref does not match the release tag")
    if workflow_sha != commit:
        raise ManifestError(
            "candidate workflow revision does not match the source commit"
        )


def _file_record(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"missing regular release file: {relative_path}")
    return {
        "path": relative_path,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _source_record(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"missing regular candidate source file: {relative_path}")
    return {
        "path": relative_path,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _required_string(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise ManifestError(f"release manifest {field} must be a string")
    return value


def create_manifest(
    root: Path,
    *,
    source_root: Path,
    repository: str,
    tag: str,
    tag_ref: str,
    commit: str,
    version: str,
    run_id: int,
    workflow_ref: str,
    workflow_sha: str,
) -> dict[str, Any]:
    """Describe exact packages, evidence, workflow, and verification tooling."""
    _validate_identity(
        repository=repository,
        tag=tag,
        tag_ref=tag_ref,
        commit=commit,
        version=version,
        run_id=run_id,
    )
    _validate_workflow_identity(
        repository=repository,
        tag=tag,
        commit=commit,
        workflow_path=_WORKFLOW_PATH,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
    )

    artifacts: list[dict[str, Any]] = []
    for kind, path in _artifact_specs(version):
        sbom_path = f"sbom/{Path(path).name}.cdx.json"
        artifacts.append(
            {
                "kind": kind,
                **_file_record(root, path),
                "sbom": _file_record(root, sbom_path),
            }
        )

    return {
        "schemaVersion": _SCHEMA_VERSION,
        "repository": repository,
        "tag": tag,
        "tagRef": tag_ref,
        "commit": commit,
        "version": version,
        "runId": run_id,
        "workflow": {
            **_source_record(source_root, _WORKFLOW_PATH),
            "ref": workflow_ref,
            "sha": workflow_sha,
        },
        "verificationTools": [
            _source_record(source_root, path) for path in _VERIFICATION_TOOL_PATHS
        ],
        "evidence": _file_record(root, "artifact-checks.json"),
        "artifacts": artifacts,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _expected_paths(version: str) -> set[str]:
    paths: set[str] = {"artifact-checks.json"}
    for _, path in _artifact_specs(version):
        paths.add(path)
        paths.add(f"sbom/{Path(path).name}.cdx.json")
    return paths


def _validate_file_set(root: Path, manifest_path: Path, version: str) -> None:
    manifest_relative = manifest_path.relative_to(root).as_posix()
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != manifest_relative
    }
    expected = _expected_paths(version)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ManifestError(
            f"release file set differs: missing={missing}, unexpected={unexpected}"
        )


def verify_manifest(
    root: Path,
    manifest_path: Path,
    *,
    source_root: Path,
    expected_sha256: str,
    repository: str,
    run_id: int,
    workflow_sha: str,
) -> dict[str, Any]:
    """Verify candidate identity, source verifiers, closed files, and digests."""
    root = root.resolve()
    source_root = source_root.resolve()
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(root):
        raise ManifestError("manifest must be inside the release root")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ManifestError("expected manifest SHA-256 must be 64 lowercase hex digits")
    if _sha256(manifest_path) != expected_sha256:
        raise ManifestError(
            "release manifest SHA-256 does not match the approved value"
        )

    document: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ManifestError("release manifest must be a JSON object")
    manifest = cast(dict[str, Any], document)
    expected_keys = {
        "schemaVersion",
        "repository",
        "tag",
        "tagRef",
        "commit",
        "version",
        "runId",
        "workflow",
        "verificationTools",
        "evidence",
        "artifacts",
    }
    if set(manifest) != expected_keys:
        raise ManifestError("release manifest has an unexpected top-level shape")
    if manifest["schemaVersion"] != _SCHEMA_VERSION:
        raise ManifestError("unsupported release manifest schema version")
    if manifest["repository"] != repository:
        raise ManifestError(
            "release manifest repository does not match this repository"
        )
    if manifest["runId"] != run_id:
        raise ManifestError("release manifest run ID does not match the approved run")

    version = manifest["version"]
    _validate_identity(
        repository=manifest["repository"],
        tag=manifest["tag"],
        tag_ref=manifest["tagRef"],
        commit=manifest["commit"],
        version=version,
        run_id=manifest["runId"],
    )

    workflow_raw = manifest["workflow"]
    if not isinstance(workflow_raw, dict):
        raise ManifestError("release manifest workflow must be an object")
    workflow = cast(dict[str, Any], workflow_raw)
    _validate_workflow_identity(
        repository=manifest["repository"],
        tag=manifest["tag"],
        commit=manifest["commit"],
        workflow_path=_required_string(workflow, "path"),
        workflow_ref=_required_string(workflow, "ref"),
        workflow_sha=_required_string(workflow, "sha"),
    )
    expected_workflow = {
        **_source_record(source_root, _WORKFLOW_PATH),
        "ref": (
            f"{manifest['repository']}/{_WORKFLOW_PATH}@refs/tags/{manifest['tag']}"
        ),
        "sha": workflow_sha,
    }
    if workflow != expected_workflow or workflow["sha"] != workflow_sha:
        raise ManifestError("candidate workflow identity or bytes differ")

    expected_tools = [
        _source_record(source_root, path) for path in _VERIFICATION_TOOL_PATHS
    ]
    if manifest["verificationTools"] != expected_tools:
        raise ManifestError("candidate verification tooling differs")

    _validate_file_set(root, manifest_path, version)
    expected_evidence = _file_record(root, "artifact-checks.json")
    if manifest["evidence"] != expected_evidence:
        raise ManifestError("release evidence record differs: artifact-checks.json")

    artifacts_raw = manifest["artifacts"]
    specs = _artifact_specs(version)
    if not isinstance(artifacts_raw, list):
        raise ManifestError("release manifest artifacts must be a list")
    artifacts = cast(list[Any], artifacts_raw)
    if len(artifacts) != len(specs):
        raise ManifestError("release manifest has an unexpected artifact count")
    for record, (kind, path) in zip(artifacts, specs, strict=True):
        expected_record = {
            "kind": kind,
            **_file_record(root, path),
            "sbom": _file_record(root, f"sbom/{Path(path).name}.cdx.json"),
        }
        if record != expected_record:
            raise ManifestError(f"release artifact record differs: {path}")

    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="write a candidate manifest")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--tag", required=True)
    create.add_argument("--tag-ref", required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--run-id", type=int, required=True)
    create.add_argument("--workflow-ref", required=True)
    create.add_argument("--workflow-sha", required=True)

    verify = commands.add_parser("verify", help="verify a downloaded candidate")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--source-root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--expected-sha256", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--run-id", type=int, required=True)
    verify.add_argument("--workflow-sha", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            manifest = create_manifest(
                args.root,
                source_root=args.source_root,
                repository=args.repository,
                tag=args.tag,
                tag_ref=args.tag_ref,
                commit=args.commit,
                version=args.version,
                run_id=args.run_id,
                workflow_ref=args.workflow_ref,
                workflow_sha=args.workflow_sha,
            )
            write_manifest(args.output, manifest)
            print(
                json.dumps(
                    {
                        "manifest": str(args.output),
                        "manifestSha256": _sha256(args.output),
                    },
                    sort_keys=True,
                )
            )
            return 0

        manifest = verify_manifest(
            args.root,
            args.manifest,
            source_root=args.source_root,
            expected_sha256=args.expected_sha256,
            repository=args.repository,
            run_id=args.run_id,
            workflow_sha=args.workflow_sha,
        )
        print(
            json.dumps(
                {
                    key: manifest[key]
                    for key in (
                        "tag",
                        "tagRef",
                        "commit",
                        "version",
                        "runId",
                        "workflow",
                        "verificationTools",
                    )
                },
                sort_keys=True,
            )
        )
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
