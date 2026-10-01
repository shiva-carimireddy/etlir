"""Render the evaluation tables from a results directory (benchmarks/protocol.md).

    python scripts/paper_tables.py results/v0.2.0 > results/v0.2.0/tables.md

Reads ``corpus/corpus.json`` (``etlir corpus``) and every ``benchmark-*/results.json``
(``etlir benchmark``) under the directory. Every number printed is copied from those files
as a numerator and a denominator; nothing is computed except sums already defined there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def ratio(r: dict[str, int] | None) -> str:
    if not r:
        return "-"
    pct = f" ({100 * r['count'] / r['of']:.1f}%)" if r["of"] else ""
    return f"{r['count']}/{r['of']}{pct}"


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def corpus_tables(c: dict[str, Any], top: int) -> str:
    targets = sorted(c["totals"]["targets"])
    header = ["Group", "License", "Files verified", "Operations mapped", "Expressions parsed"]
    header += [f"Dataflows emitted ({t})" for t in targets]
    header += [f"Tasks runnable ({t})" for t in targets]
    rows = []
    for g in [*c["groups"], {"group": "**Total**", "license": "", **c["totals"]}]:
        if g.get("status") == "no-pinned-files":
            continue
        tg = g.get("targets", {})
        rows.append(
            [
                g["group"],
                g.get("license", ""),
                ratio(g.get("files_verified")),
                ratio(g.get("operations_mapped")),
                ratio(g.get("expressions_parsed")),
                *(ratio(tg.get(t, {}).get("dataflows_emitted")) for t in targets),
                *(ratio(tg.get(t, {}).get("tasks_runnable")) for t in targets),
            ]
        )
    out = [
        f"### Public corpus (ETLIR {c['etlir_version']}, Canonical IR {c['ir_version']})",
        "",
        table(header, rows),
    ]
    for key, title in (
        ("unsupported_operations", "Unsupported operations"),
        ("opaque_expressions", "Opaque expressions"),
    ):
        items = c["blockers"][key]
        out += [
            "",
            f"#### {title} (top {min(top, len(items))} of {len(items)} reasons, "
            f"{sum(b['count'] for b in items)} occurrences)",
            "",
            table(["Occurrences", "Reason"], [[str(b["count"]), b["reason"]] for b in items[:top]]),
        ]
    return "\n".join(out)


def benchmark_tables(results: list[dict[str, Any]]) -> str:
    measures = [
        "deterministic_conversion",
        "expectation_checks_passed",
        "execution_as_expected",
        "output_agreement",
        "mutations_detected",
    ]
    rows = []
    cases: dict[str, dict[str, str]] = {}
    for r in results:
        label = ", ".join(r.get("targets", [])) or "?"
        rows.append([label, *(ratio(r["summary"].get(m)) for m in measures)])
        for row in r["rows"]:
            execution = row.get("checks", {}).get("execution", {})
            cell = (row.get("comparison") or {}).get("status") or (
                f"{execution.get('actual', '-')} (expected)"
                if execution.get("ok")
                else str(execution.get("actual", "-"))
            )
            cases.setdefault(row["case"], {})[row["target"]] = cell
    targets = sorted({t for c in cases.values() for t in c})
    return "\n".join(
        [
            "### Synthetic benchmark (specified-behavior agreement)",
            "",
            table(["Targets", *measures], rows),
            "",
            table(
                ["Case", *targets],
                [[c, *(v.get(t, "-") for t in targets)] for c, v in sorted(cases.items())],
            ),
        ]
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = Path(argv[1])
    parts = []
    corpus = base / "corpus" / "corpus.json"
    if corpus.is_file():
        parts.append(corpus_tables(json.loads(corpus.read_text("utf-8")), top=15))
    results = [
        json.loads(p.read_text("utf-8")) for p in sorted(base.glob("benchmark-*/results.json"))
    ]
    if results:
        parts.append(benchmark_tables(results))
    if not parts:
        print(f"no corpus/corpus.json or benchmark-*/results.json under {base}", file=sys.stderr)
        return 1
    print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
