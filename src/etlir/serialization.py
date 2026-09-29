"""Deterministic serialization and schema export.

All ETLIR artifacts are written with sorted keys, fixed indentation, UTF-8, LF line
endings and a trailing newline, so identical inputs yield byte-identical outputs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from etlir.canonical.model import IR_VERSION, CanonicalDocument


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_schema() -> dict[str, Any]:
    schema = CanonicalDocument.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://github.com/shiva-carimireddy/etlir/schemas/canonical/"
        f"canonical-ir-{IR_VERSION}.schema.json"
    )
    return schema


def canonical_schema_filename() -> str:
    return f"canonical-ir-{IR_VERSION}.schema.json"
