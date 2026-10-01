"""Graph and reference invariants for Canonical IR documents.

Schema validation (pydantic / JSON Schema) checks shape. This module checks the rules a
schema cannot: global identifier uniqueness, reference resolution, acyclicity of both the
task graph and each dataflow graph, and explicit reporting of unsupported constructs.

Diagnostic codes are stable and documented in :data:`CODES`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from etlir.canonical.functions import CATALOG, arity_ok
from etlir.canonical.graph import output_columns, slot_columns, slot_inputs
from etlir.canonical.model import (
    CallNode,
    CanonicalDocument,
    CastNode,
    ColumnRefNode,
    Dataflow,
    Dataset,
    DependencyCondition,
    ExpressionNode,
    LiteralNode,
    OpaqueNode,
    ParameterRefNode,
    Pipeline,
    SourceRef,
    TaskKind,
)
from etlir.evidence import Diagnostic, Severity

CODES: dict[str, str] = {
    "IR-V-001": "Identifier is not unique within the document.",
    "IR-V-002": "Reference does not resolve.",
    "IR-V-003": "Task dependency graph contains a cycle.",
    "IR-V-004": "Dataflow operation graph contains a cycle.",
    "IR-V-005": "Task kind and dataflow_id are inconsistent.",
    "IR-V-006": "Expression dependency lacks an expression_id.",
    "IR-V-007": "Read has an incoming edge or write has an outgoing edge.",
    "IR-V-008": "Edge references an undeclared output group.",
    "IR-V-009": "Sensitive parameter carries a default value.",
    "IR-V-010": "Unsupported operation is present.",
    "IR-V-011": "Unsupported task is present.",
    "IR-V-012": "Expression is opaque (not parsed into the supported grammar).",
    "IR-V-013": "Input slot is fed by more than one upstream output.",
    "IR-V-014": "Function is not in the canonical catalog, has wrong arity, or is misplaced.",
    "IR-V-015": "Column does not resolve.",
    "IR-V-016": "Join inputs are missing or have overlapping column names.",
    "IR-V-017": "Operation does not declare its output columns.",
    "IR-V-018": "Operation is missing a required input.",
    "IR-V-019": "Keyed write without valid key columns, or keys on a non-keyed write.",
}

# Input slots each operation kind needs (join inputs are checked by IR-V-016).
_REQUIRED_INPUTS = {
    "project": ("in",),
    "derive": ("in",),
    "filter": ("in",),
    "route": ("in",),
    "aggregate": ("in",),
    "write": ("in",),
    "lookup": ("in", "lookup"),
    "sequence": ("in",),
}


def _diag(
    code: str,
    message: str,
    subject_id: str | None,
    source: SourceRef | None,
    severity: Severity = Severity.ERROR,
) -> Diagnostic:
    return Diagnostic(
        code=code, severity=severity, message=message, subject_id=subject_id, source=source
    )


def walk_expression(node: ExpressionNode) -> Iterator[ExpressionNode]:
    """Yield an expression node and all of its descendants, depth first."""
    yield node
    if isinstance(node, CallNode):
        for arg in node.args:
            yield from walk_expression(arg)
    elif isinstance(node, CastNode):
        yield from walk_expression(node.arg)


def _find_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[str]:
    """Return the nodes left after Kahn's algorithm (non-empty iff a cycle exists)."""
    order = list(dict.fromkeys(nodes))
    indegree = {n: 0 for n in order}
    successors: dict[str, list[str]] = {n: [] for n in order}
    for a, b in edges:
        if a in indegree and b in indegree:
            successors[a].append(b)
            indegree[b] += 1
    ready = [n for n in order if indegree[n] == 0]
    seen = 0
    while ready:
        n = ready.pop()
        seen += 1
        for m in successors[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)
    return [n for n in order if indegree[n] > 0] if seen < len(order) else []


def _ids(doc: CanonicalDocument) -> Iterator[tuple[str, SourceRef]]:
    for p in doc.pipelines:
        yield p.id, p.source
        for t in p.tasks:
            yield t.id, t.source
        for e in p.expressions:
            yield e.id, e.source
    for df in doc.dataflows:
        yield df.id, df.source
        for op in df.operations:
            yield op.id, op.source
        for e in df.expressions:
            yield e.id, e.source
    for ds in doc.datasets:
        yield ds.id, ds.source
    for prm in doc.parameters:
        yield prm.id, prm.source
    for b in doc.bindings:
        yield b.id, b.source


