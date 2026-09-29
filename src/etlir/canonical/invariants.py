"""Graph and reference invariants for Canonical IR documents.

Schema validation (pydantic / JSON Schema) checks shape. This module checks the rules a
schema cannot: global identifier uniqueness, reference resolution, acyclicity of both the
task graph and each dataflow graph, and explicit reporting of unsupported constructs.

Diagnostic codes are stable and documented in :data:`CODES`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from etlir.canonical.model import (
    CallNode,
    CanonicalDocument,
    Dataflow,
    DependencyCondition,
    ExpressionNode,
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

    for df in doc.dataflows:
        out.extend(_validate_dataflow(df, dataset_ids, parameter_ids))

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
        refs_ds = [getattr(spec, "dataset_id", None)]
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

    cycle = _find_cycle(ops, edges)
    if cycle:
        out.append(_diag("IR-V-004", f"Cycle among operations {sorted(cycle)}.", df.id, df.source))
    return out
