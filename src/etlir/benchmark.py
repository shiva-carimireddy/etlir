"""Benchmark runner over case manifests (``benchmarks/cases/<id>/case.toml``).

For each case: convert (twice, to check byte-for-byte determinism), check the declared
expectations (blocked tasks, diagnostic codes), execute each target's package with the
reference runner, compare outputs with independently authored expectations, and, for one
fast target, run semantic mutation operators on the Canonical IR to confirm that the
comparator detects wrong semantics. Every number in ``results.json`` comes with its
numerator and denominator.
"""

from __future__ import annotations

import filecmp
import json
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from etlir import __version__
from etlir.canonical.model import (
    CallNode,
    CanonicalDocument,
    CastNode,
    Dataflow,
    Expression,
    ExpressionNode,
    Operation,
)
from etlir.compare import compare_outputs
from etlir.pipeline import convert
from etlir.registry import Registry
from etlir.runner import run_package
from etlir.serialization import write_json

EXECUTABLE = ("locally-executable", "behaviorally-comparable")


def load_case(case_dir: Path) -> dict[str, Any]:
    data = tomllib.loads((case_dir / "case.toml").read_text("utf-8"))
    data["dir"] = case_dir
    return data


def _same_tree(a: Path, b: Path) -> bool:
    fa = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    fb = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    return fa == fb and all(filecmp.cmp(a / f, b / f, shallow=False) for f in fa)


def _same_code(a: Path, b: Path) -> bool:
    def code(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and p.relative_to(root).parts[0] in ("jobs", "sql")
        }

    return code(a) == code(b)


def _bindings(case: dict[str, Any], package: Path, run_dir: Path) -> Path:
    example = json.loads((package / "bindings.example.json").read_text("utf-8"))
    declared = case.get("bindings", {})
    out: dict[str, Any] = {}
    for bid, spec in example.items():
        if bid in declared:
            b = dict(declared[bid])
            b["path"] = str((case["dir"] / b["path"]).resolve())
            out[bid] = b
        elif spec["format"] == "jsonl":
            out[bid] = {
                "format": "jsonl",
                "path": str(run_dir / "outputs" / spec["dataset"].split(":")[-1]),
            }
    path = run_dir / "bindings.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(path, out)
    return path


def _params(case: dict[str, Any], run_dir: Path) -> Path | None:
    values = case.get("params")
    if not values:
        return None
    path = run_dir / "params.json"
    write_json(path, values)
    return path


def _task_names(package: Path, status: str) -> list[str]:
    plan = json.loads((package / "workflow_plan.json").read_text("utf-8"))
    return sorted(t["name"] for p in plan["pipelines"] for t in p["tasks"] if t["status"] == status)


def run_case(
    case: dict[str, Any],
    out: Path,
    targets: list[str],
    launcher: str,
    spark_writer: str | None,
    mutation_target: str | None,
    registry: Registry,
) -> list[dict[str, Any]]:
    cid = case["id"]
    inputs = [case["dir"] / p for p in case["inputs"]]
    base = out / cid
    if base.exists():
        shutil.rmtree(base)
    summary = convert(
        inputs, base / "convert", source=case.get("source"), targets=targets, registry=registry
    )
    convert(
        inputs,
        base / "convert-repeat",
        source=case.get("source"),
        targets=targets,
        registry=registry,
    )
    deterministic = _same_tree(base / "convert", base / "convert-repeat")
    shutil.rmtree(base / "convert-repeat")
    validation = json.loads((base / "convert" / "validation.json").read_text("utf-8"))
    codes = sorted(validation["counts"])
    expect = case.get("expect", {})
    doc = CanonicalDocument.model_validate_json(
        (base / "convert" / "canonical_ir.json").read_text("utf-8")
    )

    rows = []
    for tid in targets:
        package = base / "convert" / "targets" / tid
        blocked = _task_names(package, "blocked")
        row: dict[str, Any] = {
            "case": cid,
            "target": tid,
            "partition": case.get("partition"),
            "provenance": case.get("provenance"),
            "deterministic_conversion": deterministic,
            "summary": summary["targets"][tid],
            "checks": {
                "blocked_tasks": {
                    "expected": sorted(expect.get("blocked_tasks", [])),
                    "actual": blocked,
                    "ok": blocked == sorted(expect.get("blocked_tasks", [])),
                },
                "diagnostic_codes": {
                    "expected": sorted(expect.get("diagnostic_codes", [])),
                    "actual": codes,
                    "ok": codes == sorted(expect.get("diagnostic_codes", [])),
                },
            },
        }
        if case.get("partition") in EXECUTABLE:
            run_dir = base / "runs" / tid
            bindings = _bindings(case, package, run_dir)
            params = _params(case, run_dir)
            execution = run_package(
                package,
                bindings,
                run_dir,
                params,
                launcher=launcher,
                spark_writer=spark_writer,
                allow_partial=case.get("run", {}).get("allow_partial", False),
            )
            row["execution"] = {
                "status": execution["status"],
                "tasks": {
                    t["name"]: t["status"]
                    for p in execution["pipelines"]
                    for t in p["tasks"]
                    if "name" in t
                },
                "duration_s": round(
                    sum(t.get("duration_s", 0) for p in execution["pipelines"] for t in p["tasks"]),
                    3,
                ),
            }
            expected_status = expect.get(
                "execution", "partial" if case.get("run", {}).get("allow_partial") else "succeeded"
            )
            row["checks"]["execution"] = {
                "expected": expected_status,
                "actual": execution["status"],
                "ok": execution["status"] == expected_status,
            }
            if case.get("partition") == "behaviorally-comparable" and expected_status != "failed":
                expectations = {b: case["dir"] / p for b, p in case.get("outputs", {}).items()}
                cmp = compare_outputs(
                    doc, json.loads(bindings.read_text("utf-8")), bindings, expectations
                )
                write_json(run_dir / "comparison.json", cmp)
                row["comparison"] = {
                    "status": cmp["status"],
                    "outputs": {b: o["status"] for b, o in cmp["outputs"].items()},
                }
                if tid == mutation_target:
                    row["mutations"] = run_mutations(
                        doc,
                        case,
                        registry.targets[tid](),
                        base / "mutations",
                        expectations,
                        launcher,
                        spark_writer,
                    )
        rows.append(row)
    return rows


