"""The ETLIR translation pipeline: inventory -> Raw IR -> Canonical IR -> validation ->
capability analysis -> target packages, with every stage written as an artifact.

Conversion output is deterministic: identical inputs and tool versions give
byte-identical files (no timestamps; volatile run data lives in execution.json only).
"""

from __future__ import annotations

import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from etlir import __version__
from etlir.canonical.invariants import validate
from etlir.canonical.model import IR_VERSION, CanonicalDocument, OpaqueNode
from etlir.capabilities import CapabilityState, analyze
from etlir.contracts import SourceAdapter, TargetEmitter
from etlir.evidence import Severity
from etlir.registry import Registry
from etlir.serialization import write_json


class ConversionError(Exception):
    pass


def discover(paths: list[Path], adapter: SourceAdapter) -> tuple[list[Path], Path]:
    """Accepted input files and their common root (all files of one corpus group)."""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += [
                f
                for f in sorted(p.rglob("*"))
                if f.is_file() and ".git" not in f.parts and adapter.accepts(f)
            ]
        elif p.is_file():
            files.append(p)
        else:
            raise ConversionError(f"input not found: {p}")
    if not files:
        raise ConversionError("no input files accepted by the source adapter")
    resolved = [f.resolve() for f in files]
    root = Path(*Path(resolved[0]).parts[: _common(resolved)])
    if root.is_file():
        root = root.parent
    return sorted(set(resolved)), root


def _common(paths: list[Path]) -> int:
    parts = [p.parent.parts for p in paths]
    n = 0
    for items in zip(*parts, strict=False):
        if len(set(items)) != 1:
            break
        n += 1
    return n


def pick_source(reg: Registry, source: str | None, paths: list[Path]) -> SourceAdapter:
    if source:
        if source not in reg.sources:
            raise ConversionError(f"unknown source adapter '{source}'; see `etlir plugins`")
        return reg.sources[source]()
    for cls in reg.sources.values():
        adapter = cls()
        probe = [
            f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*"))) if f.is_file()
        ][:200]
        if any(adapter.accepts(f) for f in probe):
            return adapter
    raise ConversionError("no installed source adapter accepts these inputs")


def convert(
    paths: list[Path],
    out: Path,
    *,
    source: str | None = None,
    targets: list[str] | None = None,
    registry: Registry | None = None,
) -> dict[str, Any]:
    reg = registry or Registry.discover()
    adapter = pick_source(reg, source, paths)
    files, root = discover(paths, adapter)
    target_ids = targets if targets is not None else sorted(reg.targets)
    unknown = [t for t in target_ids if t not in reg.targets]
    if unknown:
        raise ConversionError(f"unknown target(s) {unknown}; see `etlir plugins`")
    emitters: list[TargetEmitter] = [reg.targets[t]() for t in target_ids]

    out.mkdir(parents=True, exist_ok=True)
    raw = adapter.load(files, root)
    inventory = adapter.inventory(raw) if hasattr(adapter, "inventory") else None
    result = adapter.normalize(raw)
    doc = result.document
    ir_diags = validate(doc)
    diagnostics = [*result.diagnostics, *ir_diags]

    write_json(
        out / "run_manifest.json",
        {
            "tool": "etlir",
            "etlir_version": __version__,
            "canonical_ir_version": IR_VERSION,
            "source": {
                "adapter": adapter.id,
                "version": adapter.version,
                "raw_ir_version": raw.raw_ir_version,
            },
            "targets": {e.id: e.version for e in emitters},
            "environment": {"python": platform.python_version(), "platform": sys.platform},
            "inputs": [a.model_dump() for a in raw.inputs],
        },
    )
    if inventory is not None:
        write_json(out / "inventory.json", inventory)
    write_json(out / "raw_ir.json", raw)
    write_json(out / "canonical_ir.json", doc)
    write_json(
        out / "validation.json",
        {
            "status": "invalid"
            if any(d.severity is Severity.ERROR and d.code.startswith("IR-") for d in ir_diags)
            else "valid",
            "counts": dict(sorted(Counter(d.code for d in diagnostics).items())),
            "diagnostics": [d.model_dump(mode="json", exclude_none=True) for d in diagnostics],
        },
    )

    evidence: dict[str, Any] = {
        "source": [r.model_dump(mode="json", exclude_none=True) for r in result.evidence],
        "targets": {},
    }
    target_summaries: dict[str, Any] = {}
    for emitter in emitters:
        plan = emitter.plan(doc)
        emitted = emitter.write(plan, out / "targets" / emitter.id)
        evidence["targets"][emitter.id] = [
            r.model_dump(mode="json", exclude_none=True) for r in emitted.evidence
        ]
        target_summaries[emitter.id] = _target_summary(doc, emitter, plan)
    write_json(out / "evidence.json", evidence)
    summary = {
        "etlir_version": __version__,
        "source": adapter.id,
        "inputs": {
            "files": len(files),
            "accepted": len(raw.payload.get("files", raw.inputs)),
            "rejected": len([d for d in raw.diagnostics]),
        },
        "references": {
            k: {"resolved": v["resolved"], "required": v["required"]}
            for k, v in (inventory or {}).get("references", {}).items()
        },
        "canonical": _canonical_summary(doc),
        "diagnostics": dict(sorted(Counter(d.code for d in diagnostics).items())),
        "targets": target_summaries,
    }
    write_json(out / "summary.json", summary)
    from etlir.report import write_report

    write_report(out)
    return summary