def validate(doc: CanonicalDocument) -> list[Diagnostic]:
    """Return all invariant violations and unsupported-construct findings, in stable order."""
    out: list[Diagnostic] = []

    seen: set[str] = set()
    for ident, src in _ids(doc):
        if ident in seen:
            out.append(_diag("IR-V-001", f"Duplicate identifier '{ident}'.", ident, src))
        seen.add(ident)

    dataset_ids = {d.id for d in doc.datasets}
    dataflow_ids = {d.id for d in doc.dataflows}
    parameter_ids = {p.id for p in doc.parameters}
    binding_ids = {b.id for b in doc.bindings}

    for prm in doc.parameters:
        if prm.sensitive and prm.default is not None:
            out.append(
                _diag(
                    "IR-V-009", f"Sensitive parameter '{prm.id}' has a default.", prm.id, prm.source
                )
            )

    for ds in doc.datasets:
        if ds.binding_id is not None and ds.binding_id not in binding_ids:
            out.append(_diag("IR-V-002", f"Unknown binding '{ds.binding_id}'.", ds.id, ds.source))

    for pipeline in doc.pipelines:
        out.extend(_validate_pipeline(pipeline, dataflow_ids, parameter_ids))

    datasets = {d.id: d for d in doc.datasets}
    for df in doc.dataflows:
        out.extend(_validate_dataflow(df, dataset_ids, parameter_ids))
        out.extend(_validate_columns(df, datasets))

    for edge in doc.lineage:
        if edge.dataflow_id not in dataflow_ids:
            out.append(
                _diag(
                    "IR-V-002", f"Unknown dataflow '{edge.dataflow_id}'.", edge.target, edge.source
                )
            )

    return out