# ------------------------------------------------------------------ semantic mutations


def _map_calls(
    node: ExpressionNode, fn: Callable[[CallNode], ExpressionNode | None]
) -> tuple[ExpressionNode, int]:
    if isinstance(node, CallNode):
        count = 0
        args = []
        for a in node.args:
            new, n = _map_calls(a, fn)
            args.append(new)
            count += n
        rebuilt = node.model_copy(update={"args": args})
        replaced = fn(rebuilt)
        return (replaced, count + 1) if replaced is not None else (rebuilt, count)
    if isinstance(node, CastNode):
        inner, n = _map_calls(node.arg, fn)
        return node.model_copy(update={"arg": inner}), n
    return node, 0


def _rewrite_expressions(
    doc: CanonicalDocument, fn: Callable[[CallNode], ExpressionNode | None]
) -> tuple[CanonicalDocument, int]:
    total = 0
    dataflows: list[Dataflow] = []
    for df in doc.dataflows:
        exprs: list[Expression] = []
        for e in df.expressions:
            ast, n = _map_calls(e.ast, fn)
            total += n
            exprs.append(e.model_copy(update={"ast": ast}))
        dataflows.append(df.model_copy(update={"expressions": exprs}))
    return doc.model_copy(update={"dataflows": dataflows}), total


def _swap(mapping: dict[str, str]) -> Callable[[CallNode], ExpressionNode | None]:
    return lambda c: (
        c.model_copy(update={"function": mapping[c.function]}) if c.function in mapping else None
    )


def _negate_filters(doc: CanonicalDocument) -> tuple[CanonicalDocument, int]:
    preds = {
        o.spec.predicate_expression_id
        for df in doc.dataflows
        for o in df.operations
        if o.spec.kind == "filter"
    }
    n = 0
    dataflows = []
    for df in doc.dataflows:
        exprs = []
        for e in df.expressions:
            if e.id in preds:
                n += 1
                e = e.model_copy(update={"ast": CallNode(function="not", args=[e.ast])})
            exprs.append(e)
        dataflows.append(df.model_copy(update={"expressions": exprs}))
    return doc.model_copy(update={"dataflows": dataflows}), n


def _join_to_inner(doc: CanonicalDocument) -> tuple[CanonicalDocument, int]:
    n = 0
    dataflows = []
    for df in doc.dataflows:
        ops: list[Operation] = []
        for o in df.operations:
            if o.spec.kind == "join" and o.spec.join_type != "inner":
                n += 1
                o = o.model_copy(update={"spec": o.spec.model_copy(update={"join_type": "inner"})})
            ops.append(o)
        dataflows.append(df.model_copy(update={"operations": ops}))
    return doc.model_copy(update={"dataflows": dataflows}), n


