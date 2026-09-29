# Roadmap and acceptance gates

Each gate lists what must be implemented and the evidence that closes it. Status is
updated only when the listed evidence exists in the repository.

| Gate | Scope | Status |
|---|---|---|
| **G0** Repository/corpus audit | Repo inspection, license/contribution model, ADRs, corpus manifest with verified rights | Repo: **done** (was empty apart from a README). Corpus rights: **open** |
| **G1** Secure ingestion | PowerCenter XML inventory, Raw IR, stable identities, multi-file symbol table, unknown-feature ledger; malformed/XXE tests | **Not started** |
| **G2** Canonical slice | Typed schema, adapter contract, workflow and dataflow graphs, expression AST, parameters, provenance, validators | **Core done** (see below); PowerCenter normalization **not started** |
| **G3** Spark emission | Emitter contract, capability manifest, target plan, PySpark jobs, workflow plan + reference runner, package validator, fail-closed diagnostics | Contract + capability analysis **done**; Spark emitter **not started** |
| **G4** Independent execution | Pinned Spark container, controlled fixtures, predeclared expectations, comparator, mutation tests | **Not started** |
| **G5** Public corpus evaluation | Frozen protocol, batch runs, negative cases, regenerated tables, optional baseline | **Not started** |
| **G6** Paper/release | Tagged release, archived DOI, manuscript claims audited against artifacts | **Not started** |

## Done in G0/G2 (framework core)

* Canonical IR 0.1.0 model and committed JSON Schema (`schemas/canonical/`).
* Invariant validator with stable diagnostic codes `IR-V-001`…`IR-V-012`.
* Capability manifests and fail-closed analysis with downstream blocking (`CAP-*`).
* Source-adapter and target-emitter contracts; entry-point registry with IR-version
  rejection.
* Conformance kit (`etlir.testing`) checking determinism, provenance, round-trip,
  non-mutation, and reporting of blocked constructs.
* Boundary tests: core imports no plugin; sources and targets import no plugin from the
  other side; the canonical schema contains no product vocabulary.
* Interface test doubles (`tests/fixtures/plugins.py`). These are **not** a second
  source or target.

## Next steps, in order

1. **G1:** `etlir.sources.powercenter` using `defusedxml`: inventory counts with explicit
   units, Raw IR with omissions list, repository-level symbol table across companion
   exports. Fixtures: an original synthetic export (two sessions, one mapping each:
   source → expression → filter → target) plus malformed and XXE cases.
2. **G2:** normalization rules for Source Qualifier/read, Expression, Filter, Router,
   target write, session → mapping, workflow links; PowerCenter expression-language
   parser for a documented function subset into the canonical AST.
3. **G3:** `etlir.targets.spark`: capability manifest, deterministic PySpark job per
   supported session, `workflow_plan.json`, reference runner; Dockerfile pinning
   Java/Python/PySpark.
4. **G4:** comparator with a declared policy (multiset, null, decimal, timestamp rules),
   expectations authored independently of the emitter, and mutation tests showing that
   injected semantic errors are detected.
5. **G5/G6:** see [benchmarks/protocol.md](benchmarks/protocol.md).

## Future (not part of the first paper)

Additional targets (SQL/DuckDB, Airflow orchestration), additional sources, and
cross-pair evaluation. See [ADR-0005](docs/decisions/0005-standards-alignment.md) for the
proposed prioritization.
