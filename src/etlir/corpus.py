"""Measure a pinned corpus (benchmarks/corpus.toml): verify every file's digest, convert
each group, and aggregate the per-group summaries into ``corpus.json``.

Every rate is a numerator and a denominator. Blocker reasons are ranked with object names
removed, so a reason reads as a construct ("transformation type '…' is not supported")
rather than as one export's port. The output holds no timestamps or absolute paths.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from etlir import __version__
from etlir.canonical.invariants import walk_expression
from etlir.canonical.model import IR_VERSION, CanonicalDocument, OpaqueNode
from etlir.serialization import write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_reason(reason: str) -> str:
    """Drop object names from a blocker reason so equal causes count together."""
    while reason.startswith("depends on variable port ") or reason.startswith(
        "depends on variable "
    ):
        _, _, reason = reason.partition(": ")
    reason = re.sub(r"'[^']*'", "'<name>'", reason)
    reason = re.sub(r"\[[^\]]*\]", "[<names>]", reason)
    reason = re.sub(
        r"\b(port|variable port|parameter|table) [A-Za-z_$#@][\w$#@.]*", r"\1 <name>", reason
    )
    return reason.strip()


def _blockers(doc: CanonicalDocument) -> tuple[Counter[str], Counter[str]]:
    operations: Counter[str] = Counter()
    expressions: Counter[str] = Counter()
    for df in doc.dataflows:
        for op in df.operations:
            if op.spec.kind == "unsupported":
                operations[f"{op.spec.native_kind}: {normalize_reason(op.spec.reason)}"] += 1
        for ex in df.expressions:
            for node in walk_expression(ex.ast):
                if isinstance(node, OpaqueNode):
                    expressions[normalize_reason(node.reason or "")] += 1
                    break
    return operations, expressions


def _ranked(counts: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"reason": r, "count": n} for r, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _ratio(count: int, of: int) -> dict[str, int]:
    return {"count": count, "of": of}


def evaluate_corpus(
    manifest: Path, root: Path, out: Path, targets: list[str] | None = None
) -> dict[str, Any]:
    from etlir.pipeline import convert

    spec = tomllib.loads(manifest.read_text("utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    op_blockers: Counter[str] = Counter()
    ex_blockers: Counter[str] = Counter()
    for group in sorted(spec.get("group", []), key=lambda g: g["id"]):
        gdir = root / group["id"]
        files = group.get("files", [])
        verified = sum(
            1
            for f in files
            if (gdir / f["path"]).is_file() and _sha256(gdir / f["path"]) == f["sha256"]
        )
        row: dict[str, Any] = {
            "group": group["id"],
            "license": group.get("license", ""),
            "files_verified": _ratio(verified, len(files)),
        }
        if not files:  # e.g. the synthetic cases, measured by `etlir benchmark`
            row["status"] = "no-pinned-files"
            rows.append(row)
            continue
        if verified != len(files):
            row["status"] = "not-verified"
            rows.append(row)
            continue
        summary = convert([gdir / f["path"] for f in files], out / group["id"], targets=targets)
        doc = CanonicalDocument.model_validate_json(
            (out / group["id"] / "canonical_ir.json").read_text("utf-8")
        )
        ops, exprs = _blockers(doc)
        op_blockers.update(ops)
        ex_blockers.update(exprs)
        canon = summary["canonical"]
        row.update(
            {
                "status": "converted",
                "inputs_accepted": _ratio(
                    summary["inputs"]["accepted"], summary["inputs"]["files"]
                ),
                "operations_mapped": _ratio(
                    canon["operations"]["mapped"], canon["operations"]["total"]
                ),
                "expressions_parsed": _ratio(
                    canon["expressions"]["parsed"], canon["expressions"]["total"]
                ),
                "targets": {
                    tid: {
                        "dataflows_emitted": _ratio(
                            t["dataflows"]["emitted"], t["dataflows"]["total"]
                        ),
                        "tasks_runnable": _ratio(t["tasks"]["runnable"], t["tasks"]["total"]),
                    }
                    for tid, t in sorted(summary["targets"].items())
                },
            }
        )
        rows.append(row)

    def total(path: list[str]) -> dict[str, int]:
        count = of = 0
        for r in rows:
            node: Any = r
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if node:
                count, of = count + node["count"], of + node["of"]
        return _ratio(count, of)

    target_ids = sorted({t for r in rows for t in r.get("targets", {})})
    result = {
        "etlir_version": __version__,
        "ir_version": IR_VERSION,
        "manifest_sha256": _sha256(manifest),
        "groups": rows,
        "totals": {
            "files_verified": total(["files_verified"]),
            "inputs_accepted": total(["inputs_accepted"]),
            "operations_mapped": total(["operations_mapped"]),
            "expressions_parsed": total(["expressions_parsed"]),
            "targets": {
                t: {
                    "dataflows_emitted": total(["targets", t, "dataflows_emitted"]),
                    "tasks_runnable": total(["targets", t, "tasks_runnable"]),
                }
                for t in target_ids
            },
        },
        "blockers": {
            "unsupported_operations": _ranked(op_blockers),
            "opaque_expressions": _ranked(ex_blockers),
        },
    }
    write_json(out / "corpus.json", result)
    return result


__all__ = ["evaluate_corpus", "normalize_reason"]
