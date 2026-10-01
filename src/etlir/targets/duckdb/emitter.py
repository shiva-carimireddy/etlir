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
from etlir.canonical.functions import (
    LEADING_NUMBER_REGEX,
    NUMBER_REGEX,
    WHITESPACE_REGEX,
    format_regex,
    parse_format,
)
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
        for k in (
            "read",
            "write",
            "project",
            "derive",
            "filter",
            "route",
            "join",
            "aggregate",
            "lookup",
            "sequence",
        )
    ),
    *(f"lookup.{p}" for p in ("any", "error", "all")),
    *(f"write.{m}" for m in ("append", "overwrite", "error_if_exists", "update", "upsert")),
    "task.dataflow",
    "task.notify",
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
            "case",
            "upper",
            "lower",
            "ltrim",
            "rtrim",
            "length",
            "substr",
            "sign",
            "lpad",
            "rpad",
            "instr",
            "translate",
            "replace",
            "replace_ci",
            "chr",
            "matches_number",
            "is_whitespace",
            "leading_decimal",
            "to_string",
            "format_timestamp",
            "parse_timestamp",
            "can_parse_timestamp",
            "trunc",
            "round",
            "trunc_timestamp",
            "add_interval",
            "timestamp_part",
            "fail",
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
    "task.notify": ["the reference runner records the notification; it does not send it"],
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
    if fn == "case":
        arms = " ".join(f"WHEN {c} THEN {v}" for c, v in zip(a[:-1:2], a[1:-1:2], strict=True))
        return f"(CASE {arms} ELSE {a[-1]} END)"
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
    return _extended(node, a, [infer(x, env, penv) for x in node.args])


def _literal(node: CallNode, i: int) -> Any:
    arg = node.args[i]
    if not isinstance(arg, LiteralNode) or arg.value is None:
        raise Unlowerable(f"{node.function} argument {i + 1} must be a non-NULL literal")
    return arg.value


_STRFTIME = {
    "YYYY": "%Y",
    "YY": "%y",
    "MM": "%m",
    "DD": "%d",
    "HH24": "%H",
    "MI": "%M",
    "SS": "%S",
}
_UNITS = ("year", "month", "day", "hour", "minute", "second")
_PUNCT = set("""!"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~""")


def _format(fmt: str) -> tuple[str, str]:
    """Canonical timestamp format -> (strftime/strptime format, strict match regex)."""
    tokens = parse_format(fmt)
    if tokens is None:
        raise Unlowerable(f"timestamp format {fmt!r} is outside the canonical token set")
    return "".join(_STRFTIME.get(t, t) for t in tokens), format_regex(tokens)


def _unit(node: CallNode, i: int) -> str:
    unit = str(_literal(node, i))
    if unit not in _UNITS:
        raise Unlowerable(f"unknown time unit {unit!r}")
    return unit


