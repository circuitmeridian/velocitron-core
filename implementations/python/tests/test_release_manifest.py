from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[3]
_TOOL_PATH = _REPOSITORY_ROOT / "tools" / "release_manifest.py"
_SPEC = importlib.util.spec_from_file_location("release_manifest", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RELEASE_MANIFEST_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RELEASE_MANIFEST_MODULE)


class _ReleaseManifestTool(Protocol):
    ManifestError: type[ValueError]

    def create_manifest(
        self,
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
    ) -> dict[str, object]: ...

    def write_manifest(self, path: Path, manifest: dict[str, object]) -> None: ...

    def verify_manifest(
        self,
        root: Path,
        manifest_path: Path,
        *,
        source_root: Path,
        expected_sha256: str,
        repository: str,
        run_id: int,
        workflow_sha: str,
    ) -> dict[str, object]: ...


_TOOL = cast(_ReleaseManifestTool, cast(object, _RELEASE_MANIFEST_MODULE))

_REPOSITORY = "circuitmeridian/velocitron-core"
_VERSION = "0.1.0"
_TAG = f"v{_VERSION}"
_TAG_REF = "a" * 40
_COMMIT = "b" * 40
_RUN_ID = 1234
_WORKFLOW_PATH = ".github/workflows/release-candidate.yml"
_WORKFLOW_REF = f"{_REPOSITORY}/{_WORKFLOW_PATH}@refs/tags/{_TAG}"


