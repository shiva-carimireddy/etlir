# Architecture

## Stages and artifacts

An **ETLIR translation pipeline** is one staged run of the following. Each stage writes a
deterministic artifact so it can be inspected, diffed and reproduced.

| Stage | Owner | Artifact |
|---|---|---|
| Inventory | Source adapter | `inventory.json`: counts with explicit units |
| Load | Source adapter | `raw_ir.json`: source-preserving Raw IR, input digests, omissions |
| Normalize | Source adapter | `canonical_ir.json` plus provenance evidence |
| Validate | Core | `validation.json`: invariant diagnostics (`IR-V-*`) |
| Analyze capabilities | Core + emitter manifest | capability decisions, blocked tasks (`CAP-*`) |
| Plan / write | Target emitter | `target/…`: target plan and package |
| Execute / compare | Target runner + core comparator | `execution.json`, `comparison.json` |

## Boundaries (enforced by tests)

```
etlir.canonical, etlir.evidence, etlir.capabilities,
etlir.contracts, etlir.registry, etlir.serialization    <- core: imports no plugin
etlir.sources.<name>   -> core only (no targets, no sibling sources)
etlir.targets.<name>   -> core only (no sources, no sibling targets)
```

A target emitter receives a `CanonicalDocument`. It never sees source files or Raw IR.
Target-specific concerns (lowering choices, runtime settings) live in the **target
plan**, never in the Canonical IR.

## Canonical IR (0.1.0)

Two separate graphs:

* **Workflow graph:** `Pipeline` → `Task`s with typed `Dependency` edges
  (`success`, `failure`, `completion`, `expression`). A `dataflow` task references one
  `Dataflow`.
* **Dataflow graph:** `Dataflow` → `Operation`s connected by `DataEdge`s. Edges carry an
  output group (for routing) and an input slot (for joins), plus optional column maps.

Other entities: `Dataset`, `Expression` (typed AST: literal, column, parameter, call,
opaque), `Parameter`, `Binding` (runtime references, never secrets), and `LineageEdge`
(column-level data lineage, distinct from provenance).

**Invariants** (in addition to the JSON Schema):

1. Every entity has a `SourceRef` (adapter, artifact, locator, native id, rule).
2. Identifiers are unique across the document (`IR-V-001`).
3. All references resolve (`IR-V-002`).
4. Both graphs are acyclic (`IR-V-003`, `IR-V-004`).
5. Reads have no inputs; writes have no outputs (`IR-V-007`).
6. Sensitive parameters have no defaults (`IR-V-009`).
7. Unsupported operations, unsupported tasks, and opaque expressions are kept and reported
   (`IR-V-010`…`IR-V-012`). They are never dropped.

The canonical function catalog (names used by `CallNode.function`) will be documented
with typed signatures and null/decimal/time semantics as functions are added. A function
not in the catalog must be represented as `opaque`.

## Capability analysis

Each emitter publishes a `CapabilityManifest` mapping construct ids
(`operation.filter`, `task.command`, `dependency.failure`, `function.substr`,
`expression.opaque`, …) to `supported`, `constrained`, `approximated`, or `blocked`.
Undeclared constructs are blocked. A task is blocked if its kind, any construct in its
dataflow, or any upstream dependency is blocked. Emitters check the preconditions of
`constrained` rules in their own preflight step.

## Evidence

`EvidenceRecord`s link a subject to its source reference, the rule applied, a status
(`PRESERVED`, `TRANSFORMED_EQUIVALENT`, `APPROXIMATED`, `AMBIGUOUS`, `UNSUPPORTED`,
`MISSING_INFORMATION`, `BLOCKED`), and target artifact locations. Evidence documents
decisions. It does not prove behavioral equivalence.
