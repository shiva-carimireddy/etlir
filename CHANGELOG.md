# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The Canonical IR has its own version; see
[ADR-0004](docs/decisions/0004-versioning-and-compatibility.md).

## [0.1.0] - 2026-09-28

First release: PowerCenter XML → Canonical IR → Spark and DuckDB, end to end, for a
documented subset. Canonical IR 0.1.0.

### Added
- **Lookups**: canonical `lookup` operation (slots `in`/`lookup`; policies `any`, `error`,
  `all`) lowered by both emitters; PowerCenter connected lookups (flat file or relational,
  equality or range conditions, SQL overrides and source filters) and unconnected lookup
  calls (`:LKP.name(args)`).
- **SQL overrides** via SQLGlot: source-qualifier queries, source filters, user-defined
  joins, SELECT DISTINCT, Oracle `(+)` joins, GROUP BY aggregates, `$$` parameters.
- **Row-aligned merge fusion** (`etlir.canonical.rowalign`).
- Mapping variables that the mapping never modifies become run parameters.
- Global aggregates (no group keys); string→decimal casts for SQL parameter substitution.
- Benchmark: case parameters, expected execution outcomes, a lookup-policy mutation, and
  mutations that change no emitted code reported as not applicable. Four new cases.
- **Canonical IR 0.1.0**: typed model and JSON Schema; invariants `IR-V-001`…`IR-V-017`
  (including column resolution); function catalog with NULL semantics; type inference;
  documented semantics (`docs/semantics.md`).
- **Capability analysis**: per-target manifests; fail-closed decisions for operations,
  write modes, tasks, dependency conditions, triggers, functions, casts and opaque
  expressions; downstream blocking; emitter preflight.
- **Plugin contracts**: `SourceAdapter`, `TargetEmitter`, entry-point registry with
  IR-version gating, and a conformance kit (`etlir.testing`).
- **PowerCenter XML source adapter** (`powercenter-xml`): hardened loading, content
  sniffing, source-preserving Raw IR with a preservation check, inventory with explicit
  units, cross-file symbol table, normalization of Source Qualifier, Expression (stateless
  variables inlined), Filter, Router, Joiner, Aggregator, targets, parameters, sessions,
  workflows and links; typed expression parser with fail-closed rules; diagnostics
  `PC-*`; evidence per entity.
- **Spark emitter** (`spark`): PySpark DataFrame jobs, `spark-submit` launch, pinned
  session settings, driver writer for Windows.
- **DuckDB emitter** (`duckdb`): static SQL views, zero-JVM execution.
- **Reference workflow runner**, **output comparator**, **benchmark runner** with semantic
  mutation analysis, **HTML report**.
- CLI: `inspect`, `convert`, `validate`, `run`, `compare`, `report`, `benchmark`,
  `capabilities`, `plugins`, `schema`, `version`.
- Seven original synthetic benchmark cases with hand-authored expectations; a pinned
  public corpus manifest with a fetch/verify script.
- Apache-2.0 license, DCO contribution model, governance, security policy, citation
  metadata, ADRs 0001–0007.
