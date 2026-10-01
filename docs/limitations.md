# Known limitations (0.2.0)

* **Subset coverage.** On the pinned public corpus, 74 of 157 dataflows are emitted
  (`results/v0.2.0`). The rest combine constructs that are blocked on purpose: Normalizer
  and VSAM/COBOL layouts, mapplets, variable ports whose value depends on row order,
  Rank, custom code. Run `etlir convert` on your exports to see what blocks them.
* **Row order.** Canonical relations are unordered. Constructs that depend on arrival
  order are blocked (stateful variable ports, Aggregator ports that pass the last row) or
  `APPROXIMATED`: a Sequence Generator numbers rows in a deterministic order (all columns
  ascending), not PowerCenter's arrival order; the keys are unique and consecutive, but a
  given row may receive a different key.
* **State between runs is not persisted.** Sequence current values and mapping variables
  changed with `SETVARIABLE` are run parameters (start values); ETLIR does not save the
  final value for the next run.
* **Keyed writes** (`update`/`upsert`) fail the task on duplicate keys in the input
  instead of applying updates in an undefined order, and work on jsonl datasets in the
  reference targets.
* **Run-time values.** `SYSDATE` is read once at run start. `TO_DATE` on a malformed
  string fails the run (PowerCenter rejects the row). Notification (Email) tasks are
  recorded by the reference runner, never sent.
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
