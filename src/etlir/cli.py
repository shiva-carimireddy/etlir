"""ETLIR command-line interface.

    etlir inspect   PATH...                  inventory of source artifacts
    etlir convert   PATH... --out DIR        full translation pipeline, all stages written
    etlir validate  canonical_ir.json        Canonical IR schema + invariants
    etlir run       DIR --target T --bindings FILE
    etlir compare   --canonical F --bindings F --expect BINDING=FILE ...
    etlir report    DIR                      (re)build report.html from the JSON artifacts
    etlir benchmark CASE_DIR... --out DIR    convert + run + compare + mutation checks
    etlir capabilities [TARGET] [--markdown] support declared by target emitters
    etlir plugins | schema | version

Exit codes: 0 success; 1 validation errors, failed execution or disagreement; 2 usage or
input errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from etlir import __version__
from etlir.canonical.invariants import validate
from etlir.canonical.model import IR_VERSION, CanonicalDocument
from etlir.evidence import has_errors
from etlir.registry import Registry
from etlir.serialization import canonical_schema, canonical_schema_filename, dumps, write_json


def _print(value: object) -> None:
    sys.stdout.write(dumps(value))


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"etlir {__version__} (canonical IR {IR_VERSION})")
    return 0


def _cmd_plugins(_: argparse.Namespace) -> int:
    reg = Registry.discover()
    _print(
        {
            "sources": {k: v.version for k, v in sorted(reg.sources.items())},
            "targets": {k: v.version for k, v in sorted(reg.targets.items())},
            "errors": reg.errors,
        }
    )
    return 1 if reg.errors else 0


def _cmd_schema(args: argparse.Namespace) -> int:
    out = Path(args.out) / canonical_schema_filename()
    write_json(out, canonical_schema())
    print(out)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        doc = CanonicalDocument.model_validate_json(Path(args.file).read_bytes())
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return 2
    diagnostics = validate(doc)
    _print({"diagnostics": [d.model_dump(mode="json") for d in diagnostics]})
    return 1 if has_errors(diagnostics) else 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    from etlir.pipeline import discover, pick_source

    paths = [Path(p) for p in args.paths]
    adapter = pick_source(Registry.discover(), args.source, paths)
    files, root = discover(paths, adapter)
    raw = adapter.load(files, root)
    inventory = getattr(adapter, "inventory", None)
    result = inventory(raw) if inventory else {"inputs": [a.model_dump() for a in raw.inputs]}
    if args.out:
        write_json(Path(args.out), result)
    else:
        _print(result)
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    from etlir.pipeline import convert

    summary = convert(
        [Path(p) for p in args.paths],
        Path(args.out),
        source=args.source,
        targets=args.target or None,
    )
    canon = summary["canonical"]
    print(
        f"converted {summary['inputs']['files']} file(s): {canon['pipelines']} pipeline(s), "
        f"{canon['dataflows']} dataflow(s), operations mapped "
        f"{canon['operations']['mapped']}/{canon['operations']['total']}"
    )
    for tid, t in summary["targets"].items():
        print(
            f"  {tid}: dataflows emitted {t['dataflows']['emitted']}/{t['dataflows']['total']},"
            f" tasks runnable {t['tasks']['runnable']}/{t['tasks']['total']}"
        )
    print(f"artifacts: {args.out} (open report.html)")
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    from etlir.corpus import evaluate_corpus

    result = evaluate_corpus(
        Path(args.manifest), Path(args.root), Path(args.out), targets=args.target or None
    )
    t = result["totals"]
    print(
        f"files verified {t['files_verified']['count']}/{t['files_verified']['of']}, "
        f"operations mapped {t['operations_mapped']['count']}/{t['operations_mapped']['of']}"
    )
    for tid, v in t["targets"].items():
        d, k = v["dataflows_emitted"], v["tasks_runnable"]
        print(
            f"  {tid}: dataflows emitted {d['count']}/{d['of']}, "
            f"tasks runnable {k['count']}/{k['of']}"
        )
    print(f"results: {Path(args.out) / 'corpus.json'}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from etlir.runner import run_package

    package = Path(args.dir) / "targets" / args.target
    if not (package / "workflow_plan.json").exists():
        package = Path(args.dir)
    if not (package / "workflow_plan.json").exists():
        print(f"no workflow_plan.json for target '{args.target}' under {args.dir}", file=sys.stderr)
        return 2
    run_dir = Path(args.run_dir or Path(args.dir) / "runs" / args.target)
    execution = run_package(
        package,
        Path(args.bindings).resolve(),
        run_dir.resolve(),
        Path(args.params).resolve() if args.params else None,
        launcher=args.launcher,
        spark_writer=args.spark_writer,
        allow_partial=args.allow_partial,
        pipelines=args.pipeline or None,
    )
    for p in execution["pipelines"]:
        print(f"{p['name']}: {p['status']}" + (f" ({p['reason']})" if "reason" in p else ""))
        for t in p["tasks"]:
            print(f"  {t.get('name', t['id'])}: {t['status']}")
    print(f"execution: {execution['status']} -> {run_dir / 'execution.json'}")
    return 0 if execution["status"] == "succeeded" else 1


def _cmd_compare(args: argparse.Namespace) -> int:
    from etlir.compare import compare_outputs

    doc = CanonicalDocument.model_validate_json(Path(args.canonical).read_bytes())
    bindings_file = Path(args.bindings).resolve()
    expectations = {}
    for item in args.expect:
        binding, _, path = item.partition("=")
        expectations[binding] = Path(path)
    result = compare_outputs(
        doc, json.loads(bindings_file.read_text("utf-8")), bindings_file, expectations
    )
    if args.out:
        write_json(Path(args.out), result)
    _print(result)
    return 0 if result["status"] == "agree" else 1


def _cmd_report(args: argparse.Namespace) -> int:
    from etlir.report import write_report

    print(write_report(Path(args.dir)))
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from etlir.benchmark import run_benchmark

    cases: list[Path] = []
    for p in map(Path, args.cases):
        cases += (
            sorted(c.parent for c in p.rglob("case.toml"))
            if not (p / "case.toml").exists()
            else [p]
        )
    results = run_benchmark(
        cases,
        Path(args.out),
        targets=args.target or None,
        launcher=args.launcher,
        spark_writer=args.spark_writer,
        mutation_target=None if args.no_mutations else args.mutation_target,
    )
    s = results["summary"]
    for k, v in s.items():
        print(f"{k}: {v['count']}/{v['of']}")
    ok = all(v["count"] == v["of"] for v in s.values())
    print(f"results: {Path(args.out) / 'results.json'}")
    return 0 if ok else 1


def _cmd_capabilities(args: argparse.Namespace) -> int:
    reg = Registry.discover()
    ids = [args.target] if args.target else sorted(reg.targets)
    manifests = {t: reg.targets[t]().capabilities() for t in ids}
    if not args.markdown:
        _print({t: m.model_dump(mode="json") for t, m in manifests.items()})
        return 0
    constructs = sorted({r.construct_id for m in manifests.values() for r in m.rules})
    print("| Construct | " + " | ".join(ids) + " |")
    print("|---|" + "---|" * len(ids))
    for c in constructs:
        cells = []
        for t in ids:
            rule = manifests[t].lookup(c)
            cells.append(
                rule.state.value + (" (c)" if rule and rule.preconditions else "")
                if rule
                else "blocked"
            )
        print(f"| `{c}` | " + " | ".join(cells) + " |")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etlir", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print tool and IR versions.").set_defaults(func=_cmd_version)
    sub.add_parser(
        "plugins", help="List installed source adapters and target emitters."
    ).set_defaults(func=_cmd_plugins)

    p = sub.add_parser("schema", help="Export the Canonical IR JSON Schema.")
    p.add_argument("--out", default="schemas/canonical")
    p.set_defaults(func=_cmd_schema)

    p = sub.add_parser("validate", help="Validate a Canonical IR JSON document.")
    p.add_argument("file")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("inspect", help="Inventory source artifacts without converting.")
    p.add_argument("paths", nargs="+")
    p.add_argument("--source")
    p.add_argument("--out")
    p.set_defaults(func=_cmd_inspect)

    p = sub.add_parser("convert", help="Run the translation pipeline and write all stages.")
    p.add_argument("paths", nargs="+", help="Files or directories of one corpus group.")
    p.add_argument("--out", required=True)
    p.add_argument("--source", help="Source adapter id (default: auto-detect).")
    p.add_argument(
        "--target", action="append", help="Target emitter id (repeatable; default: all installed)."
    )
    p.set_defaults(func=_cmd_convert)

    p = sub.add_parser("run", help="Execute a target package with the reference runner.")
    p.add_argument("dir", help="Conversion output directory or a target package directory.")
    p.add_argument("--target", required=True)
    p.add_argument("--bindings", required=True, help="JSON: binding id -> format/path.")
    p.add_argument("--params", help="JSON: parameter id -> value.")
    p.add_argument("--run-dir")
    p.add_argument("--pipeline", action="append")
    p.add_argument("--launcher", choices=["auto", "spark-submit", "python"], default="auto")
    p.add_argument("--spark-writer", choices=["spark", "driver"])
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="Run unblocked tasks of pipelines that contain blocked tasks.",
    )
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("compare", help="Compare outputs with expected JSONL files.")
    p.add_argument("--canonical", required=True)
    p.add_argument("--bindings", required=True)
    p.add_argument("--expect", action="append", required=True, metavar="BINDING=FILE")
    p.add_argument("--out")
    p.set_defaults(func=_cmd_compare)

    p = sub.add_parser("report", help="Rebuild report.html from a conversion directory.")
    p.add_argument("dir")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("benchmark", help="Run benchmark cases end to end.")
    p.add_argument("cases", nargs="+", help="Case directories or a directory of cases.")
    p.add_argument("--out", required=True)
    p.add_argument("--target", action="append")
    p.add_argument("--launcher", choices=["auto", "spark-submit", "python"], default="auto")
    p.add_argument("--spark-writer", choices=["spark", "driver"])
    p.add_argument("--mutation-target", default="duckdb")
    p.add_argument("--no-mutations", action="store_true")
    p.set_defaults(func=_cmd_benchmark)

    p = sub.add_parser("corpus", help="Verify, convert and measure a pinned corpus.")
    p.add_argument("--manifest", default="benchmarks/corpus.toml")
    p.add_argument("--root", default="benchmarks/external", help="Fetched corpus directory.")
    p.add_argument("--out", required=True)
    p.add_argument("--target", action="append")
    p.set_defaults(func=_cmd_corpus)

    p = sub.add_parser("capabilities", help="Show target capability manifests.")
    p.add_argument("target", nargs="?")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=_cmd_capabilities)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except Exception as exc:
        from etlir.pipeline import ConversionError

        if isinstance(exc, (ConversionError, FileNotFoundError)):
            print(f"error: {exc}", file=sys.stderr)
            return 2
        raise
    return code


if __name__ == "__main__":
    raise SystemExit(main())
