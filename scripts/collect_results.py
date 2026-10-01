"""Produce the publication results of a release (benchmarks/protocol.md, protocol v1).

    python scripts/fetch_corpus.py              # once: fetch and verify the pinned corpus
    python scripts/collect_results.py           # writes results/v<version>/
    python scripts/collect_results.py --skip-spark

Runs ``etlir benchmark`` (DuckDB with mutation analysis, Spark without) and ``etlir corpus``
into ``out/release/`` (ignored by git), then copies only the summary files into
``results/v<version>/``: ``benchmark-<target>/results.json``, ``corpus/corpus.json``,
``environment.json`` and ``tables.md``. Per-group conversion outputs stay in ``out/``: they
contain third-party metadata that is never committed.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def etlir(*args: str) -> None:
    cmd = [sys.executable, "-m", "etlir", *args]
    print("+ etlir", " ".join(args), flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)  # noqa: S603


def version_of(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-spark", action="store_true")
    args = parser.parse_args()

    from etlir import __version__
    from etlir.canonical.model import IR_VERSION

    work = REPO / "out" / "release"
    dest = REPO / "results" / f"v{__version__}"
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)

    runs = [("duckdb", ["--target", "duckdb"])]
    if not args.skip_spark:
        runs.append(("spark", ["--target", "spark", "--no-mutations"]))
    for target, extra in runs:
        etlir("benchmark", "benchmarks/cases", "--out", str(work / f"benchmark-{target}"), *extra)
        (dest / f"benchmark-{target}").mkdir(parents=True)
        shutil.copyfile(
            work / f"benchmark-{target}" / "results.json",
            dest / f"benchmark-{target}" / "results.json",
        )
    etlir("corpus", "--out", str(work / "corpus"))
    (dest / "corpus").mkdir(parents=True)
    shutil.copyfile(work / "corpus" / "corpus.json", dest / "corpus" / "corpus.json")

    environment = {
        "etlir": __version__,
        "canonical_ir": IR_VERSION,
        "protocol": "v1",
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "duckdb": version_of("duckdb"),
        "pyspark": None if args.skip_spark else version_of("pyspark"),
        "sqlglot": version_of("sqlglot"),
    }
    (dest / "environment.json").write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    tables = subprocess.run(  # noqa: S603
        [sys.executable, str(REPO / "scripts" / "paper_tables.py"), str(dest)],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    (dest / "tables.md").write_text(tables, "utf-8")
    print(f"results: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
