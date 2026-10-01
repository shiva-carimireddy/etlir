# Evaluation protocol v1 (frozen with ETLIR 0.2.0)

This protocol is frozen. A change requires a new protocol version (v2, …) recorded here,
never an edit of v1 in place. Publication results are produced from a tagged release with
the commands under *Producing results* and committed to `results/v<version>/`; any other
number (from development branches or ad-hoc scripts) is not a result.

## Case partitions

Each case or corpus group is placed in the highest partition it reaches, with reasons:

1. `inventory-only`: parsed, counted
2. `structurally-resolvable`: references resolved, Canonical IR valid
3. `emit-capable`: at least one target package emitted
4. `locally-executable`: runs with controlled fixtures
5. `behaviorally-comparable`: independent expectations exist

Parse success, emission, execution and output agreement are separate claims.

## Implemented measures

`etlir convert` writes `summary.json`; `etlir benchmark` writes `results.json`. Every rate
is reported as numerator/denominator.

| Measure | Source | Numerator / denominator |
|---|---|---|
| Files accepted | summary `inputs` | accepted / attempted |
| Reference resolution | summary `references` | resolved / required, per link type |
| Canonical operation coverage | summary `canonical.operations` | mapped / total operations |
| Expression coverage | summary `canonical.expressions` | parsed / total expressions |
| Dataflow emission | summary `targets.<t>.dataflows` | emitted / total dataflows |
| Operation emission | summary `targets.<t>.operations_in_emitted_dataflows` | operations in emitted dataflows / all operations |
| Traceability | summary `targets.<t>.traceability` | operations with a code location / emitted operations |
| Task runnability | summary `targets.<t>.tasks` | runnable / total tasks |
| Corpus coverage | corpus `totals` and `groups` (`etlir corpus`) | the measures above summed over verified groups; a group whose files fail their digest is not converted |
| Corpus blockers | corpus `blockers` | occurrences of each normalized unsupported-operation and opaque-expression reason |
| Deterministic conversion | results `deterministic_conversion` | byte-identical repeat conversions / cases |
| Expectation checks | results `expectation_checks_passed` | cases whose blocked tasks and diagnostic codes match the manifest / cases |
| Execution | results `execution_as_expected` | runs with the declared outcome (succeeded, partial or failed) / executed cases |
| Output agreement | results `output_agreement` | cases agreeing on all declared outputs / compared cases |
| Mutation detection | results `mutations_detected` | detected / applicable mutation operators |

Out of scope for v1: stage timings, peak memory and any other performance measure. No
performance claim may be made from v1 results.

## Producing results

```bash
python scripts/fetch_corpus.py        # fetch the pinned corpus, verify every digest
python scripts/collect_results.py     # benchmark (DuckDB with mutations, Spark) and corpus
```

`collect_results.py` writes `results/v<version>/`: `benchmark-duckdb/results.json`,
`benchmark-spark/results.json`, `corpus/corpus.json`, `environment.json` (tool, IR,
engine and Python versions) and `tables.md`, rendered by `scripts/paper_tables.py` from
those files only. Per-group conversion outputs stay in the ignored `out/` directory: they
contain third-party metadata.

## Comparison policy

Implemented in `etlir/compare.py` and recorded in every `comparison.json`: multiset rows;
values normalized by declared column type; decimals exact (scale may not exceed the
declared scale); integers integral; timestamps as UTC instants; strings exact; NULL equals
NULL; doubles at 12 significant digits.

## Expectations

Authored from the fixture specification and `docs/semantics.md`, never from emitter
output (see `benchmarks/cases/README.md`, including its change log). Agreement is reported
as **specified-behavior agreement**, not PowerCenter equivalence.

## Mutation analysis

Operators (in `etlir/benchmark.py`): negate filter predicates, shift comparison
boundaries, subtract→add, concat→coalesce, remove trimming, outer→inner join,
count→count(*), lookup any→error. A mutation is detected if execution fails or any
compared output disagrees. Operators with no site in a case, or whose sites all lie in
blocked dataflows (emitted code unchanged), are reported as not applicable. A surviving
mutation indicates a gap in the fixture or the expectations and is fixed by adding data,
recorded in the case change log.

## Baseline

A direct XML-to-Spark baseline on the same subset and inputs is optional. Without it, no
claim that the IR approach is faster, more accurate, or easier to debug than direct
conversion may be made.
