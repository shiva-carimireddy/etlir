# Known limitations (0.1.0)

* **Subset coverage.** Lookups, SQL overrides and row-aligned merges are supported, but
  whole-dataflow coverage on real exports is still low: most blocked sessions combine
  several unsupported constructs (update strategies, sequence generators, normalizers,
  mapplets, `DECODE` and other functions, stateful variables). See the ranked list in
  ROADMAP.md, and run `etlir convert` on your exports to see what blocks them.
* **Lookup order policies.** "Use First/Last Value" run only when keys are unique; with
  duplicate matches the task fails instead of guessing PowerCenter's cache order.
* **SQL runs outside the source database.** Translated overrides assume binary collation
  and the dialect conventions listed in docs/sources/powercenter.md.
* **No source-platform equivalence.** Agreement is measured against independently
  authored expectations on synthetic fixtures (*specified-behavior agreement*).
  Equivalence with PowerCenter would require authorized paired executions.
* **Assumptions about PowerCenter behavior** are documented where they are made (operator
  precedence, unconditional links meaning "on completion", division by zero, NULL
  handling in concatenation). They follow public documentation and have not been verified
  against a live PowerCenter runtime.
* **Decimal precision.** Canonical decimals are exact. PowerCenter with high precision
  disabled (the export default) computes decimals as doubles. Affected dataflows are
  flagged `APPROXIMATED` (`PC-N-002`).
* **String precision is not enforced.** Values longer than a port's precision are not
  truncated or rejected.
* **Flat files only** for execution. Relational sources and targets are modeled but need
  bindings to files (CSV/JSON Lines) for the reference targets.
* **Reference runner, not a scheduler.** It runs tasks serially; there are no schedules,
  event waits, retries or recovery. `trigger = any` (OR-joined links) is blocked.
* **Spark on Windows** writes through the driver (`--spark-writer driver`), which is only
  suitable for small data.
* **One source adapter.** Claims about adding sources without changing the core are
  architectural and tested at the interface level only. A second independently evaluated
  source adapter does not exist yet.
