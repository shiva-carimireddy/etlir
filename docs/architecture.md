# Architecture

## Stages and artifacts

`etlir convert` runs one **ETLIR translation pipeline** and writes every stage:

| Stage | Owner | Artifact |
|---|---|---|
| Inventory | Source adapter | `inventory.json`: counts with explicit units, reference resolution |
| Load | Source adapter | `raw_ir.json`: source-preserving Raw IR, input digests, omissions |
| Normalize | Source adapter | `canonical_ir.json`; provenance in `evidence.json` |
| Validate | Core | `validation.json`: adapter + invariant diagnostics |
| Analyze + emit | Core + each emitter | `targets/<t>/`: target plan, workflow plan, code |
| Summarize | Core | `summary.json`, `report.html`, `run_manifest.json` |
| Execute | Core runner | `execution.json`, logs, outputs |
| Compare | Core comparator | `comparison.json` |

## Modules and boundaries (enforced by tests)

```
core (imports no plugin)
  etlir.canonical      model, invariants, functions catalog, typing, graph, plan
  etlir.capabilities   manifests, fail-closed analysis
  etlir.contracts      SourceAdapter / TargetEmitter interfaces
  etlir.registry       entry-point discovery, IR-version gating
  etlir.workflow       target-neutral workflow plan
  etlir.package        target package writer + target evidence
  etlir.runner         reference workflow runner
  etlir.compare        output comparator
  etlir.pipeline       convert orchestration; etlir.benchmark, etlir.report, etlir.cli
  etlir.testing        conformance kit
plugins
  etlir.sources.powercenter   -> core only
  etlir.targets.spark         -> core only
  etlir.targets.duckdb        -> core only
generated runtime helpers (etlir_*_runtime.py) -> no etlir import at all
```

A target emitter receives a `CanonicalDocument` only. Target-specific choices live in the
target plan, never in the Canonical IR. The schema is checked for product vocabulary.

## Canonical IR (0.1.0)

Two separate graphs:

* **Workflow graph:** `Pipeline` → `Task`s with typed `Dependency` edges. A `dataflow`
  task references one `Dataflow`.
* **Dataflow graph:** `Operation`s connected by `DataEdge`s (output group → input slot,
  with column renames).

Entities also include `Dataset`, `Expression` (typed AST), `Parameter`, `Binding`
(runtime reference, never a secret) and `LineageEdge`. Semantics are defined in
[semantics.md](semantics.md).

**Invariants** (`IR-V-001`…`IR-V-019`): every entity has a source trace; identifiers are
unique; references resolve; both graphs are acyclic; reads have no inputs and writes no
outputs; sensitive parameters have no defaults; functions exist in the catalog with the
right arity, and aggregates appear only in aggregations; every column resolves; join
inputs are disjoint; operations declare their outputs; each input slot has one upstream.
Unsupported operations, unsupported tasks and opaque expressions are kept and reported.

## Capability analysis

Each emitter publishes a manifest mapping construct ids (`operation.join`,
`write.overwrite`, `task.command`, `dependency.failure`, `trigger.any`,
`function.substr`, `cast.integer`, `expression.opaque`, …) to `supported`,
`constrained`, `approximated` or `blocked`. Undeclared constructs are blocked. An emitter's
own preflight (for example, unknown column types) can block further dataflows. A task is
blocked if its kind, a dependency condition, anything in its dataflow, or any upstream task
is blocked.

## Evidence

`evidence.json` holds source evidence (per canonical entity: status, rule, source
reference) and target evidence (per emitted job and operation: file and line). Statuses:
`PRESERVED`, `TRANSFORMED_EQUIVALENT`, `APPROXIMATED`, `AMBIGUOUS`, `UNSUPPORTED`,
`MISSING_INFORMATION`, `BLOCKED`. Evidence documents decisions. It does not prove
behavioral equivalence.
