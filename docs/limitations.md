# Known limitations

* **Pre-alpha.** No source adapter or target emitter has been released yet. The
  framework core is implemented; the first pair (PowerCenter XML → Spark) is in
  development.
* **No source-platform equivalence.** ETLIR can show agreement with independently
  specified expected output. Equivalence with PowerCenter requires authorized paired
  executions, which the public evaluation does not have.
* **Workflow control is narrower than enterprise schedulers.** The planned Spark
  reference runner executes success-path session dependencies only. Calendars, event
  waits, email, retries, and recovery are reported and blocked until implemented and
  tested.
* **Opaque SQL and scripts.** SQL overrides outside a declared, parsed subset, custom
  code, and command tasks are preserved as opaque/unsupported and block affected paths.
* **Public corpora cannot be executed as-is.** Public XML exports lack source systems,
  data, and credentials. Execution uses original synthetic fixtures.