def _extended(node: CallNode, a: list[str], arg_types: list[DataType]) -> str:
    fn = node.function
    if fn == "sign":
        return f"CAST(sign({a[0]}) AS INTEGER)"
    if fn in ("lpad", "rpad"):
        return (
            f"(CASE WHEN {a[0]} IS NULL OR {a[1]} IS NULL THEN NULL WHEN {a[1]} <= 0 THEN '' "
            f"ELSE {fn}({a[0]}, {a[1]}, {lit(str(_literal(node, 2)))}) END)"
        )
    if fn == "instr":
        start = int(_literal(node, 2))
        found = f"instr(substring({a[0]}, {start}), {a[1]})"
        return (
            f"(CASE WHEN {a[0]} IS NULL OR {a[1]} IS NULL THEN NULL WHEN {a[1]} = '' THEN 0 "
            f"WHEN {found} = 0 THEN 0 ELSE {found} + {start - 1} END)"
        )
    if fn == "translate":
        return f"translate({a[0]}, {lit(str(_literal(node, 1)))}, {lit(str(_literal(node, 2)))})"
    if fn == "replace":
        return f"replace({a[0]}, {lit(str(_literal(node, 1)))}, {lit(str(_literal(node, 2)))})"
    if fn == "replace_ci":
        old, new = str(_literal(node, 1)), str(_literal(node, 2))
        pattern = "".join("\\" + c if c in _PUNCT else c for c in old)
        replacement = lit(new.replace("\\", "\\\\"))  # RE2 rewrite strings escape only "\"
        return f"regexp_replace({a[0]}, {lit(pattern)}, {replacement}, 'gi')"
    if fn == "chr":
        return f"(CASE WHEN {a[0]} BETWEEN 1 AND 127 THEN chr(CAST({a[0]} AS INTEGER)) END)"
    if fn in ("matches_number", "is_whitespace"):
        regex = NUMBER_REGEX if fn == "matches_number" else WHITESPACE_REGEX
        return f"regexp_matches({a[0]}, {lit(regex)})"
    if fn == "leading_decimal":
        scale = int(_literal(node, 1))
        prefix = f"regexp_extract({a[0]}, {lit(LEADING_NUMBER_REGEX)}, 1)"
        value = f"CAST(CASE WHEN {prefix} = '' THEN '0' ELSE {prefix} END AS DECIMAL(38,18))"
        return f"CAST(round({value}, {scale}) AS DECIMAL(38,{scale}))"
    if fn == "to_string":
        if arg_types[0].kind is TypeKind.DECIMAL:  # no trailing fractional zeros
            text = f"CAST({a[0]} AS VARCHAR)"
            return (
                f"(CASE WHEN strpos({text}, '.') > 0 "
                f"THEN rtrim(rtrim({text}, '0'), '.') ELSE {text} END)"
            )
        return f"CAST({a[0]} AS VARCHAR)"
    if fn == "format_timestamp":
        return f"strftime({a[0]}, {lit(_format(_literal(node, 1))[0])})"
    if fn in ("parse_timestamp", "can_parse_timestamp"):
        if "YY" in (parse_format(str(_literal(node, 1))) or []):
            raise Unlowerable("a two-digit year cannot be parsed (no century)")
        fmt, regex = _format(_literal(node, 1))
        parsed = f"try_strptime({a[0]}, {lit(fmt)})"
        ok = f"regexp_matches({a[0]}, {lit(regex)}) AND {parsed} IS NOT NULL"
        if fn == "can_parse_timestamp":
            return f"(CASE WHEN {a[0]} IS NULL THEN NULL ELSE {ok} END)"
        message = lit(f"cannot parse timestamp with format {fmt}: ")
        return (
            f"(CASE WHEN {a[0]} IS NULL THEN NULL WHEN {ok} THEN {parsed} "
            f"ELSE error({message} || {a[0]}) END)"
        )
    if fn in ("trunc", "round"):
        st = sql_type(arg_types[0])
        body = f"{fn}({a[0]}, {int(_literal(node, 1))})"
        return f"CAST({body} AS {st})" if st else body
    if fn == "trunc_timestamp":
        return f"date_trunc({lit(_unit(node, 1))}, {a[0]})"
    if fn == "add_interval":
        unit = _unit(node, 1)
        return f"({a[0]} + to_{unit}s({a[2]}))"
    if fn == "timestamp_part":
        return f"CAST(date_part({lit(_unit(node, 1))}, {a[0]}) AS INTEGER)"
    if fn == "fail":
        return f"error({lit(str(_literal(node, 0)))})"
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
        self.writes: list[tuple[str, str, str, list[str], list[str]]] = []
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
        if s.kind == "lookup":
            lk_types = {c.name: c.type for c in s.inputs["lookup"].columns}
            computed |= {m.to_column: lk_types[m.from_column] for m in spec.returns}
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
                    else (f"CAST(NULL AS {sql_type(c.type)})" if sql_type(c.type) else "NULL")
                )
                + f" AS {q(c.name)}"
                for c in ds.columns
            )
            view = f"w{len(self.writes) + 1}"
            self.create(view, f"SELECT {items} FROM {self.src(ref)}")
            provided = [c.name for c in ds.columns if c.name in present]
            self.writes.append(
                (ds.binding_id or ds.id, view, spec.mode.value, list(spec.keys), provided)
            )
        elif s.kind == "lookup":
            i_src, l_src = self.src(s.inputs["in"]), self.src(s.inputs["lookup"])
            rets = {m.to_column: q(m.from_column) for m in spec.returns}
            cond = ex[spec.condition_expression_id]
            policy = spec.on_multiple_match
            if policy == "all":
                body = f"{i_src} AS i LEFT JOIN {l_src} AS l ON {cond}"
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns, rets)} FROM {body}",
                )
            else:
                order = ", ".join(
                    f"l.{q(c.name)} ASC NULLS LAST" for c in s.inputs["lookup"].columns
                )
                inner = (
                    "SELECT i.*, l.*, row_number() OVER (PARTITION BY i.__etlir_rid ORDER BY "
                    f"{order}) AS __etlir_rn, count(l.__etlir_hit) OVER (PARTITION BY "
                    "i.__etlir_rid) AS __etlir_cnt FROM (SELECT *, row_number() OVER () AS "
                    f"__etlir_rid FROM {i_src}) AS i LEFT JOIN (SELECT *, 1 AS __etlir_hit "
                    f"FROM {l_src}) AS l ON {cond}"
                )
                where = "__etlir_rn = 1"
                if policy == "error":
                    name = s.op_id.split(":")[-1].replace("'", "''")
                    where += (
                        " AND (CASE WHEN __etlir_cnt > 1 THEN error('lookup "
                        f"{name}: an input row has more than one match') ELSE TRUE END)"
                    )
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns, rets)} FROM ({inner}) WHERE {where}",
                )
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
            elif s.kind == "sequence":
                order = ", ".join(f"{q(c.name)} ASC NULLS FIRST" for c in s.inputs["in"].columns)
                var, _ = self.params[spec.start_parameter_id]
                number = (
                    f"CAST(getvariable({lit(var)}) AS BIGINT) + "
                    f"(row_number() OVER (ORDER BY {order}) - 1) * {spec.increment}"
                )
                if "after" in s.inputs:
                    after = self.src(s.inputs["after"])
                    number += f" + (SELECT count(*) FROM {after}) * {spec.increment}"
                self.create(
                    self.view(s.output_relation()),
                    f"SELECT {cols(s.outputs[0].columns)} FROM "
                    f"(SELECT *, CAST({number} AS BIGINT) AS {q(spec.column)} FROM {src})",
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
                if not aggs:
                    inner = f"SELECT DISTINCT {keys} FROM {src}"
                elif not keys:  # global aggregate: exactly one row, even for empty input
                    inner = f"SELECT {', '.join(aggs)} FROM {src}"
                else:
                    inner = f"SELECT {', '.join([keys, *aggs])} FROM {src} GROUP BY {keys}"
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


def _launcher(
    df_id: str, sql: str, job: _Job, defaults: dict[str, Any], builtins: Mapping[str, str]
) -> str:
    spec = {
        "dataflow_id": df_id,
        "sql": sql,
        "reads": job.reads,
        "writes": job.writes,
        "params": job.params,
        "defaults": defaults,
        "builtins": builtins,
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
        builtins = {p.id: p.builtin for p in document.parameters if p.builtin}
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
            files[f"jobs/{name}.py"] = _launcher(df.id, f"sql/{name}.sql", job, defaults, builtins)
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
