# Known limitations (0.1.0)

* **Small construct subset.** On the pinned public corpus, lookups, SQL overrides,
  row-aligned merges (passive transformations fed by several upstream transformations),
  mapplets, update strategies and many expression functions (`DECODE`, `TO_CHAR`,
  `LPAD`, date functions) are common and are blocked in 0.1. Run `etlir convert` on your
  exports and read `report.html` to see what is blocked and why.
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
