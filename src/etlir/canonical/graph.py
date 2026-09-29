"""Target-neutral helpers over a dataflow graph, shared by validators and emitters."""

from __future__ import annotations

from dataclasses import dataclass

from etlir.canonical.model import Column, ColumnMapping, Dataflow, Dataset, Operation


@dataclass(frozen=True)
class SlotInput:
    """The relation arriving at one input slot of an operation."""

    slot: str
    from_operation: str
    from_group: str
    mappings: tuple[ColumnMapping, ...]  # empty: pass every upstream column by name


def topological_order(df: Dataflow) -> list[Operation]:
    """Deterministic topological order (ties broken by declaration order).

    Raises ValueError if the graph has a cycle; validate the document first.
    """
    position = {op.id: i for i, op in enumerate(df.operations)}
    indegree = {op.id: 0 for op in df.operations}
    succ: dict[str, list[str]] = {op.id: [] for op in df.operations}
    for e in df.edges:
        if e.from_operation in succ and e.to_operation in indegree:
            succ[e.from_operation].append(e.to_operation)
            indegree[e.to_operation] += 1
    ready = sorted((i for i, d in indegree.items() if d == 0), key=position.__getitem__)
    ops = {op.id: op for op in df.operations}
    out: list[Operation] = []
    while ready:
        n = ready.pop(0)
        out.append(ops[n])
        for m in succ[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)
                ready.sort(key=position.__getitem__)
    if len(out) != len(ops):
        raise ValueError(f"dataflow {df.id} has a cycle")
    return out


def slot_inputs(df: Dataflow, op_id: str) -> dict[str, list[SlotInput]]:
    """Incoming edges grouped by slot, one entry per distinct upstream (op, group)."""
    grouped: dict[str, dict[tuple[str, str], list[ColumnMapping]]] = {}
    for e in df.edges:
        if e.to_operation != op_id:
            continue
        per_slot = grouped.setdefault(e.to_input, {})
        per_slot.setdefault((e.from_operation, e.from_group), []).extend(e.columns)
    return {
        slot: [SlotInput(slot, op, grp, tuple(maps)) for (op, grp), maps in ups.items()]
        for slot, ups in sorted(grouped.items())
    }


def output_columns(op: Operation, group: str, datasets: dict[str, Dataset]) -> list[Column]:
    """Columns of an operation's output group (reads default to the dataset's columns)."""
    for g in op.outputs:
        if g.name == group:
            return list(g.columns)
    ds_id = getattr(op.spec, "dataset_id", None)
    if op.spec.kind == "read" and ds_id in datasets:
        return list(datasets[ds_id].columns)
    return []


def slot_columns(df: Dataflow, op_id: str, datasets: dict[str, Dataset]) -> dict[str, list[Column]]:
    """Input columns per slot after applying edge column mappings."""
    ops = {o.id: o for o in df.operations}
    out: dict[str, list[Column]] = {}
    for slot, ups in slot_inputs(df, op_id).items():
        cols: list[Column] = []
        for up in ups:
            upstream = ops.get(up.from_operation)
            if upstream is None:
                continue
            upcols = {c.name: c for c in output_columns(upstream, up.from_group, datasets)}
            if not up.mappings:
                cols.extend(upcols.values())
                continue
            for m in up.mappings:
                src = upcols.get(m.from_column)
                if src is not None:
                    cols.append(Column(name=m.to_column, type=src.type))
        out[slot] = cols
    return out
