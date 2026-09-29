"""Architecture boundary tests.

* The core (everything outside etlir.sources / etlir.targets) imports no plugin.
* A source adapter imports no target emitter and no other source adapter.
* A target emitter imports no source adapter.
* The Canonical IR schema contains no source- or target-product vocabulary.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from etlir.serialization import canonical_schema

PKG = Path(__file__).resolve().parents[1] / "src" / "etlir"

# Extend when a new in-tree adapter or emitter introduces product vocabulary.
FORBIDDEN_CANONICAL_TERMS = [
    "informatica",
    "powercenter",
    "powermart",
    "mapplet",
    "sessextn",
    "workflow_manager",
    "spark",
    "pyspark",
    "dataframe",
    "airflow",
    "dag",
    "databricks",
    "duckdb",
    "snowflake",
    "ssis",
    "datastage",
    "talend",
    "kettle",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _module(path: Path) -> str:
    return "etlir." + ".".join(path.relative_to(PKG).with_suffix("").parts)


def _plugin_of(module: str) -> tuple[str, str] | None:
    parts = module.split(".")
    if len(parts) >= 3 and parts[1] in ("sources", "targets"):
        return parts[1], parts[2]
    return None


FILES = sorted(PKG.rglob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(PKG).as_posix())
def test_import_boundaries(path: Path) -> None:
    me = _plugin_of(_module(path))
    for imported in _imports(path):
        other = _plugin_of(imported)
        if other is None:
            continue
        assert me is not None, f"core module imports plugin {imported}"
        assert me[0] == other[0], f"{me[0]} plugin imports {other[0]} plugin {imported}"
        assert me == other, f"plugin {me[1]} imports sibling plugin {imported}"


@pytest.mark.parametrize("path", sorted(PKG.rglob("etlir_*_runtime.py")), ids=lambda p: p.name)
def test_generated_runtime_depends_on_no_etlir_module(path: Path) -> None:
    """Runtime helpers ship inside generated packages, which must run without ETLIR."""
    assert not [m for m in _imports(path) if m == "etlir" or m.startswith("etlir.")]


def test_canonical_schema_is_vendor_neutral() -> None:
    text = json.dumps(canonical_schema()).lower()
    hits = [t for t in FORBIDDEN_CANONICAL_TERMS if t in text]
    assert hits == []
