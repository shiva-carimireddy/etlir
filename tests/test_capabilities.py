from __future__ import annotations

from etlir.canonical.model import CanonicalDocument, Dependency, Pipeline, SourceRef, Task, TaskKind
from etlir.capabilities import CapabilityManifest, CapabilityRule, CapabilityState, analyze

SRC = SourceRef(adapter="test", artifact="a", locator="/")


def manifest(*constructs: str) -> CapabilityManifest:
    return CapabilityManifest(
        target="t",
        target_version="0",
        ir_versions=">=0.1",
        rules=[CapabilityRule(construct_id=c, state=CapabilityState.SUPPORTED) for c in constructs],
    )


def test_undeclared_construct_is_blocked(toy_doc: CanonicalDocument) -> None:
    report = analyze(toy_doc, manifest("operation.read", "operation.write", "task.dataflow"))
    assert report.blocked_task_ids == ["t.orders"]
    blocked = {d.construct_id for d in report.decisions if d.state is CapabilityState.BLOCKED}
    assert blocked == {"operation.filter", "operation.derive", "function.eq", "function.subtract"}
    assert all(d.code == "CAP-B-001" and d.source is not None for d in report.diagnostics)


def test_blocking_propagates_downstream_only() -> None:
    def task(i: str, kind: TaskKind, *deps: str) -> Task:
        return Task(
            id=i, name=i, kind=kind, depends_on=[Dependency(task_id=d) for d in deps], source=SRC
        )

    doc = CanonicalDocument(
        pipelines=[
            Pipeline(
                id="p",
                name="p",
                source=SRC,
                tasks=[
                    task("a", TaskKind.ASSIGNMENT),
                    task("b", TaskKind.UNSUPPORTED, "a"),
                    task("c", TaskKind.ASSIGNMENT, "b"),
                    task("d", TaskKind.ASSIGNMENT, "a"),
                ],
            )
        ]
    )
    report = analyze(doc, manifest("task.assignment", "dependency.success"))
    assert report.blocked_task_ids == ["b", "c"]


def test_full_support_blocks_nothing(toy_doc: CanonicalDocument) -> None:
    report = analyze(
        toy_doc,
        manifest(
            "operation.read",
            "operation.filter",
            "operation.derive",
            "operation.write",
            "task.dataflow",
            "function.eq",
            "function.subtract",
        ),
    )
    assert report.blocked_task_ids == []
    assert report.diagnostics == []
