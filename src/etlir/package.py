"""Writing a target package from an emitter plan (layout shared by all in-tree emitters).

A plan dict holds ``files`` (relative path -> text), ``jobs``, ``workflow``,
``bindings_example``, ``parameters_example`` and ``diagnostics``. Everything except
``files`` is also written as ``target_plan.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etlir.canonical.model import CanonicalDocument
from etlir.contracts import EmitResult
from etlir.evidence import Diagnostic, EvidenceRecord, EvidenceStatus, TargetLocation
from etlir.serialization import dumps


def bindings_example(doc: CanonicalDocument, emitted: set[str]) -> dict[str, Any]:
    """Example bindings for every dataset read or written by an emitted dataflow."""
    datasets = {d.id: d for d in doc.datasets}
    out: dict[str, Any] = {}
    for df in doc.dataflows:
        if df.id not in emitted:
            continue
        for op in df.operations:
            ds_id = getattr(op.spec, "dataset_id", None)
            if ds_id not in datasets:
                continue
            ds = datasets[ds_id]
            is_read = op.spec.kind == "read"
            out[ds.binding_id or ds.id] = {
                "dataset": ds.id,
                "format": "csv" if is_read else "jsonl",
                "path": f"data/in/{ds.name}.csv" if is_read else f"data/out/{ds.name}",
            }
    return dict(sorted(out.items()))


def write_package(plan: dict[str, Any], out_dir: Path) -> EmitResult:
    written: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    def put(rel: str, text: str) -> None:
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        written.append(rel)

    for rel, text in sorted(plan["files"].items()):
        put(rel, text)
    put("target_plan.json", dumps({k: v for k, v in plan.items() if k != "files"}))
    put("workflow_plan.json", dumps(plan["workflow"]))
    put("bindings.example.json", dumps(plan["bindings_example"]))
    put("parameters.example.json", dumps(plan["parameters_example"]))

    target = plan["target"]
    evidence: list[EvidenceRecord] = []
    for job in plan["jobs"]:
        if job["status"] != "emitted":
            evidence.append(
                EvidenceRecord(
                    subject_id=job["dataflow_id"],
                    status=EvidenceStatus.BLOCKED,
                    rule=f"{target}.preflight",
                )
            )
            continue
        evidence.append(
            EvidenceRecord(
                subject_id=job["dataflow_id"],
                status=EvidenceStatus.TRANSFORMED_EQUIVALENT,
                rule=f"{target}.job",
                targets=[TargetLocation(artifact=job["module"])],
            )
        )
        for op_id, line in sorted(job["op_lines"].items()):
            evidence.append(
                EvidenceRecord(
                    subject_id=op_id,
                    status=EvidenceStatus.TRANSFORMED_EQUIVALENT,
                    rule=f"{target}.op",
                    targets=[TargetLocation(artifact=job["module"], locator=f"L{line}")],
                )
            )
    return EmitResult(
        artifacts=sorted(written),
        diagnostics=[Diagnostic.model_validate(d) for d in plan["diagnostics"]],
        evidence=evidence,
    )
