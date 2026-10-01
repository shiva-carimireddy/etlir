# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The Canonical IR has its own version; see
[ADR-0004](docs/decisions/0004-versioning-and-compatibility.md).

## [Unreleased]

### Added

- Canonical function catalog: `case`, `sign`, `trunc`, `round`, `lpad`, `rpad`, `instr`,
  `translate`, `replace`, `replace_ci`, `chr`, `matches_number`, `is_whitespace`,
  `leading_decimal`, `to_string`, `format_timestamp`, `parse_timestamp`,
  `can_parse_timestamp`, `trunc_timestamp`, `add_interval`, `timestamp_part`, `fail`, with
  lowerings in both targets and engine differences resolved explicitly
  (docs/semantics.md). Arguments that fix a call's meaning must be literals (IR-V-014).
- Built-in parameters (`Parameter.builtin = "run_start_time"`): the runner gives every task
  of a run the same start instant.
- PowerCenter: `DECODE`, two-argument `IIF`, `IN`, `SIGN`, `LPAD`/`RPAD`, `INSTR`,
  `REPLACECHR`/`REPLACESTR`, `CHR`, `IS_NUMBER`, `IS_SPACES`, `IS_DATE`, `TO_DATE`,
  `TO_CHAR`, `TO_DECIMAL`, `TO_INTEGER`, `TRUNC`, `ROUND`, `ADD_TO_DATE`, `GET_DATE_PART`,
  `ABORT`, `SETVARIABLE`, `SESSSTARTTIME`, `SYSDATE`, `$PM…` names, mapping variables as run
  parameters, empty output expressions, whole numbers into string ports.
- Benchmark case `pc-function-semantics`: a hand-derived truth table for the new functions
  on every target.
- Keyed writes: `WriteOp` modes `update` and `upsert` with `keys`, in both targets.
  PowerCenter Update Strategy with constant row operations and data-driven sessions.
- Canonical `sequence` operation; PowerCenter Sequence Generator (deterministic numbering,
  start value as a run parameter, consecutive blocks for two consumers). PowerCenter
  Sorter (pass-through, or DISTINCT).
- `to_string` on fractional decimals; `YY` in timestamp formatting.
- PowerCenter Aggregator without group-by ports (no row for empty input), unknown `$PM…`
  variables as run parameters, booleans into string ports, unused unconnected ports.
- Invariants IR-V-018 (missing input) and IR-V-019 (keyed write keys); a dataflow violating
  a structural invariant is blocked instead of emitted.
- Benchmark manifests can seed existing target content (`[seed]`). New cases
  `pc-update-strategy` and `pc-sequence`.

### Fixed

- NULL literals were serialized without a value and could not be read back.
- A variable port that reads its own previous value is reported as stateful, not as an
  unknown port.

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
