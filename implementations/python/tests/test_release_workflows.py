from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[3]
_WORKFLOWS = _REPOSITORY_ROOT / ".github" / "workflows"
_PUBLISH_WORKFLOWS = ("publish-pypi.yml", "publish-npm.yml")


def _workflow(name: str) -> str:
    return (_WORKFLOWS / name).read_text(encoding="utf-8")


def _step(workflow: str, name: str) -> str:
    start = workflow.index(f"      - name: {name}\n")
    end = workflow.find("\n      - name:", start + 1)
    return workflow[start:] if end == -1 else workflow[start:end]


def _step_names(job: str) -> list[str]:
    return re.findall(r"^      - name: (.+)$", job, flags=re.MULTILINE)


@pytest.mark.parametrize(
    "workflow_name", ("release-candidate.yml", *_PUBLISH_WORKFLOWS)
)
def test_workflow_rejects_the_wrong_repository_identity(
    workflow_name: str,
) -> None:
    # given: every live release workflow
    workflow = _workflow(workflow_name)

    # when/then: it pins and checks the Circuit Meridian repository before use
    assert "EXPECTED_REPOSITORY: circuitmeridian/velocitron-core" in workflow
    assert 'test "$GITHUB_REPOSITORY" = "$EXPECTED_REPOSITORY"' in workflow


def test_candidate_requires_an_annotated_tag_without_claiming_provider_protection() -> (
    None
):
    # given: the local release-candidate workflow
    workflow = _workflow("release-candidate.yml")
    identity = _step(workflow, "Bind tag, source, and package versions")

    # when: its tag gate and operator-facing dispatch contract are inspected
    # then: candidate construction invokes the fixture-tested annotated-tag gate
    assert "tools/release_identity.py verify-local-tag" in identity
    # and: local workflow prose does not claim unobserved provider-side protection
    assert "Existing annotated release tag" in workflow
    assert "protected release tag" not in workflow


@pytest.mark.parametrize("workflow_name", _PUBLISH_WORKFLOWS)
def test_publish_verification_checks_out_the_candidate_bound_revision(
    workflow_name: str,
) -> None:
    # given: a delayed publisher that accepts an earlier candidate run
    workflow = _workflow(workflow_name)

    # when: the verification-job steps are inspected
    origin = workflow.index("      - name: Bind candidate workflow revision\n")
    checkout = workflow.index(
        "      - name: Check out candidate-bound verification tooling\n"
    )
    checkout_step = _step(workflow, "Check out candidate-bound verification tooling")

    # then: the run origin is bound before any candidate verifier is loaded
    assert origin < checkout
    # and: checkout cannot silently follow the mutable default branch
    assert "ref: ${{ steps.origin.outputs.commit }}" in checkout_step
    assert "sparse-checkout:" in checkout_step
    assert "tools/release_manifest.py" in checkout_step
    assert "tools/release_identity.py" in checkout_step


@pytest.mark.parametrize(
    ("workflow_name", "mutation_step"),
    (
        ("publish-pypi.yml", "Publish through PyPI trusted publishing"),
        ("publish-npm.yml", "Publish through npm trusted publishing"),
    ),
)
def test_publish_revalidates_the_live_annotated_tag_immediately_before_mutation(
    workflow_name: str,
    mutation_step: str,
) -> None:
    # given: a publish job delayed behind a human environment approval
    workflow = _workflow(workflow_name)
    publish_job = workflow.split("\n  publish:\n", maxsplit=1)[1]

    # when: the ordered publish steps are inspected
    names = _step_names(publish_job)
    revalidate_index = names.index("Revalidate live annotated tag after approval")
    mutation_index = names.index(mutation_step)
    revalidate = _step(workflow, "Revalidate live annotated tag after approval")

    # then: no mutable step separates the final world-state read from publication
    assert mutation_index == revalidate_index + 1
    # and: the candidate-bound verifier checks the live ref and annotated object
    assert "tools/release_identity.py verify-live-tag" in revalidate
    assert '"$RELEASE_TAG"' in revalidate
    assert '"$RELEASE_TAG_REF"' in revalidate
    assert '"$RELEASE_COMMIT"' in revalidate
