# Roadmap and acceptance gates

Status changes only when the listed evidence exists in the repository.

| Gate | Scope | Status (0.1.0) |
|---|---|---|
| **G0** Repository/corpus audit | License, contribution model, ADRs, corpus manifest | **Done.** Corpus pinned by commit and SHA-256 (67 files, 7 groups) with license findings in `benchmarks/corpus.toml`. |
| **G1** Secure ingestion | Inventory, Raw IR, identities, multi-file symbol table, malformed/XXE tests | **Done** for the documented scope; Raw IR preservation passes on all pinned public files. |
| **G2** Canonical slice | Schema, contracts, graphs, expression AST, parameters, provenance, validators | **Done** for the subset in `docs/sources/powercenter.md`. |
| **G3** Spark emission | Emitter, manifest, jobs, workflow plan + runner, package validation, fail closed | **Done**, plus a second emitter (DuckDB, ADR-0006). |
| **G4** Independent execution | Pinned environment, controlled inputs, independent expectations, comparator, mutations | **Done** on seven synthetic cases on both targets, including an expected-failure case; mutation analysis in the benchmark. A container image is not yet published. |
| **G5** Public corpus evaluation | Frozen protocol, per-file/per-workflow rows, timings, analysis script | **Open.** Tooling for structural evaluation exists; the protocol is not frozen and no results are published. |
| **G6** Paper/release | Tagged release, archived DOI, claims audited against artifacts | **Open.** 0.1.0 is the first release candidate. |

## Done since the first commit

Lookups (connected and unconnected, SQL-override and range lookups), SQL overrides
(SQLGlot), row-aligned merge fusion, run-constant mapping variables. Development
measurement on the pinned public corpus (not a publication result; the protocol is not
frozen): unsupported operations 544 → 258 of 1,725 (lookups 145 → 11, source qualifiers
95 → 17, merged expressions 87 → 21); fully emittable dataflows 18 → 25 of 157. Of the
emitted real-corpus dataflows, 24 of 25 execute on empty inputs on both Spark and DuckDB;
the other stops correctly because it reads its own not-yet-written target.

## Next, ordered by how many blocked dataflows each construct affects

Measured on the 132 public-corpus dataflows still blocked. 97 of them have two or more
independent blockers, so whole-dataflow coverage rises in steps.

| Construct | Blocked dataflows affected |
|---|---|
| `DECODE` | 39 |
| Normalizer | 35 |
| `SETVARIABLE` (stateful mapping variables) | 30 |
| `IIF` without else | 25 |
| VSAM / COBOL sources | 24 |
| `SESSSTARTTIME`, `SYSDATE` (run-time values) | 21, 8 |
| Sequence Generator | 20 |
| Update Strategy | 18 |
| `LPAD`, `TO_CHAR`, `IS_DATE`, `IS_NUMBER`, `TO_DECIMAL` | 16, 15, 9, 9, 7 |
| Aggregator without group-by | 15 |

Planned order: expression functions with clear semantics (`DECODE`, 2-argument `IIF`,
`SIGN`, `LPAD`/`RPAD`, `TO_CHAR`/`TO_DECIMAL` with formats) and run-time values as explicit
run parameters (`SESSSTARTTIME`); then Sequence Generator (start value as a run parameter
plus row numbering, `APPROXIMATED` for order) and Update Strategy (insert/update/delete as
canonical write modes); then Normalizer and mapplet expansion. G5: freeze the protocol, add
per-file/per-workflow rows and timings, and publish a frozen results release.

## Later

* OpenLineage export of `LineageEdge`s; populate column-level lineage.
* A second **source** adapter that can be executed locally (Apache Hop / Kettle, ADR-0005)
  for measured source-vs-target agreement.
* An orchestration emitter (Airflow) consuming the canonical workflow graph.
* Evaluate Substrait for the relational core (ADR-0005).
