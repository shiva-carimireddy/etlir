"""Reference runner semantics and comparator policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from etlir.canonical.model import Column, DataType, TypeKind
from etlir.compare import compare_dataset, normalize
from etlir.runner import run_package


def _plan(tmp_path: Path, tasks: list[dict[str, Any]], exits: dict[str, int]) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "jobs").mkdir(parents=True)
    for name, code in exits.items():
        (pkg / "jobs" / f"{name}.py").write_text(f"import sys\nsys.exit({code})\n", "utf-8")
    for t in tasks:
        t.setdefault("kind", "dataflow")
        t.setdefault("reasons", [])
        t.setdefault("trigger", "all")
        t.setdefault("status", "runnable")
        t["command"] = (
            None
            if t["status"] == "blocked"
            else {"kind": "python", "script": f"jobs/{t['name']}.py"}
        )
    plan = {
        "plan_version": 1,
        "target": "test",
        "standalone_jobs": [],
        "pipelines": [{"id": "p", "name": "p", "status": "runnable", "tasks": tasks}],
    }
    (pkg / "workflow_plan.json").write_text(json.dumps(plan), "utf-8")
    (tmp_path / "bindings.json").write_text("{}", "utf-8")
    return pkg


def _dep(task: str, cond: str) -> dict[str, str]:
    return {"task_id": task, "condition": cond}


def _run(tmp_path: Path, pkg: Path, **kw: Any) -> dict[str, str]:
    ex = run_package(pkg, tmp_path / "bindings.json", tmp_path / "run", **kw)
    return {t["id"]: t["status"] for t in ex["pipelines"][0]["tasks"]} | {"_": ex["status"]}


def test_dependency_conditions(tmp_path: Path) -> None:
    pkg = _plan(
        tmp_path,
        [
            {"id": "a", "name": "a", "depends_on": []},
            {"id": "on_ok", "name": "on_ok", "depends_on": [_dep("a", "success")]},
            {"id": "on_fail", "name": "on_fail", "depends_on": [_dep("a", "failure")]},
            {"id": "always", "name": "always", "depends_on": [_dep("a", "completion")]},
            {"id": "after", "name": "after", "depends_on": [_dep("on_ok", "success")]},
        ],
        {"a": 1, "on_ok": 0, "on_fail": 0, "always": 0, "after": 0},
    )
    assert _run(tmp_path, pkg) == {
        "a": "failed",
        "on_ok": "not_triggered",
        "on_fail": "succeeded",
        "always": "succeeded",
        "after": "not_triggered",
        "_": "failed",
    }


def test_blocked_pipeline_is_refused_unless_partial(tmp_path: Path) -> None:
    pkg = _plan(
        tmp_path,
        [
            {"id": "ok", "name": "ok", "depends_on": []},
            {"id": "bad", "name": "bad", "depends_on": [], "status": "blocked"},
        ],
        {"ok": 0},
    )
    assert _run(tmp_path, pkg) == {"ok": "not_run", "bad": "blocked", "_": "failed"}
    assert _run(tmp_path, pkg, allow_partial=True) == {
        "ok": "succeeded",
        "bad": "blocked",
        "_": "partial",
    }


DEC = Column(name="v", type=DataType(kind=TypeKind.DECIMAL, precision=12, scale=2))
TS = Column(name="t", type=DataType(kind=TypeKind.TIMESTAMP))


def test_normalization_policy() -> None:
    assert normalize("1.50", DEC.type) == normalize(1.5, DEC.type)
    assert normalize(None, DEC.type) is None
    assert normalize("2026-01-05T10:00:00+02:00", TS.type) == normalize(
        "2026-01-05 08:00:00", TS.type
    )


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    return path


def test_multiset_comparison(tmp_path: Path) -> None:
    exp = _jsonl(tmp_path / "e.jsonl", [{"v": "1.00"}, {"v": "1.00"}, {"v": None}])
    same = _jsonl(tmp_path / "a.jsonl", [{"v": None}, {"v": 1}, {"v": "1.0"}])
    fewer = _jsonl(tmp_path / "b.jsonl", [{"v": "1.00"}, {"v": None}])
    too_precise = _jsonl(tmp_path / "c.jsonl", [{"v": "1.001"}, {"v": "1"}, {"v": None}])
    assert compare_dataset(same, exp, [DEC])["status"] == "match"
    result = compare_dataset(fewer, exp, [DEC])
    assert result["status"] == "mismatch" and result["missing"][0]["count"] == 1
    assert compare_dataset(too_precise, exp, [DEC])["status"] == "unreadable"
    assert compare_dataset(tmp_path / "nope", exp, [DEC])["status"] == "missing_output"