def _lookup_any_to_error(doc: CanonicalDocument) -> tuple[CanonicalDocument, int]:
    n = 0
    dataflows = []
    for df in doc.dataflows:
        ops: list[Operation] = []
        for o in df.operations:
            if o.spec.kind == "lookup" and o.spec.on_multiple_match == "any":
                n += 1
                o = o.model_copy(
                    update={"spec": o.spec.model_copy(update={"on_multiple_match": "error"})}
                )
            ops.append(o)
        dataflows.append(df.model_copy(update={"operations": ops}))
    return doc.model_copy(update={"dataflows": dataflows}), n


MUTATIONS: dict[str, Callable[[CanonicalDocument], tuple[CanonicalDocument, int]]] = {
    "negate-filter-predicates": _negate_filters,
    "comparison-boundary-shift": lambda d: _rewrite_expressions(
        d, _swap({"ge": "gt", "gt": "ge", "le": "lt", "lt": "le"})
    ),
    "subtract-to-add": lambda d: _rewrite_expressions(d, _swap({"subtract": "add"})),
    "concat-to-coalesce": lambda d: _rewrite_expressions(d, _swap({"concat": "coalesce"})),
    "trim-removed": lambda d: _rewrite_expressions(
        d, lambda c: c.args[0] if c.function in ("ltrim", "rtrim") else None
    ),
    "outer-join-to-inner": _join_to_inner,
    "lookup-any-to-error": _lookup_any_to_error,
    "count-to-count-all": lambda d: _rewrite_expressions(
        d, lambda c: CallNode(function="count_all") if c.function == "count" else None
    ),
}


def run_mutations(
    doc: CanonicalDocument,
    case: dict[str, Any],
    emitter: Any,
    out: Path,
    expectations: dict[str, Path],
    launcher: str,
    spark_writer: str | None,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    baseline = out / "baseline"
    emitter.write(emitter.plan(doc), baseline)
    for name, mutate in MUTATIONS.items():
        mutated, sites = mutate(doc)
        if sites == 0:
            results[name] = {"status": "not_applicable", "sites": 0}
            continue
        package = out / name / "package"
        emitter.write(emitter.plan(mutated), package)
        if _same_code(baseline, package):
            # Every mutated site is in a blocked dataflow: nothing observable changed.
            results[name] = {"status": "not_applicable", "sites": 0}
            continue
        run_dir = out / name / "run"
        bindings = _bindings(case, package, run_dir)
        execution = run_package(
            package,
            bindings,
            run_dir,
            _params(case, run_dir),
            launcher=launcher,
            spark_writer=spark_writer,
            allow_partial=case.get("run", {}).get("allow_partial", False),
        )
        cmp = compare_outputs(
            mutated, json.loads(bindings.read_text("utf-8")), bindings, expectations
        )
        detected = execution["status"] == "failed" or cmp["status"] != "agree"
        results[name] = {"status": "detected" if detected else "survived", "sites": sites}
    applicable = [r for r in results.values() if r["status"] != "not_applicable"]
    return {
        "operators": results,
        "detected": sum(1 for r in applicable if r["status"] == "detected"),
        "applicable": len(applicable),
    }


def run_benchmark(
    case_dirs: list[Path],
    out: Path,
    *,
    targets: list[str] | None = None,
    launcher: str = "auto",
    spark_writer: str | None = None,
    mutation_target: str | None = "duckdb",
) -> dict[str, Any]:
    registry = Registry.discover()
    target_ids = targets or sorted(registry.targets)
    rows: list[dict[str, Any]] = []
    for d in sorted(case_dirs):
        rows += run_case(
            load_case(d),
            out,
            target_ids,
            launcher,
            spark_writer,
            mutation_target if mutation_target in target_ids else None,
            registry,
        )

    def count(
        pred: Callable[[dict[str, Any]], bool], among: list[dict[str, Any]]
    ) -> dict[str, int]:
        return {"count": sum(1 for r in among if pred(r)), "of": len(among)}

    executed = [r for r in rows if "execution" in r]
    compared = [r for r in rows if "comparison" in r]
    mutated = [r["mutations"] for r in rows if "mutations" in r]
    results = {
        "etlir_version": __version__,
        "targets": target_ids,
        "comparison_label": "specified-behavior agreement (not source-platform equivalence)",
        "rows": rows,
        "summary": {
            "deterministic_conversion": count(lambda r: r["deterministic_conversion"], rows),
            "expectation_checks_passed": count(
                lambda r: all(c["ok"] for c in r["checks"].values()), rows
            ),
            "execution_as_expected": count(lambda r: r["checks"]["execution"]["ok"], executed),
            "output_agreement": count(lambda r: r["comparison"]["status"] == "agree", compared),
            "mutations_detected": {
                "count": sum(m["detected"] for m in mutated),
                "of": sum(m["applicable"] for m in mutated),
            },
        },
    }
    write_json(out / "results.json", results)
    return results
