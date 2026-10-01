"""Interface test doubles.

``ToyAdapter`` and ``PlanOnlyEmitter`` exist only to exercise the extension contracts.
They are NOT a second source or target platform and must not be reported as such.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from etlir.canonical.model import (
    Assignment,
    CallNode,
    CanonicalDocument,
    Column,
    ColumnRefNode,
    DataEdge,
    Dataflow,
    Dataset,
    DatasetKind,
    DataType,
    DeriveOp,
    Expression,
    FilterOp,
    LiteralNode,
    Operation,
    OutputGroup,
    Pipeline,
    ReadOp,
    SourceRef,
    Task,
    TaskKind,
    TypeKind,
    WriteOp,
)
from etlir.capabilities import CapabilityManifest, CapabilityRule, CapabilityState, analyze
from etlir.contracts import (
    EmitResult,
    InputArtifact,
    NormalizationResult,
    RawBundle,
    SourceAdapter,
    TargetEmitter,
)
from etlir.serialization import sha256_file, write_json

TYPES = {"string": TypeKind.STRING, "integer": TypeKind.INTEGER, "decimal": TypeKind.DECIMAL}


class ToyAdapter(SourceAdapter):
    """Reads '*.toy.json': one read -> filter(equals) -> derive(subtract) -> write flow."""

    id = "toy-json"
    version = "0.0.1"
    ir_version = "0.2.0"

    def accepts(self, path: Path) -> bool:
        return path.name.endswith(".toy.json")

    def load(self, inputs: Sequence[Path], root: Path) -> RawBundle:
        files = sorted(inputs, key=lambda p: p.relative_to(root).as_posix())
        return RawBundle(
            adapter=self.id,
            adapter_version=self.version,
            raw_ir_version="0.0.1",
            inputs=[
                InputArtifact(
                    path=p.relative_to(root).as_posix(),
                    sha256=sha256_file(p),
                    size=p.stat().st_size,
                )
                for p in files
            ],
            payload={
                "flows": {
                    p.relative_to(root).as_posix(): json.loads(p.read_text("utf-8")) for p in files
                }
            },
        )

    def normalize(self, raw: RawBundle) -> NormalizationResult:
        doc = CanonicalDocument()
        for artifact, flow in sorted(raw.payload["flows"].items()):
            doc = _merge(doc, self._flow(artifact, flow))
        return NormalizationResult(document=doc)

    def _flow(self, artifact: str, f: dict[str, Any]) -> CanonicalDocument:
        name = f["flow"]

        def ref(locator: str, rule: str) -> SourceRef:
            return SourceRef(adapter=self.id, artifact=artifact, locator=locator, rule=rule)

        cols = [Column(name=c, type=DataType(kind=TYPES[t])) for c, t in f["source"]["columns"]]
        src_ds, tgt_ds = f"ds.{f['source']['name']}", f"ds.{f['target']}"
        fcol, fval = f["filter"]["column"], f["filter"]["equals"]
        ((out_col, (fn, left, right)),) = f["derive"].items()
        derived = [*cols, Column(name=out_col, type=DataType(kind=TypeKind.DECIMAL))]
        return CanonicalDocument(
            datasets=[
                Dataset(
                    id=src_ds,
                    name=f["source"]["name"],
                    kind=DatasetKind.FILE,
                    columns=cols,
                    format="csv",
                    source=ref("/source", "toy.dataset"),
                ),
                Dataset(
                    id=tgt_ds,
                    name=f["target"],
                    kind=DatasetKind.FILE,
                    columns=derived,
                    format="csv",
                    source=ref("/target", "toy.dataset"),
                ),
            ],
            dataflows=[
                Dataflow(
                    id=f"df.{name}",
                    name=name,
                    source=ref("/", "toy.flow"),
                    expressions=[
                        Expression(
                            id=f"ex.{name}.filter",
                            ast=CallNode(
                                function="eq",
                                args=[
                                    ColumnRefNode(name=fcol),
                                    LiteralNode(value=fval, type=DataType(kind=TypeKind.STRING)),
                                ],
                            ),
                            source=ref("/filter", "toy.filter"),
                        ),
                        Expression(
                            id=f"ex.{name}.derive",
                            ast=CallNode(
                                function=fn,
                                args=[ColumnRefNode(name=left), ColumnRefNode(name=right)],
                            ),
                            source=ref(f"/derive/{out_col}", "toy.derive"),
                        ),
                    ],
                    operations=[
                        Operation(
                            id=f"op.{name}.read",
                            spec=ReadOp(dataset_id=src_ds),
                            source=ref("/source", "toy.read"),
                        ),
                        Operation(
                            id=f"op.{name}.filter",
                            spec=FilterOp(predicate_expression_id=f"ex.{name}.filter"),
                            outputs=[OutputGroup(columns=cols)],
                            source=ref("/filter", "toy.filter"),
                        ),
                        Operation(
                            id=f"op.{name}.derive",
                            spec=DeriveOp(
                                assignments=[
                                    Assignment(column=out_col, expression_id=f"ex.{name}.derive")
                                ]
                            ),
                            outputs=[OutputGroup(columns=derived)],
                            source=ref("/derive", "toy.derive"),
                        ),
                        Operation(
                            id=f"op.{name}.write",
                            spec=WriteOp(dataset_id=tgt_ds),
                            source=ref("/target", "toy.write"),
                        ),
                    ],
                    edges=[
                        DataEdge(
                            from_operation=f"op.{name}.read", to_operation=f"op.{name}.filter"
                        ),
                        DataEdge(
                            from_operation=f"op.{name}.filter", to_operation=f"op.{name}.derive"
                        ),
                        DataEdge(
                            from_operation=f"op.{name}.derive", to_operation=f"op.{name}.write"
                        ),
                    ],
                )
            ],
            pipelines=[
                Pipeline(
                    id=f"pl.{name}",
                    name=name,
                    source=ref("/", "toy.pipeline"),
                    tasks=[
                        Task(
                            id=f"t.{name}",
                            name=name,
                            kind=TaskKind.DATAFLOW,
                            dataflow_id=f"df.{name}",
                            source=ref("/", "toy.task"),
                        )
                    ],
                )
            ],
        )


def _merge(a: CanonicalDocument, b: CanonicalDocument) -> CanonicalDocument:
    return CanonicalDocument(
        pipelines=a.pipelines + b.pipelines,
        dataflows=a.dataflows + b.dataflows,
        datasets=a.datasets + b.datasets,
        parameters=a.parameters + b.parameters,
        bindings=a.bindings + b.bindings,
        lineage=a.lineage + b.lineage,
    )


class PlanOnlyEmitter(TargetEmitter):
    """Writes the plan as JSON and nothing else."""

    id = "plan-only"
    version = "0.0.1"
    ir_versions = ">=0.2.0,<0.3"

    def __init__(self, rules: list[CapabilityRule] | None = None) -> None:
        self._rules = (
            rules
            if rules is not None
            else [
                CapabilityRule(construct_id=c, state=CapabilityState.SUPPORTED)
                for c in (
                    "operation.read",
                    "operation.filter",
                    "operation.derive",
                    "operation.write",
                    "write.append",
                    "task.dataflow",
                    "dependency.success",
                    "function.eq",
                    "function.subtract",
                )
            ]
        )

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            target=self.id,
            target_version=self.version,
            ir_versions=self.ir_versions,
            rules=self._rules,
        )

    def plan(self, document: CanonicalDocument) -> dict[str, Any]:
        report = analyze(document, self.capabilities())
        return {
            "blocked_tasks": report.blocked_task_ids,
            "diagnostics": [d.model_dump(mode="json") for d in report.diagnostics],
            "tasks": sorted(t.id for p in document.pipelines for t in p.tasks),
        }

    def write(self, plan: dict[str, Any], out_dir: Path) -> EmitResult:
        write_json(out_dir / "target_plan.json", plan)
        return EmitResult(artifacts=["target_plan.json"])
