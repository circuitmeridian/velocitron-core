"""Contract tests for canonical and packaged JSON Schema resources."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from velocitron.schema_resources import (
    COMPOSITION_SCHEMA,
    COMPOSITION_SCHEMA_NAME,
    NET_SCHEMA,
    NET_SCHEMA_NAME,
    SchemaDocument,
    SchemaName,
    schema_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_CASES: tuple[tuple[SchemaName, Path, SchemaDocument], ...] = (
    (NET_SCHEMA_NAME, _REPO_ROOT / "spec" / "net.schema.json", NET_SCHEMA),
    (
        COMPOSITION_SCHEMA_NAME,
        _REPO_ROOT / "spec" / "composition.schema.json",
        COMPOSITION_SCHEMA,
    ),
)


@pytest.mark.parametrize(("name", "canonical_path", "loaded_schema"), _SCHEMA_CASES)
def test_given_a_canonical_schema_then_it_is_valid_draft_2020_12(
    name: SchemaName, canonical_path: Path, loaded_schema: dict[str, Any]
) -> None:
    # given: the standalone canonical JSON document
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    # when/then: it is a valid schema and is the document loaded by the package
    Draft202012Validator.check_schema(canonical)
    assert canonical == loaded_schema, name


@pytest.mark.parametrize(("name", "canonical_path", "loaded_schema"), _SCHEMA_CASES)
def test_given_a_packaged_copy_then_its_bytes_cannot_drift_from_canonical(
    name: SchemaName, canonical_path: Path, loaded_schema: dict[str, Any]
) -> None:
    del loaded_schema
    # given: canonical source bytes and the resource bytes shipped by velocitron
    canonical_bytes = canonical_path.read_bytes()
    packaged_bytes = schema_text(name).encode("utf-8")
    # when/then: the package copy is an exact sync, not a second schema definition
    assert packaged_bytes == canonical_bytes


@pytest.mark.parametrize(("name", "canonical_path", "loaded_schema"), _SCHEMA_CASES)
def test_given_only_package_resources_then_each_schema_is_available(
    name: SchemaName, canonical_path: Path, loaded_schema: dict[str, Any]
) -> None:
    del canonical_path
    # given: the importlib resource package used by an installed wheel
    resource = files("velocitron.schemas").joinpath(name)
    # when/then: the schema is present and readable without a repository-relative path
    assert resource.is_file()
    assert json.loads(resource.read_text(encoding="utf-8")) == loaded_schema
