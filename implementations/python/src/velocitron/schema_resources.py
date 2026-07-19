"""Load the JSON Schemas shipped inside the :mod:`velocitron` wheel."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Final, Literal, cast

SchemaName = Literal["net.schema.json", "composition.schema.json"]
SchemaDocument = dict[str, Any]

NET_SCHEMA_NAME: Final[SchemaName] = "net.schema.json"
COMPOSITION_SCHEMA_NAME: Final[SchemaName] = "composition.schema.json"
_SCHEMA_PACKAGE: Final = "velocitron.schemas"


def schema_text(name: SchemaName) -> str:
    """Return one packaged schema as UTF-8 text."""
    return files(_SCHEMA_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def _load_schema(name: SchemaName) -> SchemaDocument:
    document = json.loads(schema_text(name))
    if not isinstance(document, dict):
        raise TypeError(f"packaged schema {name!r} must contain a JSON object")
    return cast(SchemaDocument, document)


NET_SCHEMA: Final[SchemaDocument] = _load_schema(NET_SCHEMA_NAME)
COMPOSITION_SCHEMA: Final[SchemaDocument] = _load_schema(COMPOSITION_SCHEMA_NAME)
