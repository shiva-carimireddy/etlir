# Target emitters and the reference runner

Both in-tree emitters consume only the Canonical IR, publish a capability manifest
([support matrix](support-matrix.md)), refuse to emit anything blocked, and write the same
package layout:

```
targets/<target>/
  target_plan.json          # jobs, blocked dataflows/tasks, diagnostics
  workflow_plan.json        # tasks, dependencies, runnable/blocked + reasons, commands
  bindings.example.json     # every binding the emitted jobs read or write
  parameters.example.json   # parameter ids -> defaults (sensitive ones omitted)
  jobs/<name>.py            # one launcher per emitted dataflow
  sql/<name>.sql            # DuckDB only: one view per canonical operation
  etlir_<target>_runtime.py # helpers copied verbatim; generated code needs no ETLIR
```

Every generated step starts with a comment naming its canonical operation id and the
source locator; `evidence.json` maps each operation to its file and line.

## `spark` — Apache Spark (PySpark DataFrame API)

* Tested with PySpark 4.2.0 on Java 17 (Temurin 17.0.20.1), Python 3.12.
* Jobs run through `spark-submit --master local[1]` (PySpark's bundled launcher) or plain
  `python` (`--launcher python`). Session settings are pinned and recorded per task:
  `spark.sql.session.timeZone=UTC`, `spark.sql.ansi.enabled=true`.
* Reads use an explicit schema and `mode=FAILFAST`. Writes use Spark's writer
  (`--spark-writer spark`, default on Linux/macOS) or a driver-side JSON Lines writer
  (`--spark-writer driver`, default on Windows, where Spark's file writer needs Hadoop's
  native `winutils`). The driver writer collects rows on the driver and is meant for small
  local runs.

## `duckdb` — DuckDB SQL

* Tested with DuckDB 1.5.6. No JVM; the zero-setup local profile.
* Static SQL per dataflow (`CREATE TEMP VIEW` per operation), runtime parameters as
  DuckDB variables, outputs via `COPY … (FORMAT json)` with decimals as strings.

## Reference workflow runner (`etlir run`)

Executes `workflow_plan.json` serially in a deterministic dependency order. A task runs
when all its dependency conditions hold: `success`, `failure` or `completion` of the
upstream task. Tasks whose conditions cannot hold are `not_triggered`. **A pipeline that
contains any blocked task is refused as a whole** unless `--allow-partial` is given; then
only the unaffected tasks run (blocking already covers everything downstream of a blocked
task) and the result is `partial`. `execution.json` records per-task status, return code,
duration, log file, launcher, and the engine's runtime settings.

Notification tasks (`notify`, from PowerCenter Email tasks) are recorded in the run
report as succeeded with a note; the runner never sends anything (the capability is
`constrained` for that reason).

It is a reference runner, not a scheduler: no calendars, event waits, retries, recovery,
or parallel execution. A future orchestration emitter (e.g. Airflow) can consume the same
canonical workflow graph.
