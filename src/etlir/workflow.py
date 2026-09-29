"""Target-neutral workflow plan built from Canonical IR and a capability report.

The plan lists, per pipeline, every task with its dependencies, whether it is runnable
or blocked (with reasons), and a structured launch command for runnable dataflow tasks.
It is executed by :mod:`etlir.runner`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from etlir.canonical.model import CanonicalDocument, Task
from etlir.capabilities import CapabilityReport, CapabilityState

PLAN_VERSION = 1

Command = dict[str, Any]


def blocked_reasons(report: CapabilityReport) -> dict[str, list[str]]:
    """Per subject id, the constructs that are blocked for it."""
    out: dict[str, list[str]] = {}
    for d in report.decisions:
        if d.state is CapabilityState.BLOCKED:
            out.setdefault(d.subject_id, []).append(d.construct_id)
    return {k: sorted(set(v)) for k, v in out.items()}


def build_workflow_plan(
    doc: CanonicalDocument,
    report: CapabilityReport,
    extra_blocked: Mapping[str, list[str]],
    commands: Mapping[str, Command],
    target: str,
) -> dict[str, Any]:
    """``commands`` maps dataflow id -> launch command for every emitted job."""
    reasons = blocked_reasons(report)
    dataflow_of: dict[str, set[str]] = {}
    for df in doc.dataflows:
        subjects = {df.id} | {o.id for o in df.operations} | {e.id for e in df.expressions}
        dataflow_of[df.id] = subjects
    blocked = set(report.blocked_task_ids)

    def task_reasons(t: Task) -> list[str]:
        r = list(reasons.get(t.id, []))
        if t.dataflow_id:
            for sid in sorted(dataflow_of.get(t.dataflow_id, set())):
                r += [f"{sid}: {c}" for c in reasons.get(sid, [])]
            r += [f"{t.dataflow_id}: {m}" for m in extra_blocked.get(t.dataflow_id, [])]
        if t.unsupported_reason:
            r.append(t.unsupported_reason)
        upstream = [d.task_id for d in t.depends_on if d.task_id in blocked]
        if upstream and not r:
            r.append(f"depends on blocked task(s) {sorted(upstream)}")
        return r

    pipelines = []
    for p in doc.pipelines:
        tasks = []
        for t in p.tasks:
            is_blocked = t.id in blocked or (
                t.dataflow_id is not None and t.dataflow_id not in commands
            )
            tasks.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "kind": t.kind.value,
                    "dataflow_id": t.dataflow_id,
                    "status": "blocked" if is_blocked else "runnable",
                    "reasons": task_reasons(t) if is_blocked else [],
                    "command": None
                    if is_blocked or t.dataflow_id is None
                    else commands[t.dataflow_id],
                    "depends_on": [
                        {"task_id": d.task_id, "condition": d.condition.value} for d in t.depends_on
                    ],
                    "trigger": t.trigger,
                }
            )
        n_blocked = sum(1 for t in tasks if t["status"] == "blocked")
        pipelines.append(
            {
                "id": p.id,
                "name": p.name,
                "status": "runnable"
                if n_blocked == 0
                else "blocked"
                if n_blocked == len(tasks)
                else "partially-blocked",
                "tasks": tasks,
            }
        )
    in_pipelines = {t.dataflow_id for p in doc.pipelines for t in p.tasks if t.dataflow_id}
    standalone = [
        {"dataflow_id": df_id, "command": cmd}
        for df_id, cmd in sorted(commands.items())
        if df_id not in in_pipelines
    ]
    return {
        "plan_version": PLAN_VERSION,
        "target": target,
        "pipelines": pipelines,
        "standalone_jobs": standalone,
    }


def job_name(dataflow_id: str, taken: set[str], clean: Callable[[str], str]) -> str:
    """Short, unique, filesystem-safe module name for a dataflow."""
    base = clean(dataflow_id.split(":")[-1]) or "job"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name
