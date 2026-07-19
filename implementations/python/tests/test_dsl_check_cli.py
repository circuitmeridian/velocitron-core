"""BDD-style contracts for the ``velocitron check`` DSL syntax command.

``check`` validates ``*.petrinet`` files and ``petrinet`` Markdown fences;
it is DSL syntax validation, distinct from the advisory semantic lint in
``velocitron.lint`` (ADR 0016).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from velocitron.dsl import cli

VALID_NET = (
    "net demo\n"
    "\n"
    "(inbox) -task-> [handle] -task-> (done)\n"
    "\n"
    '[handle] handler "handlers.handle"\n'
)

# A consume chain missing its transition endpoint: the ANTLR parser reports
# PN101 at line 3, where the malformed chain lives.
INVALID_NET = "net demo\n\n(inbox) -task->\n"


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_valid_petrinet_file_passes_with_zero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a syntactically valid .petrinet file
    source = tmp_path / "demo.petrinet"
    source.write_text(VALID_NET, encoding="utf-8")

    # when: checking the file
    exit_code, out, err = _run(["check", str(source)], capsys)

    # then: the command exits zero with a clean summary
    assert exit_code == 0
    assert err == ""
    # and: the summary counts the one file and zero errors
    assert "1 petrinet file(s)" in out
    assert "0 error(s)" in out


def test_invalid_petrinet_file_fails_with_file_and_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a .petrinet file truncated after malformed line 3
    source = tmp_path / "broken.petrinet"
    source.write_text(INVALID_NET, encoding="utf-8")

    # when: checking the file
    exit_code, out, err = _run(["check", str(source)], capsys)

    # then: the command exits nonzero
    assert exit_code == 1
    # and: the diagnostic points at zero-width EOF on the following line
    assert f"{source}:4:" in err
    # and: the summary counts the error
    assert "1 error(s)" in out


def test_valid_markdown_fence_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a markdown file with one valid petrinet fence
    document = tmp_path / "library.md"
    document.write_text(
        "# Library\n\n```petrinet\n" + VALID_NET + "```\n", encoding="utf-8"
    )

    # when: checking the file
    exit_code, out, err = _run(["check", str(document)], capsys)

    # then: the command exits zero and counts the fence
    assert exit_code == 0
    assert err == ""
    assert "1 petrinet fence(s)" in out


def test_invalid_markdown_fence_reports_lines_of_the_markdown_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a markdown file whose petrinet fence opens at line 5, so the
    # snippet's EOF sits at markdown line 9
    document = tmp_path / "library.md"
    document.write_text(
        "# Library\n\nSome prose.\n\n```petrinet\n" + INVALID_NET + "```\n",
        encoding="utf-8",
    )

    # when: checking the file
    exit_code, out, err = _run(["check", str(document)], capsys)

    # then: the diagnostic points into the markdown file, not the snippet
    assert exit_code == 1
    assert f"{document}:9:" in err
    # and: the summary counts the fence and the error
    assert "1 petrinet fence(s)" in out
    assert "1 error(s)" in out


def test_no_lint_fence_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: an intentionally-invalid example fence opting out via no-lint
    document = tmp_path / "teaching.md"
    document.write_text(
        "```petrinet no-lint\n" + INVALID_NET + "```\n", encoding="utf-8"
    )

    # when: checking the file
    exit_code, out, err = _run(["check", str(document)], capsys)

    # then: the invalid content produces no error
    assert exit_code == 0
    assert err == ""
    # and: the summary names the skipped fence and checks zero fences
    assert "0 petrinet fence(s)" in out
    assert "1 no-lint fence(s) skipped" in out


def test_non_petrinet_fences_are_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a markdown file whose only fence is a text fence holding
    # content that would fail petrinet compilation
    document = tmp_path / "notes.md"
    document.write_text("```text\n" + INVALID_NET + "```\n", encoding="utf-8")

    # when: checking the file
    exit_code, out, err = _run(["check", str(document)], capsys)

    # then: nothing is checked and nothing fails
    assert exit_code == 0
    assert err == ""
    assert "0 petrinet fence(s)" in out


def test_directory_walk_recurses_and_skips_hidden_and_dependency_dirs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a nested valid .petrinet file and a nested markdown fence
    nested = tmp_path / "docs" / "deep"
    nested.mkdir(parents=True)
    (nested / "demo.petrinet").write_text(VALID_NET, encoding="utf-8")
    (nested / "guide.md").write_text(
        "```petrinet\n" + VALID_NET + "```\n", encoding="utf-8"
    )
    # and: invalid content in directories the walk must skip
    for skipped in (tmp_path / ".hidden", tmp_path / "node_modules"):
        skipped.mkdir()
        (skipped / "broken.petrinet").write_text(INVALID_NET, encoding="utf-8")

    # when: checking the directory
    exit_code, out, err = _run(["check", str(tmp_path)], capsys)

    # then: only the visible content is checked, and it passes
    assert exit_code == 0
    assert err == ""
    assert "1 petrinet file(s)" in out
    assert "1 petrinet fence(s)" in out


def test_no_paths_default_to_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # given: the current directory holds one invalid .petrinet file
    monkeypatch.chdir(tmp_path)
    (tmp_path / "broken.petrinet").write_text(INVALID_NET, encoding="utf-8")

    # when: checking with no path arguments
    exit_code, out, err = _run(["check"], capsys)

    # then: the file is found and its defect reported
    assert exit_code == 1
    assert "broken.petrinet:4:" in err
    assert "1 petrinet file(s)" in out


def test_missing_path_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a path that does not exist
    missing = tmp_path / "nowhere.petrinet"

    # when: checking it
    exit_code, out, err = _run(["check", str(missing)], capsys)

    # then: the command fails and names the missing path
    assert exit_code == 1
    assert str(missing) in err
    assert "1 error(s)" in out


def test_composition_file_resolves_references_relative_to_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a composition .petrinet file wiring two sibling net files
    (tmp_path / "source.petrinet").write_text(
        "net source\n\n(seed) -seed-> [emit] -pulse-> (pulse_out)\n\n"
        '[emit] handler "emit"\n(pulse_out) port output pulse\n',
        encoding="utf-8",
    )
    (tmp_path / "sink.petrinet").write_text(
        "net sink\n\n(pulse_in) -pulse-> [receive] -receipt-> (received)\n\n"
        '[receive] handler "receive"\n(pulse_in) port input pulse\n',
        encoding="utf-8",
    )
    composition = tmp_path / "wired.petrinet"
    composition.write_text(
        "composition wired\n\n"
        'use "source.petrinet" as source\n'
        'use "sink.petrinet" as sink\n'
        "wire source.(pulse_out) -> sink.(pulse_in)\n",
        encoding="utf-8",
    )

    # when: checking only the composition file
    exit_code, out, err = _run(["check", str(composition)], capsys)

    # then: its use references resolve relative to the file and it passes
    assert exit_code == 0
    assert err == ""
    assert "0 error(s)" in out


def test_composition_fence_is_checked_without_resolving_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given: a markdown composition fence whose use references name files
    # that do not exist next to the markdown
    document = tmp_path / "compositions.md"
    document.write_text(
        "```petrinet\n"
        "composition wired\n\n"
        'use "missing_source.petrinet" as source\n'
        'use "missing_sink.petrinet" as sink\n'
        "wire source.(pulse_out) -> sink.(pulse_in)\n"
        "```\n",
        encoding="utf-8",
    )

    # when: checking the markdown file
    exit_code, out, err = _run(["check", str(document)], capsys)

    # then: the fence compiles syntactically without loading the references
    assert exit_code == 0
    assert err == ""
    assert "1 petrinet fence(s)" in out
