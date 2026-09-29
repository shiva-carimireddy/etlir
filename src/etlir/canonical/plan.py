"""Target-neutral lowering plan shared by emitters.

For one dataflow, :func:`lower_dataflow` resolves operations in topological order into
steps with explicit input relations (slot -> upstream relation + column renames), the
declared output columns, and resolved expressions. Emitters render these steps; they do
not re-derive graph semantics, which keeps different targets consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from etlir.canonical.graph import output_columns, slot_inputs, topological_order
from etlir.canonical.model import (
    CanonicalDocument,
    Column,
    Dataflow,
    Dataset,
    Expression,
    OutputGroup,
)


def relation_name(op_id: str, group: str) -> str:
    return f"{op_id}#{group}"


@dataclass(frozen=True)
class SlotRef:
    relation: str  # upstream relation name
    renames: tuple[tuple[str, str], ...]  # (from_column, to_column); empty: all by name
    columns: tuple[Column, ...]  # resulting input columns in order


@dataclass
class Step:
    op_id: str
    kind: str
    spec: object
    inputs: dict[str, SlotRef]
    outputs: list[OutputGroup]
    expressions: dict[str, Expression] = field(default_factory=dict)

    def output_relation(self, group: str = "out") -> str:
        return relation_name(self.op_id, group)


def lower_dataflow(doc: CanonicalDocument, df: Dataflow) -> list[Step]:
    datasets: dict[str, Dataset] = {d.id: d for d in doc.datasets}
    ops = {o.id: o for o in df.operations}
    exprs = {e.id: e for e in df.expressions}
    steps: list[Step] = []
    for op in topological_order(df):
        inputs: dict[str, SlotRef] = {}
        for slot, ups in slot_inputs(df, op.id).items():
            up = ups[0]
            upstream = ops[up.from_operation]
            upcols = {c.name: c for c in output_columns(upstream, up.from_group, datasets)}
            if up.mappings:
                renames = tuple((m.from_column, m.to_column) for m in up.mappings)
                cols = tuple(Column(name=t, type=upcols[f].type) for f, t in renames if f in upcols)
            else:
                renames = ()
                cols = tuple(upcols.values())
            inputs[slot] = SlotRef(relation_name(up.from_operation, up.from_group), renames, cols)
        outputs = list(op.outputs)
        if op.spec.kind == "read" and not outputs:
            ds = datasets[op.spec.dataset_id]
            outputs = [OutputGroup(columns=list(ds.columns))]
        used = {
            v
            for attr in ("predicate_expression_id", "condition_expression_id")
            if (v := getattr(op.spec, attr, None)) is not None
        }
        used |= {a.expression_id for a in getattr(op.spec, "assignments", [])}
        used |= {a.expression_id for a in getattr(op.spec, "aggregations", [])}
        used |= {g.predicate_expression_id for g in getattr(op.spec, "groups", [])}
        steps.append(
            Step(
                op.id,
                op.spec.kind,
                op.spec,
                inputs,
                outputs,
                {i: exprs[i] for i in sorted(used) if i in exprs},
            )
        )
    return steps