def _write_candidate(root: Path) -> None:
    files = {
        f"python/velocitron-{_VERSION}.tar.gz": b"sdist",
        f"python/velocitron-{_VERSION}-py3-none-any.whl": b"wheel",
        f"npm/velocitron-core-{_VERSION}.tgz": b"npm",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        sbom = root / "sbom" / f"{path.name}.cdx.json"
        sbom.parent.mkdir(parents=True, exist_ok=True)
        sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
    (root / "artifact-checks.json").write_text(
        '{"checks":["python-sdist","python-wheel","npm-package"]}\n',
        encoding="utf-8",
    )


def _source_root(root: Path) -> Path:
    return root.with_name(f"{root.name}-source")


def _write_source(root: Path) -> Path:
    source_root = _source_root(root)
    files = {
        _WORKFLOW_PATH: b"name: Build release candidate\n",
        "tools/release_identity.py": b"# identity verifier\n",
        "tools/release_manifest.py": b"# manifest verifier\n",
    }
    for relative, content in files.items():
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return source_root


def _create(root: Path) -> tuple[Path, str]:
    source_root = _write_source(root)
    manifest = _TOOL.create_manifest(
        root,
        source_root=source_root,
        repository=_REPOSITORY,
        tag=_TAG,
        tag_ref=_TAG_REF,
        commit=_COMMIT,
        version=_VERSION,
        run_id=_RUN_ID,
        workflow_ref=_WORKFLOW_REF,
        workflow_sha=_COMMIT,
    )
    path = root / "release-manifest.json"
    _TOOL.write_manifest(path, manifest)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def test_manifest_creation_rejects_a_different_repository_identity(
    tmp_path: Path,
) -> None:
    # given: complete candidate inputs paired with a caller-controlled owner
    _write_candidate(tmp_path)
    source_root = _write_source(tmp_path)

    # when/then: provenance cannot be created for another organization
    with pytest.raises(_TOOL.ManifestError, match="repository must be"):
        _TOOL.create_manifest(
            tmp_path,
            source_root=source_root,
            repository="wrong-owner/" + "velocitron-core",
            tag=_TAG,
            tag_ref=_TAG_REF,
            commit=_COMMIT,
            version=_VERSION,
            run_id=_RUN_ID,
            workflow_ref=_WORKFLOW_REF,
            workflow_sha=_COMMIT,
        )


def test_manifest_verification_pins_identity_closed_file_set_and_digests(
    tmp_path: Path,
) -> None:
    # given: an exact release candidate and its approved manifest digest
    _write_candidate(tmp_path)
    manifest_path, manifest_digest = _create(tmp_path)

    # when: the downloaded candidate is verified
    verified = _TOOL.verify_manifest(
        tmp_path,
        manifest_path,
        source_root=_source_root(tmp_path),
        expected_sha256=manifest_digest,
        repository=_REPOSITORY,
        run_id=_RUN_ID,
        workflow_sha=_COMMIT,
    )

    # then: the exact source and tag identity survive verification
    assert verified["tag"] == _TAG
    # and: the manifest binds the candidate to its source commit and workflow run
    assert verified["commit"] == _COMMIT
    assert verified["runId"] == _RUN_ID
    # and: the approved manifest binds exact workflow and verifier bytes
    assert verified["workflow"] == {
        "path": _WORKFLOW_PATH,
        "ref": _WORKFLOW_REF,
        "sha": _COMMIT,
        "sha256": hashlib.sha256(b"name: Build release candidate\n").hexdigest(),
        "size": len(b"name: Build release candidate\n"),
    }
    assert [
        record["path"]
        for record in verified["verificationTools"]  # type: ignore[index]
    ] == ["tools/release_identity.py", "tools/release_manifest.py"]


def test_manifest_verification_rejects_a_changed_package(tmp_path: Path) -> None:
    # given: an approved manifest whose wheel is changed after manifest creation
    _write_candidate(tmp_path)
    manifest_path, manifest_digest = _create(tmp_path)
    wheel = tmp_path / f"python/velocitron-{_VERSION}-py3-none-any.whl"
    wheel.write_bytes(b"changed wheel")

    # when: the downloaded candidate is verified
    # then: publication cannot proceed with bytes outside the approved digest set
    with pytest.raises(_TOOL.ManifestError, match="artifact record differs"):
        _TOOL.verify_manifest(
            tmp_path,
            manifest_path,
            source_root=_source_root(tmp_path),
            expected_sha256=manifest_digest,
            repository=_REPOSITORY,
            run_id=_RUN_ID,
            workflow_sha=_COMMIT,
        )


def test_manifest_verification_rejects_changed_release_check_evidence(
    tmp_path: Path,
) -> None:
    # given: approved release-check evidence changed after manifest creation
    _write_candidate(tmp_path)
    manifest_path, manifest_digest = _create(tmp_path)
    (tmp_path / "artifact-checks.json").write_text(
        '{"checks":["changed"]}\n', encoding="utf-8"
    )

    # when: the downloaded candidate is verified
    # then: publication cannot proceed with evidence outside the approved digest
    with pytest.raises(_TOOL.ManifestError, match="evidence record differs"):
        _TOOL.verify_manifest(
            tmp_path,
            manifest_path,
            source_root=_source_root(tmp_path),
            expected_sha256=manifest_digest,
            repository=_REPOSITORY,
            run_id=_RUN_ID,
            workflow_sha=_COMMIT,
        )


def test_manifest_verification_rejects_an_unlisted_publishable_file(
    tmp_path: Path,
) -> None:
    # given: a valid candidate plus an unlisted wheel in the publish directory
    _write_candidate(tmp_path)
    manifest_path, manifest_digest = _create(tmp_path)
    (tmp_path / "python" / "unlisted.whl").write_bytes(b"unapproved")

    # when: the downloaded candidate is verified
    # then: directory-wide publishers cannot pick up unapproved package files
    with pytest.raises(_TOOL.ManifestError, match="unexpected=.*unlisted.whl"):
        _TOOL.verify_manifest(
            tmp_path,
            manifest_path,
            source_root=_source_root(tmp_path),
            expected_sha256=manifest_digest,
            repository=_REPOSITORY,
            run_id=_RUN_ID,
            workflow_sha=_COMMIT,
        )


def test_manifest_verification_rejects_changed_candidate_verifier(
    tmp_path: Path,
) -> None:
    # given: an approved manifest bound to exact candidate verification tooling
    _write_candidate(tmp_path)
    manifest_path, manifest_digest = _create(tmp_path)
    (_source_root(tmp_path) / "tools" / "release_identity.py").write_bytes(
        b"# mutable default-branch verifier\n"
    )

    # when: verification is attempted with different verifier bytes
    # then: candidate evidence cannot be accepted by the mutable implementation
    with pytest.raises(_TOOL.ManifestError, match="verification tooling differs"):
        _TOOL.verify_manifest(
            tmp_path,
            manifest_path,
            source_root=_source_root(tmp_path),
            expected_sha256=manifest_digest,
            repository=_REPOSITORY,
            run_id=_RUN_ID,
            workflow_sha=_COMMIT,
        )


def test_manifest_creation_rejects_a_tag_version_mismatch(tmp_path: Path) -> None:
    # given: release files for version 0.1.0
    _write_candidate(tmp_path)

    # when: a different tag is supplied for the candidate
    # then: the candidate cannot be labelled with a different release identity
    with pytest.raises(_TOOL.ManifestError, match="does not match version"):
        _TOOL.create_manifest(
            tmp_path,
            source_root=_write_source(tmp_path),
            repository=_REPOSITORY,
            tag="v0.2.0",
            tag_ref=_TAG_REF,
            commit=_COMMIT,
            version=_VERSION,
            run_id=_RUN_ID,
            workflow_ref=_WORKFLOW_REF,
            workflow_sha=_COMMIT,
        )


def test_manifest_creation_rejects_a_prerelease_filename_ambiguity(
    tmp_path: Path,
) -> None:
    # given: release files for the final 0.1.0 version
    _write_candidate(tmp_path)

    # when: a prerelease version would normalize differently across ecosystems
    # then: the 0.1.0 release manifest rejects the ambiguous version
    with pytest.raises(_TOOL.ManifestError, match="invalid release version"):
        _TOOL.create_manifest(
            tmp_path,
            source_root=_write_source(tmp_path),
            repository=_REPOSITORY,
            tag="v0.1.0-rc.1",
            tag_ref=_TAG_REF,
            commit=_COMMIT,
            version="0.1.0-rc.1",
            run_id=_RUN_ID,
            workflow_ref=_WORKFLOW_REF,
            workflow_sha=_COMMIT,
        )
