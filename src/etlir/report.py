"""Human-readable conversion report, derived only from the JSON artifacts in a
conversion output directory (the JSON files are authoritative)."""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

_CSS = """
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px 16px;
background:#fafaf9;color:#1c1917}main{max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #e7e5e4}
.muted{color:#78716c}table{border-collapse:collapse;width:100%;margin:8px 0}
td,th{border-bottom:1px solid #e7e5e4;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f5f5f4;font-weight:600}.wrap{overflow-x:auto}
.ok{color:#15803d}.bad{color:#b91c1c}.warn{color:#b45309}code{font-size:12px}
.tiles{display:flex;flex-wrap:wrap;gap:12px}.tile{background:#fff;border:1px solid #e7e5e4;
border-radius:8px;padding:10px 14px;min-width:150px}.tile b{display:block;font-size:20px}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><tr>{head}</tr>{body}</table></div>'


def _severity(value: str) -> str:
    return f"<span class='{'bad' if value == 'error' else 'warn'}'>{_e(value)}</span>"


def _tile(label: str, value: object) -> str:
    return f"<div class='tile'>{_e(label)}<b>{_e(value)}</b></div>"


def _ratio(n: int, d: int) -> str:
    return f"{n} / {d}" + (f" ({100 * n / d:.1f}%)" if d else "")


def render(out: Path) -> str:
    def load(name: str) -> Any:
        p = out / name
        return json.loads(p.read_text("utf-8")) if p.exists() else None

    summary = load("summary.json") or {}
    validation = load("validation.json") or {"diagnostics": []}
    manifest = load("run_manifest.json") or {}
    canon = summary.get("canonical", {})
    ops, exprs = canon.get("operations", {}), canon.get("expressions", {})
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>ETLIR conversion report</title><style>{_CSS}</style></head><body><main>",
        "<h1>ETLIR conversion report</h1>",
        f"<p class='muted'>ETLIR {_e(manifest.get('etlir_version'))} · Canonical IR "
        f"{_e(manifest.get('canonical_ir_version'))} · source "
        f"{_e(summary.get('source'))} · generated from the JSON artifacts in this directory, "
        "which are authoritative.</p>",
        "<div class='tiles'>",
        f"<div class='tile'>Input files<b>{_e(summary.get('inputs', {}).get('files'))}</b></div>",
        f"<div class='tile'>Pipelines<b>{_e(canon.get('pipelines'))}</b></div>",
        f"<div class='tile'>Dataflows<b>{_e(canon.get('dataflows'))}</b></div>",
        _tile("Operations mapped", _ratio(ops.get("mapped", 0), ops.get("total", 0))),
        _tile("Expressions parsed", _ratio(exprs.get("parsed", 0), exprs.get("total", 0))),
        "</div>",
        "<p class='muted'>Mapped/parsed means represented in Canonical IR with defined "
        "semantics. It is not a claim that source and target behave identically.</p>",
    ]
    refs = summary.get("references", {})
    if refs:
        parts.append("<h2>Reference resolution</h2>")
        parts.append(
            _table(
                ["Link", "Resolved / required"],
                [[_e(k), _e(_ratio(v["resolved"], v["required"]))] for k, v in refs.items()],
            )
        )
    parts.append("<h2>Targets</h2>")
    rows = []
    for tid, t in summary.get("targets", {}).items():
        cd = t["construct_decisions"]
        rows.append(
            [
                f"<b>{_e(tid)}</b> {_e(t['version'])}",
                _e(_ratio(t["dataflows"]["emitted"], t["dataflows"]["total"])),
                _e(_ratio(t["tasks"]["runnable"], t["tasks"]["total"])),
                _e(", ".join(f"{k} {v}" for k, v in t["pipelines"].items())),
                _e(", ".join(f"{k} {v}" for k, v in cd.items())),
                _e(_ratio(t["traceability"]["traced"], t["traceability"]["emitted_operations"])),
            ]
        )
    parts.append(
        _table(
            [
                "Target",
                "Dataflows emitted",
                "Tasks runnable",
                "Pipelines",
                "Construct decisions",
                "Operations traced to code",
            ],
            rows,
        )
    )

    for tid in summary.get("targets", {}):
        wf = out / "targets" / tid / "workflow_plan.json"
        if not wf.exists():
            continue
        plan = json.loads(wf.read_text("utf-8"))
        parts.append(f"<h2>Workflow plan · {_e(tid)}</h2>")
        trows = []
        for p in plan["pipelines"]:
            for t in p["tasks"]:
                cls = "ok" if t["status"] == "runnable" else "bad"
                reasons = "<br>".join(_e(r) for r in t["reasons"][:6])
                if len(t["reasons"]) > 6:
                    reasons += f"<br><span class='muted'>+{len(t['reasons']) - 6} more</span>"
                deps = ", ".join(
                    f"{d['task_id'].split(':')[-1]} ({d['condition']})" for d in t["depends_on"]
                )
                trows.append(
                    [
                        _e(p["name"]),
                        _e(t["name"]),
                        _e(t["kind"]),
                        f"<span class='{cls}'>{_e(t['status'])}</span>",
                        _e(deps),
                        reasons,
                    ]
                )
        parts.append(
            _table(["Pipeline", "Task", "Kind", "Status", "Depends on", "Blocked by"], trows)
        )

    unsupported = ops.get("unsupported_by_native_kind", {})
    if unsupported:
        parts.append("<h2>Unsupported source constructs</h2>")
        parts.append(
            _table(
                ["Native construct", "Instances"],
                [[_e(k), _e(v)] for k, v in sorted(unsupported.items(), key=lambda kv: -kv[1])],
            )
        )
    diags = validation["diagnostics"]
    parts.append(f"<h2>Diagnostics ({len(diags)})</h2>")
    counts = Counter((d["code"], d["severity"]) for d in diags)
    parts.append(
        _table(
            ["Code", "Severity", "Count"],
            [[f"<code>{_e(c)}</code>", _e(s), _e(n)] for (c, s), n in sorted(counts.items())],
        )
    )
    shown = diags[:300]
    parts.append(
        _table(
            ["Code", "Severity", "Subject", "Message", "Source"],
            [
                [
                    f"<code>{_e(d['code'])}</code>",
                    _severity(d["severity"]),
                    f"<code>{_e(d.get('subject_id'))}</code>",
                    _e(d["message"]),
                    f"<code>{_e((d.get('source') or {}).get('artifact'))} "
                    f"{_e((d.get('source') or {}).get('locator'))}</code>",
                ]
                for d in shown
            ],
        )
    )
    if len(diags) > len(shown):
        parts.append(f"<p class='muted'>{len(diags) - len(shown)} more in validation.json.</p>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def write_report(out: Path) -> Path:
    path = out / "report.html"
    path.write_bytes(render(out).encode("utf-8"))
    return path
