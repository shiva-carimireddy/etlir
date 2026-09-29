"""Spark and DuckDB emitters: conformance, fail-closed planning, generated code hygiene."""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

from etlir.canonical.model import CanonicalDocument
from etlir.contracts import TargetEmitter
from etlir.serialization import dumps
from etlir.targets.duckdb import DuckDBEmitter
from etlir.targets.spark import SparkEmitter
from etlir.testing import check_target_emitter

EMITTERS = [SparkEmitter, DuckDBEmitter]


@pytest.mark.parametrize("cls", EMITTERS, ids=lambda c: c.id)
def test_emitter_conforms(
    cls: type[TargetEmitter], orders_doc: CanonicalDocument, tmp_path: Path
) -> None:
    report = check_target_emitter(cls(), orders_doc, tmp_path)
    assert report.ok, report.failures


@pytest.mark.parametrize("cls", EMITTERS, ids=lambda c: c.id)
def test_all_orders_jobs_emitted_and_compile(
    cls: type[TargetEmitter], orders_doc: CanonicalDocument, tmp_path: Path
) -> None:
    emitter = cls()
    plan = emitter.plan(orders_doc)
    assert [j["status"] for j in plan["jobs"]] == ["emitted"] * 3
    result = emitter.write(plan, tmp_path)
    for rel in result.artifacts:
        if rel.endswith(".py"):
            py_compile.compile(str(tmp_path / rel), doraise=True)
    code = "\n".join(
        (tmp_path / rel).read_text("utf-8")
        for rel in result.artifacts
        if rel.startswith(("jobs/", "sql/"))
    )
    # Expressions are lowered from the typed AST, never pasted from the source.
    for source_text in ("IIF(", "ISNULL(", "LTRIM(RTRIM", "$$HIGH_VALUE_THRESHOLD"):
        assert source_text not in code
    ops = [o.id for df in orders_doc.dataflows for o in df.operations]
    traced = {r.subject_id for r in result.evidence if r.targets and r.targets[0].locator}
    assert set(ops) <= traced


@pytest.mark.parametrize("cls", EMITTERS, ids=lambda c: c.id)
def test_blocked_paths_get_no_job(cls: type[TargetEmitter], mixed_doc: CanonicalDocument) -> None:
    plan = cls().plan(mixed_doc)
    status = {j["dataflow_id"].split(":")[-1]: j["status"] for j in plan["jobs"]}
    assert status == {
        "s_m_CUSTOMER_COPY": "emitted",
        "s_m_CUSTOMER_OVERRIDE": "blocked",
        "s_m_CUSTOMER_REGION": "blocked",
    }
    [pipeline] = plan["workflow"]["pipelines"]
    assert pipeline["status"] == "partially-blocked"
    tasks = {t["name"]: t for t in pipeline["tasks"]}
    assert tasks["s_m_CUSTOMER_COPY"]["status"] == "runnable"
    for name in ("s_m_CUSTOMER_REGION", "s_m_CUSTOMER_OVERRIDE", "cmd_ARCHIVE"):
        assert tasks[name]["status"] == "blocked" and tasks[name]["command"] is None
        assert tasks[name]["reasons"]
    assert any("task.command" in r for r in tasks["cmd_ARCHIVE"]["reasons"])


def test_emitter_choice_does_not_change_canonical_ir(
    orders_doc: CanonicalDocument, tmp_path: Path
) -> None:
    before = dumps(orders_doc)
    for cls in EMITTERS:
        emitter = cls()
        emitter.write(emitter.plan(orders_doc), tmp_path / cls.id)
    assert dumps(orders_doc) == before


def test_capability_manifests_declare_the_same_semantics() -> None:
    spark = {r.construct_id for r in SparkEmitter().capabilities().rules}
    duck = {r.construct_id for r in DuckDBEmitter().capabilities().rules}
    assert spark == duck
    assert "expression.opaque" not in spark and "operation.unsupported" not in spark
