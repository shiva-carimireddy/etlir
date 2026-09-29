# ADR-0003: PowerCenter → Spark first, with a reference workflow runner

**Status:** Accepted, 2026-09-28

## Context
The first paper needs one real, runnable source/target pair. Earlier planning staged a
local SQL target and an Airflow target first.

## Decision
* First pair: PowerCenter XML → Apache Spark (PySpark), executed with `spark-submit`
  in local mode inside a pinned container.
* Workflow control is handled by a small **reference workflow runner** that executes the
  supported session dependency graph from `workflow_plan.json`. It runs independent
  sessions serially, stops dependents on failure, and refuses blocked tasks. It is not a
  scheduler replacement.
* Airflow and other targets are future emitters that consume the same canonical
  workflow graph.

## Consequences
Only success-path dependencies are executable initially. Failure/conditional links,
retries, event waits, and similar constructs are blocked until implemented with tests.
PySpark 4.x requires Java 17+, so development and CI will use the container rather than
host Java.
