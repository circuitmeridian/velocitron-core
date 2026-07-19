from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any, Protocol, cast

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[3]
_TOOL_PATH = _REPOSITORY_ROOT / "tools" / "release_identity.py"
_SPEC = importlib.util.spec_from_file_location("release_identity", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RELEASE_IDENTITY_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RELEASE_IDENTITY_MODULE)


class _ReleaseIdentityTool(Protocol):
    IdentityError: type[ValueError]

    def verify_candidate_run(
        self,
        run: dict[str, Any],
        *,
        repository: str,
        workflow_path: str = ".github/workflows/release-candidate.yml",
    ) -> str: ...

    def verify_local_tag(
        self,
        repository: Path,
        *,
        tag: str,
        tag_ref: str,
        commit: str,
    ) -> None: ...

    def verify_live_tag(
        self,
        ref: dict[str, Any],
        tag_object: dict[str, Any],
        *,
        tag: str,
        tag_ref: str,
        commit: str,
    ) -> None: ...


_TOOL = cast(_ReleaseIdentityTool, cast(object, _RELEASE_IDENTITY_MODULE))
_REPOSITORY = "circuitmeridian/velocitron-core"
_TAG = "v0.1.0"
_TAG_REF = "a" * 40
_COMMIT = "b" * 40


def _run(*, commit: str = _COMMIT) -> dict[str, Any]:
    return {
        "path": ".github/workflows/release-candidate.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_sha": commit,
        "head_repository": {"full_name": _REPOSITORY},
    }


def _tag_ref(*, object_type: str = "tag", object_sha: str = _TAG_REF) -> dict[str, Any]:
    return {
        "ref": f"refs/tags/{_TAG}",
        "object": {"type": object_type, "sha": object_sha},
    }


def _tag_object(*, commit: str = _COMMIT) -> dict[str, Any]:
    return {
        "sha": _TAG_REF,
        "tag": _TAG,
        "object": {"type": "commit", "sha": commit},
    }


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _local_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    _git(tmp_path, "init", "--quiet", "--initial-branch=main", str(repository))
    _git(repository, "config", "user.name", "Release Fixture")
    _git(repository, "config", "user.email", "release@example.test")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", "candidate.txt")
    _git(repository, "commit", "--quiet", "-m", "candidate")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_local_candidate_requires_one_annotated_tag_over_the_commit(
    tmp_path: Path,
) -> None:
    # given: an annotated release tag that directly targets the candidate commit
    repository, commit = _local_repository(tmp_path)
    _git(repository, "tag", "--annotate", _TAG, "--message", "release", commit)
    tag_ref = _git(repository, "rev-parse", f"refs/tags/{_TAG}")

    # when/then: candidate construction accepts the exact annotated object
    _TOOL.verify_local_tag(
        repository,
        tag=_TAG,
        tag_ref=tag_ref,
        commit=commit,
    )


def test_local_candidate_rejects_a_lightweight_tag(tmp_path: Path) -> None:
    # given: a lightweight release tag that names the candidate commit directly
    repository, commit = _local_repository(tmp_path)
    _git(repository, "tag", _TAG, commit)

    # when/then: candidate construction rejects the unannotated ref
    with pytest.raises(_TOOL.IdentityError, match="not annotated"):
        _TOOL.verify_local_tag(
            repository,
            tag=_TAG,
            tag_ref=commit,
            commit=commit,
        )


def test_local_candidate_rejects_a_nested_annotated_tag(tmp_path: Path) -> None:
    # given: the release tag points to another annotated tag over the commit
    repository, commit = _local_repository(tmp_path)
    _git(repository, "tag", "--annotate", "inner", "--message", "inner", commit)
    _git(repository, "tag", "--annotate", _TAG, "--message", "outer", "inner")
    tag_ref = _git(repository, "rev-parse", f"refs/tags/{_TAG}")

    # when/then: construction rejects the shape publishers cannot validate
    with pytest.raises(_TOOL.IdentityError, match="directly target"):
        _TOOL.verify_local_tag(
            repository,
            tag=_TAG,
            tag_ref=tag_ref,
            commit=commit,
        )


def test_candidate_run_binds_the_exact_workflow_revision() -> None:
    # given: a successful candidate workflow run from the release repository
    run = _run()

    # when: the immutable run origin is verified
    commit = _TOOL.verify_candidate_run(run, repository=_REPOSITORY)

    # then: its exact candidate revision is returned for checkout
    assert commit == _COMMIT


def test_candidate_run_rejects_a_different_repository_argument() -> None:
    # given: the approved run is presented under a different organization identity
    run = _run()

    # when/then: caller-controlled input cannot retarget release verification
    with pytest.raises(_TOOL.IdentityError, match="repository must be"):
        _TOOL.verify_candidate_run(
            run, repository="wrong-owner/" + "velocitron-core"
        )


def test_candidate_run_rejects_a_different_workflow_identity() -> None:
    # given: a successful run whose path names a different workflow
    run = {**_run(), "path": ".github/workflows/ci.yml"}

    # when/then: its head SHA cannot select release verification tooling
    with pytest.raises(_TOOL.IdentityError, match="path differs"):
        _TOOL.verify_candidate_run(run, repository=_REPOSITORY)


def test_live_tag_requires_the_approved_annotated_object_and_commit() -> None:
    # given: the live ref and annotated object retain the approved identity
    ref = _tag_ref()
    tag_object = _tag_object()

    # when/then: the release identity is accepted
    _TOOL.verify_live_tag(
        ref,
        tag_object,
        tag=_TAG,
        tag_ref=_TAG_REF,
        commit=_COMMIT,
    )


def test_live_tag_rejects_a_lightweight_ref() -> None:
    # given: the release tag was replaced by a lightweight commit ref
    ref = _tag_ref(object_type="commit", object_sha=_COMMIT)

    # when/then: the unannotated tag cannot authorize a release mutation
    with pytest.raises(_TOOL.IdentityError, match="not annotated"):
        _TOOL.verify_live_tag(
            ref,
            _tag_object(),
            tag=_TAG,
            tag_ref=_TAG_REF,
            commit=_COMMIT,
        )


def test_post_approval_revalidation_rejects_a_moved_tag() -> None:
    # given: the approved annotated tag passed the verify job
    _TOOL.verify_live_tag(
        _tag_ref(),
        _tag_object(),
        tag=_TAG,
        tag_ref=_TAG_REF,
        commit=_COMMIT,
    )
    # and: the live ref moved while the publish job waited for approval
    moved_ref = _tag_ref(object_sha="c" * 40)

    # when/then: the publish-time read refuses the changed world state
    with pytest.raises(_TOOL.IdentityError, match="object differs"):
        _TOOL.verify_live_tag(
            moved_ref,
            _tag_object(),
            tag=_TAG,
            tag_ref=_TAG_REF,
            commit=_COMMIT,
        )