def _canonical_summary(doc: CanonicalDocument) -> dict[str, Any]:
    ops = [o for df in doc.dataflows for o in df.operations]
    exprs = [e for df in doc.dataflows for e in df.expressions]
    tasks = [t for p in doc.pipelines for t in p.tasks]
    return {
        "pipelines": len(doc.pipelines),
        "tasks": {
            "total": len(tasks),
            "by_kind": dict(sorted(Counter(t.kind.value for t in tasks).items())),
        },
        "dataflows": len(doc.dataflows),
        "datasets": len(doc.datasets),
        "parameters": len(doc.parameters),
        "operations": {
            "total": len(ops),
            "mapped": sum(1 for o in ops if o.spec.kind != "unsupported"),
            "by_kind": dict(sorted(Counter(o.spec.kind for o in ops).items())),
            "unsupported_by_native_kind": dict(
                sorted(
                    Counter(o.spec.native_kind for o in ops if o.spec.kind == "unsupported").items()
                )
            ),
        },
        "expressions": {
            "total": len(exprs),
            "parsed": sum(1 for e in exprs if not isinstance(e.ast, OpaqueNode)),
        },
    }


def _target_summary(
    doc: CanonicalDocument, emitter: TargetEmitter, plan: dict[str, Any]
) -> dict[str, Any]:
    decisions = analyze(doc, emitter.capabilities()).decisions
    states = Counter(d.state.value for d in decisions)
    jobs = plan.get("jobs", [])
    emitted = {j["dataflow_id"]: j for j in jobs if j["status"] == "emitted"}
    ops_all = [o for df in doc.dataflows for o in df.operations]
    ops_emitted = [o for df in doc.dataflows if df.id in emitted for o in df.operations]
    traced = sum(
        1
        for df in doc.dataflows
        if df.id in emitted
        for o in df.operations
        if o.id in emitted[df.id].get("op_lines", {})
    )
    workflow = plan.get("workflow", {})
    pipes = Counter(p["status"] for p in workflow.get("pipelines", []))
    tasks = [t for p in workflow.get("pipelines", []) for t in p["tasks"]]
    return {
        "version": emitter.version,
        "construct_decisions": {s.value: states.get(s.value, 0) for s in CapabilityState},
        "dataflows": {"emitted": len(emitted), "total": len(doc.dataflows)},
        "operations_in_emitted_dataflows": {"count": len(ops_emitted), "of": len(ops_all)},
        "traceability": {"traced": traced, "emitted_operations": len(ops_emitted)},
        "tasks": {
            "runnable": sum(1 for t in tasks if t["status"] == "runnable"),
            "total": len(tasks),
        },
        "pipelines": {k: pipes.get(k, 0) for k in ("runnable", "partially-blocked", "blocked")},
    }
