"""DuckDB (SQL) target emitter.

Each emitted dataflow becomes a static SQL file ``sql/<name>.sql`` (one view per
canonical operation, in dependency order) and a thin launcher ``jobs/<name>.py``. It runs
locally with no JVM, which makes it the zero-setup reference profile, and it is a second,
independently implemented lowering of the same Canonical IR as the Spark emitter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from etlir import __version__ as _etlir_version
from etlir.canonical.model import (
    IR_VERSION,
    CallNode,
    CanonicalDocument,
    CastNode,
    ColumnRefNode,
    DataType,
    ExpressionNode,
    LiteralNode,
    OpaqueNode,
    ParameterRefNode,
    TypeKind,
)
from etlir.canonical.plan import SlotRef, Step, lower_dataflow
from etlir.canonical.typing import infer, rounds_on_cast
from etlir.capabilities import CapabilityManifest, CapabilityRule, CapabilityState, analyze
from etlir.contracts import EmitResult, TargetEmitter
from etlir.package import bindings_example, write_package
from etlir.workflow import build_workflow_plan, job_name

TARGET_ID = "duckdb"
RUNTIME_MODULE = "etlir_duckdb_runtime"

_SUPPORTED = [
    *(
        f"operation.{k}"
        for k in ("read", "write", "project", "derive", "filter", "route", "join", "aggregate")
    ),
    *(f"write.{m}" for m in ("append", "overwrite", "error_if_exists")),
    "task.dataflow",
    *(f"dependency.{c}" for c in ("success", "failure", "completion")),
    *(
        f"function.{f}"
        for f in (
            "add",
            "subtract",
            "multiply",
            "divide",
            "negate",
            "abs",
            "concat",
            "eq",
            "ne",
            "lt",
            "le",
            "gt",
            "ge",
            "and",
            "or",
            "not",
            "is_null",
            "if",
            "coalesce",
            "upper",
            "lower",
            "ltrim",
            "rtrim",
            "length",
            "substr",
            "sum",
            "count",
            "count_all",
            "min",
            "max",
            "avg",
        )
    ),
    *(f"cast.{t}" for t in ("integer", "bigint", "decimal", "double", "boolean")),
]
_CONSTRAINED = {
    "operation.read": ["every column has a known type", "binding format is csv or jsonl"],
    "operation.write": ["binding format is jsonl"],
}


def manifest(version: str) -> CapabilityManifest:
    return CapabilityManifest(
        target=TARGET_ID,
        target_version=version,
        ir_versions=">=0.1,<0.2",
        rules=[
            CapabilityRule(
                construct_id=c,
                state=CapabilityState.CONSTRAINED
                if c in _CONSTRAINED
                else CapabilityState.SUPPORTED,
                preconditions=_CONSTRAINED.get(c, []),
            )
            for c in _SUPPORTED
        ],
    )


def sql_type(t: DataType) -> str | None:
    if t.kind is TypeKind.DECIMAL:
        return f"DECIMAL({t.precision},{t.scale or 0})" if t.precision else None
    return {
        TypeKind.STRING: "VARCHAR",
        TypeKind.INTEGER: "INTEGER",
        TypeKind.BIGINT: "BIGINT",
        TypeKind.DOUBLE: "DOUBLE",
        TypeKind.BOOLEAN: "BOOLEAN",
        TypeKind.DATE: "DATE",
        TypeKind.TIMESTAMP: "TIMESTAMP",
        TypeKind.BINARY: "BLOB",
    }.get(t.kind)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Unlowerable(Exception):
    pass


_BIN = {
    "add": "+",
    "subtract": "-",
    "multiply": "*",
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "and": "AND",
    "or": "OR",
}
_FN = {
    "abs": "abs",
    "upper": "upper",
    "lower": "lower",
    "ltrim": "ltrim",
    "rtrim": "rtrim",
    "length": "length",
    "sum": "sum",
    "count": "count",
    "min": "min",
    "max": "max",
    "avg": "avg",
    "coalesce": "coalesce",
}


def lower(
    node: ExpressionNode,
    params: dict[str, tuple[str, str]],
    types: Mapping[str, DataType] | None = None,
    param_types: Mapping[str, DataType] | None = None,
) -> str:
    """Canonical AST -> DuckDB SQL expression (types are needed to lower casts exactly)."""
    env, penv = types or {}, param_types or {}

    def go(n: ExpressionNode) -> str:
        return lower(n, params, env, penv)

    if isinstance(node, ColumnRefNode):
        return q(node.name)
    if isinstance(node, LiteralNode):
        st = sql_type(node.type)
        if node.value is None:
            return f"CAST(NULL AS {st})" if st else "NULL"
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        if node.type.kind is TypeKind.DECIMAL:
            return f"CAST({lit(str(node.value))} AS {st or 'DECIMAL(38,18)'})"
        if isinstance(node.value, int):
            return str(node.value)
        return lit(str(node.value))
    if isinstance(node, ParameterRefNode):
        var, st = params[node.parameter_id]
        return f"CAST(getvariable({lit(var)}) AS {st})"
    if isinstance(node, CastNode):
        return _cast(go(node.arg), node.to, infer(node.arg, env, penv))
    if isinstance(node, OpaqueNode):
        raise Unlowerable("opaque expression")
    assert isinstance(node, CallNode)
    fn, a = node.function, [go(x) for x in node.args]
    if fn in _BIN:
        return f"({a[0]} {_BIN[fn]} {a[1]})"
    if fn in _FN:
        return f"{_FN[fn]}({', '.join(a)})"
    if fn == "divide":
        return f"(CASE WHEN {a[1]} = 0 THEN NULL ELSE {a[0]} / {a[1]} END)"
    if fn == "negate":
        return f"(-{a[0]})"
    if fn == "not":
        return f"(NOT {a[0]})"
    if fn == "is_null":
        return f"({a[0]} IS NULL)"
    if fn == "if":
        return f"(CASE WHEN {a[0]} THEN {a[1]} ELSE {a[2]} END)"
    if fn == "concat":
        all_null = " AND ".join(f"{x} IS NULL" for x in a)
        joined = " || ".join(f"COALESCE({x}, '')" for x in a)
        return f"(CASE WHEN {all_null} THEN NULL ELSE {joined} END)"
    if fn == "substr":
        s, start = a[0], a[1]
        ln = a[2] if len(a) > 2 else f"length({s})"
        pos = (
            f"greatest(CASE WHEN {start} > 0 THEN {start} WHEN {start} = 0 THEN 1 "
            f"ELSE length({s}) + {start} + 1 END, 1)"
        )
        return (
            f"(CASE WHEN {s} IS NULL OR {start} IS NULL OR {ln} IS NULL THEN NULL "
            f"WHEN {ln} <= 0 THEN '' ELSE substring({s}, {pos}, {ln}) END)"
        )
    if fn == "count_all":
        return "count(*)"
    raise Unlowerable(f"function {fn}")


def _cast(expr: str, t: DataType, source: DataType | None = None) -> str:
    st = sql_type(t)
    if st is None:
        return expr
    if source is not None and rounds_on_cast(source, t):
        return f"CAST(round({expr}) AS {st})"  # explicit: canonical casts round half away
    return f"CAST({expr} AS {st})"


class _Job:
    def __init__(self, doc: CanonicalDocument, df_id: str) -> None:
        self.doc = doc
        self.df = next(d for d in doc.dataflows if d.id == df_id)
        self.datasets = {d.id: d for d in doc.datasets}
        self.views: dict[str, str] = {}
        self.lines: list[str] = []
        self.op_lines: dict[str, int] = {}
        self.reads: dict[str, list[tuple[str, str]]] = {}
        self.writes: list[tuple[str, str, str]] = []
        used = sorted({p.id for p in doc.parameters})
        self.params = {
            pid: (f"p{i + 1}", sql_type(p.type) or "VARCHAR")
            for i, pid in enumerate(used)
            for p in doc.parameters
            if p.id == pid
        }

    def view(self, relation: str) -> str:
        if relation not in self.views:
            self.views[relation] = f"r{len(self.views) + 1}"
        return self.views[relation]

    def src(self, ref: SlotRef) -> str:
        v = self.views[ref.relation]
        if not ref.renames:
            return v
        cols = ", ".join(f"{q(f)} AS {q(t)}" for f, t in ref.renames)
        return f"(SELECT {cols} FROM {v})"

    def render(self) -> str:
        s = self.df.source
        self.lines += [
            f"-- Generated by ETLIR {_etlir_version} (target {TARGET_ID}, Canonical IR "
            f"{IR_VERSION}). Do not edit; regenerate with `etlir convert`.",
            f"-- Dataflow: {self.df.id}",
            f"-- Source:   {s.artifact} {s.locator}",
        ]
        for step in lower_dataflow(self.doc, self.df):
            self.step(step)
        return "\n".join(self.lines) + "\n"

    def create(self, view: str, select: str) -> None:
        self.lines.append(f"CREATE TEMP VIEW {view} AS {select};")

    def step(self, s: Step) -> None:
        op = next(o for o in self.df.operations if o.id == s.op_id)
        self.lines.append("")
        self.lines.append(f"-- [{s.op_id}] {s.kind} <- {op.source.artifact} {op.source.locator}")
        self.op_lines[s.op_id] = len(self.lines) + 1
        spec: Any = s.spec
        in_types = {c.name: c.type for ref in s.inputs.values() for c in ref.columns}
        p_types = {p.id: p.type for p in self.doc.parameters}
        out_types = {c.name: c.type for g in s.outputs for c in g.columns}
        target_of = {a.expression_id: a.column for a in getattr(spec, "assignments", [])}
        target_of |= {a.expression_id: a.column for a in getattr(spec, "aggregations", [])}
        ex: dict[str, str] = {}
        ex_types: dict[str, DataType] = {}
        for i, e in s.expressions.items():
            ast = e.ast
            ct = out_types.get(target_of.get(i, ""))
            if isinstance(ast, CastNode) and ct is not None and ast.to == ct:
                ast = ast.arg
            ex[i] = lower(ast, self.params, in_types, p_types)
            ex_types[i] = infer(ast, in_types, p_types)
        computed = {a.column: ex_types[a.expression_id] for a in getattr(spec, "assignments", [])}
        computed |= {a.column: ex_types[a.expression_id] for a in getattr(spec, "aggregations", [])}

        def cols(columns: list[Any], exprs: dict[str, str] | None = None) -> str:
            exprs = exprs or {}

            def one(c: Any) -> str:
                source = computed.get(c.name, in_types.get(c.name))
                return f"{_cast(exprs.get(c.name, q(c.name)), c.type, source)} AS {q(c.name)}"

            return ", ".join(one(c) for c in columns)

        if s.kind == "read":
            ds = self.datasets[spec.dataset_id]
            binding = ds.binding_id or ds.id
            self.reads[binding] = [(c.name, sql_type(c.type) or "VARCHAR") for c in ds.columns]
            self.create(
                self.view(s.output_relation()), f"SELECT {cols(ds.columns)} FROM {q(binding)}"
            )
        elif s.kind == "write":
            ds = self.datasets[spec.dataset_id]
            ref = s.inputs["in"]
            present = {c.name for c in ref.columns}
            items = ", ".join(
                (
                    _cast(q(c.name), c.type, in_types.get(c.name))
                    if c.name in present
                    else f"CAST(NULL AS {sql_type(c.type)})"
                )
                + f" AS {q(c.name)}"
                for c in ds.columns
            )
            view = f"w{len(self.writes) + 1}"
            self.create(view, f"SELECT {items} FROM {self.src(ref)}")
            self.writes.append((ds.binding_id or ds.id, view, spec.mode.value))
        elif s.kind == "join":
            how = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL OUTER"}
            left, right = self.src(s.inputs["left"]), self.src(s.inputs["right"])
            self.create(
                self.view(s.output_relation()),
                f"SELECT {cols(s.outputs[0].columns)} FROM {left} AS l "
                f"{how[spec.join_type]} JOIN {right} AS r "
                f"ON {ex[spec.condition_expression_id]}",
            )
        else:
            src = self.src(s.inputs["in"])
            if s.kind == "project":
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns)} FROM {src}",
                )
            elif s.kind == "derive":
                assigned = {a.column: ex[a.expression_id] for a in spec.assignments}
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns, assigned)} FROM {src}",
                )
            elif s.kind == "filter":
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns)} FROM {src} "
                    f"WHERE {ex[spec.predicate_expression_id]}",
                )
            elif s.kind == "route":
                preds = {g.name: ex[g.predicate_expression_id] for g in spec.groups}
                for g in s.outputs:
                    if g.name in preds:
                        cond = preds[g.name]
                    elif preds:
                        cond = (
                            "NOT ("
                            + " OR ".join(f"COALESCE({p}, FALSE)" for p in preds.values())
                            + ")"
                        )
                    else:
                        cond = "TRUE"
                    self.create(
                        self.view(s.output_relation(g.name)),
                        f"SELECT {cols(g.columns)} FROM {src} WHERE {cond}",
                    )
            elif s.kind == "aggregate":
                keys = ", ".join(q(k) for k in spec.group_by)
                aggs = [f"{ex[a.expression_id]} AS {q(a.column)}" for a in spec.aggregations]
                inner = (
                    f"SELECT {', '.join([keys, *aggs])} FROM {src} GROUP BY {keys}"
                    if aggs
                    else f"SELECT DISTINCT {keys} FROM {src}"
                )
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns)} FROM ({inner})",
                )
            else:
                raise Unlowerable(f"operation {s.kind}")


_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _name(text: str) -> str:
    n = _SAFE.sub("_", text).strip("_").lower() or "job"
    return f"job_{n}" if n[0].isdigit() else n


def preflight(doc: CanonicalDocument, df_id: str) -> list[str]:
    df = next(d for d in doc.dataflows if d.id == df_id)
    datasets = {d.id: d for d in doc.datasets}
    reasons: list[str] = []
    for op in df.operations:
        ds_id = getattr(op.spec, "dataset_id", None)
        if op.spec.kind == "read" and ds_id in datasets:
            reasons += [
                f"read column {c.name} has an unknown type"
                for c in datasets[ds_id].columns
                if c.type.kind is TypeKind.UNKNOWN
            ]
        if op.spec.kind != "unsupported":
            reasons += [
                f"{op.id} output {c.name} has an unknown type"
                for g in op.outputs
                for c in g.columns
                if c.type.kind is TypeKind.UNKNOWN
            ]
    return reasons


def _launcher(df_id: str, sql: str, job: _Job, defaults: dict[str, Any]) -> str:
    spec = {
        "dataflow_id": df_id,
        "sql": sql,
        "reads": job.reads,
        "writes": job.writes,
        "params": job.params,
        "defaults": defaults,
    }
    return "\n".join(
        [
            f"# Generated by ETLIR {_etlir_version} (target {TARGET_ID}). Do not edit.",
            f"# Dataflow: {df_id}",
            "import sys",
            "from pathlib import Path",
            "",
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))",
            "",
            f"import {RUNTIME_MODULE} as rt  # noqa: E402",
            "",
            f"JOB = {spec!r}",
            "",
            'if __name__ == "__main__":',
            "    rt.run(JOB)",
            "",
        ]
    )


class DuckDBEmitter(TargetEmitter):
    """Canonical IR -> DuckDB SQL views + launcher; runs locally without a JVM."""

    id = TARGET_ID
    version = _etlir_version
    ir_versions = ">=0.1,<0.2"
    description = "DuckDB SQL (local reference profile, no JVM)"

    def capabilities(self) -> CapabilityManifest:
        return manifest(self.version)

    def plan(self, document: CanonicalDocument) -> dict[str, Any]:
        extra = {df.id: r for df in document.dataflows if (r := preflight(document, df.id))}
        report = analyze(document, self.capabilities(), extra)
        blocked = set(report.blocked_dataflow_ids)
        defaults = {p.id: p.default for p in document.parameters if p.default is not None}
        taken: set[str] = set()
        files: dict[str, str] = {}
        jobs: list[dict[str, Any]] = []
        commands: dict[str, dict[str, Any]] = {}
        for df in document.dataflows:
            if df.id in blocked:
                jobs.append(
                    {
                        "dataflow_id": df.id,
                        "status": "blocked",
                        "module": None,
                        "reasons": extra.get(df.id, []),
                    }
                )
                continue
            name = job_name(df.id, taken, _name)
            job = _Job(document, df.id)
            try:
                sql = job.render()
            except Unlowerable as exc:
                jobs.append(
                    {
                        "dataflow_id": df.id,
                        "status": "blocked",
                        "module": None,
                        "reasons": [str(exc)],
                    }
                )
                continue
            files[f"sql/{name}.sql"] = sql
            files[f"jobs/{name}.py"] = _launcher(df.id, f"sql/{name}.sql", job, defaults)
            jobs.append(
                {
                    "dataflow_id": df.id,
                    "status": "emitted",
                    "module": f"sql/{name}.sql",
                    "launcher": f"jobs/{name}.py",
                    "op_lines": job.op_lines,
                }
            )
            commands[df.id] = {"kind": "python", "script": f"jobs/{name}.py"}
        files[f"{RUNTIME_MODULE}.py"] = (
            resources.files(__package__).joinpath(f"{RUNTIME_MODULE}.py").read_text("utf-8")
        )
        emitted = {j["dataflow_id"] for j in jobs if j["status"] == "emitted"}
        return {
            "target": TARGET_ID,
            "target_version": self.version,
            "ir_version": IR_VERSION,
            "jobs": jobs,
            "blocked_dataflow_ids": report.blocked_dataflow_ids,
            "blocked_task_ids": report.blocked_task_ids,
            "diagnostics": [
                d.model_dump(mode="json", exclude_none=True) for d in report.diagnostics
            ],
            "workflow": build_workflow_plan(document, report, extra, commands, TARGET_ID),
            "bindings_example": bindings_example(document, emitted),
            "parameters_example": {p.id: p.default for p in document.parameters if not p.sensitive},
            "files": files,
        }

    def write(self, plan: dict[str, Any], out_dir: Path) -> EmitResult:
        return write_package(plan, out_dir)
