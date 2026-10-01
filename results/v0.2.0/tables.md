### Public corpus (ETLIR 0.2.0, Canonical IR 0.2.0)

| Group | License | Files verified | Operations mapped | Expressions parsed | Dataflows emitted (duckdb) | Dataflows emitted (spark) | Tasks runnable (duckdb) | Tasks runnable (spark) |
|---|---|---|---|---|---|---|---|---|
| crawl-fixture | Apache-2.0 | 1/1 (100.0%) | 5/13 (38.5%) | 2/4 (50.0%) | 0/2 (0.0%) | 0/2 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| enterprise-dwh-powercenter | none-found | 18/18 (100.0%) | 72/76 (94.7%) | 31/31 (100.0%) | 11/15 (73.3%) | 11/15 (73.3%) | 11/15 (73.3%) | 11/15 (73.3%) |
| global-superstore-datamart | none-found | 1/1 (100.0%) | 59/60 (98.3%) | 55/55 (100.0%) | 5/6 (83.3%) | 5/6 (83.3%) | 0/4 (0.0%) | 0/4 (0.0%) |
| hhs-informatica | Unlicense | 12/12 (100.0%) | 1245/1377 (90.4%) | 10766/10939 (98.4%) | 45/108 (41.7%) | 45/108 (41.7%) | 19/115 (16.5%) | 19/115 (16.5%) |
| mario-deno-informatica | none-found | 2/2 (100.0%) | 4/4 (100.0%) | 2/2 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/2 (50.0%) | 1/2 (50.0%) |
| northwind-dwh | none-found | 7/7 (100.0%) | 122/125 (97.6%) | 79/79 (100.0%) | 3/7 (42.9%) | 3/7 (42.9%) | 0/0 | 0/0 |
| powercenter-tasks | none-found | 26/26 (100.0%) | 112/121 (92.6%) | 51/51 (100.0%) | 9/18 (50.0%) | 9/18 (50.0%) | 2/4 (50.0%) | 2/4 (50.0%) |
| **Total** |  | 67/67 (100.0%) | 1619/1776 (91.2%) | 10986/11161 (98.4%) | 74/157 (47.1%) | 74/157 (47.1%) | 33/145 (22.8%) | 33/145 (22.8%) |

#### Unsupported operations (top 15 of 34 reasons, 157 occurrences)

| Occurrences | Reason |
|---|---|
| 40 | Normalizer: transformation type '<name>' is not supported |
| 23 | Source Definition: hierarchical (VSAM) source layouts are not supported |
| 20 | Mapplet: mapplet expansion is not supported |
| 17 | Aggregator: port <name> passes the last row's value (order dependent) |
| 8 | Expression: row alignment cannot be established through unsupported upstream [<names>] |
| 5 | Source Qualifier: SQL override: Subquery is not supported |
| 4 | Expression: no connected inputs |
| 4 | Target Definition: row alignment cannot be established through unsupported upstream [<names>] |
| 3 | Custom Transformation: transformation type '<name>' is not supported |
| 3 | Lookup Procedure: lookup source SQL: table <name> has no source/target definition |
| 2 | Expression: input port <name> replaces NULLs with a default value |
| 2 | Filter: unconnected input ports [<names>] |
| 2 | Rank: transformation type '<name>' is not supported |
| 2 | Source Qualifier: SQL override: SQL does not parse: Expecting ). Line 3, Col: 51. |
| 2 | Target Definition: definition of Target Definition '<name>' not found |

#### Opaque expressions (top 11 of 11 reasons, 175 occurrences)

| Occurrences | Reason |
|---|---|
| 109 | variable port <name> is read before it is set (stateful) |
| 16 | function FIRST/2 is not in the supported subset |
| 13 | IS_DATE format '<name>' is not supported |
| 12 | implicit conversion from string to decimal |
| 9 | expected '<name>', found '<name>' |
| 6 | IN on letters without a CaseFlag (default case sensitivity unverified) |
| 4 | INSTR start must be an integer literal |
| 2 | empty expression |
| 2 | unknown port <name> |
| 1 | INSTR with a start position below 1 (backward search) |
| 1 | REPLACESTR OldString must be a string literal |

### Synthetic benchmark (specified-behavior agreement)

| Targets | deterministic_conversion | expectation_checks_passed | execution_as_expected | output_agreement | mutations_detected |
|---|---|---|---|---|---|
| duckdb | 10/10 (100.0%) | 10/10 (100.0%) | 10/10 (100.0%) | 9/9 (100.0%) | 21/21 (100.0%) |
| spark | 10/10 (100.0%) | 10/10 (100.0%) | 10/10 (100.0%) | 9/9 (100.0%) | 0/0 |

| Case | duckdb | spark |
|---|---|---|
| pc-expression-semantics | agree | agree |
| pc-function-semantics | agree | agree |
| pc-lookup-duplicate | failed (expected) | failed (expected) |
| pc-lookups | agree | agree |
| pc-mixed-blocked | agree | agree |
| pc-orders | agree | agree |
| pc-row-aligned | agree | agree |
| pc-sequence | agree | agree |
| pc-sql-overrides | agree | agree |
| pc-update-strategy | agree | agree |
