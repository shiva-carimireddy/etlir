"""Output comparator: actual target outputs vs independently authored expectations.

Policy (fixed; recorded in every comparison result):

* rows are compared as a multiset (order ignored, duplicates counted);
* each value is normalized by the target dataset's declared column type: decimals
  compare by exact numeric value (``1.50 == 1.5``) and must not exceed the declared
  scale; integers must be integral; timestamps compare as UTC instants; strings compare
  exactly (no trimming, case-sensitive); NULL equals NULL;
* doubles are compared after rounding to 12 significant digits.

Agreement means *specified-behavior agreement*, not equivalence with the source platform.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from etlir.canonical.model import CanonicalDocument, Column, DataType, TypeKind

POLICY = {
    "ordering": "multiset",
    "null": "NULL equals NULL",
    "decimal": "exact value; scale must not exceed the declared scale",
    "double": "12 significant digits",
    "timestamp": "UTC instant, microsecond precision",
    "string": "exact, case-sensitive, no trimming",
}


class Unparseable(ValueError):
    pass


def normalize(value: Any, t: DataType) -> Any:
    if value is None:
        return None
    k = t.kind
    try:
        if k is TypeKind.DECIMAL:
            d = Decimal(str(value))
            if t.scale is not None and d != d.quantize(Decimal(1).scaleb(-t.scale)):
                raise Unparseable(f"{value} exceeds scale {t.scale}")
            return d.normalize() if d != 0 else Decimal(0)
        if k in (TypeKind.INTEGER, TypeKind.BIGINT):
            d = Decimal(str(value))
            if d != d.to_integral_value():
                raise Unparseable(f"{value} is not integral")
            return int(d)
        if k is TypeKind.DOUBLE:
            return float(f"{float(value):.12g}")
        if k is TypeKind.BOOLEAN:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1")
        if k in (TypeKind.TIMESTAMP, TypeKind.DATE):
            ts = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if ts.tzinfo is not None:
                ts = ts.astimezone(dt.UTC).replace(tzinfo=None)
            return ts.isoformat()
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, Unparseable):
            raise
        raise Unparseable(f"cannot read {value!r} as {k.value}") from exc
    return str(value)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    files = (
        [path]
        if path.is_file()
        else sorted(
            p
            for p in path.glob("*")
            if p.is_file() and p.suffix in (".json", ".jsonl") and not p.name.startswith((".", "_"))
        )
    )
    rows: list[dict[str, Any]] = []
    for f in files:
        for line in f.read_text("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _key(row: dict[str, Any], columns: list[Column]) -> tuple[Any, ...]:
    missing = [c.name for c in columns if c.name not in row]
    if missing:
        raise Unparseable(f"row lacks columns {missing}")
    return tuple(normalize(row[c.name], c.type) for c in columns)


def compare_dataset(actual: Path, expected: Path, columns: list[Column]) -> dict[str, Any]:
    result: dict[str, Any] = {"expected": str(expected.name)}
    if not actual.exists():
        return {**result, "status": "missing_output"}
    try:
        exp = Counter(_key(r, columns) for r in read_jsonl(expected))
        act = Counter(_key(r, columns) for r in read_jsonl(actual))
    except Unparseable as exc:
        return {**result, "status": "unreadable", "error": str(exc)}
    missing, extra = exp - act, act - exp

    def show(c: Counter[tuple[Any, ...]]) -> list[dict[str, Any]]:
        return [
            {
                "row": {
                    col.name: (str(v) if v is not None else None)
                    for col, v in zip(columns, row, strict=True)
                },
                "count": n,
            }
            for row, n in sorted(c.items(), key=lambda kv: repr(kv[0]))[:20]
        ]

    return {
        **result,
        "status": "match" if not missing and not extra else "mismatch",
        "expected_rows": sum(exp.values()),
        "actual_rows": sum(act.values()),
        "missing": show(missing),
        "unexpected": show(extra),
    }


def compare_outputs(
    doc: CanonicalDocument,
    bindings: dict[str, Any],
    bindings_file: Path,
    expectations: dict[str, Path],
) -> dict[str, Any]:
    """``expectations`` maps binding id -> expected JSONL file."""
    datasets = {d.binding_id or d.id: d for d in doc.datasets}
    outputs: dict[str, Any] = {}
    for binding_id, exp_path in sorted(expectations.items()):
        ds = datasets.get(binding_id)
        if ds is None or binding_id not in bindings:
            outputs[binding_id] = {"status": "unknown_binding"}
            continue
        p = Path(bindings[binding_id]["path"])
        actual = p if p.is_absolute() else (bindings_file.parent / p)
        outputs[binding_id] = compare_dataset(actual, exp_path, list(ds.columns))
    ok = bool(outputs) and all(o["status"] == "match" for o in outputs.values())
    return {"policy": POLICY, "status": "agree" if ok else "disagree", "outputs": outputs}
