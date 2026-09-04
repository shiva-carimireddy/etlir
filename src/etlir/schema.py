"""Load and apply the normative ETLIR JSON Schemas."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from etlir.types import JsonObject

SCHEMA_NAMES = (
    "pipeline-model.schema.json",
    "diagnostic.schema.json",
    "evidence-bundle.schema.json",
)


def load_spec_schema(name: str) -> JsonObject:
    """Load a normative schema from a wheel or a source checkout."""

    if name not in SCHEMA_NAMES:
        raise ValueError(f"Unknown ETLIR schema: {name}")

    packaged = resources.files("etlir").joinpath("spec", "v0.1", "schemas", name)
    try:
        return _parse_schema(packaged.read_text(encoding="utf-8"), name)
    except FileNotFoundError:
        source_path = Path(__file__).resolve().parents[2] / "spec" / "v0.1" / "schemas" / name
        return _parse_schema(source_path.read_text(encoding="utf-8"), name)


def load_json_schema(path: Path) -> JsonObject:
    """Load an adapter-owned JSON Schema."""

    return _parse_schema(path.read_text(encoding="utf-8"), str(path))


def iter_validation_errors(
    instance: Any,
    schema: JsonObject,
    *,
    include_spec_registry: bool = False,
) -> list[ValidationError]:
    """Return deterministically ordered Draft 2020-12 validation errors."""

    Draft202012Validator.check_schema(schema)
    registry: Registry[Any] = Registry()
    if include_spec_registry:
        for name in SCHEMA_NAMES:
            registered_schema = load_spec_schema(name)
            schema_id = str(registered_schema["$id"])
            registry = registry.with_resource(
                schema_id,
                Resource.from_contents(registered_schema),
            )

    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    return sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))


def json_pointer(parts: list[str | int]) -> str:
    """Convert a JSON path into an RFC 6901 pointer."""

    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _parse_schema(text: str, source: str) -> JsonObject:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"JSON Schema must be an object: {source}")
    return value

