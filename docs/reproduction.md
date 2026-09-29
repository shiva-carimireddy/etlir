# Reproducing ETLIR results

## Environment

| Component | Version tested | Needed for |
|---|---|---|
| Python | 3.12.12 (3.11–3.13 supported) | everything |
| DuckDB | 1.5.6 | `duckdb` target |
| PySpark | 4.2.0 | `spark` target |
| Java | Temurin 17.0.20.1 (17 or 21) | `spark` target |

Exact versions used for a run are recorded in `run_manifest.json` (conversion) and
`execution.json` (execution, including Spark version and SQL settings).

## Synthetic benchmark (included, redistributable)

```bash
git clone https://github.com/shiva-carimireddy/etlir && cd etlir
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,spark]"
export JAVA_HOME=/path/to/jdk-17                        # for the spark target
etlir benchmark benchmarks/cases --out results/local
```

For each case in `benchmarks/cases/`, this converts the source export twice and checks
the two outputs are byte-identical, checks the declared blocked tasks and diagnostic
codes, runs every target package, compares outputs with the hand-authored expectations,
and applies semantic mutations on the DuckDB target to confirm the comparator catches
them. `results/local/results.json` holds every row with numerators and denominators.

Output agreement here means **specified-behavior agreement** with expectations derived
from the fixture specification and [semantics](semantics.md). It is not equivalence with
PowerCenter, which would require authorized executions of the source platform.

## Public corpus (structural evaluation)

```bash
python scripts/fetch_corpus.py          # clones pinned commits, verifies 67 SHA-256 digests
etlir convert benchmarks/external/hhs-informatica --out out/hhs --target spark
```

Public exports ship without source data, so they are evaluated structurally (parse,
resolution, canonical coverage, emission, traceability); see
[benchmarks/protocol.md](../benchmarks/protocol.md). Several groups have no license:
use them locally only and do not redistribute them.

## Determinism

Conversion writes no timestamps or absolute paths into its artifacts, so identical inputs
and tool versions produce identical files. Volatile data (start times, durations) lives
only in `execution.json`.
