# Benchmark cases

Each directory is one case: `case.toml` (manifest), `source/` (PowerCenter-format export),
`inputs/` (controlled input data), `expected/` (expected outputs). Run them with
`etlir benchmark benchmarks/cases --out <dir>`.

## Provenance

All cases are **original synthetic fixtures** written for ETLIR and licensed Apache-2.0.
The XML follows the structure of public PowerCenter exports (element and attribute
vocabulary observed in the pinned public corpus) but contains no copied third-party or
employer content. The data is invented.

## How expectations were authored

Expected outputs were written by hand from each fixture's stated behavior and
[docs/semantics.md](../../docs/semantics.md), not copied from any emitter's output. Each
case below lists the behaviors it pins.

| Case | Pins |
|---|---|
| `pc-orders` | Cross-file session→mapping resolution (mappings and workflow in separate exports); trimming and NULL-safe concatenation; stateless variable port; filter with NULL keys; router with a mapping parameter, NULL falling to the default group, a lowercase status excluded; aggregation ignoring NULLs; master-outer join keeping unmatched and NULL-key rows; unmapped target column written as NULL. |
| `pc-mixed-blocked` | Fail-closed behavior: a sequence generator (and the merge fed by it), a merge that is not row-aligned, a `SELECT *` override, a stateful variable port, a command task and a custom link condition are blocked with reasons; the clean branch runs with `allow_partial`. |
| `pc-sql-overrides` | Oracle `(+)` outer join with `NVL`, `UPPER`, arithmetic and ORDER BY; a mapping variable as a run parameter; GROUP BY aggregates with a NULL group; a source filter with `BETWEEN`/`IN` and SELECT DISTINCT; a user-defined join with a filter. |
| `pc-lookups` | "Use Any Value" over duplicate keys (deterministic choice); a range lookup under "Report Error" including a boundary value; a lookup with a SQL override; an unconnected `:LKP` call used twice (one lookup); a four-branch row-aligned merge; NULL keys never matching. |
| `pc-lookup-duplicate` | "Use First Value" over duplicate keys: the run must fail instead of guessing an order. |
| `pc-row-aligned` | An expression and a target fed by several branches of one pipeline. |
| `pc-expression-semantics` | A truth table for rounding (±.5), `SUBSTR` positions (0, negative, clamped, length ≤ 0, NULL length), concatenation with NULLs, division by zero, NULL conditions in `IIF`, empty CSV fields. |

### Change log of expectations

* `pc-lookups`: after mutation analysis showed that a shifted range-lookup boundary went
  undetected, an order of exactly 100.00 was added; derived by hand: band MID, region
  EMEA, discount 5, net 95.00.
* `pc-mixed-blocked`: redesigned when lookups and row-aligned merges became supported
  (they had been its blocked constructs); the clean branch and its expectation did not
  change.

* `pc-orders`: after mutation analysis showed that replacing `COUNT(ORDER_ID)` with
  `COUNT(*)` went undetected, an order with a NULL `ORDER_ID` was added to
  `inputs/orders.csv`, and its effects were derived by hand: one more standard-band fact
  row, and customer 3's `TOTAL_GROSS` becomes 109.99 while `ORDER_COUNT` stays 1.
