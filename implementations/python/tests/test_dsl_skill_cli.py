"""BDD-style contracts for the ``velocitron skill`` command.

``velocitron skill <pathname>`` installs the bundled ``velocitron`` skill under
``<pathname>/velocitron/`` (or true-syncs it in place when one is already
there), reading the skill exclusively from package data via
:mod:`importlib.resources`. Update is manifest-driven: shipped files are
overwritten, previously-managed files the skill no longer ships are deleted, and
user-added files are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from velocitron.dsl import cli


def _install(target: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Install into ``target`` and return the captured stdout."""
    assert cli.main(["skill", str(target)]) == 0
    return capsys.readouterr().out


def test_skill_source_is_the_packaged_resource_not_the_repo_dir() -> None:
    """Given an install, when resolving the skill source, then it is the package resource."""
    # when: the CLI resolves the bundled skill source
    resolved = cli._skill_source_dir()  # pyright: ignore[reportPrivateUsage]

    # then: it is the package data path (velocitron/skill), not the repo's
    # skills/velocitron (whose parent is "skills") — proving the single
    # importlib.resources code path, not a repo walk-up
    assert resolved.name == "skill"
    assert resolved.parent.name == "velocitron"
    # and: the managed marker file is present
    assert (resolved / "SKILL.md").is_file()


def test_skill_install_populates_a_fresh_target_with_a_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given an empty target, when installing, then the bundle and a manifest land under velocitron/."""
    # given: an empty target directory with no prior skill
    assert not (tmp_path / "velocitron" / "SKILL.md").exists()

    # when: the skill is installed there
    out = _install(tmp_path, capsys)

    # then: it reports a fresh install
    assert "installed velocitron skill" in out

    # and: managed files are copied, including the scripts subdir
    dest = tmp_path / "velocitron"
    assert (dest / "SKILL.md").is_file()
    assert (dest / "kitchen-sink.petrinet").is_file()
    assert (dest / "scripts" / "cli-help.sh").is_file()

    # and: a manifest lists the managed files but never itself
    manifest = json.loads(
        (dest / ".velocitron-skill-manifest.json").read_text(encoding="utf-8")
    )
    assert "SKILL.md" in manifest["files"]
    assert "scripts/cli-help.sh" in manifest["files"]
    assert ".velocitron-skill-manifest.json" not in manifest["files"]


def test_skill_update_reports_update_and_refreshes_managed_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a prior install whose file drifted, when re-run, then it is refreshed and reported as an update."""
    # given: a prior install whose SKILL.md has been corrupted
    _install(tmp_path, capsys)
    dest = tmp_path / "velocitron"
    (dest / "SKILL.md").write_text("stale content", encoding="utf-8")

    # when: the skill is installed again over it
    out = _install(tmp_path, capsys)

    # then: it reports an update (not a fresh install)
    assert "updated velocitron skill" in out

    # and: the drifted SKILL.md is refreshed to the source's content
    source = cli._skill_source_dir()  # pyright: ignore[reportPrivateUsage]
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == (
        source / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_skill_update_removes_a_previously_managed_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a prior manifest naming a now-unshipped file, when re-run, then that file is deleted."""
    # given: an install, plus a file recorded as managed by a prior skill version
    _install(tmp_path, capsys)
    dest = tmp_path / "velocitron"
    stale = dest / "legacy-guide.md"
    stale.write_text("from an older skill version", encoding="utf-8")
    manifest_path = dest / ".velocitron-skill-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append("legacy-guide.md")
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    # when: the skill is installed again (the current skill does not ship it)
    _install(tmp_path, capsys)

    # then: the previously-managed file is removed by the true sync
    assert not stale.exists()


def test_skill_update_preserves_a_user_added_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a user-added file (in no manifest), when updating, then it survives."""
    # given: an install plus a file the user dropped in (never managed)
    _install(tmp_path, capsys)
    dest = tmp_path / "velocitron"
    user_file = dest / "my-notes.md"
    user_file.write_text("my own notes", encoding="utf-8")

    # when: the skill is installed again
    _install(tmp_path, capsys)

    # then: the user's file is left untouched
    assert user_file.read_text(encoding="utf-8") == "my own notes"


def test_skill_install_rejects_a_nonexistent_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a target path that does not exist, when installing, then it errors and writes nothing."""
    # given: a path whose parent directory does not exist
    missing = tmp_path / "nope"

    # when: an install is attempted there
    code = cli.main(["skill", str(missing)])

    # then: the command fails with a diagnostic and creates no skill dir
    assert code == 1
    assert "error" in capsys.readouterr().err
    assert not (missing / "velocitron").exists()
