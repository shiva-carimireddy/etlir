# Roadmap and acceptance gates

Status changes only when the listed evidence exists in the repository.

| Gate | Scope | Status (0.2.0) |
|---|---|---|
| **G0** Repository/corpus audit | License, contribution model, ADRs, corpus manifest | **Done.** Corpus pinned by commit and SHA-256 (67 files, 7 groups) with license findings in `benchmarks/corpus.toml`. |
| **G1** Secure ingestion | Inventory, Raw IR, identities, multi-file symbol table, malformed/XXE tests | **Done** for the documented scope; Raw IR preservation passes on all pinned public files. |
| **G2** Canonical slice | Schema, contracts, graphs, expression AST, parameters, provenance, validators | **Done** for the subset in `docs/sources/powercenter.md`. |
| **G3** Spark emission | Emitter, manifest, jobs, workflow plan + runner, package validation, fail closed | **Done**, plus a second emitter (DuckDB, ADR-0006). |
| **G4** Independent execution | Pinned environment, controlled inputs, independent expectations, comparator, mutations | **Done** on ten synthetic cases on both targets, including an expected-failure case and seeded keyed writes; mutation analysis in the benchmark. A container image is not yet published. |
| **G5** Public corpus evaluation | Frozen protocol, per-group rows, analysis script | **Done.** Protocol v1 frozen (`benchmarks/protocol.md`); `etlir corpus`, `scripts/collect_results.py` and `scripts/paper_tables.py`; results in `results/v0.2.0/`. Timings and memory are out of scope for v1. |
| **G6** Paper/release | Tagged release, archived DOI, claims audited against artifacts | **Open** until the 0.2.0 tag is archived with a DOI and the paper cites it. |

## 0.2.0 in one paragraph

The expression language (`DECODE`, two-argument `IIF`, `IN`, string, number and date
functions, run-time values), Update Strategy with keyed writes, Sequence Generator,
Sorter, global aggregators and Email tasks. On the pinned public corpus (protocol v1,
`results/v0.2.0/corpus/corpus.json`): dataflows emitted 74 of 157 (0.1.0: 25) and tasks
runnable 33 of 145 (0.1.0: 9) on both targets.

## Next

The remaining blockers, ranked by occurrence in `results/v0.2.0/tables.md`, need new
semantics rather than more mappings:

1. **Row-order semantics.** Variable ports that read the previous row (counters,
   carry-forward of header values, previous keys) and Aggregator ports that pass the last
   row. This needs an explicit, verifiable order through the pipeline (file order or a
   declared sort), not an assumed one.
2. **Mainframe layouts.** VSAM/COBOL sources and the Normalizer (`OCCURS`), with a
   fixed-width reader for the reference targets.
3. **Mapplet expansion** (inline mapplets as sub-dataflows).
4. Rank (top-N per group with an explicit tie policy), the `FIRST`/`LAST` aggregates, SQL
   subqueries, two-digit-year parsing with an explicit century rule.

## Later

* OpenLineage export of `LineageEdge`s; populate column-level lineage.
* A second **source** adapter that can be executed locally (Apache Hop / Kettle, ADR-0005)
  for measured source-vs-target agreement.
* An orchestration emitter (Airflow) consuming the canonical workflow graph.
* Evaluate Substrait for the relational core (ADR-0005).
