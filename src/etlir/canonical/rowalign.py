"""Row-aligned merge fusion.

Several ETL tools let one transformation take columns from several upstream
transformations when those are *row-aligned*: they all derive, row by row, from the same
upstream relation through row-preserving steps only (in PowerCenter: passive
transformations of one pipeline). Canonical IR has no "zip" of relations, so adapters
express such merges with several edges into one slot and call :func:`fuse_row_aligned`,
which rewrites them into plain canonical operations:

1. For a slot with several upstreams, walk up from each upstream through row-preserving
   operations (``derive``, ``project``, ``lookup`` with ``any``/``error``). Every walk
   must end at the same (operation, output group): the **anchor**. Otherwise the inputs
   are not row-aligned and fusion fails for that operation.
2. The row-preserving operations between the anchor and the merge (the **region**) are
   re-expressed, in dependency order, as a single chain over a *widened row*: each keeps
   every column seen so far and adds its own outputs under names ``<op>.<column>``.
3. Every edge that left a region operation now leaves the end of the chain, with column
   renames; the merge slot therefore has exactly one upstream.

Operation ids and expression ids are kept, so provenance and evidence stay attached.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from etlir.canonical.graph import output_columns, slot_inputs, topological_order
from etlir.canonical.model import (
    Assignment,
    CallNode,
    CastNode,
    Column,
    ColumnMapping,
    ColumnRefNode,
    DataEdge,
    Dataflow,
    Dataset,
    DataType,
    DeriveOp,
    ExpressionNode,
    LookupOp,
    Operation,
    OutputGroup,
)

Key = tuple[str, str]  # (operation id, output group)


class NotRowAligned(Exception):
    def __init__(self, op_id: str, reason: str) -> None:
        super().__init__(reason)
        self.op_id = op_id


def is_row_preserving(op: Operation) -> bool:
    kind = op.spec.kind
    if kind in ("derive", "project"):
        return True
    return isinstance(op.spec, LookupOp) and op.spec.on_multiple_match != "all"


def rename_columns(node: ExpressionNode, mapping: Mapping[str, str]) -> ExpressionNode:
    if isinstance(node, ColumnRefNode):
        return ColumnRefNode(name=mapping.get(node.name, node.name))
    if isinstance(node, CallNode):
        return node.model_copy(update={"args": [rename_columns(a, mapping) for a in node.args]})
    if isinstance(node, CastNode):
        return node.model_copy(update={"arg": rename_columns(node.arg, mapping)})
    return node


def _merge_slots(df: Dataflow) -> list[tuple[str, str]]:
    out = []
    for op in topological_order(df):
        for slot, ups in slot_inputs(df, op.id).items():
            if len(ups) > 1:
                out.append((op.id, slot))
    return out


@dataclass
class _Walk:
    boundaries: set[Key]
    region: set[str]


def _walk_up(df: Dataflow, ops: dict[str, Operation], start: Key) -> _Walk:
    boundaries: set[Key] = set()
    region: set[str] = set()
    stack = [start]
    while stack:
        op_id, group = stack.pop()
        op = ops[op_id]
        if not is_row_preserving(op):
            boundaries.add((op_id, group))
            continue
        if op_id in region:
            continue
        region.add(op_id)
        ins = slot_inputs(df, op_id).get("in", [])
        if not ins:
            boundaries.add((op_id, group))
            region.discard(op_id)
            continue
        stack.extend((u.from_operation, u.from_group) for u in ins)
    return _Walk(boundaries, region)


def fuse_row_aligned(
    df: Dataflow, datasets: dict[str, Dataset]
) -> tuple[Dataflow, list[NotRowAligned]]:
    """Fuse every row-aligned merge. Returns the new dataflow and the failures (merges
    whose inputs are not row-aligned); failed merges are left untouched for the caller."""
    failures: list[NotRowAligned] = []
    failed: set[str] = set()
    while True:
        pending = [(o, s) for o, s in _merge_slots(df) if o not in failed]
        if not pending:
            return df, failures
        op_id, slot = pending[0]
        try:
            df = _fuse_one(df, op_id, slot, datasets)
        except NotRowAligned as exc:
            failures.append(exc)
            failed.add(op_id)


def _fuse_one(df: Dataflow, merge_id: str, slot: str, datasets: dict[str, Dataset]) -> Dataflow:
    ops = {o.id: o for o in df.operations}
    ups = slot_inputs(df, merge_id)[slot]
    boundaries: set[Key] = set()
    region: set[str] = set()
    for u in ups:
        w = _walk_up(df, ops, (u.from_operation, u.from_group))
        boundaries |= w.boundaries
        region |= w.region
    if len(boundaries) != 1:
        names = sorted(f"{o.split(':')[-1]}#{g}" for o, g in boundaries)
        blocked = sorted(
            o.split(":")[-1] for o, _ in boundaries if ops[o].spec.kind == "unsupported"
        )
        if blocked:
            raise NotRowAligned(
                merge_id,
                f"row alignment cannot be established through unsupported upstream {blocked}",
            )
        raise NotRowAligned(merge_id, f"inputs are not row-aligned: they originate from {names}")
    ((anchor, anchor_group),) = boundaries
    if merge_id in region:
        raise NotRowAligned(merge_id, "merge operation lies inside its own region")

    order = [o for o in topological_order(df) if o.id in region]
    alias: dict[str, str] = {}
    for op_id in [anchor, *(o.id for o in order)]:
        base = op_id.split(":")[-1]
        name, n = base, 2
        while name in alias.values():
            name, n = f"{base}_{n}", n + 1
        alias[op_id] = name

    wide: dict[tuple[str, str, str], str] = {}
    wide_cols: list[Column] = []
    for c in output_columns(ops[anchor], anchor_group, datasets):
        name = f"{alias[anchor]}.{c.name}"
        wide[(anchor, anchor_group, c.name)] = name
        wide_cols.append(Column(name=name, type=c.type))
    first_edge = DataEdge(
        from_operation=anchor,
        from_group=anchor_group,
        to_operation=order[0].id,
        columns=[
            ColumnMapping(from_column=c.name, to_column=wide[(anchor, anchor_group, c.name)])
            for c in output_columns(ops[anchor], anchor_group, datasets)
        ],
    )

    exprs = {e.id: e for e in df.expressions}
    new_ops: dict[str, Operation] = {}
    chain_edges = [first_edge]
    prev: str | None = None
    for op in order:
        in_map: dict[str, str] = {}
        for u in slot_inputs(df, op.id).get("in", []):
            up_cols = output_columns(ops[u.from_operation], u.from_group, datasets)
            pairs = [(m.from_column, m.to_column) for m in u.mappings] or [
                (c.name, c.name) for c in up_cols
            ]
            for f, t in pairs:
                in_map[t] = wide[(u.from_operation, u.from_group, f)]
        declared: dict[str, DataType] = {c.name: c.type for g in op.outputs for c in g.columns}
        new_cols: list[Column] = []
        spec = op.spec
        if isinstance(spec, DeriveOp):
            assignments = []
            for a in spec.assignments:
                name = f"{alias[op.id]}.{a.column}"
                e = exprs[a.expression_id]
                exprs[e.id] = e.model_copy(update={"ast": rename_columns(e.ast, in_map)})
                assignments.append(Assignment(column=name, expression_id=e.id))
                wide[(op.id, "out", a.column)] = name
                new_cols.append(Column(name=name, type=declared[a.column]))
            assigned = {a.column for a in spec.assignments}
            for col_name in declared:
                if col_name not in assigned:
                    wide[(op.id, "out", col_name)] = in_map[col_name]
            spec = spec.model_copy(update={"assignments": assignments})
        elif isinstance(spec, LookupOp):
            returns = []
            for m in spec.returns:
                name = f"{alias[op.id]}.{m.to_column}"
                returns.append(ColumnMapping(from_column=m.from_column, to_column=name))
                wide[(op.id, "out", m.to_column)] = name
                new_cols.append(Column(name=name, type=declared[m.to_column]))
            returned = {m.to_column for m in spec.returns}
            for col_name in declared:
                if col_name not in returned:
                    wide[(op.id, "out", col_name)] = in_map[col_name]
            e = exprs[spec.condition_expression_id]
            exprs[e.id] = e.model_copy(update={"ast": rename_columns(e.ast, in_map)})
            spec = spec.model_copy(update={"returns": returns})
        else:  # project: pure pass-through
            for col_name in declared:
                wide[(op.id, "out", col_name)] = in_map[col_name]
            spec = DeriveOp(assignments=[])
        wide_cols = wide_cols + new_cols
        new_ops[op.id] = op.model_copy(
            update={"spec": spec, "outputs": [OutputGroup(columns=list(wide_cols))]}
        )
        if prev is not None:
            chain_edges.append(DataEdge(from_operation=prev, to_operation=op.id))
        prev = op.id
    end = prev
    assert end is not None

    # Reroute: edges leaving the region, and anchor edges into slots that also take region
    # edges, now leave the chain end. Edges into region operations are replaced.
    region_slots = {
        (e.to_operation, e.to_input)
        for e in df.edges
        if e.from_operation in region and e.to_operation not in region
    }
    kept: list[DataEdge] = []
    rerouted: dict[tuple[str, str], list[ColumnMapping]] = {}
    for edge in df.edges:
        if edge.to_operation in region:
            if edge.from_operation == anchor or edge.from_operation in region:
                continue
            kept.append(edge)  # e.g. a lookup's own lookup-source slot
            continue
        from_region = edge.from_operation in region
        from_anchor_into_merge = (
            edge.from_operation == anchor
            and edge.from_group == anchor_group
            and (edge.to_operation, edge.to_input) in region_slots
        )
        if not (from_region or from_anchor_into_merge):
            kept.append(edge)
            continue
        up_cols = output_columns(ops[edge.from_operation], edge.from_group, datasets)
        pairs = [(m.from_column, m.to_column) for m in edge.columns] or [
            (c.name, c.name) for c in up_cols
        ]
        maps = rerouted.setdefault((edge.to_operation, edge.to_input), [])
        maps += [
            ColumnMapping(from_column=wide[(edge.from_operation, edge.from_group, f)], to_column=t)
            for f, t in pairs
        ]
    for (to_op, to_slot), maps in sorted(rerouted.items()):
        kept.append(
            DataEdge(from_operation=end, to_operation=to_op, to_input=to_slot, columns=maps)
        )
    operations = [new_ops.get(o.id, o) for o in df.operations]
    return df.model_copy(
        update={
            "operations": operations,
            "edges": [*chain_edges, *kept],
            "expressions": [exprs[e.id] for e in df.expressions],
        }
    )