def _check_expression_params(
    expressions: Iterable[tuple[str, ExpressionNode, SourceRef]], parameter_ids: set[str]
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for expr_id, ast, src in expressions:
        for node in walk_expression(ast):
            if isinstance(node, ParameterRefNode) and node.parameter_id not in parameter_ids:
                out.append(
                    _diag("IR-V-002", f"Unknown parameter '{node.parameter_id}'.", expr_id, src)
                )
            elif (
                isinstance(node, CallNode)
                and node.function in CATALOG
                and any(
                    i < len(node.args) and not isinstance(node.args[i], LiteralNode)
                    for i in CATALOG[node.function].literal_args
                )
            ):
                out.append(
                    _diag(
                        "IR-V-014",
                        f"{node.function} requires literal arguments at positions "
                        f"{list(CATALOG[node.function].literal_args)}.",
                        expr_id,
                        src,
                    )
                )
            elif isinstance(node, CallNode) and not arity_ok(node.function, len(node.args)):
                out.append(
                    _diag(
                        "IR-V-014",
                        f"Unknown function or wrong arity: {node.function}/{len(node.args)}.",
                        expr_id,
                        src,
                    )
                )
            elif isinstance(node, OpaqueNode):
                out.append(
                    _diag(
                        "IR-V-012",
                        f"Opaque {node.dialect} expression.",
                        expr_id,
                        src,
                        Severity.WARNING,
                    )
                )
    return out


def _validate_pipeline(
    p: Pipeline, dataflow_ids: set[str], parameter_ids: set[str]
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    task_ids = {t.id for t in p.tasks}
    expr_ids = {e.id for e in p.expressions}
    out.extend(
        _check_expression_params(((e.id, e.ast, e.source) for e in p.expressions), parameter_ids)
    )
    edges: list[tuple[str, str]] = []
    for t in p.tasks:
        if (t.kind is TaskKind.DATAFLOW) != (t.dataflow_id is not None):
            out.append(
                _diag("IR-V-005", f"Task '{t.id}' kind/dataflow_id mismatch.", t.id, t.source)
            )
        if t.dataflow_id is not None and t.dataflow_id not in dataflow_ids:
            out.append(_diag("IR-V-002", f"Unknown dataflow '{t.dataflow_id}'.", t.id, t.source))
        for pid in t.parameter_ids:
            if pid not in parameter_ids:
                out.append(_diag("IR-V-002", f"Unknown parameter '{pid}'.", t.id, t.source))
        if t.kind is TaskKind.UNSUPPORTED:
            out.append(
                _diag(
                    "IR-V-011",
                    f"Unsupported task '{t.id}': {t.unsupported_reason or 'no reason given'}.",
                    t.id,
                    t.source,
                    Severity.WARNING,
                )
            )
        for dep in t.depends_on:
            if dep.task_id not in task_ids:
                out.append(_diag("IR-V-002", f"Unknown task '{dep.task_id}'.", t.id, t.source))
            else:
                edges.append((dep.task_id, t.id))
            if dep.condition is DependencyCondition.EXPRESSION and dep.expression_id is None:
                out.append(_diag("IR-V-006", "Expression dependency without id.", t.id, t.source))
            if dep.expression_id is not None and dep.expression_id not in expr_ids:
                out.append(
                    _diag("IR-V-002", f"Unknown expression '{dep.expression_id}'.", t.id, t.source)
                )
    cycle = _find_cycle((t.id for t in p.tasks), edges)
    if cycle:
        out.append(_diag("IR-V-003", f"Cycle among tasks {sorted(cycle)}.", p.id, p.source))
    return out


def _validate_dataflow(
    df: Dataflow, dataset_ids: set[str], parameter_ids: set[str]
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    ops = {op.id: op for op in df.operations}
    expr_ids = {e.id for e in df.expressions}
    out.extend(
        _check_expression_params(((e.id, e.ast, e.source) for e in df.expressions), parameter_ids)
    )

    for op in df.operations:
        spec = op.spec
        refs_ds = [getattr(spec, "dataset_id", None)]  # read / write
        refs_expr: list[str] = []
        for attr in ("predicate_expression_id", "condition_expression_id"):
            value = getattr(spec, attr, None)
            if value is not None:
                refs_expr.append(value)
        refs_expr += [a.expression_id for a in getattr(spec, "assignments", [])]
        refs_expr += [a.expression_id for a in getattr(spec, "aggregations", [])]
        refs_expr += [g.predicate_expression_id for g in getattr(spec, "groups", [])]
        for ds in refs_ds:
            if ds is not None and ds not in dataset_ids:
                out.append(_diag("IR-V-002", f"Unknown dataset '{ds}'.", op.id, op.source))
        for ex in refs_expr:
            if ex not in expr_ids:
                out.append(_diag("IR-V-002", f"Unknown expression '{ex}'.", op.id, op.source))
        if spec.kind == "unsupported":
            out.append(
                _diag(
                    "IR-V-010",
                    f"Unsupported operation '{op.id}' ({spec.native_kind}): {spec.reason}.",
                    op.id,
                    op.source,
                    Severity.WARNING,
                )
            )

    edges: list[tuple[str, str]] = []
    for e in df.edges:
        src_op, dst_op = ops.get(e.from_operation), ops.get(e.to_operation)
        if src_op is None or dst_op is None:
            missing = e.from_operation if src_op is None else e.to_operation
            out.append(_diag("IR-V-002", f"Unknown operation '{missing}'.", df.id, df.source))
            continue
        edges.append((e.from_operation, e.to_operation))
        declared = {g.name for g in src_op.outputs} or {"out"}
        if e.from_group not in declared:
            out.append(
                _diag(
                    "IR-V-008",
                    f"'{e.from_operation}' has no output group '{e.from_group}'.",
                    src_op.id,
                    src_op.source,
                )
            )
        if dst_op.spec.kind == "read":
            out.append(_diag("IR-V-007", "Read has an incoming edge.", dst_op.id, dst_op.source))
        if src_op.spec.kind == "write":
            out.append(_diag("IR-V-007", "Write has an outgoing edge.", src_op.id, src_op.source))

    fed: dict[str, set[str]] = {}
    for e in df.edges:
        fed.setdefault(e.to_operation, set()).add(e.to_input)
    for op in df.operations:
        absent = [
            slot
            for slot in _REQUIRED_INPUTS.get(op.spec.kind, ())
            if slot not in fed.get(op.id, set())
        ]
        if op.spec.kind == "union" and not fed.get(op.id):
            absent = ["in"]
        if absent:
            out.append(
                _diag("IR-V-018", f"Operation '{op.id}' has no input {absent}.", op.id, op.source)
            )

    cycle = _find_cycle(ops, edges)
    if cycle:
        out.append(_diag("IR-V-004", f"Cycle among operations {sorted(cycle)}.", df.id, df.source))
    return out


def _expression_columns(ast: ExpressionNode) -> set[str]:
    return {n.name for n in walk_expression(ast) if isinstance(n, ColumnRefNode)}


def _has_aggregate(ast: ExpressionNode) -> bool:
    return any(
        isinstance(n, CallNode) and n.function in CATALOG and CATALOG[n.function].aggregate
        for n in walk_expression(ast)
    )


def _validate_columns(df: Dataflow, datasets: dict[str, Dataset]) -> list[Diagnostic]:
    """Column-level resolution: edges, expressions, pass-through outputs, and writes."""
    out: list[Diagnostic] = []
    ops = {o.id: o for o in df.operations}
    exprs = {e.id: e for e in df.expressions}

    for op in df.operations:
        slots = slot_inputs(df, op.id)
        for slot, ups in slots.items():
            if len(ups) > 1:
                out.append(
                    _diag("IR-V-013", f"Slot '{slot}' has {len(ups)} upstreams.", op.id, op.source)
                )
            for up in ups:
                upstream = ops.get(up.from_operation)
                if upstream is None:
                    continue
                known = {c.name for c in output_columns(upstream, up.from_group, datasets)}
                for m in up.mappings:
                    if known and m.from_column not in known:
                        out.append(
                            _diag(
                                "IR-V-015",
                                f"'{up.from_operation}' has no column '{m.from_column}'.",
                                op.id,
                                op.source,
                            )
                        )

        spec = op.spec
        if spec.kind in ("read", "unsupported"):
            continue
        if spec.kind != "write" and not op.outputs:
            out.append(_diag("IR-V-017", "No output groups declared.", op.id, op.source))
        cols = slot_columns(df, op.id, datasets)
        if spec.kind in ("join", "lookup"):
            names = ("left", "right") if spec.kind == "join" else ("in", "lookup")
            left, right = cols.get(names[0]), cols.get(names[1])
            overlap = {c.name for c in left} & {c.name for c in right} if left and right else set()
            if left is None or right is None or overlap:
                out.append(
                    _diag(
                        "IR-V-016",
                        f"{spec.kind} needs disjoint '{names[0]}' and '{names[1]}' inputs "
                        f"(overlap {sorted(overlap)}).",
                        op.id,
                        op.source,
                    )
                )
        visible = {c.name for group in cols.values() for c in group}

        expr_ids: list[str] = []
        for attr in ("predicate_expression_id", "condition_expression_id"):
            value = getattr(spec, attr, None)
            if value is not None:
                expr_ids.append(value)
        expr_ids += [a.expression_id for a in getattr(spec, "assignments", [])]
        expr_ids += [a.expression_id for a in getattr(spec, "aggregations", [])]
        expr_ids += [g.predicate_expression_id for g in getattr(spec, "groups", [])]
        for ex_id in expr_ids:
            ex = exprs.get(ex_id)
            if ex is None:
                continue
            for name in sorted(_expression_columns(ex.ast) - visible):
                out.append(_diag("IR-V-015", f"Unknown input column '{name}'.", ex.id, ex.source))
            if _has_aggregate(ex.ast) and spec.kind != "aggregate":
                out.append(
                    _diag("IR-V-014", "Aggregate function outside an aggregate.", ex.id, ex.source)
                )

        if spec.kind == "derive":
            assigned = {a.column for a in spec.assignments}
            for group in op.outputs:
                for c in group.columns:
                    if c.name not in assigned and c.name not in visible:
                        out.append(
                            _diag("IR-V-015", f"Output '{c.name}' has no source.", op.id, op.source)
                        )
        elif spec.kind == "lookup":
            lookup_cols = {c.name for c in cols.get("lookup", [])}
            in_cols = {c.name for c in cols.get("in", [])}
            returned = set()
            for m in spec.returns:
                returned.add(m.to_column)
                if m.from_column not in lookup_cols:
                    out.append(
                        _diag(
                            "IR-V-015", f"Lookup has no column '{m.from_column}'.", op.id, op.source
                        )
                    )
            for group in op.outputs:
                for c in group.columns:
                    if c.name not in returned and c.name not in in_cols:
                        out.append(
                            _diag("IR-V-015", f"Output '{c.name}' has no source.", op.id, op.source)
                        )
        elif spec.kind == "sequence":
            if spec.column in visible:
                out.append(
                    _diag("IR-V-015", f"Column '{spec.column}' already exists.", op.id, op.source)
                )
            for group in op.outputs:
                for c in group.columns:
                    if c.name != spec.column and c.name not in visible:
                        out.append(
                            _diag("IR-V-015", f"Output '{c.name}' has no source.", op.id, op.source)
                        )
        elif spec.kind == "project":
            for name in spec.columns:
                if name not in visible:
                    out.append(_diag("IR-V-015", f"Unknown column '{name}'.", op.id, op.source))
        elif spec.kind == "aggregate":
            for name in spec.group_by:
                if name not in visible:
                    out.append(_diag("IR-V-015", f"Unknown group key '{name}'.", op.id, op.source))
        elif spec.kind == "write" and spec.dataset_id in datasets:
            target = {c.name for c in datasets[spec.dataset_id].columns}
            for name in sorted(visible - target):
                out.append(_diag("IR-V-015", f"Dataset has no column '{name}'.", op.id, op.source))
            keyed = spec.mode in ("update", "upsert")
            if keyed != bool(spec.keys) or not set(spec.keys) <= target & visible:
                out.append(
                    _diag(
                        "IR-V-019",
                        f"Write mode '{spec.mode}' with keys {spec.keys}: keyed modes need key "
                        "columns that the input provides and the dataset declares.",
                        op.id,
                        op.source,
                    )
                )
    return out
