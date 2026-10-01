"""Apache Spark (PySpark DataFrame) target emitter.

Each emitted dataflow becomes ``jobs/<name>.py``, runnable with ``spark-submit`` (or plain
``python`` for local runs). Expressions are lowered from the typed canonical AST into
PySpark ``Column`` code; source expression text is never pasted into generated code.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from etlir import __version__ as _etlir_version
from etlir.canonical.functions import format_regex, parse_format
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

TARGET_ID = "spark"
RUNTIME_MODULE = "etlir_spark_runtime"

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
    "operation.write": ["binding format is jsonl or csv"],
}


def manifest(version: str) -> CapabilityManifest:
    rules = [
        CapabilityRule(
            construct_id=c,
            state=CapabilityState.CONSTRAINED if c in _CONSTRAINED else CapabilityState.SUPPORTED,
            preconditions=_CONSTRAINED.get(c, []),
        )
        for c in _SUPPORTED
    ]
    return CapabilityManifest(
        target=TARGET_ID, target_version=version, ir_versions=">=0.1,<0.2", rules=rules
    )


def spark_type(t: DataType) -> str | None:
    k = t.kind
    if k is TypeKind.DECIMAL:
        return f"decimal({t.precision},{t.scale or 0})" if t.precision else None
    return {
        TypeKind.STRING: "string",
        TypeKind.INTEGER: "int",
        TypeKind.BIGINT: "bigint",
        TypeKind.DOUBLE: "double",
        TypeKind.BOOLEAN: "boolean",
        TypeKind.DATE: "date",
        TypeKind.TIMESTAMP: "timestamp",
        TypeKind.BINARY: "binary",
    }.get(k)


def _struct_type(t: DataType) -> str:
    k = t.kind
    if k is TypeKind.DECIMAL:
        return f"T.DecimalType({t.precision or 38}, {t.scale or 0})"
    return {
        TypeKind.STRING: "T.StringType()",
        TypeKind.INTEGER: "T.IntegerType()",
        TypeKind.BIGINT: "T.LongType()",
        TypeKind.DOUBLE: "T.DoubleType()",
        TypeKind.BOOLEAN: "T.BooleanType()",
        TypeKind.DATE: "T.DateType()",
        TypeKind.TIMESTAMP: "T.TimestampType()",
        TypeKind.BINARY: "T.BinaryType()",
    }[k]


class Unlowerable(Exception):
    pass


_BIN = {
    "add": "+",
    "subtract": "-",
    "multiply": "*",
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "and": "&",
    "or": "|",
}
_UNARY_F = {
    "abs": "F.abs",
    "upper": "F.upper",
    "lower": "F.lower",
    "ltrim": "F.ltrim",
    "rtrim": "F.rtrim",
    "length": "F.length",
    "sum": "F.sum",
    "count": "F.count",
    "min": "F.min",
    "max": "F.max",
    "avg": "F.avg",
}


def lower(
    node: ExpressionNode,
    types: Mapping[str, DataType] | None = None,
    params: Mapping[str, DataType] | None = None,
) -> str:
    """Canonical AST -> PySpark Column expression source.

    ``types``/``params`` give input column and parameter types, needed to lower casts
    exactly (canonical casts to integral types round half away from zero).
    """
    env, penv = types or {}, params or {}

    def go(n: ExpressionNode) -> str:
        return lower(n, env, penv)

    if isinstance(node, ColumnRefNode):
        return f"rt.col({node.name!r})"
    if isinstance(node, LiteralNode):
        st = spark_type(node.type)
        if node.value is None:
            return f"F.lit(None).cast({st!r})" if st else "F.lit(None)"
        if node.type.kind is TypeKind.DECIMAL:
            return f"F.lit(decimal.Decimal({str(node.value)!r}))"
        return f"F.lit({node.value!r})"
    if isinstance(node, ParameterRefNode):
        return f"rt.param(ctx, {node.parameter_id!r}, PARAM_TYPES.get({node.parameter_id!r}))"
    if isinstance(node, CastNode):
        return _cast(go(node.arg), node.to, infer(node.arg, env, penv))
    if isinstance(node, OpaqueNode):
        raise Unlowerable("opaque expression")
    fn, args = node.function, [go(a) for a in node.args]
    if fn in _BIN:
        return f"({args[0]} {_BIN[fn]} {args[1]})"
    if fn in _UNARY_F:
        return f"{_UNARY_F[fn]}({args[0]})"
    simple: dict[str, Callable[[], str]] = {
        "divide": lambda: f"F.try_divide({args[0]}, {args[1]})",
        "negate": lambda: f"(-{args[0]})",
        "not": lambda: f"(~{args[0]})",
        "is_null": lambda: f"{args[0]}.isNull()",
        "if": lambda: f"F.when({args[0]}, {args[1]}).otherwise({args[2]})",
        "case": lambda: (
            "F"
            + "".join(f".when({c}, {v})" for c, v in zip(args[:-1:2], args[1:-1:2], strict=True))
            + f".otherwise({args[-1]})"
        ),
        "coalesce": lambda: f"F.coalesce({', '.join(args)})",
        "concat": lambda: f"rt.concat({', '.join(args)})",
        "substr": lambda: f"rt.substr({', '.join(args)})",
        "count_all": lambda: "F.count(F.lit(1))",
    }
    if fn in simple:
        return simple[fn]()
    return _extended(node, args, [infer(a, env, penv) for a in node.args])


def _literal(node: CallNode, i: int) -> Any:
    arg = node.args[i]
    if not isinstance(arg, LiteralNode) or arg.value is None:
        raise Unlowerable(f"{node.function} argument {i + 1} must be a non-NULL literal")
    return arg.value


_SPARK_FORMAT = {
    "YYYY": "yyyy",
    "YY": "yy",
    "MM": "MM",
    "DD": "dd",
    "HH24": "HH",
    "MI": "mm",
    "SS": "ss",
}
_UNIT_PART = {
    "year": "F.year",
    "month": "F.month",
    "day": "F.dayofmonth",
    "hour": "F.hour",
    "minute": "F.minute",
    "second": "F.second",
}
_INTERVAL_ARG = {
    "year": "years",
    "month": "months",
    "day": "days",
    "hour": "hours",
    "minute": "mins",
    "second": "secs",
}


def _format(fmt: str) -> tuple[str, str]:
    """Canonical timestamp format -> (Spark datetime pattern, strict match regex)."""
    tokens = parse_format(fmt)
    if tokens is None:
        raise Unlowerable(f"timestamp format {fmt!r} is outside the canonical token set")
    pattern = "".join(_SPARK_FORMAT.get(t, "'T'" if t == "T" else t) for t in tokens)
    return pattern, format_regex(tokens)


def _unit(node: CallNode, i: int) -> str:
    unit = str(_literal(node, i))
    if unit not in _UNIT_PART:
        raise Unlowerable(f"unknown time unit {unit!r}")
    return unit


def _extended(node: CallNode, args: list[str], arg_types: list[DataType]) -> str:
    fn = node.function
    if fn == "sign":
        return f"F.signum({args[0]}).cast('int')"
    if fn in ("lpad", "rpad"):
        return f"rt.pad({args[0]}, {args[1]}, {_literal(node, 2)!r}, left={fn == 'lpad'})"
    if fn == "instr":
        return f"rt.instr({args[0]}, {args[1]}, {int(_literal(node, 2))})"
    if fn == "translate":
        return f"F.translate({args[0]}, {_literal(node, 1)!r}, {_literal(node, 2)!r})"
    if fn in ("replace", "replace_ci"):
        old, new = _literal(node, 1), _literal(node, 2)
        return f"rt.replace({args[0]}, {old!r}, {new!r}, ignore_case={fn == 'replace_ci'})"
    if fn in ("chr", "matches_number", "is_whitespace"):
        helper = "chr_ascii" if fn == "chr" else fn
        return f"rt.{helper}({args[0]})"
    if fn == "leading_decimal":
        return f"rt.leading_decimal({args[0]}, {int(_literal(node, 1))})"
    if fn == "to_string":
        if arg_types[0].kind is TypeKind.DECIMAL:
            return f"rt.decimal_text({args[0]})"
        return f"({args[0]}).cast('string')"
    if fn == "format_timestamp":
        return f"F.date_format({args[0]}, {_format(_literal(node, 1))[0]!r})"
    if fn in ("parse_timestamp", "can_parse_timestamp"):
        if "YY" in (parse_format(str(_literal(node, 1))) or []):
            raise Unlowerable("a two-digit year cannot be parsed (no century)")
        pattern, regex = _format(_literal(node, 1))
        return f"rt.{fn}({args[0]}, {pattern!r}, {regex!r})"
    if fn in ("trunc", "round"):
        places = int(_literal(node, 1))
        st = spark_type(arg_types[0])
        helper = "F.round" if fn == "round" else "rt.trunc_decimal"
        body = f"{helper}({args[0]}, {places})"
        return f"({body}).cast({st!r})" if st else body
    if fn == "trunc_timestamp":
        return f"F.date_trunc({_unit(node, 1)!r}, {args[0]})"
    if fn == "add_interval":
        return f"({args[0]} + F.make_interval({_INTERVAL_ARG[_unit(node, 1)]}={args[2]}))"
    if fn == "timestamp_part":
        return f"{_UNIT_PART[_unit(node, 1)]}({args[0]}).cast('int')"
    if fn == "fail":
        return f"F.raise_error(F.lit({str(_literal(node, 0))!r}))"
    raise Unlowerable(f"function {fn}")


def _cast(expr: str, t: DataType, source: DataType | None = None) -> str:
    st = spark_type(t)
    if st is None:
        return expr
    if source is not None and rounds_on_cast(source, t):
        return f"F.round({expr}, 0).cast({st!r})"  # Spark casts truncate; canonical rounds
    return f"({expr}).cast({st!r})"


class _Job:
    """Renders one dataflow into a PySpark module, tracking the line of every step."""

    def __init__(self, doc: CanonicalDocument, df_id: str, module: str) -> None:
        self.doc = doc
        self.df = next(d for d in doc.dataflows if d.id == df_id)
        self.module = module
        self.lines: list[str] = []
        self.vars: dict[str, str] = {}
        self.op_lines: dict[str, int] = {}
        self.datasets = {d.id: d for d in doc.datasets}

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    def var(self, relation: str) -> str:
        if relation not in self.vars:
            self.vars[relation] = f"r{len(self.vars) + 1}"
        return self.vars[relation]

    def slot(self, ref: SlotRef) -> str:
        v = self.vars[ref.relation]
        if not ref.renames:
            return v
        items = ", ".join(f"rt.col({f!r}).alias({t!r})" for f, t in ref.renames)
        return f"{v}.select({items})"

    def render(self) -> str:
        params = {p.id: spark_type(p.type) for p in self.doc.parameters}
        defaults = {p.id: p.default for p in self.doc.parameters if p.default is not None}
        builtins = {p.id: p.builtin for p in self.doc.parameters if p.builtin}
        src = self.df.source
        header = [
            f"# Generated by ETLIR {_etlir_version} (target {TARGET_ID}, Canonical IR "
            f"{IR_VERSION}). Do not edit; regenerate with `etlir convert`.",
            f"# Dataflow: {self.df.id}",
            f"# Source:   {src.artifact} {src.locator}",
            "from __future__ import annotations",
            "",
            "import decimal  # noqa: F401",
            "import sys",
            "from pathlib import Path",
            "",
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))",
            "",
            "from pyspark.sql import functions as F  # noqa: E402,F401",
            "from pyspark.sql import types as T  # noqa: E402,F401",
            "",
            f"import {RUNTIME_MODULE} as rt  # noqa: E402",
            "",
            f"DATAFLOW_ID = {self.df.id!r}",
            f"PARAM_TYPES = {params!r}",
            f"PARAM_DEFAULTS = {defaults!r}",
            f"PARAM_BUILTINS = {builtins!r}",
            "",
            "",
            "def run(spark, ctx):",
        ]
        self.lines.extend(header)
        for step in lower_dataflow(self.doc, self.df):
            self.step(step)
        self.lines.extend(
            [
                "",
                "",
                'if __name__ == "__main__":',
                "    rt.main(run, DATAFLOW_ID, PARAM_DEFAULTS, PARAM_BUILTINS)",
                "",
            ]
        )
        return "\n".join(self.lines)

    def step(self, s: Step) -> None:
        op = next(o for o in self.df.operations if o.id == s.op_id)
        self.emit(f"    # [{s.op_id}] {s.kind} <- {op.source.artifact} {op.source.locator}")
        self.op_lines[s.op_id] = len(self.lines)
        spec: Any = s.spec
        in_types = {c.name: c.type for ref in s.inputs.values() for c in ref.columns}
        p_types = {p.id: p.type for p in self.doc.parameters}
        out_types = {c.name: c.type for g in s.outputs for c in g.columns}
        target_of = {a.expression_id: a.column for a in getattr(spec, "assignments", [])}
        target_of |= {a.expression_id: a.column for a in getattr(spec, "aggregations", [])}
        exprs: dict[str, str] = {}
        expr_types: dict[str, DataType] = {}
        for i, e in s.expressions.items():
            ast = e.ast
            col_type = out_types.get(target_of.get(i, ""))
            if isinstance(ast, CastNode) and col_type is not None and ast.to == col_type:
                ast = ast.arg
            exprs[i] = lower(ast, in_types, p_types)
            expr_types[i] = infer(ast, in_types, p_types)

        def outcols(
            group_cols: list[Any],
            source: dict[str, str] | None = None,
            source_types: dict[str, DataType] | None = None,
        ) -> str:
            source, source_types = source or {}, source_types or {}
            return ", ".join(
                _cast(
                    source.get(c.name, f"rt.col({c.name!r})"),
                    c.type,
                    source_types.get(c.name, in_types.get(c.name)),
                )
                + f".alias({c.name!r})"
                for c in group_cols
            )

        if s.kind == "read":
            ds = self.datasets[spec.dataset_id]
            fields = ", ".join(
                f"T.StructField({c.name!r}, {_struct_type(c.type)}, True)" for c in ds.columns
            )
            out = self.var(s.output_relation())
            self.emit(
                f"    {out} = rt.read(spark, ctx, {ds.binding_id or ds.id!r}, "
                f"T.StructType([{fields}]))"
            )
            return
        if s.kind == "write":
            ds = self.datasets[spec.dataset_id]
            ref = s.inputs["in"]
            present = {c.name for c in ref.columns}
            items = ", ".join(
                (
                    _cast(f"rt.col({c.name!r})", c.type, in_types.get(c.name))
                    if c.name in present
                    else (
                        f"F.lit(None).cast({spark_type(c.type)!r})"
                        if spark_type(c.type)
                        else "F.lit(None)"
                    )
                )
                + f".alias({c.name!r})"
                for c in ds.columns
            )
            binding = ds.binding_id or ds.id
            if spec.keys:
                provided = [c.name for c in ds.columns if c.name in present]
                self.emit(
                    f"    rt.write_keyed(spark, ctx, {binding!r}, "
                    f"{self.slot(ref)}.select({items}), "
                    f"{spec.mode.value!r}, {spec.keys!r}, {provided!r})"
                )
                return
            self.emit(
                f"    rt.write(ctx, {binding!r}, "
                f"{self.slot(ref)}.select({items}), {spec.mode.value!r})"
            )
            return
        if s.kind == "lookup":
            ref_in, ref_lk = s.inputs["in"], s.inputs["lookup"]
            lk_types = {c.name: c.type for c in ref_lk.columns}
            ret_src = {m.to_column: f"rt.col({m.from_column!r})" for m in spec.returns}
            ret_types = {m.to_column: lk_types[m.from_column] for m in spec.returns}
            order = [c.name for c in ref_lk.columns]
            cond = exprs[spec.condition_expression_id]
            self.emit(
                f"    {self.var(s.output_relation())} = rt.lookup({self.slot(ref_in)}, "
                f"{self.slot(ref_lk)}, {cond}, {spec.on_multiple_match!r}, {order!r}, "
                f"{s.op_id.split(':')[-1]!r})"
                f".select({outcols(s.outputs[0].columns, ret_src, ret_types)})"
            )
            return
        if s.kind == "join":
            left, right = self.slot(s.inputs["left"]), self.slot(s.inputs["right"])
            out = self.var(s.output_relation())
            cond = exprs[spec.condition_expression_id]
            self.emit(
                f"    {out} = {left}.join({right}, on={cond}, how={spec.join_type!r})"
                f".select({outcols(s.outputs[0].columns)})"
            )
            return
        src = self.slot(s.inputs["in"])
        if s.kind == "project":
            self.emit(
                f"    {self.var(s.output_relation())} = "
                f"{src}.select({outcols(s.outputs[0].columns)})"
            )
        elif s.kind == "derive":
            assigned = {a.column: exprs[a.expression_id] for a in spec.assignments}
            atypes = {a.column: expr_types[a.expression_id] for a in spec.assignments}
            self.emit(
                f"    {self.var(s.output_relation())} = "
                f"{src}.select({outcols(s.outputs[0].columns, assigned, atypes)})"
            )
        elif s.kind == "sequence":
            ordering = ", ".join(
                f"rt.col({c.name!r}).asc_nulls_first()" for c in s.inputs["in"].columns
            )
            start = f"rt.param(ctx, {spec.start_parameter_id!r}, 'bigint')"
            if "after" in s.inputs:
                start += f" + F.lit({self.slot(s.inputs['after'])}.count() * {spec.increment})"
            self.emit(
                f"    {self.var(s.output_relation())} = {src}.withColumn({spec.column!r}, "
                f"({start} + (F.row_number().over(rt.Window.orderBy({ordering})) - 1) "
                f"* {spec.increment}).cast('bigint'))"
                f".select({outcols(s.outputs[0].columns)})"
            )
        elif s.kind == "filter":
            self.emit(
                f"    {self.var(s.output_relation())} = "
                f"{src}.filter({exprs[spec.predicate_expression_id]})"
                f".select({outcols(s.outputs[0].columns)})"
            )
        elif s.kind == "route":
            tmp = f"_in_{self.var(s.output_relation(s.outputs[0].name))}"
            self.emit(f"    {tmp} = {src}")
            preds = {g.name: exprs[g.predicate_expression_id] for g in spec.groups}
            for g in s.outputs:
                v = self.var(s.output_relation(g.name))
                if g.name in preds:
                    cond = preds[g.name]
                elif preds:
                    matched = " | ".join(f"F.coalesce({p}, F.lit(False))" for p in preds.values())
                    cond = f"~({matched})"
                else:
                    cond = "F.lit(True)"
                self.emit(f"    {v} = {tmp}.filter({cond}).select({outcols(g.columns)})")
        elif s.kind == "aggregate":
            keys = ", ".join(f"rt.col({k!r})" for k in spec.group_by)
            aggs = ", ".join(
                f"({exprs[a.expression_id]}).alias({a.column!r})" for a in spec.aggregations
            )
            grouped = (
                f"{src}.groupBy({keys}).agg({aggs})" if aggs else f"{src}.select({keys}).distinct()"
            )
            agg_types = {a.column: expr_types[a.expression_id] for a in spec.aggregations}
            self.emit(
                f"    {self.var(s.output_relation())} = "
                f"{grouped}.select({outcols(s.outputs[0].columns, None, agg_types)})"
            )
        else:
            raise Unlowerable(f"operation {s.kind}")


_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _module_name(text: str) -> str:
    name = _SAFE.sub("_", text).strip("_").lower() or "job"
    return f"job_{name}" if name[0].isdigit() else name


def preflight(doc: CanonicalDocument, df_id: str) -> list[str]:
    """Emitter-specific checks that the generic capability analysis cannot express."""
    df = next(d for d in doc.dataflows if d.id == df_id)
    datasets = {d.id: d for d in doc.datasets}
    reasons: list[str] = []
    for op in df.operations:
        ds_id = getattr(op.spec, "dataset_id", None)
        if op.spec.kind == "read" and ds_id in datasets:
            for c in datasets[ds_id].columns:
                if c.type.kind is TypeKind.UNKNOWN:
                    reasons.append(f"read column {c.name} has an unknown type")
        for g in op.outputs:
            for c in g.columns:
                if c.type.kind is TypeKind.UNKNOWN and op.spec.kind != "unsupported":
                    reasons.append(f"{op.id} output {c.name} has an unknown type")
    return reasons


class SparkEmitter(TargetEmitter):
    """Canonical IR -> PySpark DataFrame jobs + workflow plan for the reference runner."""

    id = TARGET_ID
    version = _etlir_version
    ir_versions = ">=0.1,<0.2"
    description = "Apache Spark (PySpark DataFrame API); local spark-submit reference runner"

    def capabilities(self) -> CapabilityManifest:
        return manifest(self.version)

    def plan(self, document: CanonicalDocument) -> dict[str, Any]:
        extra = {df.id: r for df in document.dataflows if (r := preflight(document, df.id))}
        report = analyze(document, self.capabilities(), extra)
        blocked = set(report.blocked_dataflow_ids)
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
            name = job_name(df.id, taken, _module_name)
            module = f"jobs/{name}.py"
            job = _Job(document, df.id, module)
            try:
                files[module] = job.render()
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
            jobs.append(
                {
                    "dataflow_id": df.id,
                    "status": "emitted",
                    "module": module,
                    "op_lines": job.op_lines,
                }
            )
            commands[df.id] = {
                "kind": "spark-submit",
                "script": module,
                "master": "local[1]",
                "conf": {"spark.ui.enabled": "false"},
            }
        files[f"{RUNTIME_MODULE}.py"] = (
            resources.files(__package__).joinpath(f"{RUNTIME_MODULE}.py").read_text("utf-8")
        )
        workflow = build_workflow_plan(document, report, extra, commands, TARGET_ID)
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
            "workflow": workflow,
            "bindings_example": bindings_example(
                document, {j["dataflow_id"] for j in jobs if j["status"] == "emitted"}
            ),
            "parameters_example": {p.id: p.default for p in document.parameters if not p.sensitive},
            "files": files,
        }

    def write(self, plan: dict[str, Any], out_dir: Path) -> EmitResult:
        return write_package(plan, out_dir)
