# Roadmap and acceptance gates

Status changes only when the listed evidence exists in the repository.

| Gate | Scope | Status (0.1.0) |
|---|---|---|
| **G0** Repository/corpus audit | License, contribution model, ADRs, corpus manifest | **Done.** Corpus pinned by commit and SHA-256 (67 files, 7 groups) with license findings in `benchmarks/corpus.toml`. |
| **G1** Secure ingestion | Inventory, Raw IR, identities, multi-file symbol table, malformed/XXE tests | **Done** for the documented scope; Raw IR preservation passes on all pinned public files. |
| **G2** Canonical slice | Schema, contracts, graphs, expression AST, parameters, provenance, validators | **Done** for the subset in `docs/sources/powercenter.md`. |
| **G3** Spark emission | Emitter, manifest, jobs, workflow plan + runner, package validation, fail closed | **Done**, plus a second emitter (DuckDB, ADR-0006). |
| **G4** Independent execution | Pinned environment, controlled inputs, independent expectations, comparator, mutations | **Done** on three synthetic cases; mutation analysis in the benchmark. A container image is not yet published. |
| **G5** Public corpus evaluation | Frozen protocol, per-file/per-workflow rows, timings, analysis script | **Open.** Tooling for structural evaluation exists; the protocol is not frozen and no results are published. |
| **G6** Paper/release | Tagged release, archived DOI, claims audited against artifacts | **Open.** 0.1.0 is the first release candidate. |

## Next (0.2), ordered by blocking frequency in the public corpus

1. **Connected Lookup** (static, flat file or table source; `Report Error` or explicit
   first/last on a declared order) as a canonical `lookup` lowering.
2. **Row-aligned merges**: passive transformations fed by several upstream transformations
   of the same pipeline.
3. **SQL override classification** with a dialect-aware parser (SQLGlot); simple
   `SELECT … WHERE` overrides mapped, the rest kept opaque.
4. **Expression functions**: `DECODE`, `TO_CHAR`/`TO_DATE` with explicit formats, `LPAD`/
   `RPAD`, `IN`, date arithmetic.
5. **Sorter, Union, Sequence Generator**; mapplet expansion.
6. G5: freeze the protocol, add per-file/per-workflow rows and timings, and publish a
   frozen results release.

## Later

* OpenLineage export of `LineageEdge`s; populate column-level lineage.
* A second **source** adapter that can be executed locally (Apache Hop / Kettle, ADR-0005)
  for measured source-vs-target agreement.
* An orchestration emitter (Airflow) consuming the canonical workflow graph.
* Evaluate Substrait for the relational core (ADR-0005).
