#!/usr/bin/env python3
"""Validate immutable GitHub workflow and annotated-tag release identity."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

_HEX_40 = re.compile(r"[0-9a-f]{40}")
_WORKFLOW_PATH = ".github/workflows/release-candidate.yml"
_EXPECTED_REPOSITORY = "circuitmeridian/velocitron-core"


class IdentityError(ValueError):
    """A workflow run or live Git tag conflicts with the approved candidate."""


def _load_json(path: Path) -> dict[str, Any]:
    document: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise IdentityError(f"GitHub response must be an object: {path}")
    return cast(dict[str, Any], document)


def verify_candidate_run(
    run: dict[str, Any], *, repository: str, workflow_path: str = _WORKFLOW_PATH
) -> str:
    """Return the candidate commit after binding a completed workflow run."""
    if repository != _EXPECTED_REPOSITORY:
        raise IdentityError(
            f"repository must be {_EXPECTED_REPOSITORY!r}, got {repository!r}"
        )
    expected = {
        "path": workflow_path,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
    }
    for field, value in expected.items():
        if run.get(field) != value:
            raise IdentityError(
                f"candidate workflow run {field} differs: {run.get(field)!r}"
            )

    head_repository_raw = run.get("head_repository")
    if not isinstance(head_repository_raw, dict):
        raise IdentityError("candidate workflow run repository differs")
    head_repository = cast(dict[str, Any], head_repository_raw)
    if head_repository.get("full_name") != repository:
        raise IdentityError("candidate workflow run repository differs")
    commit = run.get("head_sha")
    if not isinstance(commit, str) or not _HEX_40.fullmatch(commit):
        raise IdentityError("candidate workflow run commit is not a Git object ID")
    return commit


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise IdentityError(
            f"cannot inspect local release tag: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def verify_local_tag(repository: Path, *, tag: str, tag_ref: str, commit: str) -> None:
    """Require a local release ref to be one annotated tag over one commit."""
    if not _HEX_40.fullmatch(tag_ref) or not _HEX_40.fullmatch(commit):
        raise IdentityError("local release identity is not a Git object ID")
    if _git(repository, "rev-parse", f"refs/tags/{tag}") != tag_ref:
        raise IdentityError("local release tag ref differs")
    if _git(repository, "cat-file", "-t", tag_ref) != "tag":
        raise IdentityError("local release tag is not annotated")

    headers: dict[str, str] = {}
    for line in _git(repository, "cat-file", "-p", tag_ref).splitlines():
        if not line:
            break
        field, separator, value = line.partition(" ")
        if separator:
            headers[field] = value
    if headers.get("type") != "commit" or headers.get("object") != commit:
        raise IdentityError("local annotated tag does not directly target the commit")
    if headers.get("tag") != tag:
        raise IdentityError("local annotated-tag name differs")


def verify_live_tag(
    ref: dict[str, Any],
    tag_object: dict[str, Any],
    *,
    tag: str,
    tag_ref: str,
    commit: str,
) -> None:
    """Require the live ref to retain the approved annotated-tag identity."""
    if ref.get("ref") != f"refs/tags/{tag}":
        raise IdentityError("live tag name differs")
    ref_object_raw = ref.get("object")
    if not isinstance(ref_object_raw, dict):
        raise IdentityError("live release tag is not annotated")
    ref_object = cast(dict[str, Any], ref_object_raw)
    if ref_object.get("type") != "tag":
        raise IdentityError("live release tag is not annotated")
    if ref_object.get("sha") != tag_ref:
        raise IdentityError("live annotated-tag object differs")

    if tag_object.get("sha") != tag_ref or tag_object.get("tag") != tag:
        raise IdentityError("live annotated-tag identity differs")
    target_raw = tag_object.get("object")
    if not isinstance(target_raw, dict):
        raise IdentityError("live annotated tag does not target a commit")
    target = cast(dict[str, Any], target_raw)
    if target.get("type") != "commit":
        raise IdentityError("live annotated tag does not target a commit")
    if target.get("sha") != commit:
        raise IdentityError("live annotated-tag commit differs")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("verify-run", help="bind a candidate workflow run")
    run.add_argument("--run-json", type=Path, required=True)
    run.add_argument("--repository", required=True)
    run.add_argument("--workflow-path", default=_WORKFLOW_PATH)

    local_tag = commands.add_parser(
        "verify-local-tag", help="verify a local annotated tag"
    )
    local_tag.add_argument("--repository", type=Path, required=True)
    local_tag.add_argument("--tag", required=True)
    local_tag.add_argument("--tag-ref", required=True)
    local_tag.add_argument("--commit", required=True)

    tag = commands.add_parser("verify-live-tag", help="verify a live annotated tag")
    tag.add_argument("--ref-json", type=Path, required=True)
    tag.add_argument("--tag-json", type=Path, required=True)
    tag.add_argument("--tag", required=True)
    tag.add_argument("--tag-ref", required=True)
    tag.add_argument("--commit", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "verify-run":
            commit = verify_candidate_run(
                _load_json(args.run_json),
                repository=args.repository,
                workflow_path=args.workflow_path,
            )
            print(json.dumps({"commit": commit}, sort_keys=True))
            return 0

        if args.command == "verify-local-tag":
            verify_local_tag(
                args.repository,
                tag=args.tag,
                tag_ref=args.tag_ref,
                commit=args.commit,
            )
            print(
                json.dumps(
                    {"commit": args.commit, "tag": args.tag, "tagRef": args.tag_ref},
                    sort_keys=True,
                )
            )
            return 0

        verify_live_tag(
            _load_json(args.ref_json),
            _load_json(args.tag_json),
            tag=args.tag,
            tag_ref=args.tag_ref,
            commit=args.commit,
        )
        print(
            json.dumps(
                {"commit": args.commit, "tag": args.tag, "tagRef": args.tag_ref},
                sort_keys=True,
            )
        )
        return 0
    except (IdentityError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
